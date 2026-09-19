"""Étape 2 — extraction du hook et découpe des extraits (ffmpeg)."""

from __future__ import annotations

import logging
import random
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from pathlib import Path

from . import config
from .analyzer import snap_to_scene
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
    """Extrait le hook de chaque source, toutes en parallèle."""

    def extract(source: Source) -> None:
        if not source.ok:
            return
        try:
            source.hook_path = str(extract_hook(source, out_dir))
        except MediaError as exc:
            source.warnings.append(f"Hook non extrait : {exc}")
            log.warning("Hook KO pour %s : %s", source.url, exc)

    usable = [s for s in sources if s.ok]
    if usable:
        with ThreadPoolExecutor(max_workers=min(len(usable), 4)) as pool:
            list(pool.map(extract, usable))
    return sources


def plan_segments(
    sources: list[Source],
    *,
    hook_index: int,
    target_duration: float,
    hook_duration: float | None = None,
    scenes: dict[int, list[float]] | None = None,
    seed: int | None = None,
) -> list[Segment]:
    """Construit la liste des extraits couvrant la durée de la voix off.

    Le hook choisi ouvre la vidéo ; le reste du temps est réparti entre toutes
    les sources, en alternant pour garder du rythme. Les points de départ sont
    tirés dans la partie utile de chaque source (on évite les intros/outros).

    Quand `scenes` fournit les changements de plan détectés par `analyzer`, les
    coupes sont recalées dessus : un extrait démarre sur un plan et s'arrête
    avant le suivant, au lieu de trancher au milieu d'une action.
    """
    hook_duration = hook_duration or config.HOOK_DURATION
    usable = [s for s in sources if s.ok and s.duration > 0]
    if not usable:
        raise MediaError("Aucune source exploitable pour le montage.")

    rng = random.Random(seed)
    scenes = scenes or {}
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
        cuts = scenes.get(source.index) or []

        available = source.duration - cursor[source.index] - 0.2
        if available < MIN_SEGMENT:
            cursor[source.index] = _intro_skip(source)   # on reboucle sur la source
            available = source.duration - cursor[source.index] - 0.2
            if available < MIN_SEGMENT:
                continue

        start = cursor[source.index]
        jitter = rng.uniform(0, max(0.0, available - MIN_SEGMENT) * 0.35)
        start = max(0.0, min(start + jitter, source.duration - MIN_SEGMENT - 0.05))
        if cuts:
            start = snap_to_scene(
                start, cuts, tolerance=1.5,
                upper_bound=source.duration - MIN_SEGMENT - 0.05,
            )

        available = source.duration - start - 0.1
        if available < MIN_SEGMENT:
            cursor[source.index] = _intro_skip(source)
            continue

        length = min(MAX_SEGMENT, available, remaining)
        is_last = remaining - length < MIN_SEGMENT
        if is_last:
            length = min(available, remaining)
        elif cuts:
            # S'arrêter juste avant le plan suivant, si la longueur reste tenable.
            following = next((c for c in cuts if c >= start + MIN_SEGMENT), None)
            if following and MIN_SEGMENT <= following - start <= MAX_SEGMENT:
                length = min(following - start, available, remaining)

        segments.append(
            Segment(source_index=source.index, start=round(start, 3),
                    duration=round(length, 3))
        )
        cursor[source.index] = start + length
        remaining -= length

    return _ensure_coverage(segments, by_index, target_duration)


def _ensure_coverage(
    segments: list[Segment],
    sources: dict[int, Source],
    target_duration: float,
) -> list[Segment]:
    """Garantit que le montage couvre toute la voix off (jamais d'écran figé)."""
    total = sum(s.duration for s in segments)
    missing = target_duration - total
    if missing <= 0.05 or not segments:
        return segments

    last = segments[-1]
    source = sources.get(last.source_index)
    if source:
        room = source.duration - (last.start + last.duration) - 0.05
        added = min(missing, max(0.0, room))
        if added > 0.01:
            last.duration = round(last.duration + added, 3)
            missing -= added

    # S'il manque encore du temps, on rejoue le début de la source la plus longue.
    if missing > 0.05:
        longest = max(sources.values(), key=lambda s: s.duration, default=None)
        if longest and longest.duration > 0:
            segments.append(Segment(
                source_index=longest.index,
                start=round(max(0.0, min(_intro_skip(longest),
                                         longest.duration - missing)), 3),
                duration=round(min(missing, longest.duration), 3),
            ))
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
        framing=settings.framing,
        focus_x=settings.focus_x,
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
        # Pas de volume ici : `assembler.finalize` mixe le son d'origine et
        # applique le curseur. Le faire deux fois l'élevait au carré.
        args += ["-map", "0:a:0", "-af", "aresample=48000"]
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
