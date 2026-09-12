"""Étape 5c — assemblage final avec ffmpeg.

Deux passes seulement :
1. concaténation des extraits déjà normalisés (concat demuxer, sans réencodage) ;
2. une passe unique qui incruste les sous-titres, mixe voix + musique et
   encode le fichier final en `.mp4`.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from . import config
from .media import MediaError, ffmpeg, has_media_duration, probe

log = logging.getLogger(__name__)

ProgressFn = Callable[[float, str], None]


@dataclass
class AssemblyResult:
    path: str
    duration: float
    width: int
    height: int
    size_bytes: int


def concat_clips(clips: list[Path], out_path: Path) -> Path:
    """Concatène des extraits homogènes sans réencoder (rapide)."""
    if not clips:
        raise MediaError("Aucun extrait à concaténer.")
    out_path.parent.mkdir(parents=True, exist_ok=True)

    listing = out_path.parent / "concat.txt"
    listing.write_text(
        "\n".join(f"file '{Path(c).resolve().as_posix()}'" for c in clips) + "\n",
        encoding="utf-8",
    )
    ffmpeg([
        "-f", "concat", "-safe", "0", "-i", str(listing),
        "-c", "copy", "-movflags", "+faststart", str(out_path),
    ], timeout=900)

    if not has_media_duration(out_path):
        raise MediaError("La concaténation a produit un fichier vide.")
    return out_path


def _escape_filter_path(path: Path) -> str:
    """Échappe un chemin pour le filtre `subtitles` de ffmpeg."""
    text = str(path)
    for char in ("\\", ":", "'", "[", "]", ","):
        text = text.replace(char, f"\\{char}")
    return text


def finalize(
    montage: Path,
    out_path: Path,
    *,
    voice_path: Path | None,
    subtitle_path: Path | None,
    music_path: Path | None,
    settings: config.RenderSettings,
    duration: float | None = None,
    fmt: config.VideoFormat | None = None,
) -> Path:
    """Incruste les sous-titres, mixe l'audio et encode le fichier final."""
    fmt = fmt or config.FORMAT
    out_path.parent.mkdir(parents=True, exist_ok=True)

    montage_info = probe(montage)
    target = duration or montage_info.duration
    target = min(target, montage_info.duration)

    args: list[str] = ["-i", str(montage)]
    inputs = {"montage": 0}

    if voice_path:
        inputs["voice"] = len(inputs)
        args += ["-i", str(voice_path)]
    if music_path:
        inputs["music"] = len(inputs)
        args += ["-stream_loop", "-1", "-i", str(music_path)]

    filters: list[str] = []

    # --- Vidéo ---
    video_label = f"{inputs['montage']}:v"
    if subtitle_path and settings.subtitles:
        filters.append(
            f"[{video_label}]subtitles=filename='{_escape_filter_path(subtitle_path)}'"
            f":alpha=1[vout]"
        )
        video_label = "vout"

    # --- Audio ---
    tracks: list[str] = []
    if settings.keep_source_audio and montage_info.has_audio:
        filters.append(
            f"[{inputs['montage']}:a]volume={settings.source_audio_volume:.3f},"
            f"aresample=48000[asrc]"
        )
        tracks.append("asrc")
    if voice_path:
        filters.append(
            f"[{inputs['voice']}:a]aresample=48000,"
            f"dynaudnorm=f=200:g=5:p=0.9[avoice]"
        )
        tracks.append("avoice")
    if music_path:
        fade_out = max(0.5, target - 1.5)
        filters.append(
            f"[{inputs['music']}:a]aresample=48000,"
            f"volume={max(0.0, min(1.0, settings.music_volume)):.3f},"
            f"afade=t=in:st=0:d=1,afade=t=out:st={fade_out:.2f}:d=1.5[amusic]"
        )
        tracks.append("amusic")

    if len(tracks) > 1:
        joined = "".join(f"[{t}]" for t in tracks)
        filters.append(
            f"{joined}amix=inputs={len(tracks)}:duration=first:"
            f"dropout_transition=0:normalize=0[aout]"
        )
        audio_label = "aout"
    elif tracks:
        audio_label = tracks[0]
    else:
        audio_label = None

    if filters:
        args += ["-filter_complex", ";".join(filters)]

    # Un label de filtre se référence entre crochets, un flux d'entrée non.
    args += ["-map", "[vout]" if video_label == "vout" else video_label]
    if audio_label:
        args += ["-map", f"[{audio_label}]"]

    args += [
        "-t", f"{target:.3f}",
        "-c:v", "libx264", "-preset", "medium", "-crf", "20",
        "-profile:v", "high", "-level", "4.1", "-pix_fmt", "yuv420p",
        "-r", str(fmt.fps), "-b:v", fmt.video_bitrate, "-maxrate", fmt.video_bitrate,
        "-bufsize", "12M",
        "-c:a", "aac", "-b:a", fmt.audio_bitrate, "-ar", "48000", "-ac", "2",
        "-movflags", "+faststart",
        str(out_path),
    ]
    ffmpeg(args, timeout=3600)

    if not has_media_duration(out_path, minimum=1.0):
        raise MediaError("Le rendu final est vide.")
    return out_path


def output_name(topic: str = "") -> str:
    """Nom de fichier lisible pour le rendu final."""
    import re

    slug = re.sub(r"[^a-zA-Z0-9]+", "-", topic.strip().lower()).strip("-")[:40]
    stamp = time.strftime("%Y%m%d-%H%M%S")
    return f"flambee-{stamp}{'-' + slug if slug else ''}.mp4"


def describe(path: Path) -> AssemblyResult:
    info = probe(path)
    return AssemblyResult(
        path=str(path),
        duration=info.duration,
        width=info.width,
        height=info.height,
        size_bytes=path.stat().st_size,
    )
