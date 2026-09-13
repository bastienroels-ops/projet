"""Authentification par session.

Le visiteur se connecte, reçoit un cookie signé, et chaque page de l'atelier
vérifie ce cookie. Deux détails qui comptent :

- le cookie est `HttpOnly` et `SameSite=Lax` : inaccessible au JavaScript, et
  non transmis lors d'une navigation venue d'un autre site ;
- `Secure` est posé dès que la requête arrive en HTTPS, pour que le cookie ne
  circule jamais en clair une fois le service en ligne.

Un verrou global hérité (`FLAMBEE_PASSWORD`) reste possible pour un déploiement
privé : il s'ajoute aux comptes plutôt que de les remplacer.
"""

from __future__ import annotations

import base64
import binascii
import logging
import secrets

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse, Response

from . import config, users
from .users import SESSION_COOKIE, Utilisateur

log = logging.getLogger(__name__)

_CHALLENGE = {"WWW-Authenticate": 'Basic realm="Flambee", charset="UTF-8"'}

# Chemins qui restent ouverts sans compte.
PUBLIC = (
    "/", "/fonctionnalites", "/tarifs", "/faq", "/mentions-legales",
    "/conditions", "/confidentialite", "/connexion", "/inscription",
    "/deconnexion", "/api/demo", "/api/health",
)
PREFIXES_PUBLICS = ("/static/", "/api/presets/")


def utilisateur_courant(request: Request) -> Utilisateur | None:
    """Le compte connecté, ou None. Mis en cache pour la durée de la requête."""
    if hasattr(request.state, "utilisateur"):
        return request.state.utilisateur

    jeton = request.cookies.get(SESSION_COOKIE, "")
    identifiant = users.lire_session(jeton) if jeton else None
    utilisateur = users.par_id(identifiant) if identifiant else None
    request.state.utilisateur = utilisateur
    return utilisateur


def ouvrir_session(reponse: Response, utilisateur: Utilisateur,
                   *, securise: bool) -> Response:
    reponse.set_cookie(
        SESSION_COOKIE,
        users.creer_session(utilisateur.id),
        max_age=users.SESSION_DAYS * 86400,
        httponly=True,
        samesite="lax",
        secure=securise,
        path="/",
    )
    return reponse


def fermer_session(reponse: Response) -> Response:
    reponse.delete_cookie(SESSION_COOKIE, path="/")
    return reponse


def requete_securisee(request: Request) -> bool:
    """HTTPS direct, ou derrière un proxy qui l'annonce."""
    if request.url.scheme == "https":
        return True
    return request.headers.get("x-forwarded-proto", "").split(",")[0].strip() == "https"


def _est_public(chemin: str) -> bool:
    return chemin in PUBLIC or chemin.startswith(PREFIXES_PUBLICS)


# --- Verrou global hérité -------------------------------------------------
def _autorise_basic(entete: str | None) -> bool:
    if not entete or not entete.lower().startswith("basic "):
        return False
    try:
        decode = base64.b64decode(entete.split(" ", 1)[1], validate=True)
        identifiant, _, mot_de_passe = decode.decode("utf-8").partition(":")
    except (binascii.Error, UnicodeDecodeError, IndexError):
        return False
    return (secrets.compare_digest(identifiant, config.USERNAME)
            and secrets.compare_digest(mot_de_passe, config.PASSWORD))


def install_auth(app: FastAPI) -> None:
    """Installe les deux contrôles : verrou global puis session."""

    @app.middleware("http")
    async def controler(request: Request, call_next) -> Response:
        # 1. Verrou global, s'il est configuré : il précède tout le reste.
        if config.PASSWORD and not _autorise_basic(request.headers.get("authorization")):
            return JSONResponse(status_code=401,
                                content={"detail": "Authentification requise."},
                                headers=_CHALLENGE)

        chemin = request.url.path
        if _est_public(chemin) or request.method == "OPTIONS":
            return await call_next(request)

        # 2. Session : tout ce qui touche à l'atelier exige un compte.
        if utilisateur_courant(request) is None:
            if chemin.startswith("/api/"):
                return JSONResponse(status_code=401,
                                    content={"detail": "Connecte-toi pour continuer."})
            suite = request.url.path
            if request.url.query:
                suite += "?" + request.url.query
            return RedirectResponse(f"/connexion?suite={suite}", status_code=303)

        return await call_next(request)

    if config.PASSWORD:
        log.info("Verrou global actif (utilisateur « %s »).", config.USERNAME)
    log.info("Comptes : %s inscription(s) %s.",
             users.compter() if config.WORK_DIR.exists() else 0,
             "ouvertes" if users.inscriptions_ouvertes() else "fermées")
