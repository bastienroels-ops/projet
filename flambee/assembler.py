"""Étape 5c — assemblage final avec ffmpeg.

Deux stratégies :

- **une seule passe** (par défaut) : tous les extraits sont découpés, recadrés,
  concaténés, sous-titrés et mixés dans un unique `-filter_complex`. La vidéo
  n'est encodée qu'une fois — c'est à la fois le plus rapide et le plus propre
  (pas de perte de génération) ;
- **repli multi-passes** : un fichier par extrait puis concaténation, utilisé
  automatiquement si le graphe unique échoue (source exotique, ffmpeg ancien).
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, Sequence

from . import config
from .downloader import Source
from .media import (
    Encoder,
    MediaError,
    detect_encoder,
    ffmpeg,
    ffmpeg_progress,
    has_media_duration,
    motion_for,
    probe,
    vertical_filter,
)
from .trimmer import Segment

log = logging.getLogger(__name__)

ProgressFn = Callable[[float], None]

# Mixage audio : valeurs calées sur ce qu'on entend sur les vidéos virales.
VOICE_LOUDNESS = "loudnorm=I=-14:TP=-1.5:LRA=11"
DUCK = "sidechaincompress=threshold=0.02:ratio=9:attack=15:release=350:makeup=1"


@dataclass
class AssemblyResult:
    path: str
    duration: float
    width: int
    height: int
    size_bytes: int
    encoder: str
    elapsed: float = 0.0


@dataclass
class _Graph:
    """Arguments ffmpeg d'un rendu en une passe."""

    inputs: list[str]
    filters: list[str]
    video_label: str
    audio_label: str | None


def _escape_filter_path(path: Path) -> str:
    """Échappe un chemin pour le filtre `subtitles` de ffmpeg."""
    text = str(path)
    for char in ("\\", ":", "'", "[", "]", ","):
        text = text.replace(char, f"\\{char}")
    return text


def build_graph(
    segments: Sequence[Segment],
    sources: dict[int, Source],
    *,
    voice_path: Path | None,
    subtitle_path: Path | None,
    music_path: Path | None,
    settings: config.RenderSettings,
    duration: float,
    fmt: config.VideoFormat,
) -> _Graph:
    """Construit le `-filter_complex` complet du montage.

    Chaque extrait devient une entrée avec recherche rapide (`-ss` avant `-i`),
    est normalisé au format de sortie puis coupé à la durée exacte voulue —
    c'est ce qui garantit la synchronisation avec les sous-titres.
    """
    inputs: list[str] = []
    filters: list[str] = []
    video_labels: list[str] = []
    audio_labels: list[str] = []
    ambience = settings.keep_source_audio

    for position, segment in enumerate(segments):
        source = sources.get(segment.source_index)
        if source is None or not source.path:
            continue

        stream = _next_input_index(inputs)
        inputs += ["-ss", f"{segment.start:.3f}", "-t", f"{segment.duration:.3f}",
                   "-i", source.path]

        chain = vertical_filter(
            width=fmt.width, height=fmt.height, fps=fmt.fps,
            mask=settings.mask_mode if settings.mask_source_subtitles else None,
            mask_height_ratio=settings.mask_height_ratio,
            motion=motion_for(position) if settings.motion else None,
            motion_duration=segment.duration,
            tag=f"m{position}",
        )
        label = f"v{position}"
        filters.append(
            f"[{stream}:v]{chain},trim=duration={segment.duration:.3f},"
            f"setpts=PTS-STARTPTS[{label}]"
        )
        video_labels.append(label)

        if ambience:
            audio_label = f"a{position}"
            if source.has_audio:
                filters.append(
                    f"[{stream}:a]aresample=48000:async=1,"
                    f"atrim=duration={segment.duration:.3f},"
                    f"asetpts=PTS-STARTPTS[{audio_label}]"
                )
            else:
                silence = _next_input_index(inputs)
                inputs += ["-f", "lavfi", "-t", f"{segment.duration:.3f}",
                           "-i", "anullsrc=channel_layout=stereo:sample_rate=48000"]
                filters.append(f"[{silence}:a]asetpts=PTS-STARTPTS[{audio_label}]")
            audio_labels.append(audio_label)

    if not video_labels:
        raise MediaError("Aucun extrait exploitable pour le montage.")

    # --- Concaténation ----------------------------------------------------
    if len(video_labels) == 1 and not (ambience and audio_labels):
        video_out = video_labels[0]
    elif ambience and audio_labels:
        pairs = "".join(f"[{v}][{a}]" for v, a in zip(video_labels, audio_labels))
        filters.append(
            f"{pairs}concat=n={len(video_labels)}:v=1:a=1[vcat][acat]"
        )
        video_out = "vcat"
    else:
        joined = "".join(f"[{v}]" for v in video_labels)
        filters.append(f"{joined}concat=n={len(video_labels)}:v=1:a=0[vcat]")
        video_out = "vcat"

    # --- Sous-titres ------------------------------------------------------
    if subtitle_path and settings.subtitles:
        filters.append(
            f"[{video_out}]subtitles=filename='{_escape_filter_path(subtitle_path)}'"
            f":alpha=1[vout]"
        )
        video_out = "vout"

    # --- Pistes audio -----------------------------------------------------
    tracks: list[str] = []
    voice_side: str | None = None

    if ambience and audio_labels:
        filters.append(
            f"[acat]volume={settings.source_audio_volume:.3f}[aamb]"
        )
        tracks.append("aamb")

    if voice_path:
        stream = _next_input_index(inputs)
        inputs += ["-i", str(voice_path)]
        if music_path:
            # La voix sert aussi de déclencheur au ducking : on la duplique.
            filters.append(
                f"[{stream}:a]aresample=48000,{VOICE_LOUDNESS},"
                f"asplit=2[avoice][aduck]"
            )
            voice_side = "aduck"
        else:
            filters.append(f"[{stream}:a]aresample=48000,{VOICE_LOUDNESS}[avoice]")
        tracks.append("avoice")

    if music_path:
        stream = _next_input_index(inputs)
        inputs += ["-stream_loop", "-1", "-i", str(music_path)]
        fade_out = max(0.5, duration - 1.5)
        volume = max(0.0, min(1.0, settings.music_volume))
        filters.append(
            f"[{stream}:a]aresample=48000,volume={volume:.3f},"
            f"afade=t=in:st=0:d=1,afade=t=out:st={fade_out:.2f}:d=1.5[amusic0]"
        )
        if voice_side:
            # La musique baisse automatiquement quand la voix parle.
            filters.append(f"[amusic0][{voice_side}]{DUCK}[amusic]")
        else:
            filters.append("[amusic0]anull[amusic]")
        tracks.append("amusic")

    if len(tracks) > 1:
        joined = "".join(f"[{t}]" for t in tracks)
        filters.append(
            f"{joined}amix=inputs={len(tracks)}:duration=first:"
            f"dropout_transition=0:normalize=0,alimiter=limit=0.95[aout]"
        )
        audio_out = "aout"
    elif tracks:
        filters.append(f"[{tracks[0]}]alimiter=limit=0.95[aout]")
        audio_out = "aout"
    else:
        audio_out = None

    return _Graph(inputs=inputs, filters=filters, video_label=video_out,
                  audio_label=audio_out)


def _next_input_index(inputs: list[str]) -> int:
    """Numéro de la prochaine entrée ffmpeg (compte les `-i` déjà posés)."""
    return inputs.count("-i")


def render(
    segments: Sequence[Segment],
    sources: Iterable[Source],
    out_path: Path,
    *,
    voice_path: Path | None,
    subtitle_path: Path | None,
    music_path: Path | None,
    settings: config.RenderSettings,
    duration: float,
    fmt: config.VideoFormat | None = None,
    encoder: Encoder | None = None,
    fast: bool = False,
    on_progress: ProgressFn | None = None,
    cancel: threading.Event | None = None,
) -> AssemblyResult:
    """Rend la vidéo finale en une seule passe ffmpeg."""
    fmt = fmt or config.FORMAT
    encoder = encoder or detect_encoder()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    by_index = {s.index: s for s in sources}

    graph = build_graph(
        segments, by_index,
        voice_path=voice_path, subtitle_path=subtitle_path, music_path=music_path,
        settings=settings, duration=duration, fmt=fmt,
    )

    args = [*graph.inputs, "-filter_complex", ";".join(graph.filters)]
    args += ["-map", f"[{graph.video_label}]"]
    if graph.audio_label:
        args += ["-map", f"[{graph.audio_label}]"]

    args += [
        "-t", f"{duration:.3f}",
        *encoder.args(fast=fast),
        "-pix_fmt", "yuv420p", "-r", str(fmt.fps),
        "-c:a", "aac", "-b:a", fmt.audio_bitrate, "-ar", "48000", "-ac", "2",
        "-movflags", "+faststart",
        "-threads", "0",
        str(out_path),
    ]

    started = time.monotonic()
    ffmpeg_progress(args, duration=duration, on_progress=on_progress,
                    cancel=cancel, timeout=3600)

    if not has_media_duration(out_path, minimum=0.5):
        raise MediaError("Le rendu final est vide.")
    return describe(out_path, encoder=encoder.name,
                    elapsed=time.monotonic() - started)


# --- Repli multi-passes ---------------------------------------------------
def concat_clips(clips: list[Path], out_path: Path) -> Path:
    """Concatène des extraits homogènes sans réencoder (chemin de repli)."""
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
    encoder: Encoder | None = None,
    on_progress: ProgressFn | None = None,
    cancel: threading.Event | None = None,
) -> AssemblyResult:
    """Incruste les sous-titres et mixe l'audio sur un montage déjà concaténé."""
    fmt = fmt or config.FORMAT
    encoder = encoder or detect_encoder()
    out_path.parent.mkdir(parents=True, exist_ok=True)

    info = probe(montage)
    target = min(duration or info.duration, info.duration)

    # Un montage déjà assemblé = un seul « extrait » qui couvre tout.
    pseudo_source = Source(index=0, url="", path=str(montage), duration=info.duration)
    pseudo_source.has_audio = info.has_audio
    segment = Segment(source_index=0, start=0.0, duration=target)
    passthrough = config.RenderSettings(**{**settings.__dict__, "motion": False,
                                           "mask_source_subtitles": False})

    graph = build_graph(
        [segment], {0: pseudo_source},
        voice_path=voice_path, subtitle_path=subtitle_path, music_path=music_path,
        settings=passthrough, duration=target, fmt=fmt,
    )
    args = [*graph.inputs, "-filter_complex", ";".join(graph.filters),
            "-map", f"[{graph.video_label}]"]
    if graph.audio_label:
        args += ["-map", f"[{graph.audio_label}]"]
    args += [
        "-t", f"{target:.3f}", *encoder.args(),
        "-pix_fmt", "yuv420p", "-r", str(fmt.fps),
        "-c:a", "aac", "-b:a", fmt.audio_bitrate, "-ar", "48000", "-ac", "2",
        "-movflags", "+faststart", str(out_path),
    ]

    started = time.monotonic()
    ffmpeg_progress(args, duration=target, on_progress=on_progress, cancel=cancel,
                    timeout=3600)
    if not has_media_duration(out_path, minimum=0.5):
        raise MediaError("Le rendu final est vide.")
    return describe(out_path, encoder=encoder.name,
                    elapsed=time.monotonic() - started)


def output_name(topic: str = "") -> str:
    """Nom de fichier lisible pour le rendu final."""
    import re
    import unicodedata

    normalized = unicodedata.normalize("NFKD", topic).encode("ascii", "ignore").decode()
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", normalized.lower()).strip("-")[:40]
    stamp = time.strftime("%Y%m%d-%H%M%S")
    return f"flambee-{stamp}{'-' + slug if slug else ''}.mp4"


def describe(path: Path, *, encoder: str = "", elapsed: float = 0.0) -> AssemblyResult:
    info = probe(path)
    return AssemblyResult(
        path=str(path),
        duration=info.duration,
        width=info.width,
        height=info.height,
        size_bytes=path.stat().st_size,
        encoder=encoder,
        elapsed=elapsed,
    )
