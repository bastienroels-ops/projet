"""Étape 1 — téléchargement des vidéos sources via yt-dlp.

Usage strictement personnel : les vidéos téléchargées restent en local dans
`work/` et ne sont ni partagées ni republiées telles quelles.
"""

from __future__ import annotations

import logging
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable, Iterable

from . import config
from .media import MediaError, probe

log = logging.getLogger(__name__)

_URL_RE = re.compile(r"^https?://", re.IGNORECASE)

# Formats acceptés : on privilégie un mp4 H.264/AAC directement exploitable.
_YDL_FORMAT = (
    "bestvideo[height<=1920][ext=mp4]+bestaudio[ext=m4a]/"
    "best[height<=1920][ext=mp4]/best"
)


class DownloadError(RuntimeError):
    """Le téléchargement d'une source a échoué."""


@dataclass
class Source:
    """Une vidéo source téléchargée."""

    index: int
    url: str
    path: str = ""
    title: str = ""
    uploader: str = ""
    duration: float = 0.0
    width: int = 0
    height: int = 0
    view_count: int | None = None
    like_count: int | None = None
    extractor: str = ""
    warnings: list[str] = field(default_factory=list)
    error: str = ""
    hook_path: str = ""          # rempli par trimmer.extract_hooks()
    hook_url: str = ""           # chemin servi par l'app

    @property
    def ok(self) -> bool:
        return bool(self.path) and not self.error

    def to_dict(self) -> dict:
        data = asdict(self)
        data["ok"] = self.ok
        return data


def normalize_urls(raw: str | Iterable[str]) -> list[str]:
    """Extrait les URLs d'un bloc de texte collé par l'utilisateur."""
    if isinstance(raw, str):
        candidates = re.split(r"[\s,;]+", raw.strip())
    else:
        candidates = [str(item).strip() for item in raw]

    urls: list[str] = []
    for candidate in candidates:
        candidate = candidate.strip().strip("<>\"'")
        if candidate and _URL_RE.match(candidate) and candidate not in urls:
            urls.append(candidate)
    return urls


def validate_urls(urls: list[str]) -> None:
    """Vérifie le nombre de liens fournis (2 à 5 d'après le cahier des charges)."""
    if len(urls) < config.MIN_SOURCES:
        raise DownloadError(
            f"Il faut au moins {config.MIN_SOURCES} liens vidéo "
            f"({len(urls)} détecté(s))."
        )
    if len(urls) > config.MAX_SOURCES:
        raise DownloadError(
            f"Maximum {config.MAX_SOURCES} liens ({len(urls)} détecté(s))."
        )


def _ydl_options(dest_dir: Path, index: int) -> dict:
    return {
        "outtmpl": str(dest_dir / f"source_{index:02d}.%(ext)s"),
        "format": _YDL_FORMAT,
        "merge_output_format": "mp4",
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "retries": 3,
        "fragment_retries": 3,
        "concurrent_fragment_downloads": 4,
        "restrictfilenames": True,
        "overwrites": True,
        "postprocessors": [
            {"key": "FFmpegVideoRemuxer", "preferedformat": "mp4"},
        ],
    }


def download_one(url: str, dest_dir: Path, index: int) -> Source:
    """Télécharge une vidéo et retourne ses métadonnées."""
    from yt_dlp import YoutubeDL  # import tardif : démarrage de l'app plus rapide
    from yt_dlp.utils import DownloadError as YdlError

    dest_dir.mkdir(parents=True, exist_ok=True)
    source = Source(index=index, url=url)

    try:
        with YoutubeDL(_ydl_options(dest_dir, index)) as ydl:
            info = ydl.extract_info(url, download=True)
            if info.get("_type") == "playlist":         # sécurité : 1re entrée
                entries = [e for e in info.get("entries") or [] if e]
                if not entries:
                    raise DownloadError("Aucune vidéo trouvée derrière ce lien.")
                info = entries[0]
            path = Path(ydl.prepare_filename(info))
    except YdlError as exc:
        source.error = _clean_ydl_error(str(exc))
        return source
    except Exception as exc:  # pragma: no cover - dépend du site distant
        source.error = f"Téléchargement impossible : {exc}"
        return source

    if not path.exists():
        # Le post-processing a pu changer l'extension (mkv -> mp4, etc.).
        matches = sorted(dest_dir.glob(f"source_{index:02d}.*"))
        if not matches:
            source.error = "Fichier téléchargé introuvable."
            return source
        path = matches[0]

    source.path = str(path)
    source.title = info.get("title") or path.stem
    source.uploader = info.get("uploader") or info.get("channel") or ""
    source.extractor = info.get("extractor_key") or ""
    source.view_count = info.get("view_count")
    source.like_count = info.get("like_count")
    source.duration = float(info.get("duration") or 0.0)
    source.width = int(info.get("width") or 0)
    source.height = int(info.get("height") or 0)

    # Les métadonnées yt-dlp sont parfois absentes : on complète via ffprobe.
    if not source.duration or not source.width:
        try:
            media = probe(path)
            source.duration = source.duration or media.duration
            source.width = source.width or media.width
            source.height = source.height or media.height
        except MediaError as exc:
            source.warnings.append(f"Analyse ffprobe impossible : {exc}")

    source.warnings.extend(_check_source(source))
    return source


def _check_source(source: Source) -> list[str]:
    """Contrôles simples demandés par le cahier des charges."""
    warnings: list[str] = []
    if source.duration and source.duration < config.MIN_SOURCE_DURATION:
        warnings.append(
            f"Vidéo courte ({source.duration:.0f}s) : moins de "
            f"{config.MIN_SOURCE_DURATION:.0f}s, peu de matière à découper."
        )
    if source.width and source.height and source.width > source.height:
        warnings.append(
            "Vidéo horizontale : elle sera recadrée en 9:16 (perte sur les côtés)."
        )
    return warnings


def _clean_ydl_error(message: str) -> str:
    message = re.sub(r"\x1b\[[0-9;]*m", "", message)
    message = message.replace("ERROR: ", "").strip()
    return message.splitlines()[0] if message else "Téléchargement impossible."


def download_all(
    urls: list[str],
    dest_dir: Path,
    *,
    on_progress: Callable[[int, int, Source], None] | None = None,
) -> list[Source]:
    """Télécharge toutes les sources, en continuant malgré les échecs."""
    sources: list[Source] = []
    total = len(urls)
    for index, url in enumerate(urls, start=1):
        log.info("Téléchargement %s/%s : %s", index, total, url)
        source = download_one(url, dest_dir, index)
        sources.append(source)
        if on_progress:
            on_progress(index, total, source)
    return sources
