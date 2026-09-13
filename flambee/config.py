"""Configuration globale de Flambée.

Tout est local : aucun compte, aucune base de données. Les chemins sont
relatifs à la racine du projet, surchargeables par variables d'environnement.
"""

from __future__ import annotations

import functools
import logging
import os
import struct
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent.parent


def _path_env(name: str, default: Path) -> Path:
    raw = os.environ.get(name)
    return Path(raw).expanduser().resolve() if raw else default


# --- Dossiers -------------------------------------------------------------
WORK_DIR = _path_env("FLAMBEE_WORK_DIR", BASE_DIR / "work")
OUTPUT_DIR = _path_env("FLAMBEE_OUTPUT_DIR", BASE_DIR / "output")
ASSETS_DIR = _path_env("FLAMBEE_ASSETS_DIR", BASE_DIR / "assets")
MUSIC_DIR = ASSETS_DIR / "music"

for _d in (WORK_DIR, OUTPUT_DIR, MUSIC_DIR):
    _d.mkdir(parents=True, exist_ok=True)


# --- Binaires externes ----------------------------------------------------
FFMPEG_BIN = os.environ.get("FLAMBEE_FFMPEG", "ffmpeg")
FFPROBE_BIN = os.environ.get("FLAMBEE_FFPROBE", "ffprobe")

# Priorité de l'encodage (0 = normale, 10 = efface devant le reste). Sur une
# machine distante, un ffmpeg qui monopolise les cœurs finit par étrangler le
# tunnel réseau, et la page devient injoignable en plein rendu.
FFMPEG_NICE = int(os.environ.get("FLAMBEE_NICE", "0"))


def _threads_par_defaut() -> int:
    """Cœurs laissés à l'encodage. 0 signifie « tous », au sens de ffmpeg.

    Sur une machine à deux cœurs — celle de Colab — un encodage qui les prend
    tous les deux prive le tunnel HTTPS de son souffle : cloudflared perd sa
    liaison et le navigateur affiche une erreur 1033 en plein rendu. Abaisser
    la priorité ne suffit pas, puisque les deux cœurs restent occupés ; il faut
    vraiment en laisser un. On encode alors un peu moins vite, mais on ne perd
    pas la session.
    """
    coeurs = os.cpu_count() or 1
    return 1 if coeurs <= 2 else 0


FFMPEG_THREADS = int(os.environ.get("FLAMBEE_FFMPEG_THREADS",
                                    _threads_par_defaut()))


# --- Cookies yt-dlp -------------------------------------------------------
# YouTube/TikTok demandent parfois une connexion (« Sign in to confirm you're
# not a bot »). Deux solutions, au choix :
#   FLAMBEE_COOKIES_FROM_BROWSER=chrome|safari|firefox|brave|edge
#   FLAMBEE_COOKIES_FILE=/chemin/vers/cookies.txt   (export Netscape)
COOKIES_FROM_BROWSER = os.environ.get("FLAMBEE_COOKIES_FROM_BROWSER", "").strip()
COOKIES_FILE = os.environ.get("FLAMBEE_COOKIES_FILE", "").strip()


# --- Import de fichiers ---------------------------------------------------
UPLOAD_EXTENSIONS = {".mp4", ".mov", ".m4v", ".webm", ".mkv", ".avi", ".3gp"}
MAX_UPLOAD_BYTES = int(os.environ.get("FLAMBEE_MAX_UPLOAD_MB", "600")) * 1024 * 1024


# --- Accès ----------------------------------------------------------------
# Vide = aucune authentification (usage local). Dès que l'app est exposée
# au-delà de la machine, définir FLAMBEE_PASSWORD.
PASSWORD = os.environ.get("FLAMBEE_PASSWORD", "").strip()
USERNAME = os.environ.get("FLAMBEE_USERNAME", "flambee").strip() or "flambee"


# --- Contraintes sources --------------------------------------------------
MIN_SOURCES = 2
MAX_SOURCES = 5
MIN_SOURCE_DURATION = float(os.environ.get("FLAMBEE_MIN_DURATION", "45"))
HOOK_DURATION = float(os.environ.get("FLAMBEE_HOOK_DURATION", "3"))


# --- Format de sortie -----------------------------------------------------
@dataclass(frozen=True)
class VideoFormat:
    width: int = 1080
    height: int = 1920
    fps: int = 30
    video_bitrate: str = "6M"
    audio_bitrate: str = "192k"

    @property
    def size(self) -> str:
        return f"{self.width}x{self.height}"

    @property
    def aspect(self) -> float:
        return self.width / self.height


FORMAT = VideoFormat()

# Aperçu : même cadrage, quatre fois moins de pixels — pour vérifier un montage
# en quelques secondes avant de lancer le rendu définitif.
PREVIEW_FORMAT = VideoFormat(
    width=FORMAT.width // 2, height=FORMAT.height // 2, fps=FORMAT.fps,
    video_bitrate="2M", audio_bitrate="128k",
)


# --- Voix edge-tts françaises --------------------------------------------
# Liste figée (rapide, sans réseau) ; `voice.list_voices()` interroge
# edge-tts pour la liste à jour quand la machine est connectée.
FRENCH_VOICES: list[dict[str, str]] = [
    {"id": "fr-FR-DeniseNeural", "label": "Denise (FR, féminine)"},
    {"id": "fr-FR-EloiseNeural", "label": "Eloise (FR, féminine, jeune)"},
    {"id": "fr-FR-HenriNeural", "label": "Henri (FR, masculine)"},
    {"id": "fr-FR-RemyMultilingualNeural", "label": "Rémy (FR, masculine, multilingue)"},
    {"id": "fr-FR-VivienneMultilingualNeural", "label": "Vivienne (FR, féminine, multilingue)"},
    {"id": "fr-BE-CharlineNeural", "label": "Charline (BE, féminine)"},
    {"id": "fr-BE-GerardNeural", "label": "Gérard (BE, masculine)"},
    {"id": "fr-CA-SylvieNeural", "label": "Sylvie (CA, féminine)"},
    {"id": "fr-CA-AntoineNeural", "label": "Antoine (CA, masculine)"},
    {"id": "fr-CH-ArianeNeural", "label": "Ariane (CH, féminine)"},
]
DEFAULT_VOICE = "fr-FR-DeniseNeural"


# --- Style des sous-titres ------------------------------------------------
FONTS_DIR = ASSETS_DIR / "fonts"


@dataclass
class SubtitleStyle:
    """Un style de sous-titres complet, traduit en en-tête ASS."""

    label: str = "Punch"
    description: str = ""
    font: str = "Archivo Black"
    font_size: int = 84
    bold: int = 0                          # 0 : la police est déjà grasse
    primary_color: str = "&H00FFFFFF"      # blanc (ABGR)
    highlight_color: str = "&H0000D7FF"    # ambre sur le mot prononcé
    outline_color: str = "&H00000000"      # contour noir
    back_color: str = "&H64000000"         # fond (utile si border_style=3)
    border_style: int = 1                  # 1 = contour, 3 = bandeau plein
    outline: int = 6
    shadow: int = 2
    spacing: int = 0                       # interlettrage
    margin_v: int = 420                    # hauteur au-dessus du bas de l'image
    max_chars_per_line: int = 22
    max_words_per_line: int = 4
    animate: bool = True                   # effet « pop » sur chaque ligne
    uppercase: bool = False
    glow: int = 0                          # flou du contour (effet néon)
    highlight_scale: int = 100             # grossissement du mot actif, en %


# Le fichier de chaque police, pour que le navigateur puisse charger les
# mêmes que ffmpeg. Sans cette table, l'aperçu à l'écran retomberait sur une
# police système et ne ressemblerait plus au rendu final.
POLICES_FICHIERS = {
    "Archivo Black": "ArchivoBlack-Regular.ttf",
    "Anton": "Anton-Regular.ttf",
    "Bebas Neue": "BebasNeue-Regular.ttf",
    "Playfair Display": "PlayfairDisplay-Bold.ttf",
}


@functools.lru_cache(maxsize=None)
def echelle_police(fichier: str) -> float:
    """Rapport entre la taille d'un style ASS et la même taille en pixels CSS.

    libass ne traite pas `Fontsize` comme le cadratin de la police, mais comme
    la hauteur totale ascendante + descendante déclarée dans la table OS/2 —
    la règle héritée de VSFilter. Un `font_size` de 92 donne donc, pour Archivo
    Black, un cadratin de 68 pixels seulement.

    Sans ce rapport, l'aperçu du navigateur dessine un tiers trop gros : le
    texte déborde du cadre et se replie là où le moteur, lui, tient la ligne.
    Mesuré ici dans le fichier de police plutôt que réglé à la main, pour qu'un
    changement de police n'introduise pas d'écart silencieux.
    """
    chemin = FONTS_DIR / fichier
    try:
        données = chemin.read_bytes()
        nombre = struct.unpack(">H", données[4:6])[0]
        tables = {}
        for i in range(nombre):
            entree = 12 + i * 16
            nom = données[entree:entree + 4].decode("latin-1")
            tables[nom] = struct.unpack(">I", données[entree + 8:entree + 12])[0]
        tete = tables["head"]
        cadratin = struct.unpack(">H", données[tete + 18:tete + 20])[0]
        os2 = tables["OS/2"]
        montant, descendant = struct.unpack(">HH", données[os2 + 74:os2 + 78])
        hauteur = montant + descendant
        if cadratin and hauteur:
            return round(cadratin / hauteur, 5)
    except (OSError, KeyError, struct.error, IndexError):
        log.warning("Métrique illisible pour %s : aperçu approximatif.", fichier)
    return 0.75          # l'ordre de grandeur commun aux polices d'affichage


def couleur_web(valeur: str) -> str:
    """Traduit une couleur ASS « &HAABBGGRR » en rgba() CSS.

    Deux pièges : l'ordre des octets est inversé par rapport au web, et AA est
    une *transparence*, non une opacité — &H00 est opaque, &HFF invisible.
    """
    v = valeur.lstrip("&Hh").rjust(8, "0")[-8:]
    a, b, g, r = (int(v[i:i + 2], 16) for i in (0, 2, 4, 6))
    return f"rgba({r},{g},{b},{round(1 - a / 255, 3)})"


def style_pour_le_web(style: "SubtitleStyle") -> dict:
    """Le nécessaire pour redessiner ce style dans un canevas.

    Les tailles restent exprimées dans le cadre de 1080 × 1920 : c'est le
    navigateur qui les met à l'échelle de son affichage, comme ffmpeg le fait
    pour la vidéo. Une conversion ici et les deux rendus divergeraient.
    """
    return {
        "police": style.font,
        "fichier": POLICES_FICHIERS.get(style.font, ""),
        "taille": style.font_size,
        "echelle": echelle_police(POLICES_FICHIERS.get(style.font, "")),
        "contour": style.outline,
        "ombre": style.shadow,
        "halo": style.glow,
        "bandeau": style.border_style == 3,
        "capitales": style.uppercase,
        "interlettre": style.spacing,
        "grossissement": style.highlight_scale / 100,
        "marge_bas": style.margin_v,
        "car_par_ligne": style.max_chars_per_line,
        "mots_par_ligne": style.max_words_per_line,
        "couleurs": {
            "texte": couleur_web(style.primary_color),
            "actif": couleur_web(style.highlight_color),
            "contour": couleur_web(style.outline_color),
            "fond": couleur_web(style.back_color),
        },
    }


# Six rendus prêts à l'emploi, du plus viral au plus sobre.
SUBTITLE_PRESETS: dict[str, SubtitleStyle] = {
    "punch": SubtitleStyle(
        label="Punch",
        description="Le classique des vidéos virales : gros, blanc, mot actif ambre.",
        font_size=92, outline=7, highlight_scale=104,
    ),
    "impact": SubtitleStyle(
        label="Impact",
        description="Capitales condensées et serrées, pour un ton affirmé.",
        font="Anton", font_size=104, uppercase=True, outline=7, shadow=0,
        highlight_color="&H004080FF", max_chars_per_line=18,
        max_words_per_line=3, highlight_scale=106, margin_v=440,
    ),
    "neon": SubtitleStyle(
        label="Néon",
        description="Halo lumineux sur le mot prononcé, ambiance nocturne.",
        font="Archivo Black", font_size=88, outline=5, shadow=0, glow=6,
        highlight_color="&H00F5FF00", outline_color="&H00902000",
        max_chars_per_line=18, max_words_per_line=3, margin_v=470,
    ),
    "studio": SubtitleStyle(
        label="Studio",
        description="Texte posé sur un bandeau sombre : lisible sur toute image.",
        font="Archivo Black", font_size=70, border_style=3, outline=18, shadow=0,
        back_color="&HB4000000", outline_color="&HB4000000",
        highlight_color="&H0000D7FF", max_chars_per_line=26,
        max_words_per_line=5, margin_v=360,
    ),
    "signature": SubtitleStyle(
        label="Signature",
        description="Serif haut de gamme, or discret, rythme posé.",
        font="Playfair Display", font_size=88, bold=0, outline=4, shadow=5,
        primary_color="&H00F2F6FA", highlight_color="&H0078C0F0",
        spacing=2, max_chars_per_line=24, max_words_per_line=4,
        margin_v=360, highlight_scale=100,
    ),
    "minimal": SubtitleStyle(
        label="Minimal",
        description="Capitales fines et espacées, sans animation.",
        font="Bebas Neue", font_size=76, outline=3, shadow=1, spacing=4,
        uppercase=True, animate=False, highlight_color="&H00FFFFFF",
        max_chars_per_line=30, max_words_per_line=6, margin_v=300,
    ),
}
DEFAULT_SUBTITLE_PRESET = "punch"
SUBTITLE_STYLE = SUBTITLE_PRESETS[DEFAULT_SUBTITLE_PRESET]


def subtitle_style(preset: str | None) -> SubtitleStyle:
    """Retourne le style de sous-titres correspondant au preset demandé."""
    return SUBTITLE_PRESETS.get(preset or "", SUBTITLE_PRESETS[DEFAULT_SUBTITLE_PRESET])


# --- Génération de script (API Claude) ------------------------------------
ANTHROPIC_MODEL = os.environ.get("FLAMBEE_ANTHROPIC_MODEL", "claude-sonnet-5")
ANTHROPIC_API_KEY_ENV = "ANTHROPIC_API_KEY"

SCRIPT_SYSTEM_PROMPT = (
    "Tu es spécialiste de l'écriture de scripts viraux pour vidéos courtes "
    "verticales de 30 à 60 secondes (TikTok, Reels, Shorts).\n"
    "Écris un script en français avec un hook très fort dans les 3 premières "
    "secondes.\n"
    "Structure imposée : accroche, développement, chute.\n"
    "Contraintes : phrases courtes, orales, rythmées ; pas d'emoji ; pas de "
    "didascalie ; pas de titres de section ; pas de mise en forme Markdown ; "
    "uniquement le texte qui sera lu par la voix off, prêt à être synthétisé."
)


@dataclass
class RenderSettings:
    """Réglages de l'étape 3 (style) transmis à l'assemblage."""

    voice: str = DEFAULT_VOICE
    voice_rate: str = "+0%"
    voice_pitch: str = "+0Hz"
    subtitles: bool = True
    music: str | None = None           # nom de fichier dans assets/music
    music_volume: float = 0.12         # volume relatif de la musique
    mask_source_subtitles: bool = False
    mask_mode: str = "blur"            # "blur" | "black"
    mask_height_ratio: float = 0.22    # part basse de l'image à masquer
    keep_source_audio: bool = False
    source_audio_volume: float = 0.05
    motion: bool = True                # léger travelling sur chaque plan
    scene_aware: bool = True           # caler les coupes sur les changements de plan
    subtitle_preset: str = "punch"
    extra: dict = field(default_factory=dict)
