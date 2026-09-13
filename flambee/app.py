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
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

from . import (__version__, account, config, downloader, media, pipeline,
               plans, samples, scriptgen, site, transcribe, voice, voicestudio)
from .auth import install_auth
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
def _get(project_id: str) -> Project:
    project = store.get(project_id)
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
def _contexte_site(page: str, **extra) -> dict:
    """Contexte commun à toutes les pages publiques."""
    return {
        "page": page,
        "annee": time.strftime("%Y"),
        "plans": plans.PLANS,
        "features": plans.FEATURES,
        "steps": plans.STEPS,
        "faq": plans.FAQ,
        "presets": [
            {"id": nom, "label": style.label, "description": style.description}
            for nom, style in config.SUBTITLE_PRESETS.items()
        ],
        **extra,
    }


@app.get("/", response_class=HTMLResponse)
async def accueil(request: Request):
    return templates.TemplateResponse(request, "site/index.html",
                                      _contexte_site("accueil"))


@app.get("/fonctionnalites", response_class=HTMLResponse)
async def fonctionnalites(request: Request):
    return templates.TemplateResponse(request, "site/fonctionnalites.html",
                                      _contexte_site("fonctionnalites"))


@app.get("/tarifs", response_class=HTMLResponse)
async def tarifs(request: Request):
    return templates.TemplateResponse(request, "site/tarifs.html",
                                      _contexte_site("tarifs"))


@app.get("/faq", response_class=HTMLResponse)
async def faq(request: Request):
    return templates.TemplateResponse(request, "site/faq.html",
                                      _contexte_site("faq"))


@app.get("/connexion", response_class=HTMLResponse)
async def connexion(request: Request):
    return templates.TemplateResponse(request, "site/connexion.html",
                                      _contexte_site("connexion"))


@app.get("/inscription", response_class=HTMLResponse)
async def inscription(request: Request, formule: str = ""):
    return templates.TemplateResponse(
        request, "site/inscription.html",
        _contexte_site("inscription", formule=formule),
    )


@app.post("/inscription", response_class=HTMLResponse)
async def inscription_envoi(
    request: Request,
    email: str = Form(""),
    formule: str = Form(""),
    usage: str = Form(""),
):
    """Enregistre une inscription à la liste d'attente."""
    if not site.register(email, formule, usage):
        return templates.TemplateResponse(
            request, "site/inscription.html",
            _contexte_site("inscription", formule=formule,
                           erreur="Cette adresse e-mail ne semble pas valide."),
            status_code=400,
        )
    return templates.TemplateResponse(request, "site/merci.html",
                                      _contexte_site("merci"))


def _page_legale(request: Request, page: str) -> HTMLResponse:
    """Rend l'une des pages légales. Routes explicites : une route attrape-tout
    masquerait /studio et toute page ajoutée ensuite."""
    surtitre, titre, sections = site.PAGES_LEGALES[page]
    return templates.TemplateResponse(
        request, "site/legal.html",
        _contexte_site(page, surtitre=surtitre, titre=titre, sections=sections,
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
def _contexte_app(rubrique: str, **extra) -> dict:
    """Contexte commun à toutes les pages de l'application connectée."""
    compte = account.charger()
    return {
        "rubrique": rubrique,
        "compte": compte,
        "plan_actuel": {
            "name": compte.formule.name,
            "videos": compte.formule.videos,
            "price_monthly": compte.formule.price_monthly,
            "pro": compte.pro,
        },
        "credits": {
            "consommes": compte.consommes(),
            "restants": compte.restants(),
            "quota": compte.quota,
        },
        "profil": {"initiales": compte.initiales},
        "plans": plans.PLANS,
        "steps": plans.STEPS,
        "version": __version__,
        **extra,
    }


@app.get("/studio", response_class=HTMLResponse)
async def studio(request: Request):
    return templates.TemplateResponse(
        request, "studio/creer.html",
        _contexte_app("creer",
                      min_sources=config.MIN_SOURCES,
                      max_sources=config.MAX_SOURCES,
                      output_dir=str(config.OUTPUT_DIR)),
    )


@app.get("/studio/creations", response_class=HTMLResponse)
async def studio_creations(request: Request):
    creations = []
    for projet in store.list_recent(limit=40):
        creations.append({
            "id": projet.id,
            "titre": projet.topic or (projet.sources[0].title if projet.sources
                                      else "Projet sans titre"),
            "date": time.strftime("%d/%m/%Y", time.localtime(projet.created_at)),
            "step": projet.step,
            "viewing_url": (f"/api/projects/{projet.id}/viewing"
                            if projet.output_path
                            and Path(projet.output_path).exists() else ""),
        })
    return templates.TemplateResponse(
        request, "studio/creations.html",
        _contexte_app("creations", creations=creations),
    )


@app.get("/studio/tutoriel", response_class=HTMLResponse)
async def studio_tutoriel(request: Request):
    return templates.TemplateResponse(request, "studio/tutoriel.html",
                                      _contexte_app("tutoriel"))


@app.get("/studio/communaute", response_class=HTMLResponse)
async def studio_communaute(request: Request):
    return templates.TemplateResponse(request, "studio/communaute.html",
                                      _contexte_app("communaute"))


@app.get("/studio/abonnement", response_class=HTMLResponse)
async def studio_abonnement(request: Request):
    return templates.TemplateResponse(request, "studio/abonnement.html",
                                      _contexte_app("abonnement"))


@app.post("/studio/abonnement", response_class=HTMLResponse)
async def studio_changer_formule(request: Request, plan: str = Form("")):
    account.changer_de_formule(plan)
    return RedirectResponse("/studio/abonnement", status_code=303)


@app.get("/studio/credits", response_class=HTMLResponse)
async def studio_credits(request: Request):
    compte = account.charger()
    libelles = {"rendu": "Vidéo rendue", "apercu": "Aperçu",
                "transcription": "Transcription", "voix": "Voix importée"}
    historique = [
        {"date": evenement.get("date", ""),
         "libelle": libelles.get(evenement.get("type", ""), evenement.get("type", ""))}
        for evenement in reversed(compte.historique[-25:])
    ]
    return templates.TemplateResponse(
        request, "studio/credits.html",
        _contexte_app("credits", historique=historique),
    )


@app.get("/studio/profil", response_class=HTMLResponse)
async def studio_profil(request: Request, enregistre: bool = False):
    return templates.TemplateResponse(
        request, "studio/profil.html",
        _contexte_app("profil", auth=bool(config.PASSWORD), enregistre=enregistre),
    )


@app.post("/studio/profil", response_class=HTMLResponse)
async def studio_profil_envoi(request: Request, nom: str = Form(""),
                              email: str = Form("")):
    account.mettre_a_jour_profil(nom, email)
    return RedirectResponse("/studio/profil?enregistre=true", status_code=303)


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
        ("Transcription", "disponible" if transcribe.available()
         else "non installée", transcribe.available()),
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
        _contexte_app("parametres", etat=etat, variables=variables,
                      dossiers={"sortie": str(config.OUTPUT_DIR),
                                "travail": str(config.WORK_DIR),
                                "musiques": str(config.MUSIC_DIR)}),
    )


# --- Script Viral ---------------------------------------------------------
@app.get("/studio/script-viral", response_class=HTMLResponse)
async def studio_script(request: Request):
    return templates.TemplateResponse(
        request, "studio/script_viral.html",
        _contexte_app("script", transcription_disponible=transcribe.available()),
    )


@app.post("/studio/script-viral", response_class=HTMLResponse)
async def studio_script_envoi(
    request: Request,
    url: str = Form(""),
    fichier: UploadFile | None = File(None),
):
    """Transcrit une vidéo, depuis un lien ou un fichier importé."""
    compte = account.charger()
    contexte = {"transcription_disponible": transcribe.available(), "url": url}

    def echec(message: str, code: int = 400):
        return templates.TemplateResponse(
            request, "studio/script_viral.html",
            _contexte_app("script", erreur=message, **contexte),
            status_code=code,
        )

    if not compte.pro:
        return echec("Cette rubrique demande la formule Créateur.", 402)
    if not transcribe.available():
        return echec("Le moteur de transcription n'est pas installé.", 503)

    dossier = config.WORK_DIR / ".transcriptions"
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

    account.noter("transcription", url or (fichier.filename if fichier else ""))
    jeton = hashlib.sha256(transcription.text.encode("utf-8")).hexdigest()[:12]
    (dossier / f"{jeton}.txt").write_text(transcription.text, encoding="utf-8")

    return templates.TemplateResponse(
        request, "studio/script_viral.html",
        _contexte_app("script", **contexte, resultat={
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
    infos = voicestudio.charger()
    voix = None
    if infos:
        voix = {"nom": infos.get("nom", "voix.wav"),
                "duree": f"{infos.get('duree', 0):.0f} s",
                "mots": len(infos.get("mots", []))}
    return templates.TemplateResponse(request, "studio/voix.html",
                                      _contexte_app("voix", voix=voix))


@app.post("/studio/voix", response_class=HTMLResponse)
async def studio_voix_envoi(request: Request, fichier: UploadFile = File(...)):
    compte = account.charger()

    def echec(message: str, code: int = 400):
        return templates.TemplateResponse(
            request, "studio/voix.html",
            _contexte_app("voix", voix=None, erreur=message), status_code=code,
        )

    if not compte.pro:
        return echec("Cette rubrique demande la formule Créateur.", 402)
    if not transcribe.available():
        return echec("Le moteur de transcription n'est pas installé.", 503)

    suffixe = Path(fichier.filename or "").suffix.lower()
    if suffixe not in voicestudio.EXTENSIONS:
        return echec(f"Format non reconnu : {fichier.filename}")

    brut = voicestudio.dossier() / f"import{suffixe}"
    try:
        if await _stream_to_disk(fichier, brut) == 0:
            return echec("Fichier vide.")
        await asyncio.to_thread(voicestudio.enregistrer, brut,
                                fichier.filename or "enregistrement")
    except (media.MediaError, transcribe.TranscriptionError, ValueError) as exc:
        return echec(str(exc), 500)
    finally:
        brut.unlink(missing_ok=True)

    account.noter("voix", fichier.filename or "")
    return RedirectResponse("/studio/voix", status_code=303)


@app.get("/studio/voix/fichier")
async def studio_voix_fichier():
    chemin = voicestudio.chemin_audio()
    if not chemin.exists():
        raise HTTPException(status_code=404, detail="Aucune voix importée.")
    return FileResponse(chemin, media_type="audio/wav")


@app.post("/studio/voix/supprimer")
async def studio_voix_supprimer():
    voicestudio.supprimer()
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
async def voices(refresh: bool = False):
    if refresh:
        try:
            listed = await asyncio.wait_for(voice.list_voices("fr"), timeout=12)
        except Exception:                  # réseau indisponible : liste locale
            listed = config.FRENCH_VOICES
    else:
        listed = list(config.FRENCH_VOICES)

    # La voix importée passe en tête : c'est celle qu'on veut quand on l'a.
    if voicestudio.charger():
        listed = [{"id": voicestudio.VOICE_ID, "label": "Ma voix (importée)"},
                  *listed]
    return {"voices": listed, "default": config.DEFAULT_VOICE}


@app.get("/api/music")
async def music():
    return {"tracks": pipeline.list_music(), "dir": str(config.MUSIC_DIR)}


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
async def create_project():
    project = store.create()
    return _project_payload(project)


@app.get("/api/projects")
async def list_projects():
    return {
        "projects": [
            {
                "id": p.id,
                "created_at": p.created_at,
                "step": p.step,
                "topic": p.topic,
                "output_name": Path(p.output_path).name if p.output_path else "",
            }
            for p in store.list_recent()
        ]
    }


@app.get("/api/projects/{project_id}")
async def get_project(project_id: str):
    return _project_payload(_get(project_id))


@app.delete("/api/projects/{project_id}")
async def delete_project(project_id: str):
    return {"deleted": store.delete(project_id)}


# --- Étape 1 : sources ----------------------------------------------------
@app.post("/api/projects/{project_id}/sources")
async def add_sources(project_id: str, body: SourcesIn):
    project = _get(project_id)
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
async def upload_sources(project_id: str, files: list[UploadFile] = File(...)):
    """Importe des vidéos depuis l'appareil (pellicule iPhone, Fichiers…).

    C'est l'alternative au téléchargement quand les plateformes le refusent,
    et la seule voie possible quand l'app tourne sur un serveur distant.
    """
    project = _get(project_id)
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
async def choose_hook(project_id: str, body: HookIn):
    project = _get(project_id)
    source = project.source(body.hook_index)
    if source is None or not source.ok:
        raise HTTPException(status_code=400, detail="Source indisponible.")
    project.hook_index = body.hook_index
    project.step = max(project.step, 3)
    project.save()
    return _project_payload(project)


@app.get("/api/projects/{project_id}/hooks/{index}")
async def hook_preview(project_id: str, index: int):
    project = _get(project_id)
    source = project.source(index)
    if source is None or not source.hook_path or not Path(source.hook_path).exists():
        raise HTTPException(status_code=404, detail="Accroche introuvable.")
    return FileResponse(source.hook_path, media_type="video/mp4")


# --- Étape 3 : style ------------------------------------------------------
@app.post("/api/projects/{project_id}/settings")
async def update_settings(project_id: str, body: SettingsIn):
    project = _get(project_id)
    data = body.model_dump()
    if data.get("music") and data["music"] not in {
        t["id"] for t in pipeline.list_music()
    }:
        data["music"] = None
    data["music_volume"] = max(0.0, min(1.0, data["music_volume"]))
    data["mask_height_ratio"] = max(0.05, min(0.6, data["mask_height_ratio"]))
    data["source_audio_volume"] = max(0.0, min(1.0, data["source_audio_volume"]))
    if data["mask_mode"] not in ("blur", "black"):
        data["mask_mode"] = "blur"
    if data["subtitle_preset"] not in config.SUBTITLE_PRESETS:
        data["subtitle_preset"] = config.DEFAULT_SUBTITLE_PRESET

    project.settings = config.RenderSettings(**data)
    project.step = max(project.step, 4)
    project.save()
    return _project_payload(project)


# --- Étape 4 : script -----------------------------------------------------
@app.post("/api/projects/{project_id}/script/prompt")
async def script_prompt(project_id: str, body: ScriptIn):
    project = _get(project_id)
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
async def generate_script(project_id: str, body: ScriptIn):
    project = _get(project_id)
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
async def save_script(project_id: str, body: ScriptIn):
    project = _get(project_id)
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
async def render(project_id: str, body: RenderIn | None = None):
    project = _get(project_id)
    _require_idle(project)

    # Le quota se vérifie en premier : inutile de contrôler le projet si le
    # rendu ne peut de toute façon pas partir.
    fast = bool(body and body.fast)
    if not fast and not account.charger().peut_rendre():
        raise HTTPException(
            status_code=402,
            detail="Crédits épuisés pour ce mois-ci. Les aperçus restent "
                   "illimités, et la page Abonnement permet de changer de formule.",
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
async def cancel_job(project_id: str):
    """Coupe la tâche en cours (téléchargement ou rendu)."""
    project = _get(project_id)
    stopped = pipeline.request_cancel(project.id)
    return {"cancelled": stopped, "job": project.job.name}


@app.get("/api/presets")
async def presets():
    return {
        "subtitles": [
            {"id": name, "label": style.label, "description": style.description,
             "font": style.font, "animate": style.animate}
            for name, style in config.SUBTITLE_PRESETS.items()
        ],
        "default": config.DEFAULT_SUBTITLE_PRESET,
    }


@app.get("/api/projects/{project_id}/output")
async def download_output(project_id: str, download: bool = False):
    project = _get(project_id)
    if not project.output_path or not Path(project.output_path).exists():
        raise HTTPException(status_code=404, detail="Aucun rendu disponible.")
    return FileResponse(
        project.output_path,
        media_type="video/mp4",
        filename=Path(project.output_path).name if download else None,
    )


@app.get("/api/projects/{project_id}/viewing")
async def viewing_copy(project_id: str):
    """Copie allégée du rendu final, pour la lecture dans la page."""
    project = _get(project_id)
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
async def download_preview(project_id: str):
    project = _get(project_id)
    if not project.preview_path or not Path(project.preview_path).exists():
        raise HTTPException(status_code=404, detail="Aucun aperçu disponible.")
    return FileResponse(project.preview_path, media_type="video/mp4")


@app.post("/api/projects/{project_id}/cleanup")
async def cleanup(project_id: str):
    """Supprime les fichiers de travail en gardant le rendu final."""
    project = _get(project_id)
    _require_idle(project)
    freed = 0
    for folder in (project.sources_dir, project.clips_dir):
        if folder.exists():
            freed += sum(f.stat().st_size for f in folder.rglob("*") if f.is_file())
            shutil.rmtree(folder, ignore_errors=True)
    project.ensure_dirs()
    return {"freed_bytes": freed}


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
