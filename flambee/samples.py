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

OUT_WIDTH = 540

# Le fond : six aplats de couleur agrandis puis fondus. Le dégradé de maille
# obtenu a de la profondeur, là où le filtre `gradients` de ffmpeg donne un
# lavis terne. Clair en haut, sombre en bas : le texte y ressort toujours.
BACKDROP_COLORS = ("0x5a3418", "0x1d2c4e",
                   "0x8a4514", "0x141c2c",
                   "0x0b0d13", "0x0a0b10")


def backdrop_inputs(duration: float) -> list[str]:
    """Entrées ffmpeg des six aplats du fond."""
    entrees: list[str] = []
    for couleur in BACKDROP_COLORS:
        entrees += ["-f", "lavfi", "-i",
                    f"color=c={couleur}:size=8x8:d={duration:.2f}:r=25"]
    return entrees


def backdrop_filter(width: int, height: int, *, tag: str = "fond") -> str:
    """Assemble et adoucit le fond jusqu'au format demandé."""
    demi_w, demi_h = max(2, width // 2), max(2, height // 2)
    return (
        "[0][1]hstack[r1];[2][3]hstack[r2];[4][5]hstack[r3];"
        f"[r1][r2][r3]vstack=inputs=3,"
        f"scale={demi_w}:{demi_h}:flags=bicubic,"
        f"gblur=sigma={max(40, demi_w // 5)},"      # c'est le flou qui fait la maille
        f"scale={width}:{height},vignette=PI/6,"
        f"noise=alls=4:allf=t+u[{tag}]"
    )


_locks: dict[str, threading.Lock] = {}
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
    """Empreinte de l'échantillon : le cache se renouvelle dès qu'un réglage
    change — style, texte, fond ou cadrage."""
    payload = "|".join([
        repr(sorted(asdict(style).items())), SAMPLE_TEXT,
        "".join(BACKDROP_COLORS), str(BAND_HEIGHT), str(OUT_WIDTH),
    ])
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:12]


def sample_path(preset: str) -> Path:
    style = config.subtitle_style(preset)
    folder = config.WORK_DIR / ".samples"
    return folder / f"{preset}-{_signature(style)}.mp4"


BAND_HEIGHT = 340      # hauteur de bande, identique pour tous les styles


def text_band(style: config.SubtitleStyle, fmt: config.VideoFormat) -> tuple[int, int]:
    """Bande à montrer : centrée sur le texte du style, hauteur constante.

    Le texte est calé en bas de l'image, à `margin_v` du bord ; on recadre
    autour de lui pour ne pas montrer du vide. La hauteur ne varie pas d'un
    style à l'autre : les vignettes restent alignées, et l'écart de taille
    entre un style et un autre se voit tel qu'il est.
    """
    centre = fmt.height - style.margin_v - int(style.font_size * 0.6)
    haut = max(0, min(fmt.height - BAND_HEIGHT, centre - BAND_HEIGHT // 2))
    return haut, BAND_HEIGHT


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
        haut, hauteur = text_band(style, fmt)
        sortie_h = round(hauteur * OUT_WIDTH / fmt.width / 2) * 2

        from .assembler import _escape_filter_path

        graphe = (
            backdrop_filter(fmt.width, fmt.height)
            + f";[fond]subtitles=filename='{_escape_filter_path(ass_path)}'"
            f":fontsdir='{_escape_filter_path(config.FONTS_DIR)}':alpha=1,"
            f"crop={fmt.width}:{hauteur}:0:{haut},"
            f"scale={OUT_WIDTH}:{sortie_h},format=yuv420p[o]"
        )
        try:
            ffmpeg([
                *backdrop_inputs(duration),
                "-t", f"{duration:.2f}",
                "-filter_complex", graphe, "-map", "[o]",
                "-c:v", "libx264", "-preset", "veryfast", "-crf", "24",
                "-movflags", "+faststart", "-an",
                str(out_path),
            ], timeout=240)
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


# --- Copie de visionnage ---------------------------------------------------
VIEWING_WIDTH = 540


def viewing_copy(source: Path, cache_dir: Path) -> Path:
    """Version allégée d'un rendu, destinée à la lecture dans la page.

    Lire un fichier 1080p à travers un tunnel saturé, sur une machine distante
    déjà occupée à encoder, met le lecteur du navigateur en difficulté et peut
    désynchroniser l'image et le son. La copie de visionnage supprime ce
    goulot ; le téléchargement, lui, sert toujours le fichier pleine qualité.
    """
    cache_dir.mkdir(parents=True, exist_ok=True)
    empreinte = hashlib.sha256(
        f"{source.name}:{source.stat().st_mtime_ns}:{source.stat().st_size}"
        .encode("utf-8")
    ).hexdigest()[:12]
    out_path = cache_dir / f"visionnage-{empreinte}.mp4"
    if out_path.exists() and has_media_duration(out_path, minimum=0.5):
        return out_path

    with _lock_for(f"viewing:{empreinte}"):
        if out_path.exists() and has_media_duration(out_path, minimum=0.5):
            return out_path
        for ancien in cache_dir.glob("visionnage-*.mp4"):
            ancien.unlink(missing_ok=True)      # une seule copie à la fois
        ffmpeg([
            "-i", str(source),
            "-vf", f"scale={VIEWING_WIDTH}:-2",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "30",
            "-c:a", "aac", "-b:a", "96k", "-movflags", "+faststart",
            str(out_path),
        ], timeout=900)
        if not has_media_duration(out_path, minimum=0.5):
            raise MediaError("Copie de visionnage vide.")
        return out_path


# --- Démonstration de la page d'accueil ------------------------------------
HERO_WIDTH = 404
HERO_TEXT = "Voici l'astuce que personne ne connaît et qui change tout"


def build_hero() -> Path:
    """Clip vertical de démonstration : le vrai moteur de sous-titres à l'œuvre.

    Rendu une fois puis mis en cache. Volontairement léger : c'est la première
    chose que charge la page d'accueil.
    """
    style = config.subtitle_style("punch")
    empreinte = hashlib.sha256(
        "|".join([repr(sorted(asdict(style).items())), HERO_TEXT,
                  "".join(BACKDROP_COLORS), str(HERO_WIDTH)]).encode("utf-8")
    ).hexdigest()[:12]
    out_path = config.WORK_DIR / ".samples" / f"hero-{empreinte}.mp4"
    if out_path.exists() and has_media_duration(out_path, minimum=0.5):
        return out_path

    with _lock_for("hero"):
        if out_path.exists() and has_media_duration(out_path, minimum=0.5):
            return out_path

        out_path.parent.mkdir(parents=True, exist_ok=True)
        mots = [
            Word(text=mot, start=i * WORD_DURATION, end=(i + 1) * WORD_DURATION)
            for i, mot in enumerate(HERO_TEXT.split())
        ]
        duree = mots[-1].end + 0.8
        ass_path = out_path.with_suffix(".ass")
        subtitles.write_ass(mots, ass_path, style=style, max_duration=duree)

        fmt = config.FORMAT
        hauteur = round(HERO_WIDTH * fmt.height / fmt.width / 2) * 2

        from .assembler import _escape_filter_path

        # Le fond est agrandi puis lentement balayé : sur une page d'accueil,
        # une image parfaitement immobile a l'air en panne.
        large, haut_large = int(fmt.width * 1.14), int(fmt.height * 1.14)
        marge_x, marge_y = large - fmt.width, haut_large - fmt.height
        avance = f"min(1,t/{duree:.2f})"
        graphe = (
            backdrop_filter(large, haut_large)
            + f";[fond]crop={fmt.width}:{fmt.height}"
            f":x='{marge_x}*{avance}':y='{marge_y}*(1-{avance})',"
            f"subtitles=filename='{_escape_filter_path(ass_path)}'"
            f":fontsdir='{_escape_filter_path(config.FONTS_DIR)}':alpha=1,"
            f"scale={HERO_WIDTH}:{hauteur},format=yuv420p[o]"
        )
        try:
            ffmpeg([
                *backdrop_inputs(duree),
                "-t", f"{duree:.2f}",
                "-filter_complex", graphe, "-map", "[o]",
                "-c:v", "libx264", "-preset", "veryfast", "-crf", "26",
                "-movflags", "+faststart", "-an",
                str(out_path),
            ], timeout=240)
        finally:
            ass_path.unlink(missing_ok=True)

        if not has_media_duration(out_path, minimum=0.5):
            raise MediaError("Démonstration d'accueil vide.")
        return out_path
