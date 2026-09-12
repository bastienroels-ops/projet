"""Analyse des sources : changements de plan et qualité des accroches.

Deux mesures, toutes deux obtenues en un seul décodage basse résolution par
source (rapide, et parallélisé sur les cœurs disponibles) :

- les **changements de plan**, pour que les coupes du montage tombent sur les
  plans existants au lieu de les trancher au milieu ;
- un **score d'accroche**, qui combine mouvement et énergie sonore des trois
  premières secondes, pour proposer la source la plus percutante en tête.
"""

from __future__ import annotations

import logging
import os
import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Iterable, Sequence

from . import config
from .downloader import Source
from .media import MediaError, run

log = logging.getLogger(__name__)

SCENE_THRESHOLD = 0.32       # sensibilité de la détection de plan
ANALYSIS_WIDTH = 192         # on analyse en tout petit : 10 à 30× plus rapide
MAX_ANALYSIS_SECONDS = 180   # au-delà, on n'analyse que le début de la source

_PTS_RE = re.compile(r"pts_time:([0-9.]+)")
_MEAN_VOLUME_RE = re.compile(r"mean_volume:\s*(-?[0-9.]+) dB")


@dataclass
class Analysis:
    """Résultat d'analyse d'une source."""

    source_index: int
    scenes: list[float] = field(default_factory=list)
    hook_score: float = 0.0
    motion: float = 0.0
    loudness: float = -60.0
    error: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def workers(count: int) -> int:
    """Nombre de tâches parallèles raisonnable pour la machine."""
    cpus = os.cpu_count() or 2
    return max(1, min(count, cpus, 4))


def detect_scenes(path: str | Path, *, limit: float = MAX_ANALYSIS_SECONDS) -> list[float]:
    """Retourne les instants (en secondes) des changements de plan."""
    try:
        proc = run([
            config.FFMPEG_BIN, "-hide_banner", "-nostdin",
            "-t", f"{limit:.0f}", "-i", str(path),
            "-vf", f"scale={ANALYSIS_WIDTH}:-2,select='gt(scene,{SCENE_THRESHOLD})',"
                   f"showinfo",
            "-an", "-f", "null", "-",
        ], timeout=600, capture_stderr=True)
    except MediaError as exc:
        log.warning("Détection de plans impossible (%s)", exc)
        return []

    times = sorted({round(float(m), 3) for m in _PTS_RE.findall(proc.stderr or "")})
    return [t for t in times if t > 0.5]


def measure_hook(path: str | Path, *, duration: float | None = None) -> tuple[float, float]:
    """Mesure (mouvement, volume moyen en dB) sur les premières secondes."""
    duration = duration or config.HOOK_DURATION
    motion = 0.0
    loudness = -60.0

    try:
        proc = run([
            config.FFMPEG_BIN, "-hide_banner", "-nostdin",
            "-t", f"{duration:.2f}", "-i", str(path),
            "-vf", f"scale={ANALYSIS_WIDTH}:-2,select='gt(scene,0.02)',showinfo",
            "-an", "-f", "null", "-",
        ], timeout=180, capture_stderr=True)
        # Densité d'images « qui bougent » : proxy simple et fiable du dynamisme.
        moving = len(_PTS_RE.findall(proc.stderr or ""))
        motion = min(1.0, moving / max(1.0, duration * 12))
    except MediaError as exc:
        log.debug("Mesure du mouvement impossible : %s", exc)

    try:
        proc = run([
            config.FFMPEG_BIN, "-hide_banner", "-nostdin",
            "-t", f"{duration:.2f}", "-i", str(path),
            "-af", "volumedetect", "-vn", "-f", "null", "-",
        ], timeout=180, capture_stderr=True)
        match = _MEAN_VOLUME_RE.search(proc.stderr or "")
        if match:
            loudness = float(match.group(1))
    except MediaError as exc:
        log.debug("Mesure du volume impossible : %s", exc)

    return motion, loudness


def score_hook(motion: float, loudness: float, view_count: int | None) -> float:
    """Combine dynamisme, énergie sonore et popularité en un score 0 → 1."""
    # -30 dB (très calme) → 0 ; -10 dB (bien présent) → 1.
    audio = max(0.0, min(1.0, (loudness + 30) / 20))
    # 10 000 vues → ~0,4 ; 1 M → ~0,86. Échelle logarithmique, jamais bloquante.
    from math import log10

    popularity = 0.0
    if view_count and view_count > 0:
        popularity = max(0.0, min(1.0, log10(view_count) / 7))
    return round(0.45 * motion + 0.35 * audio + 0.20 * popularity, 3)


def analyze_source(source: Source, *, scenes: bool = True) -> Analysis:
    """Analyse une source (plans + accroche)."""
    result = Analysis(source_index=source.index)
    if not source.ok:
        result.error = source.error or "Source indisponible."
        return result
    try:
        result.motion, result.loudness = measure_hook(source.path)
        result.hook_score = score_hook(result.motion, result.loudness,
                                       source.view_count)
        if scenes:
            result.scenes = detect_scenes(source.path)
    except Exception as exc:  # pragma: no cover - dépend du fichier source
        result.error = str(exc)
        log.warning("Analyse KO pour la source %s : %s", source.index, exc)
    return result


def analyze_all(
    sources: Sequence[Source], *, scenes: bool = True
) -> dict[int, Analysis]:
    """Analyse toutes les sources en parallèle."""
    usable = [s for s in sources if s.ok]
    if not usable:
        return {}
    with ThreadPoolExecutor(max_workers=workers(len(usable))) as pool:
        results = pool.map(lambda s: analyze_source(s, scenes=scenes), usable)
    return {analysis.source_index: analysis for analysis in results}


def snap_to_scene(
    position: float, scenes: Iterable[float], *, tolerance: float = 1.2,
    upper_bound: float | None = None,
) -> float:
    """Rapproche un point de coupe du changement de plan le plus proche."""
    best = position
    best_gap = tolerance
    for scene in scenes:
        if upper_bound is not None and scene > upper_bound:
            break
        gap = abs(scene - position)
        if gap < best_gap:
            best, best_gap = scene, gap
    return best
