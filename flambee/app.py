"""Interface web locale (FastAPI) orchestrant les 5 étapes de Flambée."""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import threading
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

from . import __version__, config, downloader, media, pipeline, scriptgen, voice
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
        data["output_name"] = Path(project.output_path).name
    if project.preview_path and Path(project.preview_path).exists():
        data["preview_url"] = f"/api/projects/{project.id}/preview"
    data["recommended_hook"] = project.recommended_hook
    data["script_notes"] = scriptgen.review(project.script) if project.script else []
    data["estimated_duration"] = round(
        scriptgen.estimate_duration(project.script), 1
    ) if project.script else 0.0
    return data


# --- Pages ----------------------------------------------------------------
@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "version": __version__,
            "min_sources": config.MIN_SOURCES,
            "max_sources": config.MAX_SOURCES,
            "output_dir": str(config.OUTPUT_DIR),
        },
    )


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
    if not refresh:
        return {"voices": config.FRENCH_VOICES, "default": config.DEFAULT_VOICE}
    try:
        listed = await asyncio.wait_for(voice.list_voices("fr"), timeout=12)
    except Exception:                      # réseau indisponible : liste locale
        listed = config.FRENCH_VOICES
    return {"voices": listed, "default": config.DEFAULT_VOICE}


@app.get("/api/music")
async def music():
    return {"tracks": pipeline.list_music(), "dir": str(config.MUSIC_DIR)}


# --- Projets --------------------------------------------------------------
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
    if not project.script.strip():
        raise HTTPException(status_code=400, detail="Valide d'abord un script.")
    if not project.ready_sources:
        raise HTTPException(status_code=400, detail="Aucune vidéo source prête.")
    if project.hook_index is None:
        project.hook_index = (
            project.recommended_hook or project.ready_sources[0].index
        )
    fast = bool(body and body.fast)
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
