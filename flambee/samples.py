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


def _rendre_clip(preset: str, mots: list[Word], out_path: Path) -> Path:
    """Rend la bande de sous-titres pour ces mots. Appelé sous verrou."""
    style = config.subtitle_style(preset)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    duration = mots[-1].end + TAIL

    ass_path = out_path.with_suffix(".ass")
    subtitles.write_ass(mots, ass_path, style=style, max_duration=duration)

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
    return out_path


def build_sample(preset: str) -> Path:
    """Rend l'échantillon du style demandé (ou retourne celui déjà en cache)."""
    out_path = sample_path(preset)
    if out_path.exists() and has_media_duration(out_path, minimum=0.3):
        return out_path

    with _lock_for(preset):                 # deux requêtes simultanées, un seul rendu
        if out_path.exists() and has_media_duration(out_path, minimum=0.3):
            return out_path
        _rendre_clip(preset, sample_words(), out_path)
        log.info("Échantillon rendu : %s", out_path.name)
        return out_path


# --- Fond nu, pour l'aperçu du navigateur ----------------------------------
# Publié en pages statiques, le site n'a pas ffmpeg sous la main pour rendre la
# phrase d'un visiteur. Le navigateur dessine alors le texte lui-même, par
# dessus ce fond — qui reste, lui, celui du vrai moteur. Seul le texte est
# peint à l'écran ; le décor n'est pas une imitation.


def bande_apercu() -> tuple[int, int]:
    """Bande commune à tous les styles, et son sommet dans l'image.

    Chaque style pose son texte à sa propre hauteur. La bande est centrée sur
    l'ensemble d'entre eux, de sorte qu'aucun ne sorte du cadre : sans ce
    calcul, un style aux marges inhabituelles se ferait couper, et il faudrait
    y penser à chaque nouveau style plutôt qu'une fois ici.
    """
    fmt = config.FORMAT
    centres = [fmt.height - style.margin_v - int(style.font_size * 0.6)
               for style in config.SUBTITLE_PRESETS.values()]
    milieu = (min(centres) + max(centres)) // 2
    haut = max(0, min(fmt.height - BAND_HEIGHT, milieu - BAND_HEIGHT // 2))
    return haut, BAND_HEIGHT


FOND_DUREE = 6.0


def build_backdrop() -> Path:
    """Le fond des échantillons, sans aucun texte, prêt à tourner en boucle."""
    haut, hauteur = bande_apercu()
    empreinte = hashlib.sha256(
        f"{''.join(BACKDROP_COLORS)}|{haut}|{hauteur}|{OUT_WIDTH}|{FOND_DUREE}"
        .encode("utf-8")).hexdigest()[:12]
    out_path = config.WORK_DIR / ".samples" / f"fond-{empreinte}.mp4"
    if out_path.exists() and has_media_duration(out_path, minimum=0.5):
        return out_path

    with _lock_for("fond"):
        if out_path.exists() and has_media_duration(out_path, minimum=0.5):
            return out_path
        out_path.parent.mkdir(parents=True, exist_ok=True)
        fmt = config.FORMAT
        sortie_h = round(hauteur * OUT_WIDTH / fmt.width / 2) * 2
        graphe = (
            backdrop_filter(fmt.width, fmt.height)
            + f";[fond]crop={fmt.width}:{hauteur}:0:{haut},"
            f"scale={OUT_WIDTH}:{sortie_h},format=yuv420p[o]"
        )
        ffmpeg([
            *backdrop_inputs(FOND_DUREE),
            "-t", f"{FOND_DUREE:.2f}",
            "-filter_complex", graphe, "-map", "[o]",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "26",
            "-movflags", "+faststart", "-an",
            str(out_path),
        ], timeout=240)
        if not has_media_duration(out_path, minimum=0.5):
            raise MediaError("Fond d'aperçu vide.")
        return out_path


# --- Essayage : le texte du visiteur, rendu par le vrai moteur -------------
TEXTE_MAX = 70
# Ce qui entre dans un fichier ASS : les accolades y ouvrent un bloc de
# commandes, la barre oblique inversée introduit une directive, et un retour
# à la ligne termine l'événement — la suite serait lue comme du balisage.
# Les séparateurs deviennent des espaces, les directives disparaissent : sans
# cela, « ligne1\nligne2 » donnerait le mot « ligne1ligne2 ».
_INTERDITS = str.maketrans({"{": None, "}": None, "\\": None,
                            "\r": " ", "\n": " ", "\t": " "})


def nettoyer_texte(texte: str) -> str:
    """Rend un texte de visiteur inoffensif pour le format ASS.

    Le champ vient d'un formulaire public : sans ce filtre, on écrirait des
    directives de sous-titrage choisies par l'inconnu qui les tape.
    """
    # La traduction passe en premier : un retour à la ligne n'est pas un
    # caractère imprimable, le filtre suivant l'effacerait avant qu'il ait pu
    # devenir l'espace qui sépare deux mots.
    texte = texte.translate(_INTERDITS)
    texte = "".join(c for c in texte if c.isprintable())
    texte = " ".join(texte.split())          # espaces multiples, bords
    return texte[:TEXTE_MAX].strip()


def mots_du_texte(texte: str) -> list[Word]:
    """Minutage régulier : assez pour montrer le surlignage se déplacer."""
    return [
        Word(text=mot, start=i * WORD_DURATION, end=(i + 1) * WORD_DURATION)
        for i, mot in enumerate(texte.split())
    ]


def essayage_path(texte: str, preset: str) -> Path:
    style = config.subtitle_style(preset)
    empreinte = hashlib.sha256(
        f"{texte}|{preset}|{_signature(style)}".encode("utf-8")
    ).hexdigest()[:16]
    return config.WORK_DIR / ".essayages" / f"{preset}-{empreinte}.mp4"


def essayage(texte: str, preset: str) -> Path:
    """Rend la phrase du visiteur dans le style demandé, et met en cache.

    Le cache porte sur le couple texte + style : deux visiteurs qui tapent la
    même phrase ne déclenchent qu'un encodage.
    """
    texte = nettoyer_texte(texte)
    if not texte:
        raise MediaError("Écris une phrase pour voir le rendu.")
    if preset not in config.SUBTITLE_PRESETS:
        raise MediaError("Style inconnu.")

    out_path = essayage_path(texte, preset)
    if out_path.exists() and has_media_duration(out_path, minimum=0.3):
        return out_path

    with _lock_for(f"essayage:{out_path.name}"):
        if out_path.exists() and has_media_duration(out_path, minimum=0.3):
            return out_path
        _rendre_clip(preset, mots_du_texte(texte), out_path)
        _limiter_cache_essayages()
        log.info("Essayage rendu : %s (%s)", preset, texte[:40])
        return out_path


ESSAYAGES_MAX = 400


def _limiter_cache_essayages() -> None:
    """Le cache des essayages est alimenté par des inconnus : il faut un
    plafond, sinon le disque se remplit au rythme des visiteurs."""
    dossier = config.WORK_DIR / ".essayages"
    fichiers = sorted(dossier.glob("*.mp4"), key=lambda f: f.stat().st_mtime)
    for fichier in fichiers[:-ESSAYAGES_MAX]:
        fichier.unlink(missing_ok=True)


def clear_cache() -> int:
    """Supprime les échantillons en cache. Retourne le nombre de fichiers ôtés."""
    folder = config.WORK_DIR / ".samples"
    if not folder.exists():
        return 0
    fichiers = list(folder.glob("*.mp4")) + list(folder.glob("*.jpg"))
    for fichier in fichiers:
        fichier.unlink(missing_ok=True)
    return len(fichiers)


# --- Copie de visionnage ---------------------------------------------------
VIEWING_WIDTH = 540


def output_poster(source: Path, cache_dir: Path) -> Path:
    """Image fixe d'un rendu, pour les vignettes de la liste des créations.

    Une grille de quarante lecteurs vidéo est lourde à charger et, sans
    `preload="auto"`, la plupart des navigateurs n'en peignent aucune image :
    on obtient quarante rectangles noirs. Une image de 12 ko règle les deux.
    """
    cache_dir.mkdir(parents=True, exist_ok=True)
    empreinte = hashlib.sha256(
        f"{source.name}:{source.stat().st_mtime_ns}:{source.stat().st_size}"
        .encode("utf-8")
    ).hexdigest()[:12]
    out_path = cache_dir / f"affiche-{empreinte}.jpg"
    if out_path.exists() and out_path.stat().st_size > 800:
        return out_path

    with _lock_for(f"affiche:{empreinte}"):
        if out_path.exists() and out_path.stat().st_size > 800:
            return out_path
        for ancien in cache_dir.glob("affiche-*.jpg"):
            ancien.unlink(missing_ok=True)      # une seule affiche par projet
        # Une seconde après le début : le tout premier plan est souvent un
        # fondu, et une vignette noire n'apprend rien.
        ffmpeg(["-ss", "1.0", "-i", str(source), "-frames:v", "1",
                "-vf", "scale=360:-2", "-q:v", "5", str(out_path)], timeout=120)
        if not out_path.exists():
            raise MediaError("Affiche du rendu absente.")
        return out_path


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


# --- Un coucher de soleil, calculé ------------------------------------------
# La démonstration de la page d'accueil a besoin d'une vraie image, pas d'un
# aplat abstrait : c'est elle qui doit donner envie. Aucune banque d'images
# n'est utilisée — le plan est calculé pixel par pixel par ffmpeg, donc libre
# de droits, léger à stocker et impossible à retrouver ailleurs.
#
# Cinq couches, du fond vers l'avant : le ciel dégradé, le soleil et son halo,
# les bandes de nuages, la colline au loin, la mer et son chemin de lumière.
# Les quatre dernières sont peintes en basse définition puis agrandies : leurs
# contours sont doux par nature, l'agrandissement ne se voit pas et le calcul
# reste rapide.

HORIZON = 0.615        # hauteur de la ligne d'horizon, en fraction de l'image
SOLEIL_X = 0.37        # le soleil décentré : le cadre respire mieux
SOLEIL_Y = 0.578
SOLEIL_R = 0.135       # rayon du disque, en fraction de la largeur

# Du zénith à l'horizon : nuit, violet, braise, orange, or.
CIEL_COULEURS = ("0x0d1230", "0x2a2050", "0x7d3355",
                 "0xd65f34", "0xf59b3c", "0xffd27a")

DEF_COUCHE = 270       # largeur de calcul des couches peintes


def _couche(r: str, g: str, b: str, a: str, largeur: int, hauteur: int,
            duree: float) -> str:
    """Une couche RGBA peinte pixel par pixel, en basse définition."""
    return (f"color=c=black@0:s={largeur}x{hauteur}:d={duree:.2f}:r=25,"
            f"format=rgba,geq=r='{r}':g='{g}':b='{b}':a='{a}'")


def sunset_filter(width: int, height: int, duration: float,
                  *, tag: str = "fond") -> str:
    """Chaîne de filtres produisant le plan de coucher de soleil.

    N'attend aucune entrée : toutes les sources sont créées dans le graphe.
    """
    petit_l = DEF_COUCHE
    petit_h = round(petit_l * height / width / 2) * 2
    rapport = width / height          # pour que le soleil reste rond

    def peindre(r, g, b, a):
        return (_couche(r, g, b, a, petit_l, petit_h, duration)
                + f",scale={width}:{height}:flags=bicubic")

    ciel = (f"gradients=s={width}x{height}:d={duration:.2f}:r=25:speed=0.00001"
            f":n={len(CIEL_COULEURS)}"
            + "".join(f":c{i}={c}" for i, c in enumerate(CIEL_COULEURS))
            + f":x0=0:y0=0:x1=0:y1={int(height * HORIZON)}")

    # Le soleil : un disque net, un halo large, et la couleur qui passe du
    # blanc chaud au cœur à l'orange sur les bords.
    d = (f"(pow((X/W-{SOLEIL_X})/{SOLEIL_R}\\,2)"
         f"+pow((Y/H-{SOLEIL_Y})/{SOLEIL_R * rapport:.4f}\\,2))")
    soleil = peindre(
        "255",
        f"248-70*min(1\\,{d}/3)",
        f"214-160*min(1\\,{d}/2.2)",
        f"255*max(clip((1.05-{d})/0.08\\,0\\,1)\\,0.62*exp(-{d}/5))",
    )

    # Les nuages : des bandes fines, ondulées lentement, dont l'épaisseur
    # varie le long de la bande pour qu'elles ne soient pas des rubans.
    bande = ("max(0\\,sin(Y/H*46+1.4*sin(X/W*3.1+T*0.06)"
             "+0.5*sin(X/W*1.3-T*0.04)))")
    epaisseur = ("(0.30+0.70*pow(max(0\\,sin(X/W*2.3+Y/H*6.0+1.7))\\,1.4))")
    enveloppe = "exp(-pow((Y/H-0.31)/0.21\\,2))"
    altitude = "clip(Y/H/0.5\\,0\\,1)"
    nuages = peindre(
        f"190+65*{altitude}", f"150+22*{altitude}", f"192-84*{altitude}",
        f"255*0.72*pow({bande}\\,3)*{epaisseur}*{enveloppe}",
    )

    # La colline : deux bosses, presque noires, qui posent la profondeur.
    crete = (f"({HORIZON}-0.052*exp(-pow((X/W-0.87)/0.19\\,2))"
             f"-0.030*exp(-pow((X/W-0.63)/0.085\\,2)))")
    collines = peindre("26", "21", "48",
                       f"255*clip((Y/H-{crete})*{petit_h * 0.9:.0f}\\,0\\,1)")

    # La mer : chaude sous l'horizon, profonde au premier plan, avec le chemin
    # de lumière qui s'élargit en venant vers l'objectif.
    v = f"clip((Y/H-{HORIZON})/{1 - HORIZON:.3f}\\,0\\,1)"
    # La chaleur de la mer vient du soleil : elle décroît avec la distance à
    # sa verticale, sinon toute la bande sous l'horizon vire au brun uniforme.
    lateral = f"exp(-pow((X/W-{SOLEIL_X})/0.5\\,2))"
    profondeur = f"clip(pow({v}\\,0.5)+0.40*(1-{lateral})\\,0\\,1)"
    chemin = f"exp(-pow((X/W-{SOLEIL_X})/(0.02+0.30*{v})\\,2))"
    onde = "pow(max(0\\,sin(Y/H*230+T*1.3+0.8*sin(X/W*9)))\\,7)"
    tirets = "(0.18+0.82*pow(max(0\\,sin(X/W*26+Y/H*55+T*0.5))\\,2))"
    eclat = f"({chemin}*{onde}*{tirets})"
    mer = peindre(
        f"clip(216-198*{profondeur}+150*{eclat}\\,0\\,255)",
        f"clip(152-136*{profondeur}+140*{eclat}\\,0\\,255)",
        f"clip(100-56*{profondeur}+118*{eclat}\\,0\\,255)",
        f"255*clip((Y/H-{HORIZON})*{petit_h * 1.6:.0f}\\,0\\,1)",
    )

    return (
        f"{ciel}[s_ciel];{soleil}[s_soleil];{nuages}[s_nuages];"
        f"{collines}[s_collines];{mer}[s_mer];"
        f"[s_ciel][s_soleil]overlay[s_a];"
        f"[s_a][s_nuages]overlay[s_b];"
        f"[s_b][s_collines]overlay[s_c];"
        f"[s_c][s_mer]overlay,vignette=PI/5,noise=alls=5:allf=t[{tag}]"
    )


# --- Démonstration de la page d'accueil ------------------------------------
HERO_WIDTH = 404
HERO_TEXT = "Voici l'astuce que personne ne connaît et qui change tout"


def build_hero() -> Path:
    """Clip vertical de démonstration : le vrai moteur de sous-titres à l'œuvre.

    Rendu une fois puis mis en cache. Volontairement léger : c'est la première
    chose que charge la page d'accueil.
    """
    style = config.subtitle_style("punch")
    mots = [
        Word(text=mot, start=i * WORD_DURATION, end=(i + 1) * WORD_DURATION)
        for i, mot in enumerate(HERO_TEXT.split())
    ]
    duree = mots[-1].end + 0.8

    fmt = config.FORMAT
    hauteur = round(HERO_WIDTH * fmt.height / fmt.width / 2) * 2
    # Le plan est peint plus large que le cadre, puis parcouru lentement de
    # gauche à droite : un travelling de quelques pixels par seconde. Seul
    # l'axe horizontal bouge — l'horizon doit rester d'aplomb, sinon le plan
    # tangue. L'avance est adoucie aux deux bouts (3p²-2p³) pour qu'il n'y ait
    # ni départ sec ni arrêt net.
    large = int(fmt.width * 1.08) // 2 * 2
    marge_x = large - fmt.width
    p = f"min(1,t/{duree:.2f})"
    avance = f"(3*pow({p},2)-2*pow({p},3))"
    fond = sunset_filter(large, fmt.height, duree)

    # Le fond entre entier dans l'empreinte : retoucher une seule constante du
    # coucher de soleil suffit à périmer le cache, sans avoir à y penser.
    empreinte = hashlib.sha256(
        "|".join([repr(sorted(asdict(style).items())), HERO_TEXT,
                  fond, str(HERO_WIDTH), avance]).encode("utf-8")
    ).hexdigest()[:12]
    out_path = config.WORK_DIR / ".samples" / f"hero-{empreinte}.mp4"
    if out_path.exists() and has_media_duration(out_path, minimum=0.5):
        return out_path

    with _lock_for("hero"):
        if out_path.exists() and has_media_duration(out_path, minimum=0.5):
            return out_path

        out_path.parent.mkdir(parents=True, exist_ok=True)
        ass_path = out_path.with_suffix(".ass")
        subtitles.write_ass(mots, ass_path, style=style, max_duration=duree)

        from .assembler import _escape_filter_path

        graphe = (
            fond
            + f";[fond]crop={fmt.width}:{fmt.height}:x='{marge_x}*{avance}':y=0,"
            f"subtitles=filename='{_escape_filter_path(ass_path)}'"
            f":fontsdir='{_escape_filter_path(config.FONTS_DIR)}':alpha=1,"
            f"scale={HERO_WIDTH}:{hauteur},format=yuv420p[o]"
        )
        try:
            ffmpeg([
                "-t", f"{duree:.2f}",
                "-filter_complex", graphe, "-map", "[o]",
                "-c:v", "libx264", "-preset", "veryfast", "-crf", "24",
                "-movflags", "+faststart", "-an",
                str(out_path),
            ], timeout=300)
        finally:
            ass_path.unlink(missing_ok=True)

        if not has_media_duration(out_path, minimum=0.5):
            raise MediaError("Démonstration d'accueil vide.")
        return out_path


def _poster_time(preset: str) -> float:
    """Instant où saisir l'affiche : le milieu de la ligne la plus longue.

    Un instant fixe tombe facilement dans l'intervalle qui sépare deux lignes,
    où le texte a fini de s'effacer : l'affiche serait alors vide. On vise donc
    le cœur d'une ligne, là où elle est pleinement posée.
    """
    lignes = subtitles.group_words(sample_words(), config.subtitle_style(preset))
    if not lignes:
        return 0.5
    ligne = max(lignes, key=lambda l: l.end - l.start)
    return (ligne.start + ligne.end) / 2


def sample_poster(preset: str) -> Path:
    """Première image d'un échantillon de style, servie en affiche.

    Sans elle, les six vignettes de la page d'accueil restent noires tant que
    les clips n'ont pas commencé — c'est la première chose que voit un visiteur.
    """
    clip = build_sample(preset)
    poster = clip.with_suffix(".jpg")
    if poster.exists() and poster.stat().st_size > 500:
        return poster
    with _lock_for(f"{preset}-poster"):
        if poster.exists() and poster.stat().st_size > 500:
            return poster
        ffmpeg(["-ss", f"{_poster_time(preset):.2f}", "-i", str(clip),
                "-frames:v", "1", "-q:v", "4", str(poster)], timeout=120)
        if not poster.exists():
            raise MediaError(f"Affiche manquante pour le style {preset}.")
        return poster


def hero_poster() -> Path:
    """Première image de la démonstration, servie en affiche du lecteur.

    Sans elle, le cadre du téléphone reste noir tant que la vidéo n'a pas
    commencé — sur une connexion lente, cela dure.
    """
    clip = build_hero()
    poster = clip.with_suffix(".jpg")
    if poster.exists() and poster.stat().st_size > 1000:
        return poster
    with _lock_for("hero-poster"):
        if poster.exists() and poster.stat().st_size > 1000:
            return poster
        ffmpeg(["-ss", "1.2", "-i", str(clip), "-frames:v", "1",
                "-q:v", "4", str(poster)], timeout=120)
        if not poster.exists():
            raise MediaError("Affiche de démonstration absente.")
        return poster
