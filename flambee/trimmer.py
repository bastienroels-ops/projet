"""Étape 2 — extraction du hook et découpe des extraits (ffmpeg)."""

from __future__ import annotations

import logging
import random
from dataclasses import asdict, dataclass
from pathlib import Path

from . import config
from .downloader import Source
from .media import MediaError, ffmpeg, has_media_duration, probe, vertical_filter

log = logging.getLogger(__name__)

PREVIEW_WIDTH = 540
PREVIEW_HEIGHT = 960

MIN_SEGMENT = 2.5   # secondes : en dessous, le montage devient illisible
MAX_SEGMENT = 6.0   # secondes : au delà, l'attention retombe


@dataclass
class Segment:
    """Un extrait à intégrer dans le montage final."""

    source_index: int
    start: float
    duration: float
    is_hook: bool = False

    def to_dict(self) -> dict:
        return asdict(self)


def extract_hook(
    source: Source,
    out_dir: Path,
    *,
    duration: float | None = None,
) -> Path:
    """Extrait les premières secondes d'une source pour la prévisualisation."""
    duration = duration or config.HOOK_DURATION
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"hook_{source.index:02d}.mp4"

    vf = (
        f"scale={PREVIEW_WIDTH}:{PREVIEW_HEIGHT}:force_original_aspect_ratio=increase,"
        f"crop={PREVIEW_WIDTH}:{PREVIEW_HEIGHT},fps=30,setsar=1"
    )
    ffmpeg([
        "-ss", "0", "-t", f"{duration:.3f}", "-i", source.path,
        "-vf", vf,
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "26",
        "-c:a", "aac", "-b:a", "128k", "-ac", "2",
        "-movflags", "+faststart",
        str(out_path),
    ], timeout=300)

    if not has_media_duration(out_path):
        raise MediaError(f"Hook vide pour la source {source.index}.")
    return out_path


def extract_hooks(sources: list[Source], out_dir: Path) -> list[Source]:
    """Extrait le hook de chaque source téléchargée avec succès."""
    for source in sources:
        if not source.ok:
            continue
        try:
            path = extract_hook(source, out_dir)
            source.hook_path = str(path)
        except MediaError as exc:
            source.warnings.append(f"Hook non extrait : {exc}")
            log.warning("Hook KO pour %s : %s", source.url, exc)
    return sources


def plan_segments(
    sources: list[Source],
    *,
    hook_index: int,
    target_duration: float,
    hook_duration: float | None = None,
    seed: int | None = None,
) -> list[Segment]:
    """Construit la liste des extraits couvrant la durée de la voix off.

    Le hook choisi ouvre la vidéo ; le reste du temps est réparti entre toutes
    les sources, en alternant pour garder du rythme. Les points de départ sont
    tirés au hasard dans la partie utile de chaque source (on évite les toutes
    premières et dernières secondes, souvent des intros/outros).
    """
    hook_duration = hook_duration or config.HOOK_DURATION
    usable = [s for s in sources if s.ok and s.duration > 0]
    if not usable:
        raise MediaError("Aucune source exploitable pour le montage.")

    rng = random.Random(seed)
    by_index = {s.index: s for s in usable}
    hook_source = by_index.get(hook_index) or usable[0]

    segments = [
        Segment(
            source_index=hook_source.index,
            start=0.0,
            duration=min(hook_duration, hook_source.duration),
            is_hook=True,
        )
    ]

    remaining = max(0.0, target_duration - segments[0].duration)
    if remaining <= 0.1:
        return segments

    # Ordre de passage : les autres sources d'abord, puis on boucle.
    order = [s for s in usable if s.index != hook_source.index] or usable
    rotation = order + ([hook_source] if hook_source not in order else [])

    # Curseur de lecture par source : on ne repasse pas deux fois au même endroit.
    cursor = {
        s.index: (hook_duration if s.index == hook_source.index else _intro_skip(s))
        for s in usable
    }

    position = 0
    guard = 0
    while remaining > 0.4 and guard < 200:
        guard += 1
        source = rotation[position % len(rotation)]
        position += 1

        available = source.duration - cursor[source.index] - 0.2
        if available < MIN_SEGMENT:
            cursor[source.index] = _intro_skip(source)   # on reboucle sur la source
            available = source.duration - cursor[source.index] - 0.2
            if available < MIN_SEGMENT:
                continue

        length = min(MAX_SEGMENT, available, remaining)
        if remaining - length < MIN_SEGMENT:
            length = min(available, remaining)           # dernier extrait : on finit
        length = max(min(length, available), min(MIN_SEGMENT, available))

        start = cursor[source.index]
        jitter = rng.uniform(0, max(0.0, available - length) * 0.35)
        start = min(start + jitter, source.duration - length - 0.05)
        start = max(0.0, start)

        segments.append(
            Segment(source_index=source.index, start=round(start, 3),
                    duration=round(length, 3))
        )
        cursor[source.index] = start + length
        remaining -= length

    return segments


def _intro_skip(source: Source) -> float:
    """Décalage de départ : on saute l'intro pour les sources longues."""
    if source.duration > 60:
        return 5.0
    if source.duration > 25:
        return 2.0
    return 0.0


def render_segment(
    segment: Segment,
    source: Source,
    out_path: Path,
    *,
    settings: config.RenderSettings,
    fmt: config.VideoFormat | None = None,
) -> Path:
    """Découpe et normalise un extrait au format vertical de sortie."""
    fmt = fmt or config.FORMAT
    out_path.parent.mkdir(parents=True, exist_ok=True)

    mask = settings.mask_mode if settings.mask_source_subtitles else None
    vf = vertical_filter(
        width=fmt.width,
        height=fmt.height,
        fps=fmt.fps,
        mask=mask,
        mask_height_ratio=settings.mask_height_ratio,
        tag=f"s{segment.source_index}t{int(segment.start * 100)}",
    )

    media = probe(source.path)
    use_source_audio = settings.keep_source_audio and media.has_audio

    # Entrée 0 : la vidéo. Entrée 1 (si besoin) : un silence, pour que tous les
    # extraits aient une piste audio et se concatènent sans décalage.
    args = ["-ss", f"{segment.start:.3f}", "-t", f"{segment.duration:.3f}",
            "-i", source.path]
    if not use_source_audio:
        args += ["-f", "lavfi", "-t", f"{segment.duration:.3f}",
                 "-i", "anullsrc=channel_layout=stereo:sample_rate=48000"]

    args += ["-filter_complex", f"[0:v]{vf}[v]", "-map", "[v]"]

    if use_source_audio:
        args += ["-map", "0:a:0",
                 "-af", f"volume={settings.source_audio_volume:.3f},aresample=48000"]
    else:
        args += ["-map", "1:a"]

    args += [
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
        "-pix_fmt", "yuv420p", "-r", str(fmt.fps),
        "-c:a", "aac", "-b:a", "128k", "-ar", "48000", "-ac", "2",
        "-video_track_timescale", "90000",
        "-movflags", "+faststart",
        str(out_path),
    ]
    ffmpeg(args, timeout=900)

    if not has_media_duration(out_path):
        raise MediaError(f"Extrait vide (source {segment.source_index}).")
    return out_path
