"""Helpers ffmpeg / ffprobe partagés par les modules de traitement."""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

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


def _lower_priority() -> None:
    """Abaisse la priorité du processus enfant (voir config.FFMPEG_NICE)."""
    if config.FFMPEG_NICE:
        os.nice(config.FFMPEG_NICE)


def run(
    args: list[str],
    *,
    timeout: int | None = None,
    capture_stderr: bool = False,
) -> subprocess.CompletedProcess:
    """Lance une commande et lève MediaError en cas d'échec.

    `capture_stderr` accepte un code retour non nul tant que la sortie d'erreur
    a bien été produite : les filtres d'analyse (`showinfo`, `volumedetect`)
    écrivent leurs mesures sur stderr.
    """
    log.debug("run: %s", " ".join(args))
    proc = subprocess.run(
        args, capture_output=True, text=True, timeout=timeout, check=False,
        preexec_fn=_lower_priority if config.FFMPEG_NICE else None,
    )
    if capture_stderr and proc.returncode != 0 and (proc.stderr or "").strip():
        return proc
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
    motion: str | None = None,
    motion_duration: float = 0.0,
    tag: str = "",
) -> str:
    """Construit le filtre qui met une source au format 9:16.

    Le cadre est rempli par recadrage centré (`scale`+`crop`) : pas de bandes
    noires, pas de déformation.

    `mask` (``"blur"``/``"black"``) masque la zone basse où se trouvent en
    général les sous-titres incrustés de la source.

    `motion` (``"left"``, ``"right"``, ``"up"``, ``"down"``) ajoute un
    travelling lent : la source est agrandie de 8 % puis le cadre dérive sur
    `motion_duration` secondes. C'est un simple `crop` animé, bien moins coûteux
    qu'un `zoompan`, et ça suffit à donner du mouvement aux plans fixes.

    `tag` rend les labels intermédiaires uniques lorsque plusieurs chaînes
    cohabitent dans un même `-filter_complex`.
    """
    width = width or config.FORMAT.width
    height = height or config.FORMAT.height
    fps = fps or config.FORMAT.fps

    chain: list[str] = []
    if motion and motion_duration > 0.2:
        zoom = 1.08
        big_w, big_h = even(width * zoom), even(height * zoom)
        margin_x, margin_y = big_w - width, big_h - height
        progress = f"min(1,t/{motion_duration:.3f})"
        moves = {
            "left": (f"{margin_x}*(1-{progress})", f"{margin_y}/2"),
            "right": (f"{margin_x}*{progress}", f"{margin_y}/2"),
            "up": (f"{margin_x}/2", f"{margin_y}*(1-{progress})"),
            "down": (f"{margin_x}/2", f"{margin_y}*{progress}"),
        }
        x_expr, y_expr = moves.get(motion, moves["right"])
        chain += [
            f"scale={big_w}:{big_h}:force_original_aspect_ratio=increase",
            f"crop={big_w}:{big_h}",
            f"crop={width}:{height}:x='{x_expr}':y='{y_expr}'",
        ]
    else:
        chain += [
            f"scale={width}:{height}:force_original_aspect_ratio=increase",
            f"crop={width}:{height}",
        ]

    chain += [f"fps={fps}", "setsar=1"]

    if mask in ("blur", "black"):
        band = even(height * max(0.05, min(0.6, mask_height_ratio)))
        top = height - band
        if mask == "black":
            chain.append(f"drawbox=x=0:y={top}:w={width}:h={band}:color=black@1:t=fill")
        else:
            # Flou localisé : on isole la bande basse, on la floute, on la recolle.
            # Le flou se fait par réduction/agrandissement plutôt qu'avec
            # `boxblur` : visuellement équivalent sur une bande de sous-titres,
            # et bien moins coûteux (c'est le filtre le plus cher du graphe).
            base, tocrop, blurred = f"b{tag}", f"c{tag}", f"k{tag}"
            small_w, small_h = max(8, even(width / 16)), max(8, even(band / 16))
            return (
                ",".join(chain)
                + f",split=2[{base}][{tocrop}];"
                f"[{tocrop}]crop={width}:{band}:0:{top},"
                f"scale={small_w}:{small_h},"
                f"scale={width}:{band}:flags=bicubic[{blurred}];"
                f"[{base}][{blurred}]overlay=0:{top}"
            )
    return ",".join(chain)


def split_bands(height: int, ratio: float) -> tuple[int, int]:
    """Partage la hauteur entre le montage et le compagnon.

    Les deux hauteurs sont paires — un encodeur H.264 refuse une dimension
    impaire — et leur somme fait exactement la hauteur demandée, sinon
    `vstack` rendrait une image d'un pixel de trop.
    """
    ratio = max(0.25, min(0.85, ratio))
    montage = even(int(round(height * ratio)))
    montage = max(2, min(height - 2, montage))
    return montage, height - montage


def even(value: float) -> int:
    """Arrondit à un entier pair (exigé par yuv420p).

    Public : l'assembleur partage la hauteur de l'image entre deux bandes, et
    une bande de hauteur impaire fait échouer l'encodage."""
    result = int(round(value))
    return result - (result % 2)


MOTIONS = ("right", "up", "left", "down")


def motion_for(position: int) -> str:
    """Alterne le sens du travelling d'un plan à l'autre."""
    return MOTIONS[position % len(MOTIONS)]


def has_media_duration(path: str | Path, minimum: float = 0.05) -> bool:
    """Vérifie qu'un fichier produit n'est pas vide/corrompu."""
    try:
        return probe(path).duration >= minimum
    except (MediaError, json.JSONDecodeError):
        return False

# --- Encodeur : détection matérielle -------------------------------------
@dataclass(frozen=True)
class Encoder:
    """Encodeur vidéo retenu pour la machine courante."""

    name: str
    hardware: bool
    quality_args: tuple[str, ...]
    speed_args: tuple[str, ...]

    def args(self, *, fast: bool = False) -> list[str]:
        return ["-c:v", self.name, *(self.speed_args if fast else self.quality_args)]


# Du plus rapide au plus universel. VideoToolbox couvre les Mac (cible
# principale), NVENC/QSV les PC ; libx264 reste le repli garanti.
_ENCODER_CANDIDATES: tuple[Encoder, ...] = (
    Encoder("h264_videotoolbox", True,
            ("-b:v", "8M", "-maxrate", "10M", "-bufsize", "16M", "-realtime", "0"),
            ("-b:v", "4M", "-realtime", "1")),
    Encoder("h264_nvenc", True,
            ("-preset", "p5", "-rc", "vbr", "-cq", "21", "-b:v", "8M", "-maxrate", "12M"),
            ("-preset", "p1", "-rc", "vbr", "-cq", "28", "-b:v", "4M")),
    Encoder("h264_qsv", True,
            ("-preset", "medium", "-global_quality", "22", "-b:v", "8M"),
            ("-preset", "veryfast", "-global_quality", "28", "-b:v", "4M")),
    Encoder("libx264", False,
            ("-preset", "fast", "-crf", "20", "-profile:v", "high", "-level", "4.1"),
            ("-preset", "veryfast", "-crf", "24")),
)

_LIBX264 = _ENCODER_CANDIDATES[-1]
_encoder_cache: Encoder | None = None


def _probe_encoder(encoder: Encoder) -> bool:
    """Teste réellement l'encodeur : la présence du codec ne suffit pas
    (un h264_nvenc listé sans GPU disponible échoue au premier appel)."""
    try:
        run([
            config.FFMPEG_BIN, "-hide_banner", "-loglevel", "error",
            "-f", "lavfi", "-i", "color=c=black:s=256x256:d=0.2:r=10",
            "-frames:v", "3", *encoder.args(fast=True), "-f", "null", "-",
        ], timeout=45)
        return True
    except (MediaError, subprocess.TimeoutExpired, OSError):
        return False


def detect_encoder(*, force: str | None = None) -> Encoder:
    """Retourne le meilleur encodeur disponible (résultat mis en cache)."""
    global _encoder_cache

    forced = force or os.environ.get("FLAMBEE_ENCODER", "").strip()
    if forced:
        match = next((e for e in _ENCODER_CANDIDATES if e.name == forced), None)
        return match or Encoder(forced, False, ("-preset", "medium"), ("-preset", "veryfast"))

    if _encoder_cache is not None:
        return _encoder_cache

    cached = _read_encoder_cache()
    if cached:
        _encoder_cache = cached
        return cached

    for candidate in _ENCODER_CANDIDATES:
        if candidate.name == "libx264" or _probe_encoder(candidate):
            log.info("Encodeur retenu : %s (matériel=%s)", candidate.name,
                     candidate.hardware)
            _encoder_cache = candidate
            _write_encoder_cache(candidate)
            return candidate
    return _LIBX264


def _cache_file() -> Path:
    return config.WORK_DIR / ".encoder"


def _read_encoder_cache() -> Encoder | None:
    try:
        name = _cache_file().read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return next((e for e in _ENCODER_CANDIDATES if e.name == name), None)


def _write_encoder_cache(encoder: Encoder) -> None:
    try:
        _cache_file().write_text(encoder.name, encoding="utf-8")
    except OSError:      # cache best-effort : jamais bloquant
        pass


# --- Exécution avec progression et annulation -----------------------------
class Cancelled(RuntimeError):
    """La tâche a été annulée par l'utilisateur."""


def ffmpeg_progress(
    args: list[str],
    *,
    duration: float,
    on_progress: Callable[[float], None] | None = None,
    cancel: "threading.Event | None" = None,
    timeout: int | None = None,
) -> None:
    """Lance ffmpeg en suivant l'avancement réel et en restant interruptible."""
    command = [
        config.FFMPEG_BIN, "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
        *args, "-progress", "pipe:1", "-nostats",
    ]
    log.debug("ffmpeg: %s", " ".join(command))
    proc = subprocess.Popen(
        command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, bufsize=1,
        preexec_fn=_lower_priority if config.FFMPEG_NICE else None,
    )

    deadline = time.monotonic() + timeout if timeout else None
    try:
        for line in proc.stdout or []:
            if cancel is not None and cancel.is_set():
                proc.kill()
                raise Cancelled("Rendu annulé.")
            if deadline and time.monotonic() > deadline:
                proc.kill()
                raise MediaError("ffmpeg a dépassé le temps imparti.")
            if on_progress and duration > 0 and line.startswith("out_time_us="):
                value = line.split("=", 1)[1].strip()
                if value.isdigit():
                    on_progress(min(1.0, int(value) / 1e6 / duration))
    finally:
        if proc.poll() is None:
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()

    stderr = (proc.stderr.read() if proc.stderr else "") or ""
    if proc.returncode != 0:
        tail = stderr.strip().splitlines()[-12:]
        raise MediaError(f"ffmpeg a échoué (code {proc.returncode}) :\n" + "\n".join(tail))
    if on_progress:
        on_progress(1.0)
