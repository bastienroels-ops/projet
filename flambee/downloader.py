"""Étape 1 — téléchargement des vidéos sources via yt-dlp.

Usage strictement personnel : les vidéos téléchargées restent en local dans
`work/` et ne sont ni partagées ni republiées telles quelles.
"""

from __future__ import annotations

import logging
import re
import threading
from concurrent.futures import ThreadPoolExecutor
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
    has_audio: bool = True
    extractor: str = ""
    warnings: list[str] = field(default_factory=list)
    error: str = ""
    hook_path: str = ""          # rempli par trimmer.extract_hooks()
    hook_url: str = ""           # chemin servi par l'app
    hook_score: float = 0.0      # rempli par analyzer (0 → 1)

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
    """Vérifie le nombre de liens fournis (1 à 5).

    Un seul lien est un cas complet : la vidéo est retouchée sans montage."""
    if len(urls) < config.MIN_SOURCES:
        raise DownloadError(
            "Colle au moins un lien vidéo "
            f"({len(urls)} détecté(s))."
        )
    if len(urls) > config.MAX_SOURCES:
        raise DownloadError(
            f"Maximum {config.MAX_SOURCES} liens ({len(urls)} détecté(s))."
        )


def _cookie_options() -> dict:
    """Cookies du navigateur ou fichier, si l'utilisateur en a configuré."""
    options: dict = {}
    if config.COOKIES_FROM_BROWSER:
        options["cookiesfrombrowser"] = (config.COOKIES_FROM_BROWSER,)
    if config.COOKIES_FILE and Path(config.COOKIES_FILE).exists():
        options["cookiefile"] = config.COOKIES_FILE
    return options


def _ydl_options(dest_dir: Path, index: int) -> dict:
    return {
        **_cookie_options(),
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


def download_one(
    url: str,
    dest_dir: Path,
    index: int,
    *,
    cancel: threading.Event | None = None,
    solo: bool = False,
) -> Source:
    """Télécharge une vidéo et retourne ses métadonnées.

    `solo` : c'est la seule vidéo du projet, elle ne sera pas découpée."""
    from yt_dlp import YoutubeDL  # import tardif : démarrage de l'app plus rapide
    from yt_dlp.utils import DownloadError as YdlError

    dest_dir.mkdir(parents=True, exist_ok=True)
    source = Source(index=index, url=url)

    options = _ydl_options(dest_dir, index)
    if cancel is not None:
        def _abort_if_cancelled(_status: dict) -> None:
            if cancel.is_set():
                raise DownloadError("Téléchargement annulé.")

        options["progress_hooks"] = [_abort_if_cancelled]

    try:
        with YoutubeDL(options) as ydl:
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
    try:
        media = probe(path)
        source.duration = source.duration or media.duration
        source.width = source.width or media.width
        source.height = source.height or media.height
        source.has_audio = media.has_audio
    except MediaError as exc:
        source.warnings.append(f"Analyse ffprobe impossible : {exc}")

    source.warnings.extend(_check_source(source, solo=solo))
    return source


def _check_source(source: Source, *, solo: bool = False) -> list[str]:
    """Contrôles simples demandés par le cahier des charges.

    Seule, une vidéo n'est pas découpée : sa durée n'a alors rien à signaler."""
    warnings: list[str] = []
    if (not solo and source.duration
            and source.duration < config.MIN_SOURCE_DURATION):
        warnings.append(
            f"Vidéo courte ({source.duration:.0f}s) : moins de "
            f"{config.MIN_SOURCE_DURATION:.0f}s, peu de matière à découper."
        )
    if source.width and source.height and source.width > source.height:
        warnings.append(
            "Vidéo horizontale : elle sera recadrée en 9:16 (perte sur les côtés)."
        )
    return warnings


# Ces messages s'adressent à quelqu'un qui tient un téléphone, pas à
# quelqu'un devant un terminal. « Configure FLAMBEE_COOKIES_FROM_BROWSER »
# était la bonne réponse technique et la mauvaise réponse tout court : la
# seule action possible depuis un iPhone, c'est d'enregistrer la vidéo dans
# l'application puis de l'importer. On le dit, et on garde la piste des
# cookies pour ceux qui tournent sur leur propre machine.
_REPLI_IMPORT = ("Enregistre-la depuis l'application (Partager → Enregistrer "
                 "la vidéo), puis reviens l'importer avec « Choisir des "
                 "vidéos ».")

# L'ordre décide : la première correspondance gagne, et « Private video.
# Sign in if you have been granted access » contient les deux. Conseiller
# d'enregistrer depuis l'application une vidéo privée serait un mauvais
# conseil — elle ne s'y enregistre pas non plus.
_ERROR_HINTS = (
    ("private", "Vidéo privée : elle ne peut pas être téléchargée, même en "
                "étant connecté."),
    ("not a bot", "La plateforme demande une connexion pour cette vidéo. "
                  + _REPLI_IMPORT),
    ("login required", "Vidéo réservée aux comptes connectés. " + _REPLI_IMPORT),
    ("sign in", "La plateforme demande une connexion. " + _REPLI_IMPORT),
    ("unavailable", "Vidéo indisponible : supprimée, ou bloquée dans ta "
                    "région."),
    ("not available", "Vidéo indisponible : supprimée, ou bloquée dans ta "
                      "région."),
    ("unsupported url", "Lien non reconnu. Vérifie que c'est bien l'adresse "
                        "d'une vidéo, pas celle d'un profil ou d'un son."),
    ("http error 403", "La plateforme a refusé l'accès. " + _REPLI_IMPORT),
    ("http error 404", "Cette adresse ne mène à aucune vidéo : le lien est "
                       "peut-être tronqué."),
    ("timed out", "La plateforme n'a pas répondu à temps. Réessaie, puis "
                  "importe la vidéo si ça recommence."),
    ("no video formats", "Aucune vidéo derrière ce lien. " + _REPLI_IMPORT),
)


def _sans_prefixe(ligne: str) -> str:
    """Retire « [TikTok] 7234567890 : » en tête, sans manger un vrai mot.

    Un identifiant se reconnaît à ceci : collé aux deux-points, et soit
    chiffré, soit trop long pour être un mot. « Unsupported URL: » n'en est
    pas un — il y a une espace avant le deux-points. « Warning: » non plus.
    """
    ligne = re.sub(r"^\[[^\]]+\]\s*", "", ligne)
    tete = re.match(r"^([\w.-]+):\s*", ligne)
    if tete:
        jeton = tete.group(1)
        if any(c.isdigit() for c in jeton) or len(jeton) >= 8:
            ligne = ligne[tete.end():]
    ligne = ligne.strip()
    return ligne[:1].upper() + ligne[1:] if ligne else ligne


def _clean_ydl_error(message: str) -> str:
    """Rend la première ligne de yt-dlp lisible, et lui ajoute quoi faire.

    On retire le préfixe d'extracteur — « [TikTok] 7234… : » — qui n'apprend
    rien à qui lit : la liste des sources dit déjà quel lien a échoué.
    """
    message = re.sub(r"\x1b\[[0-9;]*m", "", message)
    message = message.replace("ERROR: ", "").strip()
    first = message.splitlines()[0] if message else "Téléchargement impossible."
    lowered = first.lower()
    # Le repérage se fait sur la ligne entière ; seul l'affichage est nettoyé.
    visible = _sans_prefixe(first)

    for needle, hint in _ERROR_HINTS:
        if needle in lowered:
            return f"{visible.split(' See ')[0].strip()} → {hint}"
    return visible


def download_all(
    urls: list[str],
    dest_dir: Path,
    *,
    on_progress: Callable[[int, int, Source], None] | None = None,
    cancel: threading.Event | None = None,
    parallel: bool = True,
) -> list[Source]:
    """Télécharge toutes les sources en parallèle, en continuant malgré les échecs.

    Les téléchargements passent l'essentiel de leur temps à attendre le réseau :
    les lancer ensemble divise l'attente par le nombre de liens.
    """
    total = len(urls)
    done = 0
    lock = threading.Lock()
    results: dict[int, Source] = {}

    def fetch(item: tuple[int, str]) -> Source:
        nonlocal done
        index, url = item
        if cancel is not None and cancel.is_set():
            source = Source(index=index, url=url, error="Téléchargement annulé.")
        else:
            log.info("Téléchargement %s/%s : %s", index, total, url)
            source = download_one(url, dest_dir, index, cancel=cancel,
                                  solo=total == 1)
        with lock:
            done += 1
            results[index] = source
            if on_progress:
                on_progress(done, total, source)
        return source

    items = list(enumerate(urls, start=1))
    if parallel and total > 1:
        with ThreadPoolExecutor(max_workers=min(total, 4)) as pool:
            list(pool.map(fetch, items))
    else:
        for item in items:
            fetch(item)

    return [results[index] for index, _ in items if index in results]
