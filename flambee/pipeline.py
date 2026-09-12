"""Orchestration des tâches longues (téléchargement, rendu final).

Ces fonctions tournent dans un thread de fond ; l'avancement est écrit dans
`project.job` et l'interface interroge `/api/projects/{id}` pour l'afficher.
"""

from __future__ import annotations

import logging
import traceback
from pathlib import Path

from . import assembler, config, downloader, subtitles, trimmer, voice
from .media import MediaError, ensure_tools
from .project import Project

log = logging.getLogger(__name__)

TAIL_SILENCE = 0.6   # secondes de marge après la fin de la voix off


def run_download(project: Project, urls: list[str]) -> None:
    """Étapes 1 et 2 : télécharge les sources puis extrait les hooks."""
    project.urls = urls
    project.sources = []
    project.ensure_dirs()
    project.set_job("download", "running", progress=0.02,
                    message="Démarrage du téléchargement…")

    try:
        total = len(urls)

        def on_progress(index: int, _total: int, source: downloader.Source) -> None:
            project.sources = [s for s in project.sources if s.index != source.index]
            project.sources.append(source)
            project.sources.sort(key=lambda s: s.index)
            project.set_job(
                "download", "running",
                progress=0.08 + 0.62 * index / max(1, total),
                message=f"Vidéo {index}/{total} téléchargée.",
            )

        project.sources = downloader.download_all(
            urls, project.sources_dir, on_progress=on_progress
        )

        if not project.ready_sources:
            errors = "; ".join(s.error for s in project.sources if s.error)
            raise MediaError(f"Aucune vidéo téléchargée. {errors}")

        project.set_job("download", "running", progress=0.75,
                        message="Extraction des accroches…")
        trimmer.extract_hooks(project.sources, project.hooks_dir)

        project.step = max(project.step, 2)
        project.set_job("download", "done", progress=1.0,
                        message=f"{len(project.ready_sources)} vidéo(s) prête(s).")
    except Exception as exc:
        log.error("Téléchargement KO : %s", traceback.format_exc())
        project.set_job("download", "error", message="Téléchargement interrompu.",
                        error=str(exc))


def run_render(project: Project) -> None:
    """Étape 5 : voix off, découpe, sous-titres, mixage et export."""
    project.ensure_dirs()
    project.set_job("render", "running", progress=0.02, message="Préparation…")

    try:
        missing = ensure_tools()
        if missing:
            raise MediaError(
                f"Outil manquant : {', '.join(missing)}. Installe ffmpeg "
                "(ex. `brew install ffmpeg` ou `apt install ffmpeg`)."
            )
        if not project.script.strip():
            raise MediaError("Aucun script validé.")
        if not project.ready_sources:
            raise MediaError("Aucune vidéo source exploitable.")

        settings = project.settings

        # 1. Voix off ------------------------------------------------------
        project.set_job("render", "running", progress=0.08,
                        message="Génération de la voix off…")
        track = voice.synthesize(
            project.script,
            project.dir / "voice.mp3",
            voice=settings.voice,
            rate=settings.voice_rate,
            pitch=settings.voice_pitch,
        )
        project.voice_path = track.path
        project.voice_duration = track.duration
        target_duration = track.duration + TAIL_SILENCE

        # 2. Sous-titres ---------------------------------------------------
        if settings.subtitles:
            project.set_job("render", "running", progress=0.18,
                            message="Sous-titres animés…")
            ass_path = subtitles.write_ass(
                track.words,
                project.dir / "subtitles.ass",
                max_duration=target_duration,
            )
            project.subtitle_path = str(ass_path)
        else:
            project.subtitle_path = ""

        # 3. Découpe des extraits -----------------------------------------
        project.set_job("render", "running", progress=0.24,
                        message="Découpe des extraits…")
        project.segments = trimmer.plan_segments(
            project.sources,
            hook_index=project.hook_index or project.ready_sources[0].index,
            target_duration=target_duration,
        )

        clips: list[Path] = []
        for position, segment in enumerate(project.segments, start=1):
            source = project.source(segment.source_index)
            if source is None:
                continue
            clip_path = project.clips_dir / f"clip_{position:03d}.mp4"
            trimmer.render_segment(segment, source, clip_path, settings=settings)
            clips.append(clip_path)
            project.set_job(
                "render", "running",
                progress=0.24 + 0.46 * position / max(1, len(project.segments)),
                message=f"Extrait {position}/{len(project.segments)}…",
            )

        # 4. Montage et rendu final ---------------------------------------
        project.set_job("render", "running", progress=0.74, message="Montage…")
        montage = assembler.concat_clips(clips, project.dir / "montage.mp4")

        project.set_job("render", "running", progress=0.8,
                        message="Mixage audio et encodage final…")
        music_path = _resolve_music(settings.music)
        out_path = config.OUTPUT_DIR / assembler.output_name(project.topic)
        assembler.finalize(
            montage,
            out_path,
            voice_path=Path(project.voice_path),
            subtitle_path=Path(project.subtitle_path) if project.subtitle_path else None,
            music_path=music_path,
            settings=settings,
            duration=target_duration,
        )

        project.output_path = str(out_path)
        project.step = 5
        result = assembler.describe(out_path)
        project.set_job(
            "render", "done", progress=1.0,
            message=f"Vidéo prête : {out_path.name} "
                    f"({result.duration:.0f}s, {result.size_bytes / 1e6:.1f} Mo)",
        )
    except Exception as exc:
        log.error("Rendu KO : %s", traceback.format_exc())
        project.set_job("render", "error", message="Rendu interrompu.", error=str(exc))


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
