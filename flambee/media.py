"""Helpers ffmpeg / ffprobe partagés par les modules de traitement."""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from . import config

log = logging.getLogger(__name__)


class MediaError(RuntimeError):
    """Erreur d'exécution ffmpeg/ffprobe."""


@dataclass
class MediaInfo:
    path: Path
    duration: float
    width: int
    height: int
    fps: float
    has_audio: bool

    @property
    def is_vertical(self) -> bool:
        return self.height >= self.width


def ensure_tools() -> list[str]:
    """Retourne la liste des binaires manquants (ffmpeg, ffprobe)."""
    missing = []
    for name, binary in (("ffmpeg", config.FFMPEG_BIN), ("ffprobe", config.FFPROBE_BIN)):
        if shutil.which(binary) is None:
            missing.append(name)
    return missing


def run(args: list[str], *, timeout: int | None = None) -> subprocess.CompletedProcess:
    """Lance une commande et lève MediaError en cas d'échec."""
    log.debug("run: %s", " ".join(args))
    proc = subprocess.run(
        args, capture_output=True, text=True, timeout=timeout, check=False
    )
    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout or "").strip().splitlines()[-12:]
        raise MediaError(
            f"{Path(args[0]).name} a échoué (code {proc.returncode}) :\n"
            + "\n".join(tail)
        )
    return proc


def ffmpeg(args: list[str], *, timeout: int | None = None) -> None:
    """Appelle ffmpeg avec les options communes (silencieux, écrasement)."""
    run([config.FFMPEG_BIN, "-hide_banner", "-loglevel", "error", "-y", *args],
        timeout=timeout)


def probe(path: str | Path) -> MediaInfo:
    """Retourne les métadonnées d'un fichier média."""
    path = Path(path)
    proc = run([
        config.FFPROBE_BIN, "-v", "error", "-print_format", "json",
        "-show_format", "-show_streams", str(path),
    ])
    data = json.loads(proc.stdout)
    streams = data.get("streams", [])
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)

    duration = float(data.get("format", {}).get("duration") or 0.0)
    if not duration and video:
        duration = float(video.get("duration") or 0.0)

    width = int(video.get("width") or 0) if video else 0
    height = int(video.get("height") or 0) if video else 0

    # Certaines vidéos mobiles sont stockées en paysage + rotation métadonnée.
    rotation = 0
    if video:
        rotation = abs(int(float(video.get("rotation") or 0)))
        for side in video.get("side_data_list", []) or []:
            if "rotation" in side:
                rotation = abs(int(float(side["rotation"])))
    if rotation in (90, 270):
        width, height = height, width

    return MediaInfo(
        path=path,
        duration=duration,
        width=width,
        height=height,
        fps=_parse_fps(video.get("avg_frame_rate") if video else None),
        has_audio=audio is not None,
    )


def _parse_fps(raw: str | None) -> float:
    if not raw or raw in ("0/0", "N/A"):
        return 0.0
    if "/" in raw:
        num, _, den = raw.partition("/")
        try:
            den_f = float(den)
            return float(num) / den_f if den_f else 0.0
        except ValueError:
            return 0.0
    try:
        return float(raw)
    except ValueError:
        return 0.0


def vertical_filter(
    *,
    width: int | None = None,
    height: int | None = None,
    fps: int | None = None,
    mask: str | None = None,
    mask_height_ratio: float = 0.22,
    tag: str = "",
) -> str:
    """Construit le filtre vidéo qui met une source au format 9:16.

    Le cadre est rempli par recadrage centré (`scale`+`crop`), ce qui évite les
    bandes noires ; le fond flouté prend le relais pour les sources très larges.

    `mask` vaut ``"blur"``, ``"black"`` ou ``None`` : il masque la zone basse où
    se trouvent en général les sous-titres incrustés de la vidéo source.

    `tag` rend les labels intermédiaires uniques lorsque plusieurs chaînes
    cohabitent dans un même `-filter_complex`.
    """
    width = width or config.FORMAT.width
    height = height or config.FORMAT.height
    fps = fps or config.FORMAT.fps

    chain = [
        # Fond : copie floutée et zoomée de la source, pour combler les côtés.
        f"scale={width}:{height}:force_original_aspect_ratio=increase",
        f"crop={width}:{height}",
        f"fps={fps}",
        "setsar=1",
    ]

    if mask in ("blur", "black"):
        band = max(1, int(height * max(0.05, min(0.6, mask_height_ratio))))
        band -= band % 2
        top = height - band
        if mask == "black":
            chain.append(f"drawbox=x=0:y={top}:w={width}:h={band}:color=black@1:t=fill")
        else:
            # Flou localisé : on isole la bande, on la floute, on la recolle.
            base, tocrop, blurred = f"b{tag}", f"c{tag}", f"k{tag}"
            return (
                ",".join(chain)
                + f",split=2[{base}][{tocrop}];"
                f"[{tocrop}]crop={width}:{band}:0:{top},boxblur=24:2[{blurred}];"
                f"[{base}][{blurred}]overlay=0:{top}"
            )
    return ",".join(chain)


def has_media_duration(path: str | Path, minimum: float = 0.05) -> bool:
    """Vérifie qu'un fichier produit n'est pas vide/corrompu."""
    try:
        return probe(path).duration >= minimum
    except (MediaError, json.JSONDecodeError):
        return False
