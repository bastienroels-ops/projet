"""Échantillons de sous-titres pour l'interface.

Chaque style est rendu par ffmpeg, avec les mêmes polices et la même animation
que la vidéo finale : ce que l'utilisateur voit ici est exactement ce qu'il
obtiendra. Les échantillons sont mis en cache sur disque et ne sont
regénérés que si le style change.
"""

from __future__ import annotations

import hashlib
import logging
import threading
from dataclasses import asdict
from pathlib import Path

from . import config, subtitles
from .media import MediaError, ffmpeg, has_media_duration
from .voice import Word

log = logging.getLogger(__name__)

# Une phrase courte où le surlignage a le temps de se déplacer.
SAMPLE_TEXT = "Voici l'astuce que personne ne connaît"
WORD_DURATION = 0.34
TAIL = 0.5

# On ne montre que le bas du cadre : le texte reste lisible en petit, et la
# distance au bord inférieur — qui change d'un style à l'autre — reste visible.
CROP_HEIGHT = 700
OUT_WIDTH = 540

_locks: dict[str, threading.Lock] = {}
_guard = threading.Lock()


def _lock_for(key: str) -> threading.Lock:
    with _guard:
        return _locks.setdefault(key, threading.Lock())


def sample_words() -> list[Word]:
    """Minutage régulier, suffisant pour montrer le déplacement du surlignage."""
    return [
        Word(text=mot, start=i * WORD_DURATION, end=(i + 1) * WORD_DURATION)
        for i, mot in enumerate(SAMPLE_TEXT.split())
    ]


def _signature(style: config.SubtitleStyle) -> str:
    """Empreinte du style : le cache se renouvelle dès qu'un réglage change."""
    payload = repr(sorted(asdict(style).items())) + SAMPLE_TEXT
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:12]


def sample_path(preset: str) -> Path:
    style = config.subtitle_style(preset)
    folder = config.WORK_DIR / ".samples"
    return folder / f"{preset}-{_signature(style)}.mp4"


def build_sample(preset: str) -> Path:
    """Rend l'échantillon du style demandé (ou retourne celui déjà en cache)."""
    out_path = sample_path(preset)
    if out_path.exists() and has_media_duration(out_path, minimum=0.3):
        return out_path

    with _lock_for(preset):                 # deux requêtes simultanées, un seul rendu
        if out_path.exists() and has_media_duration(out_path, minimum=0.3):
            return out_path

        style = config.subtitle_style(preset)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        words = sample_words()
        duration = words[-1].end + TAIL

        ass_path = out_path.with_suffix(".ass")
        subtitles.write_ass(words, ass_path, style=style, max_duration=duration)

        fmt = config.FORMAT
        top = max(0, fmt.height - CROP_HEIGHT)
        height = round(CROP_HEIGHT * OUT_WIDTH / fmt.width / 2) * 2

        from .assembler import _escape_filter_path

        chain = (
            f"subtitles=filename='{_escape_filter_path(ass_path)}'"
            f":fontsdir='{_escape_filter_path(config.FONTS_DIR)}':alpha=1,"
            f"crop={fmt.width}:{CROP_HEIGHT}:0:{top},"
            f"scale={OUT_WIDTH}:{height},format=yuv420p"
        )
        try:
            ffmpeg([
                # Un fond dégradé tiède : plus représentatif d'une vraie image
                # qu'un aplat noir, sur lequel tout paraît lisible.
                "-f", "lavfi", "-i",
                f"gradients=s={fmt.width}x{fmt.height}:c0=0x1d2430:c1=0x44291a"
                f":x0=0:y0=0:x1={fmt.width}:y1={fmt.height}"
                f":d={duration:.2f}:r=25",
                "-t", f"{duration:.2f}",
                "-vf", chain,
                "-c:v", "libx264", "-preset", "veryfast", "-crf", "26",
                "-movflags", "+faststart", "-an",
                str(out_path),
            ], timeout=180)
        finally:
            ass_path.unlink(missing_ok=True)

        if not has_media_duration(out_path, minimum=0.3):
            raise MediaError(f"Échantillon vide pour le style « {preset} ».")
        log.info("Échantillon rendu : %s", out_path.name)
        return out_path


def clear_cache() -> int:
    """Supprime les échantillons en cache. Retourne le nombre de fichiers ôtés."""
    folder = config.WORK_DIR / ".samples"
    if not folder.exists():
        return 0
    fichiers = list(folder.glob("*.mp4"))
    for fichier in fichiers:
        fichier.unlink(missing_ok=True)
    return len(fichiers)
