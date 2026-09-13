"""Orchestration des tâches longues (téléchargement, analyse, rendu).

Ces fonctions tournent dans un thread de fond ; l'avancement est écrit dans
`project.job` et l'interface interroge `/api/projects/{id}` pour l'afficher.
Chaque projet possède un drapeau d'annulation : l'utilisateur peut couper un
rendu en cours sans attendre la fin de ffmpeg.
"""

from __future__ import annotations

import hashlib
import logging
import threading
import traceback
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import (analyzer, assembler, config, downloader, subtitles, trimmer,
               voice, voicestudio)
from . import account, users
from .media import Cancelled, MediaError, detect_encoder, ensure_tools, probe
from .project import Project

log = logging.getLogger(__name__)

TAIL_SILENCE = 0.6   # secondes de marge après la fin de la voix off

# --- Annulation -----------------------------------------------------------
_cancels: dict[str, threading.Event] = {}
_lock = threading.Lock()


def cancel_token(project_id: str) -> threading.Event:
    with _lock:
        token = _cancels.get(project_id)
        if token is None or token.is_set():
            token = threading.Event()
            _cancels[project_id] = token
        return token


def request_cancel(project_id: str) -> bool:
    """Demande l'arrêt de la tâche en cours pour ce projet."""
    with _lock:
        token = _cancels.get(project_id)
    if token is None:
        return False
    token.set()
    return True


def _clear_cancel(project_id: str) -> None:
    with _lock:
        _cancels.pop(project_id, None)


# --- Étapes 1 et 2 --------------------------------------------------------
def run_download(project: Project, urls: list[str]) -> None:
    """Télécharge les sources, extrait les accroches et analyse les plans."""
    token = cancel_token(project.id)
    project.urls = urls
    project.sources = []
    project.analyses = {}
    project.ensure_dirs()
    project.set_job("download", "running", progress=0.02,
                    message=f"Téléchargement de {len(urls)} vidéo(s)…")

    try:
        total = len(urls)

        def on_progress(done: int, _total: int, source: downloader.Source) -> None:
            project.sources = sorted(
                [s for s in project.sources if s.index != source.index] + [source],
                key=lambda s: s.index,
            )
            project.set_job(
                "download", "running",
                progress=0.05 + 0.55 * done / max(1, total),
                message=f"{done}/{total} vidéo(s) téléchargée(s).",
            )

        project.sources = downloader.download_all(
            urls, project.sources_dir, on_progress=on_progress, cancel=token
        )
        _raise_if_cancelled(token)

        if not project.ready_sources:
            errors = " ".join(s.error for s in project.sources if s.error)
            raise MediaError(f"Aucune vidéo téléchargée. {errors}".strip())

        # Accroches et analyse tournent ensemble : ce sont deux décodages
        # indépendants, autant occuper tous les cœurs d'un coup.
        project.set_job("download", "running", progress=0.68,
                        message="Extraction des accroches et analyse des plans…")
        with ThreadPoolExecutor(max_workers=2) as pool:
            hooks = pool.submit(trimmer.extract_hooks, project.sources,
                                project.hooks_dir)
            analyses = pool.submit(analyzer.analyze_all, project.sources)
            hooks.result()
            project.analyses = {
                index: analysis.to_dict()
                for index, analysis in analyses.result().items()
            }

        _raise_if_cancelled(token)
        _apply_scores(project)

        project.step = max(project.step, 2)
        project.set_job(
            "download", "done", progress=1.0,
            message=f"{len(project.ready_sources)} vidéo(s) prête(s).",
        )
    except Cancelled:
        project.set_job("download", "error", message="Téléchargement annulé.",
                        error="Annulé par l'utilisateur.")
    except Exception as exc:
        log.error("Téléchargement KO : %s", traceback.format_exc())
        project.set_job("download", "error", message="Téléchargement interrompu.",
                        error=str(exc))
    finally:
        _clear_cancel(project.id)


def run_import(
    project: Project,
    paths: list[Path],
    titles: list[str] | None = None,
) -> None:
    """Intègre des vidéos importées depuis l'appareil (pellicule, Fichiers…).

    Même traitement que des vidéos téléchargées : contrôle du fichier, accroche
    de 3 s, détection des plans et score d'accroche.
    """
    token = cancel_token(project.id)
    project.ensure_dirs()
    project.set_job("import", "running", progress=0.05,
                    message=f"Analyse de {len(paths)} fichier(s)…")

    try:
        sources: list[downloader.Source] = []
        for position, path in enumerate(paths, start=1):
            _raise_if_cancelled(token)
            index = position
            title = (titles[position - 1] if titles and len(titles) >= position
                     else path.stem)
            source = downloader.Source(index=index, url=f"fichier://{path.name}",
                                       path=str(path), title=title)
            try:
                info = probe(path)
            except MediaError as exc:
                source.error = f"Fichier illisible : {exc}"
                sources.append(source)
                continue

            if info.duration <= 0 or info.width <= 0:
                source.error = "Ce fichier ne contient pas de vidéo exploitable."
                sources.append(source)
                continue

            source.duration = info.duration
            source.width = info.width
            source.height = info.height
            source.has_audio = info.has_audio
            source.warnings = downloader._check_source(source)
            sources.append(source)

        project.sources = sources
        project.urls = [s.url for s in sources]
        if not project.ready_sources:
            errors = " ".join(s.error for s in sources if s.error)
            raise MediaError(f"Aucune vidéo exploitable. {errors}".strip())

        project.set_job("import", "running", progress=0.5,
                        message="Extraction des accroches et analyse des plans…")
        with ThreadPoolExecutor(max_workers=2) as pool:
            hooks = pool.submit(trimmer.extract_hooks, project.sources,
                                project.hooks_dir)
            analyses = pool.submit(analyzer.analyze_all, project.sources)
            hooks.result()
            project.analyses = {
                index: analysis.to_dict()
                for index, analysis in analyses.result().items()
            }

        _raise_if_cancelled(token)
        _apply_scores(project)
        project.step = max(project.step, 2)
        project.set_job("import", "done", progress=1.0,
                        message=f"{len(project.ready_sources)} vidéo(s) prête(s).")
    except Cancelled:
        project.set_job("import", "error", message="Import annulé.",
                        error="Annulé par l'utilisateur.")
    except Exception as exc:
        log.error("Import KO : %s", traceback.format_exc())
        project.set_job("import", "error", message="Import interrompu.",
                        error=str(exc))
    finally:
        _clear_cancel(project.id)


def _apply_scores(project: Project) -> None:
    """Reporte les scores d'accroche sur les sources et propose la meilleure."""
    best_index, best_score = None, -1.0
    for source in project.sources:
        analysis = project.analyses.get(source.index) or project.analyses.get(
            str(source.index)
        )
        if not analysis:
            continue
        source.hook_score = float(analysis.get("hook_score") or 0.0)
        if source.ok and source.hook_score > best_score:
            best_index, best_score = source.index, source.hook_score
    project.recommended_hook = best_index


# --- Étape 5 --------------------------------------------------------------
def run_render(project: Project, *, fast: bool = False) -> None:
    """Voix off, sous-titres, découpe, mixage et export — en une passe ffmpeg."""
    token = cancel_token(project.id)
    project.ensure_dirs()
    project.set_job("render", "running", progress=0.02, message="Préparation…")

    try:
        missing = ensure_tools()
        if missing:
            raise MediaError(
                f"Outil manquant : {', '.join(missing)}. Installe ffmpeg "
                "(ex. `brew install ffmpeg` ou `apt install ffmpeg`)."
            )
        if (not project.script.strip()
                and project.settings.voice != voicestudio.VOICE_ID):
            raise MediaError("Aucun script validé.")
        if not project.ready_sources:
            raise MediaError("Aucune vidéo source exploitable.")

        settings = project.settings

        # 1. Voix off ------------------------------------------------------
        # Rendue une seule fois par script : un rendu final qui suit un aperçu
        # réutilise la piste déjà synthétisée (et son minutage au mot).
        track = _voice_track(project, cached_only=False)
        project.voice_path = track.path
        project.voice_duration = track.duration
        target_duration = track.duration + TAIL_SILENCE
        _raise_if_cancelled(token)

        # 2. Sous-titres ---------------------------------------------------
        if settings.subtitles:
            project.set_job("render", "running", progress=0.12,
                            message="Sous-titres animés…")
            project.subtitle_path = str(subtitles.write_ass(
                track.words,
                project.dir / "subtitles.ass",
                style=config.subtitle_style(settings.subtitle_preset),
                offset=track.lead_in,
                max_duration=target_duration,
            ))
        else:
            project.subtitle_path = ""

        # 3. Plan de montage ----------------------------------------------
        project.segments = trimmer.plan_segments(
            project.sources,
            hook_index=project.hook_index or project.ready_sources[0].index,
            target_duration=target_duration,
            scenes=project.scene_cuts() if settings.scene_aware else None,
        )
        _raise_if_cancelled(token)

        # 4. Rendu ---------------------------------------------------------
        encoder = detect_encoder()
        fmt = config.PREVIEW_FORMAT if fast else config.FORMAT
        project.set_job(
            "render", "running", progress=0.15,
            message=f"{'Aperçu' if fast else 'Montage'} et encodage "
                    f"({len(project.segments)} plans, {fmt.size}, {encoder.name})…",
        )

        def on_progress(fraction: float) -> None:
            project.set_job(
                "render", "running",
                progress=0.15 + 0.84 * fraction,
                message=f"Encodage {fraction * 100:.0f} % "
                        f"({target_duration:.0f}s, {fmt.size}, {encoder.name})",
            )

        if fast:
            out_path = project.dir / "apercu.mp4"
        else:
            out_path = config.OUTPUT_DIR / assembler.output_name(project.topic)

        result = _render_with_fallback(
            project, out_path,
            duration=target_duration, encoder=encoder, fast=fast, fmt=fmt,
            on_progress=on_progress, cancel=token,
        )

        if fast:
            project.preview_path = str(out_path)
        else:
            project.output_path = str(out_path)
            proprietaire = users.par_id(project.owner) if project.owner else None
            if proprietaire:
                account.noter(proprietaire, "rendu", out_path.name)  # un crédit
        project.step = 5
        project.set_job(
            "render", "done", progress=1.0,
            message=f"{'Aperçu prêt' if fast else 'Vidéo prête'} : {out_path.name} — "
                    f"{result.duration:.0f}s, {result.size_bytes / 1e6:.1f} Mo, "
                    f"rendu en {result.elapsed:.0f}s ({result.encoder}, {fmt.size})",
        )
    except Cancelled:
        project.set_job("render", "error", message="Rendu annulé.",
                        error="Annulé par l'utilisateur.")
    except Exception as exc:
        log.error("Rendu KO : %s", traceback.format_exc())
        project.set_job("render", "error", message="Rendu interrompu.", error=str(exc))
    finally:
        _clear_cancel(project.id)


def _render_with_fallback(
    project: Project,
    out_path: Path,
    *,
    duration: float,
    encoder,
    fast: bool,
    fmt: config.VideoFormat,
    on_progress,
    cancel: threading.Event,
) -> assembler.AssemblyResult:
    """Tente le rendu en une passe, et bascule sur le repli si ffmpeg refuse."""
    settings = project.settings
    music_path = _resolve_music(settings.music)
    subtitle_path = Path(project.subtitle_path) if project.subtitle_path else None
    voice_path = Path(project.voice_path) if project.voice_path else None

    try:
        return assembler.render(
            project.segments, project.sources, out_path,
            voice_path=voice_path, subtitle_path=subtitle_path,
            music_path=music_path, settings=settings, duration=duration,
            fmt=fmt, encoder=encoder, fast=fast, on_progress=on_progress,
            cancel=cancel,
        )
    except Cancelled:
        raise
    except MediaError as exc:
        log.warning("Rendu en une passe impossible (%s), repli multi-passes.", exc)
        project.set_job("render", "running", progress=0.2,
                        message="Montage (mode compatible)…")

        clips: list[Path] = []
        for position, segment in enumerate(project.segments, start=1):
            _raise_if_cancelled(cancel)
            source = project.source(segment.source_index)
            if source is None:
                continue
            clip = project.clips_dir / f"clip_{position:03d}.mp4"
            trimmer.render_segment(segment, source, clip, settings=settings, fmt=fmt)
            clips.append(clip)
            project.set_job(
                "render", "running",
                progress=0.2 + 0.5 * position / max(1, len(project.segments)),
                message=f"Extrait {position}/{len(project.segments)}…",
            )

        montage = assembler.concat_clips(clips, project.dir / "montage.mp4")
        return assembler.finalize(
            montage, out_path, voice_path=voice_path, subtitle_path=subtitle_path,
            music_path=music_path, settings=settings, duration=duration,
            fmt=fmt, encoder=encoder, on_progress=on_progress, cancel=cancel,
        )


def _voice_signature(project: Project) -> str:
    """Empreinte du script et des réglages de voix."""
    settings = project.settings
    payload = "|".join([
        project.script.strip(), settings.voice, settings.voice_rate,
        settings.voice_pitch,
    ])
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def _voice_track(project: Project, *, cached_only: bool = False) -> voice.VoiceTrack:
    """Retourne la voix off, depuis le cache du projet si elle est à jour."""
    # Voix importée : le fichier existe déjà, son minutage vient de la
    # transcription. Rien à synthétiser, et le script n'entre pas en jeu.
    if project.settings.voice == voicestudio.VOICE_ID:
        piste = voicestudio.piste(project.owner)
        project.set_job("render", "running", progress=0.10,
                        message="Voix importée : montage calé sur ton enregistrement.")
        return piste

    signature = _voice_signature(project)
    path = project.dir / "voice.mp3"

    if (
        project.voice_signature == signature
        and project.voice_words
        and path.exists()
        and project.voice_duration > 0
    ):
        log.info("Voix off réutilisée (script inchangé).")
        project.set_job("render", "running", progress=0.10,
                        message="Voix off réutilisée (script inchangé).")
        return voice.VoiceTrack(
            path=str(path),
            duration=project.voice_duration,
            words=[voice.Word(**word) for word in project.voice_words],
            voice=project.settings.voice,
            lead_in=project.voice_lead_in,
        )

    if cached_only:
        raise MediaError("Aucune voix off en cache.")

    project.set_job("render", "running", progress=0.06,
                    message="Génération de la voix off…")
    track = voice.synthesize(
        project.script, path,
        voice=project.settings.voice,
        rate=project.settings.voice_rate,
        pitch=project.settings.voice_pitch,
    )
    project.voice_signature = signature
    project.voice_words = [word.to_dict() for word in track.words]
    project.voice_lead_in = track.lead_in
    return track


def _raise_if_cancelled(token: threading.Event) -> None:
    if token.is_set():
        raise Cancelled("Tâche annulée.")


# --- Bibliothèque musicale ------------------------------------------------
def _resolve_music(name: str | None) -> Path | None:
    """Retourne le chemin d'une musique de la bibliothèque locale."""
    if not name:
        return None
    candidate = (config.MUSIC_DIR / name).resolve()
    if not str(candidate).startswith(str(config.MUSIC_DIR.resolve())):
        return None                      # jamais de chemin hors bibliothèque
    return candidate if candidate.exists() else None


def list_music() -> list[dict[str, str]]:
    """Bibliothèque de musiques locales (assets/music)."""
    extensions = {".mp3", ".m4a", ".wav", ".ogg", ".flac", ".aac"}
    tracks = []
    for path in sorted(config.MUSIC_DIR.iterdir()) if config.MUSIC_DIR.exists() else []:
        if path.is_file() and path.suffix.lower() in extensions:
            tracks.append({"id": path.name, "label": path.stem.replace("_", " ")})
    return tracks
