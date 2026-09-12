"""Authentification simple (HTTP Basic), active dès qu'un mot de passe existe.

En usage local sur `127.0.0.1`, l'outil n'a besoin d'aucun compte. Mais dès
qu'il est joignable depuis le réseau ou depuis Internet, tout le monde peut
lancer des téléchargements et des rendus : `FLAMBEE_PASSWORD` ferme la porte.
Safari et Chrome mémorisent l'identifiant, il n'y a donc rien à ressaisir.
"""

from __future__ import annotations

import base64
import binascii
import logging
import secrets

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response

from . import config

log = logging.getLogger(__name__)

# Un en-tête HTTP ne transporte que de l'ASCII : pas d'accent dans le realm.
_CHALLENGE = {"WWW-Authenticate": 'Basic realm="Flambee", charset="UTF-8"'}


def _authorized(header: str | None) -> bool:
    if not header or not header.lower().startswith("basic "):
        return False
    try:
        decoded = base64.b64decode(header.split(" ", 1)[1], validate=True)
        username, _, password = decoded.decode("utf-8").partition(":")
    except (binascii.Error, UnicodeDecodeError, IndexError):
        return False
    # compare_digest sur les deux champs : pas de fuite par temps de réponse.
    return (
        secrets.compare_digest(username, config.USERNAME)
        and secrets.compare_digest(password, config.PASSWORD)
    )


def install_auth(app: FastAPI) -> None:
    """Ajoute le contrôle d'accès si un mot de passe est configuré."""
    if not config.PASSWORD:
        log.info("Aucun mot de passe : accès libre (usage local).")
        return

    log.info("Accès protégé par mot de passe (utilisateur « %s »).", config.USERNAME)

    @app.middleware("http")
    async def check_password(request: Request, call_next) -> Response:
        if _authorized(request.headers.get("authorization")):
            return await call_next(request)
        return JSONResponse(
            status_code=401,
            content={"detail": "Authentification requise."},
            headers=_CHALLENGE,
        )
