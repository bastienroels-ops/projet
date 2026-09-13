"""Interface web locale (FastAPI) orchestrant les 5 étapes de Flambée."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import shutil
import time
import threading
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import RedirectResponse
from fastapi.responses import (FileResponse, HTMLResponse, JSONResponse,
                               PlainTextResponse, Response)
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

from . import (__version__, account, config, downloader, media, pipeline,
               plans, samples, scriptgen, site, transcribe, users, voice,
               voicestudio)
from . import courriel, icones
from .auth import (fermer_session, install_auth, ouvrir_session,
                   requete_securisee, utilisateur_courant)
from .project import Project, store

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s  %(levelname)-7s %(name)s: %(message)s"
)
log = logging.getLogger("flambee")

BASE = Path(__file__).resolve().parent

app = FastAPI(title="Flambée", version=__version__, docs_url="/api/docs")
install_auth(app)
app.mount("/static", StaticFiles(directory=BASE / "static"), name="static")
templates = Jinja2Templates(directory=str(BASE / "templates"))
# Le jeu d'icônes est accessible partout : {{ icone("micro") }}. Les emoji
# qu'il remplace appartenaient au système d'exploitation du visiteur, pas au
# site — trois appareils, trois dessins, trois poids visuels.
templates.env.globals["icone"] = icones.icone


# --- Modèles de requête ---------------------------------------------------
class SourcesIn(BaseModel):
    urls: str = Field(default="", description="Liens collés, un par ligne")


class HookIn(BaseModel):
    hook_index: int


class SettingsIn(BaseModel):
    voice: str = config.DEFAULT_VOICE
    voice_rate: str = "+0%"
    voice_pitch: str = "+0Hz"
    subtitles: bool = True
    music: str | None = None
    music_volume: float = 0.12
    mask_source_subtitles: bool = False
    mask_mode: str = "blur"
    mask_height_ratio: float = 0.22
    keep_source_audio: bool = False
    source_audio_volume: float = 0.05
    motion: bool = True
    scene_aware: bool = True
    subtitle_preset: str = config.DEFAULT_SUBTITLE_PRESET


class RenderIn(BaseModel):
    fast: bool = False        # aperçu rapide : encodage allégé


class ScriptIn(BaseModel):
    topic: str = ""
    instructions: str = ""
    duration: int = 45
    script: str = ""


# --- Helpers --------------------------------------------------------------
def _utilisateur(request: Request) -> users.Utilisateur:
    """Le compte connecté. Le middleware l'a déjà exigé ; ceci ferme la porte
    au cas où une route échapperait au contrôle."""
    utilisateur = utilisateur_courant(request)
    if utilisateur is None:
        raise HTTPException(status_code=401, detail="Connecte-toi pour continuer.")
    return utilisateur


def _get(project_id: str, request: Request) -> Project:
    """Un projet, à condition qu'il appartienne au compte connecté.

    Le propriétaire fait partie du chemin d'accès : un identifiant deviné ne
    donne rien s'il appartient à quelqu'un d'autre.
    """
    project = store.get(project_id, owner=_utilisateur(request).id)
    if project is None:
        raise HTTPException(status_code=404, detail="Projet introuvable.")
    return project


def _require_idle(project: Project) -> None:
    if project.job.running:
        raise HTTPException(
            status_code=409,
            detail=f"Une tâche est déjà en cours ({project.job.name}).",
        )


def _spawn(target, *args) -> None:
    threading.Thread(target=target, args=args, daemon=True).start()


def _start_job(project: Project, name: str, message: str) -> None:
    """Marque la tâche comme démarrée avant de lancer le thread.

    Sans ça, la réponse renvoyée au navigateur peut encore annoncer « idle » :
    le premier sondage conclurait qu'il n'y a rien à suivre et arrêterait le
    suivi avant même que la tâche ne commence.
    """
    project.set_job(name, "running", progress=0.01, message=message)


def _spawn_render(project: Project, *, fast: bool) -> None:
    threading.Thread(
        target=pipeline.run_render, args=(project,), kwargs={"fast": fast},
        daemon=True,
    ).start()


def _project_payload(project: Project) -> dict:
    data = project.to_dict()
    for source in data["sources"]:
        if source.get("hook_path"):
            source["hook_url"] = f"/api/projects/{project.id}/hooks/{source['index']}"
        source.pop("path", None)          # chemin disque : inutile côté client
    if project.output_path:
        data["output_url"] = f"/api/projects/{project.id}/output"
        data["viewing_url"] = f"/api/projects/{project.id}/viewing"
        data["output_name"] = Path(project.output_path).name
    if project.preview_path and Path(project.preview_path).exists():
        data["preview_url"] = f"/api/projects/{project.id}/preview"
    data["recommended_hook"] = project.recommended_hook
    data["script_notes"] = scriptgen.review(project.script) if project.script else []
    data["estimated_duration"] = round(
        scriptgen.estimate_duration(project.script), 1
    ) if project.script else 0.0
    return data


# --- Site public ----------------------------------------------------------
def _base_url(request: Request | None) -> str:
    """Adresse publique du site, pour les liens de partage."""
    configuree = os.environ.get("FLAMBEE_BASE_URL", "").strip().rstrip("/")
    if configuree:
        return configuree
    if request is None:
        return ""
    # Derrière un proxy, c'est lui qui connaît le schéma réel.
    schema = request.headers.get("x-forwarded-proto", "").split(",")[0].strip()
    schema = schema or request.url.scheme
    return f"{schema}://{request.url.netloc}"


def _contexte_site(page: str, request: Request | None = None, **extra) -> dict:
    """Contexte commun à toutes les pages publiques."""
    connecte = utilisateur_courant(request) if request is not None else None
    return {
        "page": page,
        "connecte": connecte,
        # Inviter à créer un compte quand les inscriptions sont fermées mène à
        # une page qui refuse. Sur un déploiement privé — Colab, un serveur
        # personnel — c'est la situation normale, pas l'exception.
        "inscriptions": users.inscriptions_ouvertes(),
        "base_url": _base_url(request),
        "chemin": request.url.path if request is not None else "/",
        "annee": time.strftime("%Y"),
        "plans": plans.PLANS,
        "features": plans.FEATURES,
        "steps": plans.STEPS,
        "faq": plans.FAQ,
        "duel": plans.DUEL,
        "presets": [
            {"id": nom, "label": style.label, "description": style.description,
             # De quoi redessiner le style dans un canevas, pour l'aperçu du
             # navigateur quand aucun serveur ne peut encoder à la demande.
             "rendu": config.style_pour_le_web(style)}
            for nom, style in config.SUBTITLE_PRESETS.items()
        ],
        **extra,
    }


@app.get("/", response_class=HTMLResponse)
async def accueil(request: Request):
    return templates.TemplateResponse(request, "site/index.html",
                                      _contexte_site("accueil", request))


@app.get("/fonctionnalites", response_class=HTMLResponse)
async def fonctionnalites(request: Request):
    return templates.TemplateResponse(request, "site/fonctionnalites.html",
                                      _contexte_site("fonctionnalites", request))


@app.get("/tarifs", response_class=HTMLResponse)
async def tarifs(request: Request):
    return templates.TemplateResponse(request, "site/tarifs.html",
                                      _contexte_site("tarifs", request))


@app.get("/faq", response_class=HTMLResponse)
async def faq(request: Request):
    return templates.TemplateResponse(request, "site/faq.html",
                                      _contexte_site("faq", request))


def _suite_sure(suite: str) -> str:
    """N'accepte qu'une redirection interne : une URL absolue permettrait
    d'envoyer l'utilisateur sur un autre site après connexion."""
    suite = (suite or "").strip()
    if suite.startswith("/") and not suite.startswith("//"):
        return suite
    return "/studio"


@app.get("/connexion", response_class=HTMLResponse)
async def connexion(request: Request, suite: str = "", bienvenue: bool = False):
    if utilisateur_courant(request):
        return RedirectResponse(_suite_sure(suite), status_code=303)
    return templates.TemplateResponse(
        request, "site/connexion.html",
        _contexte_site("connexion", request, suite=_suite_sure(suite), email="",
                       bienvenue=bienvenue),
    )


@app.post("/connexion", response_class=HTMLResponse)
async def connexion_envoi(request: Request, email: str = Form(""),
                          mot_de_passe: str = Form(""), suite: str = Form("")):
    try:
        utilisateur = users.authentifier(email, mot_de_passe)
    except users.CompteError as exc:
        return templates.TemplateResponse(
            request, "site/connexion.html",
            _contexte_site("connexion", request, suite=_suite_sure(suite),
                           email=email, erreur=str(exc)),
            status_code=401,
        )
    reponse = RedirectResponse(_suite_sure(suite), status_code=303)
    return ouvrir_session(reponse, utilisateur,
                          securise=requete_securisee(request))


@app.get("/inscription", response_class=HTMLResponse)
async def inscription(request: Request, formule: str = ""):
    if utilisateur_courant(request):
        return RedirectResponse("/studio", status_code=303)
    return templates.TemplateResponse(
        request, "site/inscription.html",
        _contexte_site("inscription", request, formule=formule, email="", nom="",
                       ouvert=users.inscriptions_ouvertes(),
                       invitation_requise=bool(users.code_invitation())),
    )


@app.post("/inscription", response_class=HTMLResponse)
async def inscription_envoi(
    request: Request,
    email: str = Form(""),
    nom: str = Form(""),
    mot_de_passe: str = Form(""),
    invitation: str = Form(""),
    formule: str = Form(""),
):
    """Crée le compte puis ouvre la session dans la foulée."""
    try:
        utilisateur = users.creer(email, mot_de_passe, nom, invitation)
    except users.CompteError as exc:
        return templates.TemplateResponse(
            request, "site/inscription.html",
            _contexte_site("inscription", request, formule=formule, email=email, nom=nom,
                           ouvert=users.inscriptions_ouvertes(),
                           invitation_requise=bool(users.code_invitation()),
                           erreur=str(exc)),
            status_code=400,
        )

    if formule in {plan.id for plan in plans.PLANS}:
        account.changer_de_formule(utilisateur, formule)

    reponse = RedirectResponse("/studio", status_code=303)
    return ouvrir_session(reponse, utilisateur,
                          securise=requete_securisee(request))


@app.get("/mot-de-passe-oublie", response_class=HTMLResponse)
async def oubli(request: Request):
    return templates.TemplateResponse(request, "site/oubli.html",
                                      _contexte_site("oubli", request, envoye=False))


@app.post("/mot-de-passe-oublie", response_class=HTMLResponse)
async def oubli_envoi(request: Request, email: str = Form("")):
    """Envoie un lien de réinitialisation.

    La réponse est la même que le compte existe ou non : autrement, ce
    formulaire dirait qui est inscrit.
    """
    utilisateur = users.par_email(email)
    envoye_par_courriel = False

    if utilisateur:
        jeton = users.creer_jeton_reinit(utilisateur.id)
        lien = f"{_base_url(request)}/reinitialiser?jeton={jeton}"
        envoye_par_courriel = await asyncio.to_thread(
            courriel.envoyer, utilisateur.email,
            "Réinitialiser ton mot de passe Flambée",
            "Bonjour,\n\n"
            "Voici le lien pour choisir un nouveau mot de passe. "
            f"Il reste valable une heure :\n\n{lien}\n\n"
            "Si tu n'es pas à l'origine de cette demande, ignore ce message : "
            "ton mot de passe actuel reste valable.\n",
        )
        if not envoye_par_courriel:
            # Sans SMTP, l'administrateur récupère le lien dans le journal.
            log.warning("Lien de réinitialisation pour %s : %s",
                        utilisateur.email, lien)

    return templates.TemplateResponse(
        request, "site/oubli.html",
        _contexte_site("oubli", request, envoye=True,
                       sans_courriel=not courriel.configure()),
    )


@app.get("/reinitialiser", response_class=HTMLResponse)
async def reinitialiser(request: Request, jeton: str = ""):
    valide = users.lire_jeton_reinit(jeton) is not None
    return templates.TemplateResponse(
        request, "site/reinitialiser.html",
        _contexte_site("reinitialiser", request, jeton=jeton, invalide=not valide),
        status_code=200 if valide else 400,
    )


@app.post("/reinitialiser", response_class=HTMLResponse)
async def reinitialiser_envoi(request: Request, jeton: str = Form(""),
                              mot_de_passe: str = Form("")):
    identifiant = users.lire_jeton_reinit(jeton)
    if identifiant is None:
        return templates.TemplateResponse(
            request, "site/reinitialiser.html",
            _contexte_site("reinitialiser", request, jeton=jeton, invalide=True),
            status_code=400,
        )
    try:
        users.definir_mot_de_passe(identifiant, mot_de_passe)
    except users.CompteError as exc:
        return templates.TemplateResponse(
            request, "site/reinitialiser.html",
            _contexte_site("reinitialiser", request, jeton=jeton, invalide=False,
                           erreur=str(exc)),
            status_code=400,
        )

    utilisateur = users.par_id(identifiant)
    reponse = RedirectResponse("/studio", status_code=303)
    return ouvrir_session(reponse, utilisateur, securise=requete_securisee(request))


@app.get("/deconnexion")
async def deconnexion():
    return fermer_session(RedirectResponse("/", status_code=303))


def _page_legale(request: Request, page: str) -> HTMLResponse:
    """Rend l'une des pages légales. Routes explicites : une route attrape-tout
    masquerait /studio et toute page ajoutée ensuite."""
    surtitre, titre, sections = site.PAGES_LEGALES[page]
    return templates.TemplateResponse(
        request, "site/legal.html",
        _contexte_site(page, request, surtitre=surtitre, titre=titre,
                       sections=sections,
                       a_completer=any(site.A_COMPLETER in paragraphe
                                       for bloc in sections
                                       for paragraphe in bloc.paragraphes)),
    )


@app.get("/mentions-legales", response_class=HTMLResponse)
async def mentions_legales(request: Request):
    return _page_legale(request, "mentions-legales")


@app.get("/conditions", response_class=HTMLResponse)
async def conditions(request: Request):
    return _page_legale(request, "conditions")


@app.get("/confidentialite", response_class=HTMLResponse)
async def confidentialite(request: Request):
    return _page_legale(request, "confidentialite")


# --- L'application --------------------------------------------------------
# --- Moteur de transcription ----------------------------------------------
# L'installation depuis l'interface évite d'avoir à rejoindre un terminal, ce
# qui n'est pas toujours possible — sur Colab, la cellule qui fait tourner le
# serveur monopolise le carnet. Elle est réservée au compte administrateur et
# n'installe qu'une liste de paquets figée, jamais un nom fourni par la requête.
_installation: dict = {"etat": "repos", "ok": False, "journal": ""}


def installation_autorisee() -> bool:
    return os.environ.get("FLAMBEE_INSTALL_MOTEUR", "1").strip().lower() \
        not in ("0", "non", "false")


def _etat_moteur(compte) -> dict:
    return {
        "disponible": transcribe.available(),
        "nom": transcribe.moteur_actif(),
        "raison": transcribe.raison_indisponible(),
        "installable": (installation_autorisee()
                        and users.est_proprietaire(compte)),
        "installation": dict(_installation),
    }


def _installer_moteur() -> None:
    ok, journal = transcribe.installer()
    _installation.update(etat="fini", ok=ok, journal=journal)


@app.post("/studio/moteur")
async def studio_moteur(request: Request):
    """Installe un moteur de transcription, à la demande de l'administrateur."""
    compte = _utilisateur(request)
    suite = _suite_sure(request.query_params.get("suite", "")) or "/studio/script-viral"

    if not installation_autorisee() or not users.est_proprietaire(compte):
        raise HTTPException(status_code=403,
                            detail="Réservé au compte administrateur.")
    if _installation["etat"] != "encours":
        _installation.update(etat="encours", ok=False, journal="")
        threading.Thread(target=_installer_moteur, daemon=True).start()
    return RedirectResponse(suite, status_code=303)


def _contexte_app(rubrique: str, request: Request, **extra) -> dict:
    """Contexte commun à toutes les pages de l'application connectée."""
    compte = _utilisateur(request)
    resume = account.resume(compte)
    return {
        "rubrique": rubrique,
        "compte": compte,
        "plan_actuel": resume["plan"],
        "credits": resume["credits"],
        "profil": {"initiales": compte.initiales},
        "plans": plans.PLANS,
        "steps": plans.STEPS,
        "version": __version__,
        "moteur": _etat_moteur(compte),
        **extra,
    }


@app.get("/studio", response_class=HTMLResponse)
async def studio(request: Request):
    return templates.TemplateResponse(
        request, "studio/creer.html",
        _contexte_app("creer", request,
                      min_sources=config.MIN_SOURCES,
                      max_sources=config.MAX_SOURCES,
                      output_dir=str(config.OUTPUT_DIR)),
    )


@app.get("/studio/creations", response_class=HTMLResponse)
async def studio_creations(request: Request):
    compte = _utilisateur(request)
    # La conservation annoncée par la formule s'applique ici, au moment où
    # l'on regarde ses projets : pas d'ordonnanceur à faire tourner, et le
    # ménage se fait sur le compte qui s'en sert.
    jours = account.retention_jours(compte)
    effaces = await asyncio.to_thread(store.purge, compte.id, jours)
    if effaces:
        log.info("Conservation : %d projet(s) effacé(s) pour %s (%s jours)",
                 len(effaces), compte.email, jours)

    creations = []
    for projet in store.list_recent(limit=40, owner=compte.id):
        creations.append({
            "id": projet.id,
            "titre": projet.topic or (projet.sources[0].title if projet.sources
                                      else "Projet sans titre"),
            "date": time.strftime("%d/%m/%Y", time.localtime(projet.created_at)),
            "step": projet.step,
            "viewing_url": (f"/api/projects/{projet.id}/viewing"
                            if projet.output_path
                            and Path(projet.output_path).exists() else ""),
            "poster_url": (f"/api/projects/{projet.id}/poster"
                           if projet.output_path
                           and Path(projet.output_path).exists() else ""),
        })
    return templates.TemplateResponse(
        request, "studio/creations.html",
        _contexte_app("creations", request, creations=creations,
                      retention=jours, effaces=len(effaces)),
    )


@app.get("/studio/tutoriel", response_class=HTMLResponse)
async def studio_tutoriel(request: Request):
    return templates.TemplateResponse(request, "studio/tutoriel.html",
                                      _contexte_app("tutoriel", request))


@app.get("/studio/communaute", response_class=HTMLResponse)
async def studio_communaute(request: Request):
    return templates.TemplateResponse(request, "studio/communaute.html",
                                      _contexte_app("communaute", request))


@app.get("/studio/abonnement", response_class=HTMLResponse)
async def studio_abonnement(request: Request):
    return templates.TemplateResponse(request, "studio/abonnement.html",
                                      _contexte_app("abonnement", request))


@app.post("/studio/abonnement", response_class=HTMLResponse)
async def studio_changer_formule(request: Request, plan: str = Form("")):
    account.changer_de_formule(_utilisateur(request), plan)
    return RedirectResponse("/studio/abonnement", status_code=303)


@app.get("/studio/credits", response_class=HTMLResponse)
async def studio_credits(request: Request):
    compte = _utilisateur(request)
    libelles = {"rendu": "Vidéo rendue", "apercu": "Aperçu",
                "transcription": "Transcription", "voix": "Voix importée"}
    historique = [
        {"date": evenement.get("date", ""),
         "libelle": libelles.get(evenement.get("type", ""), evenement.get("type", ""))}
        for evenement in reversed(compte.historique[-25:])
    ]
    return templates.TemplateResponse(
        request, "studio/credits.html",
        _contexte_app("credits", request, historique=historique),
    )


@app.get("/studio/profil", response_class=HTMLResponse)
async def studio_profil(request: Request, enregistre: bool = False,
                        mot_de_passe: bool = False, confirmation: str = ""):
    return templates.TemplateResponse(
        request, "studio/profil.html",
        _contexte_app("profil", request, auth=bool(config.PASSWORD),
                      enregistre=enregistre, mot_de_passe_change=mot_de_passe,
                      confirmation=confirmation),
    )


@app.post("/studio/profil", response_class=HTMLResponse)
async def studio_profil_envoi(request: Request, nom: str = Form("")):
    users.mettre_a_jour(_utilisateur(request).id, nom=nom)
    return RedirectResponse("/studio/profil?enregistre=true", status_code=303)


@app.post("/studio/profil/mot-de-passe", response_class=HTMLResponse)
async def studio_changer_mot_de_passe(request: Request, ancien: str = Form(""),
                                      nouveau: str = Form("")):
    try:
        users.changer_mot_de_passe(_utilisateur(request).id, ancien, nouveau)
    except users.CompteError as exc:
        return templates.TemplateResponse(
            request, "studio/profil.html",
            _contexte_app("profil", request, auth=bool(config.PASSWORD),
                          erreur_mdp=str(exc)),
            status_code=400,
        )
    return RedirectResponse("/studio/profil?mot_de_passe=true", status_code=303)


@app.post("/studio/profil/supprimer")
async def studio_supprimer_compte(request: Request, confirmation: str = Form("")):
    """Droit à l'effacement : le compte et tout son espace de travail."""
    utilisateur = _utilisateur(request)
    if confirmation.strip().upper() != "SUPPRIMER":
        return RedirectResponse("/studio/profil?confirmation=manquante",
                                status_code=303)
    users.supprimer(utilisateur.id)
    return fermer_session(RedirectResponse("/", status_code=303))


def _version_du_code() -> str:
    """Le commit en cours d'exécution, pour savoir si la session est à jour."""
    import subprocess

    racine = Path(__file__).resolve().parent.parent
    try:
        resultat = subprocess.run(
            ["git", "-C", str(racine), "log", "-1", "--format=%h — %cd",
             "--date=format:%d/%m/%Y %H:%M"],
            capture_output=True, text=True, timeout=5,
        )
        if resultat.returncode == 0 and resultat.stdout.strip():
            return resultat.stdout.strip()
    except Exception:
        pass
    return f"version {__version__} (dépôt git introuvable)"


@app.get("/studio/parametres", response_class=HTMLResponse)
async def studio_parametres(request: Request):
    manquants = media.ensure_tools()
    encodeur = media.detect_encoder() if not manquants else None
    try:
        import yt_dlp  # noqa: F401

        ytdlp = True
    except ImportError:
        ytdlp = False

    etat = [
        ("ffmpeg", "présent" if "ffmpeg" not in manquants else "absent",
         "ffmpeg" not in manquants),
        ("Encodeur", f"{encodeur.name}"
         + (" (matériel)" if encodeur and encodeur.hardware else "")
         if encodeur else "indisponible", bool(encodeur)),
        ("yt-dlp", "présent" if ytdlp else "absent", ytdlp),
        ("Transcription", (f"{transcribe.moteur_actif()}"
                           if transcribe.available()
                           else f"indisponible — {transcribe.raison_indisponible()}"),
         transcribe.available()),
        # Sans cette ligne, rien ne distingue une session qui tourne sur du
        # code d'hier d'une session à jour : c'est la source de confusion la
        # plus coûteuse quand le serveur vit ailleurs (Colab garde la cellule
        # en mémoire alors que le dépôt, lui, a bougé).
        ("Version du code", _version_du_code(), True),
        ("Clé API Claude", "configurée" if scriptgen.api_key_available()
         else "absente — mode manuel", scriptgen.api_key_available()),
        ("Musiques", f"{len(pipeline.list_music())} fichier(s)", True),
    ]
    variables = [
        ("FLAMBEE_PASSWORD", "protège l'accès dès que l'outil sort de la machine"),
        ("FLAMBEE_HOST", "0.0.0.0 pour ouvrir au réseau local"),
        ("ANTHROPIC_API_KEY", "génération du script en un clic"),
        ("FLAMBEE_COOKIES_FROM_BROWSER", "pour les vidéos qui exigent une connexion"),
        ("FLAMBEE_WHISPER_MODEL", f"modèle de transcription (actuel : {transcribe.MODEL_NAME})"),
        ("FLAMBEE_ENCODER", "force un encodeur vidéo précis"),
    ]
    return templates.TemplateResponse(
        request, "studio/parametres.html",
        _contexte_app("parametres", request, etat=etat, variables=variables,
                      dossiers={"sortie": str(config.OUTPUT_DIR),
                                "travail": str(config.WORK_DIR),
                                "musiques": str(config.MUSIC_DIR)}),
    )


# --- Script Viral ---------------------------------------------------------
@app.get("/studio/script-viral", response_class=HTMLResponse)
async def studio_script(request: Request):
    return templates.TemplateResponse(
        request, "studio/script_viral.html",
        _contexte_app("script", request),
    )


@app.post("/studio/script-viral", response_class=HTMLResponse)
async def studio_script_envoi(
    request: Request,
    url: str = Form(""),
    fichier: UploadFile | None = File(None),
):
    """Transcrit une vidéo, depuis un lien ou un fichier importé."""
    compte = _utilisateur(request)
    contexte = {"url": url}

    def echec(message: str, code: int = 400):
        return templates.TemplateResponse(
            request, "studio/script_viral.html",
            _contexte_app("script", request, erreur=message, **contexte),
            status_code=code,
        )

    if not account.est_pro(compte):
        return echec("Cette rubrique demande la formule Créateur.", 402)
    if not transcribe.available():
        return echec(f"Moteur de transcription indisponible : {transcribe.raison_indisponible()}.", 503)

    dossier = compte.dossier / "transcriptions"
    dossier.mkdir(parents=True, exist_ok=True)
    source: Path | None = None

    try:
        if fichier is not None and fichier.filename:
            suffixe = Path(fichier.filename).suffix.lower()
            if suffixe not in voicestudio.EXTENSIONS:
                return echec(f"Format non reconnu : {fichier.filename}")
            source = dossier / f"import{suffixe}"
            if await _stream_to_disk(fichier, source) == 0:
                return echec("Fichier vide.")
        elif url.strip():
            liens = downloader.normalize_urls(url)
            if not liens:
                return echec("Ce lien ne semble pas valide.")
            resultat = await asyncio.to_thread(
                downloader.download_one, liens[0], dossier, 1
            )
            if not resultat.ok:
                return echec(resultat.error or "Téléchargement impossible.")
            source = Path(resultat.path)
        else:
            return echec("Donne un lien ou choisis un fichier.")

        audio = await asyncio.to_thread(
            transcribe.ensure_audio, source, dossier / "audio.wav"
        )
        transcription = await asyncio.to_thread(transcribe.transcribe, audio)
    except (media.MediaError, transcribe.TranscriptionError) as exc:
        return echec(str(exc), 500)
    finally:
        if source and source.exists() and source.parent == dossier:
            source.unlink(missing_ok=True)

    account.noter(compte, "transcription",
                  url or (fichier.filename if fichier else ""))
    jeton = hashlib.sha256(transcription.text.encode("utf-8")).hexdigest()[:12]
    (dossier / f"{jeton}.txt").write_text(transcription.text, encoding="utf-8")

    return templates.TemplateResponse(
        request, "studio/script_viral.html",
        _contexte_app("script", request, **contexte, resultat={
            "texte": transcription.text,
            "mots": len(transcription.words),
            "langue": transcription.language or "inconnue",
            "duree": f"{transcription.duration:.0f} s",
            "jeton": jeton,
        }),
    )


# --- Voice Studio ---------------------------------------------------------
@app.get("/studio/voix", response_class=HTMLResponse)
async def studio_voix(request: Request):
    infos = voicestudio.charger(_utilisateur(request).id)
    voix = None
    if infos:
        voix = {"nom": infos.get("nom", "voix.wav"),
                "duree": f"{infos.get('duree', 0):.0f} s",
                "mots": len(infos.get("mots", []))}
    return templates.TemplateResponse(request, "studio/voix.html",
                                      _contexte_app("voix", request, voix=voix))


@app.post("/studio/voix", response_class=HTMLResponse)
async def studio_voix_envoi(request: Request, fichier: UploadFile = File(...)):
    compte = _utilisateur(request)

    def echec(message: str, code: int = 400):
        return templates.TemplateResponse(
            request, "studio/voix.html",
            _contexte_app("voix", request, voix=None, erreur=message), status_code=code,
        )

    if not account.est_pro(compte):
        return echec("Cette rubrique demande la formule Créateur.", 402)
    if not transcribe.available():
        return echec(f"Moteur de transcription indisponible : {transcribe.raison_indisponible()}.", 503)

    suffixe = Path(fichier.filename or "").suffix.lower()
    if suffixe not in voicestudio.EXTENSIONS:
        return echec(f"Format non reconnu : {fichier.filename}")

    brut = voicestudio.dossier(compte.id) / f"import{suffixe}"
    try:
        if await _stream_to_disk(fichier, brut) == 0:
            return echec("Fichier vide.")
        await asyncio.to_thread(voicestudio.enregistrer, brut,
                                fichier.filename or "enregistrement", compte.id)
    except (media.MediaError, transcribe.TranscriptionError, ValueError) as exc:
        return echec(str(exc), 500)
    finally:
        brut.unlink(missing_ok=True)

    account.noter(compte, "voix", fichier.filename or "")
    return RedirectResponse("/studio/voix", status_code=303)


@app.get("/studio/voix/fichier")
async def studio_voix_fichier(request: Request):
    chemin = voicestudio.chemin_audio(_utilisateur(request).id)
    if not chemin.exists():
        raise HTTPException(status_code=404, detail="Aucune voix importée.")
    return FileResponse(chemin, media_type="audio/wav")


@app.post("/studio/voix/supprimer")
async def studio_voix_supprimer(request: Request):
    voicestudio.supprimer(_utilisateur(request).id)
    return RedirectResponse("/studio/voix", status_code=303)


# --- Environnement --------------------------------------------------------
@app.get("/api/health")
async def health():
    missing = media.ensure_tools()
    try:
        import yt_dlp  # noqa: F401

        ytdlp = True
    except ImportError:
        ytdlp = False
    encoder = media.detect_encoder() if "ffmpeg" not in missing else None
    return {
        "version": __version__,
        "auth": bool(config.PASSWORD),
        "max_upload_mb": config.MAX_UPLOAD_BYTES // (1024 * 1024),
        "encoder": encoder.name if encoder else "",
        "hardware_encoder": bool(encoder and encoder.hardware),
        "ffmpeg": "ffmpeg" not in missing,
        "ffprobe": "ffprobe" not in missing,
        "yt_dlp": ytdlp,
        "anthropic_key": scriptgen.api_key_available(),
        "output_dir": str(config.OUTPUT_DIR),
        "music_count": len(pipeline.list_music()),
    }


@app.get("/api/voices")
async def voices(request: Request, refresh: bool = False):
    if refresh:
        try:
            listed = await asyncio.wait_for(voice.list_voices("fr"), timeout=12)
        except Exception:                  # réseau indisponible : liste locale
            listed = config.FRENCH_VOICES
    else:
        listed = list(config.FRENCH_VOICES)

    compte = _utilisateur(request)
    combien = account.nombre_de_voix(compte)
    if combien is not None:
        listed = list(listed)[:combien]

    # La voix importée passe en tête : c'est celle qu'on veut quand on l'a.
    if voicestudio.charger(compte.id):
        listed = [{"id": voicestudio.VOICE_ID, "label": "Ma voix (importée)"},
                  *listed]
    return {"voices": listed, "default": config.DEFAULT_VOICE,
            "toutes": combien is None,
            "debit_reglable": account.debit_reglable(compte)}


@app.get("/api/music")
async def music(request: Request):
    """La bibliothèque musicale, vide pour les formules qui n'y ont pas droit.

    On renvoie `autorisee` plutôt qu'une erreur : l'étape Style doit pouvoir
    expliquer pourquoi la liste est vide, au lieu de paraître cassée.
    """
    autorisee = account.musique_autorisee(_utilisateur(request))
    return {"tracks": pipeline.list_music() if autorisee else [],
            "autorisee": autorisee, "dir": str(config.MUSIC_DIR)}


# --- Projets --------------------------------------------------------------
@app.get("/api/demo")
async def demo_accueil():
    """Clip de démonstration de la page d'accueil (rendu réel, mis en cache)."""
    if media.ensure_tools():
        raise HTTPException(status_code=503, detail="ffmpeg est requis.")
    try:
        chemin = await asyncio.to_thread(samples.build_hero)
    except media.MediaError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return FileResponse(chemin, media_type="video/mp4",
                        headers={"Cache-Control": "public, max-age=86400"})


@app.get("/api/demo/poster")
async def demo_affiche():
    """Image fixe de la démonstration, pour que le cadre ne reste jamais noir."""
    if media.ensure_tools():
        raise HTTPException(status_code=503, detail="ffmpeg est requis.")
    try:
        chemin = await asyncio.to_thread(samples.hero_poster)
    except media.MediaError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return FileResponse(chemin, media_type="image/jpeg",
                        headers={"Cache-Control": "public, max-age=86400"})


# --- Essayage public des sous-titres --------------------------------------
# Un encodage déclenché par un formulaire ouvert : il faut un plafond, sinon
# une seule page rechargée en boucle occupe le processeur de la machine.
_ESSAIS_PAR_MINUTE = 12
_essais: dict[str, list[float]] = {}
_essais_verrou = threading.Lock()


def _peut_essayer(adresse: str) -> bool:
    maintenant = time.time()
    with _essais_verrou:
        recents = [t for t in _essais.get(adresse, []) if maintenant - t < 60]
        if len(recents) >= _ESSAIS_PAR_MINUTE:
            _essais[adresse] = recents
            return False
        recents.append(maintenant)
        _essais[adresse] = recents
        if len(_essais) > 2000:            # ménage : on ne garde pas l'Internet
            for cle in [c for c, v in _essais.items()
                        if not v or maintenant - v[-1] > 300]:
                _essais.pop(cle, None)
    return True


@app.get("/api/essayage")
async def essayage(request: Request, texte: str = "", style: str = ""):
    """Rend la phrase du visiteur dans le style choisi, avec le vrai moteur.

    C'est l'argument du site : ce que l'on voit ici n'est pas une imitation en
    HTML, c'est le fichier que ffmpeg produirait dans le montage final.
    """
    style = style or config.DEFAULT_SUBTITLE_PRESET
    if style not in config.SUBTITLE_PRESETS:
        raise HTTPException(status_code=404, detail="Style inconnu.")
    propre = samples.nettoyer_texte(texte)
    if not propre:
        raise HTTPException(status_code=400, detail="Écris une phrase.")
    if media.ensure_tools():
        raise HTTPException(status_code=503, detail="ffmpeg est requis.")

    # Un essayage déjà en cache ne coûte rien : il ne consomme pas de quota.
    chemin = samples.essayage_path(propre, style)
    if not chemin.exists():
        adresse = request.client.host if request.client else "inconnu"
        if not _peut_essayer(adresse):
            raise HTTPException(
                status_code=429,
                detail="Trop d'essais d'un coup. Laisse passer une minute.",
            )
        try:
            chemin = await asyncio.to_thread(samples.essayage, propre, style)
        except media.MediaError as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    return FileResponse(chemin, media_type="video/mp4",
                        headers={"Cache-Control": "public, max-age=86400"})


@app.get("/api/presets/{preset}/poster")
async def preset_affiche(preset: str):
    """Image fixe d'un style : la vignette n'est jamais un rectangle noir."""
    if preset not in config.SUBTITLE_PRESETS:
        raise HTTPException(status_code=404, detail="Style inconnu.")
    if media.ensure_tools():
        raise HTTPException(status_code=503, detail="ffmpeg est requis.")
    try:
        chemin = await asyncio.to_thread(samples.sample_poster, preset)
    except media.MediaError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return FileResponse(chemin, media_type="image/jpeg",
                        headers={"Cache-Control": "public, max-age=86400"})


@app.get("/api/presets/{preset}/sample")
async def preset_sample(preset: str):
    """Échantillon vidéo du style de sous-titres, rendu par ffmpeg et mis en cache.

    C'est le rendu réel — mêmes polices, même animation que la vidéo finale —
    et non une imitation en HTML.
    """
    if preset not in config.SUBTITLE_PRESETS:
        raise HTTPException(status_code=404, detail="Style inconnu.")
    if media.ensure_tools():
        raise HTTPException(status_code=503, detail="ffmpeg est requis.")
    try:
        chemin = await asyncio.to_thread(samples.build_sample, preset)
    except media.MediaError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return FileResponse(
        chemin, media_type="video/mp4",
        headers={"Cache-Control": "public, max-age=86400"},
    )


@app.post("/api/projects")
async def create_project(request: Request):
    project = store.create(owner=_utilisateur(request).id)
    return _project_payload(project)


@app.get("/api/projects")
async def list_projects(request: Request):
    return {
        "projects": [
            {
                "id": p.id,
                "created_at": p.created_at,
                "step": p.step,
                "topic": p.topic,
                "output_name": Path(p.output_path).name if p.output_path else "",
            }
            for p in store.list_recent(owner=_utilisateur(request).id)
        ]
    }


@app.get("/api/projects/{project_id}")
async def get_project(request: Request, project_id: str):
    return _project_payload(_get(project_id, request))


@app.delete("/api/projects/{project_id}")
async def delete_project(request: Request, project_id: str):
    return {"deleted": store.delete(project_id,
                                    owner=_utilisateur(request).id)}


# --- Étape 1 : sources ----------------------------------------------------
@app.post("/api/projects/{project_id}/sources")
async def add_sources(request: Request, project_id: str, body: SourcesIn):
    project = _get(project_id, request)
    _require_idle(project)

    urls = downloader.normalize_urls(body.urls)
    try:
        downloader.validate_urls(urls)
    except downloader.DownloadError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    missing = media.ensure_tools()
    if missing:
        raise HTTPException(
            status_code=400,
            detail=f"Outil manquant : {', '.join(missing)}. Installe ffmpeg.",
        )

    project.hook_index = None
    _start_job(project, "download", f"Téléchargement de {len(urls)} vidéo(s)…")
    _spawn(pipeline.run_download, project, urls)
    return _project_payload(project)


@app.post("/api/projects/{project_id}/uploads")
async def upload_sources(request: Request, project_id: str, files: list[UploadFile] = File(...)):
    """Importe des vidéos depuis l'appareil (pellicule iPhone, Fichiers…).

    C'est l'alternative au téléchargement quand les plateformes le refusent,
    et la seule voie possible quand l'app tourne sur un serveur distant.
    """
    project = _get(project_id, request)
    _require_idle(project)

    if not files:
        raise HTTPException(status_code=400, detail="Aucun fichier reçu.")
    if len(files) > config.MAX_SOURCES:
        raise HTTPException(
            status_code=400,
            detail=f"Maximum {config.MAX_SOURCES} vidéos ({len(files)} reçues).",
        )

    project.ensure_dirs()
    saved: list[Path] = []
    titles: list[str] = []
    for position, upload in enumerate(files, start=1):
        suffix = Path(upload.filename or "").suffix.lower()
        if suffix not in config.UPLOAD_EXTENSIONS:
            raise HTTPException(
                status_code=400,
                detail=f"Format non reconnu : {upload.filename}. Formats acceptés : "
                       + ", ".join(sorted(config.UPLOAD_EXTENSIONS)),
            )
        destination = project.sources_dir / f"import_{position:02d}{suffix}"
        try:
            written = await _stream_to_disk(upload, destination)
        except ValueError as exc:
            raise HTTPException(status_code=413, detail=str(exc)) from exc
        if written == 0:
            raise HTTPException(status_code=400,
                                detail=f"Fichier vide : {upload.filename}")
        saved.append(destination)
        titles.append(Path(upload.filename or destination.name).stem)

    project.hook_index = None
    _start_job(project, "import", f"Analyse de {len(saved)} fichier(s)…")
    _spawn(pipeline.run_import, project, saved, titles)
    return _project_payload(project)


async def _stream_to_disk(upload: UploadFile, destination: Path) -> int:
    """Écrit un fichier reçu par morceaux, sans le charger en mémoire."""
    written = 0
    with destination.open("wb") as handle:
        while chunk := await upload.read(1024 * 1024):
            written += len(chunk)
            if written > config.MAX_UPLOAD_BYTES:
                handle.close()
                destination.unlink(missing_ok=True)
                raise ValueError(
                    f"{upload.filename} dépasse "
                    f"{config.MAX_UPLOAD_BYTES // (1024 * 1024)} Mo."
                )
            handle.write(chunk)
    return written


# --- Étape 2 : hook -------------------------------------------------------
@app.post("/api/projects/{project_id}/hook")
async def choose_hook(request: Request, project_id: str, body: HookIn):
    project = _get(project_id, request)
    source = project.source(body.hook_index)
    if source is None or not source.ok:
        raise HTTPException(status_code=400, detail="Source indisponible.")
    project.hook_index = body.hook_index
    project.step = max(project.step, 3)
    project.save()
    return _project_payload(project)


@app.get("/api/projects/{project_id}/hooks/{index}")
async def hook_preview(request: Request, project_id: str, index: int):
    project = _get(project_id, request)
    source = project.source(index)
    if source is None or not source.hook_path or not Path(source.hook_path).exists():
        raise HTTPException(status_code=404, detail="Accroche introuvable.")
    return FileResponse(source.hook_path, media_type="video/mp4")


# --- Étape 3 : style ------------------------------------------------------
@app.post("/api/projects/{project_id}/settings")
async def update_settings(request: Request, project_id: str, body: SettingsIn):
    project = _get(project_id, request)
    data = body.model_dump()
    compte_reglages = _utilisateur(request)
    if data.get("music") and (
            not account.musique_autorisee(compte_reglages)
            or data["music"] not in {t["id"] for t in pipeline.list_music()}):
        data["music"] = None
    data["music_volume"] = max(0.0, min(1.0, data["music_volume"]))
    data["mask_height_ratio"] = max(0.05, min(0.6, data["mask_height_ratio"]))
    data["source_audio_volume"] = max(0.0, min(1.0, data["source_audio_volume"]))
    if data["mask_mode"] not in ("blur", "black"):
        data["mask_mode"] = "blur"
    if data["subtitle_preset"] not in config.SUBTITLE_PRESETS:
        data["subtitle_preset"] = config.DEFAULT_SUBTITLE_PRESET
    # La formule décide, pas la requête : sans ce contrôle, un style réservé
    # s'obtiendrait en postant son identifiant à la main.
    compte = _utilisateur(request)
    if not account.style_autorise(compte, data["subtitle_preset"]):
        raise HTTPException(
            status_code=402,
            detail="Ce style de sous-titres demande la formule Créateur.",
        )
    if not account.debit_reglable(compte):
        defauts = config.RenderSettings()
        data["voice_rate"] = defauts.voice_rate
        data["voice_pitch"] = defauts.voice_pitch

    project.settings = config.RenderSettings(**data)
    project.step = max(project.step, 4)
    project.save()
    return _project_payload(project)


# --- Étape 4 : script -----------------------------------------------------
@app.post("/api/projects/{project_id}/script/prompt")
async def script_prompt(request: Request, project_id: str, body: ScriptIn):
    project = _get(project_id, request)
    try:
        prompt = scriptgen.full_prompt_for_copy(
            body.topic, body.instructions, body.duration
        )
    except scriptgen.ScriptError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    project.topic = body.topic
    project.instructions = body.instructions
    project.save()
    return {"prompt": prompt, "api_available": scriptgen.api_key_available()}


@app.post("/api/projects/{project_id}/script/generate")
async def generate_script(request: Request, project_id: str, body: ScriptIn):
    project = _get(project_id, request)
    project.topic = body.topic
    project.instructions = body.instructions
    try:
        script = await asyncio.to_thread(
            scriptgen.generate, body.topic, body.instructions, duration=body.duration
        )
    except scriptgen.ScriptError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    project.script = script
    project.step = max(project.step, 4)
    project.save()
    return _project_payload(project)


@app.post("/api/projects/{project_id}/script")
async def save_script(request: Request, project_id: str, body: ScriptIn):
    project = _get(project_id, request)
    script = scriptgen.tidy(body.script)
    if not script:
        raise HTTPException(status_code=400, detail="Le script est vide.")
    project.script = script
    if body.topic:
        project.topic = body.topic
    if body.instructions:
        project.instructions = body.instructions
    project.step = max(project.step, 5)
    project.save()
    return _project_payload(project)


# --- Étape 5 : rendu ------------------------------------------------------
@app.post("/api/projects/{project_id}/render")
async def render(request: Request, project_id: str,
                 body: RenderIn | None = None):
    project = _get(project_id, request)
    _require_idle(project)

    # Le quota se vérifie en premier : inutile de contrôler le projet si le
    # rendu ne peut de toute façon pas partir.
    fast = bool(body and body.fast)
    if not fast and not account.peut_rendre(_utilisateur(request)):
        raise HTTPException(
            status_code=402,
            detail="Crédits épuisés pour ce mois-ci. Les aperçus restent "
                   "illimités, et la page Abonnement permet de changer de formule.",
        )

    # Un projet peut avoir été réglé sous une formule supérieure, puis le
    # compte être redescendu : on vérifie au moment du rendu, pas seulement
    # à l'enregistrement des réglages.
    if not account.style_autorise(_utilisateur(request),
                                  project.settings.subtitle_preset):
        raise HTTPException(
            status_code=402,
            detail="Le style de sous-titres de ce projet demande la formule "
                   "Créateur. Choisis-en un autre à l'étape Style.",
        )
    if not project.script.strip() and project.settings.voice != voicestudio.VOICE_ID:
        raise HTTPException(status_code=400, detail="Valide d'abord un script.")
    if not project.ready_sources:
        raise HTTPException(status_code=400, detail="Aucune vidéo source prête.")
    if project.hook_index is None:
        project.hook_index = (
            project.recommended_hook or project.ready_sources[0].index
        )
    _start_job(project, "render", "Aperçu en préparation…" if fast else "Préparation…")
    _spawn_render(project, fast=fast)
    return _project_payload(project)


@app.post("/api/projects/{project_id}/cancel")
async def cancel_job(request: Request, project_id: str):
    """Coupe la tâche en cours (téléchargement ou rendu)."""
    project = _get(project_id, request)
    stopped = pipeline.request_cancel(project.id)
    return {"cancelled": stopped, "job": project.job.name}


@app.get("/api/presets")
async def presets(request: Request):
    """Les styles, chacun marqué ouvert ou réservé selon la formule.

    On renvoie la liste entière plutôt que la liste filtrée : voir ce que l'on
    n'a pas est le propre d'une offre, et masquer les styles réservés rendrait
    la différence entre formules invisible.
    """
    compte = _utilisateur(request)
    ouverts = set(account.styles_autorises(compte))
    return {
        "subtitles": [
            {"id": name, "label": style.label, "description": style.description,
             "font": style.font, "animate": style.animate,
             "verrouille": name not in ouverts}
            for name, style in config.SUBTITLE_PRESETS.items()
        ],
        "default": config.DEFAULT_SUBTITLE_PRESET,
        "debit_reglable": account.debit_reglable(compte),
    }


@app.get("/api/projects/{project_id}/output")
async def download_output(request: Request, project_id: str, download: bool = False):
    project = _get(project_id, request)
    if not project.output_path or not Path(project.output_path).exists():
        raise HTTPException(status_code=404, detail="Aucun rendu disponible.")
    return FileResponse(
        project.output_path,
        media_type="video/mp4",
        filename=Path(project.output_path).name if download else None,
    )


@app.get("/api/projects/{project_id}/poster")
async def projet_affiche(request: Request, project_id: str):
    """Vignette d'un rendu, pour la liste des créations."""
    project = _get(project_id, request)
    if not project.output_path or not Path(project.output_path).exists():
        raise HTTPException(status_code=404, detail="Aucun rendu disponible.")
    try:
        chemin = await asyncio.to_thread(
            samples.output_poster, Path(project.output_path),
            project.dir / ".viewing")
    except media.MediaError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return FileResponse(chemin, media_type="image/jpeg",
                        headers={"Cache-Control": "private, max-age=3600"})


@app.get("/api/projects/{project_id}/viewing")
async def viewing_copy(request: Request, project_id: str):
    """Copie allégée du rendu final, pour la lecture dans la page."""
    project = _get(project_id, request)
    if not project.output_path or not Path(project.output_path).exists():
        raise HTTPException(status_code=404, detail="Aucun rendu disponible.")
    try:
        chemin = await asyncio.to_thread(
            samples.viewing_copy, Path(project.output_path), project.dir / ".viewing"
        )
    except media.MediaError:
        chemin = Path(project.output_path)      # à défaut, le fichier d'origine
    return FileResponse(chemin, media_type="video/mp4")


@app.get("/api/projects/{project_id}/preview")
async def download_preview(request: Request, project_id: str):
    project = _get(project_id, request)
    if not project.preview_path or not Path(project.preview_path).exists():
        raise HTTPException(status_code=404, detail="Aucun aperçu disponible.")
    return FileResponse(project.preview_path, media_type="video/mp4")


@app.post("/api/projects/{project_id}/cleanup")
async def cleanup(request: Request, project_id: str):
    """Supprime les fichiers de travail en gardant le rendu final."""
    project = _get(project_id, request)
    _require_idle(project)
    freed = 0
    for folder in (project.sources_dir, project.clips_dir):
        if folder.exists():
            freed += sum(f.stat().st_size for f in folder.rglob("*") if f.is_file())
            shutil.rmtree(folder, ignore_errors=True)
    project.ensure_dirs()
    return {"freed_bytes": freed}


@app.get("/robots.txt", response_class=PlainTextResponse)
async def robots(request: Request):
    """L'atelier n'a rien à faire dans un moteur de recherche."""
    return "\n".join([
        "User-agent: *",
        "Allow: /",
        "Disallow: /studio",
        "Disallow: /api/",
        f"Sitemap: {_base_url(request)}/sitemap.xml",
        "",
    ])


@app.get("/sitemap.xml")
async def sitemap(request: Request):
    base = _base_url(request)
    pages = ["/", "/fonctionnalites", "/tarifs", "/faq", "/inscription",
             "/connexion", "/mentions-legales", "/conditions", "/confidentialite"]
    entrees = "".join(
        f"<url><loc>{base}{page}</loc><changefreq>weekly</changefreq></url>"
        for page in pages
    )
    return Response(
        content=f'<?xml version="1.0" encoding="UTF-8"?>'
                f'<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
                f"{entrees}</urlset>",
        media_type="application/xml",
    )


@app.exception_handler(404)
async def page_introuvable(request: Request, _exc):
    """Une page d'erreur qui reste dans l'identité du site."""
    if request.url.path.startswith("/api/"):
        return JSONResponse(status_code=404, content={"detail": "Introuvable."})
    return templates.TemplateResponse(
        request, "site/erreur.html",
        _contexte_site("erreur", request, code=404, titre="Cette page n'existe pas",
                       message="Le lien est peut-être ancien, ou l'adresse mal "
                               "recopiée. Le reste du site fonctionne."),
        status_code=404,
    )


@app.exception_handler(500)
async def erreur_serveur(request: Request, _exc):
    # « L'incident est enregistré côté serveur » n'est vrai que si on l'écrit.
    # Starlette relaie bien l'exception au serveur, mais derrière un tunnel
    # Colab cette trace se perd : une ligne explicite, préfixée, se retrouve
    # dans le journal même quand tout le reste défile.
    log.exception("ERREUR 500 sur %s %s", request.method, request.url.path)
    if request.url.path.startswith("/api/"):
        return JSONResponse(status_code=500,
                            content={"detail": "Erreur interne."})
    return templates.TemplateResponse(
        request, "site/erreur.html",
        _contexte_site("erreur", request, code=500, titre="Quelque chose a cédé",
                       message="L'incident est enregistré côté serveur. "
                               "Réessaie dans un instant."),
        status_code=500,
    )


@app.exception_handler(media.MediaError)
async def media_error_handler(_request: Request, exc: media.MediaError):
    return JSONResponse(status_code=500, content={"detail": str(exc)})


def main() -> None:
    """Point d'entrée `python -m flambee.app`.

    Par défaut le serveur n'écoute que sur la machine locale. `FLAMBEE_HOST=0.0.0.0`
    l'ouvre au réseau local, pour piloter l'outil depuis un téléphone sur le même
    Wi-Fi — tout le monde sur ce réseau peut alors y accéder.
    """
    import uvicorn

    uvicorn.run(
        "flambee.app:app",
        host=os.environ.get("FLAMBEE_HOST", "127.0.0.1"),
        port=int(os.environ.get("FLAMBEE_PORT", "8000")),
        reload=False,
        log_level="info",
    )


if __name__ == "__main__":
    main()
