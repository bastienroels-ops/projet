"""Configuration globale de Flambée.

Tout est local : aucun compte, aucune base de données. Les chemins sont
relatifs à la racine du projet, surchargeables par variables d'environnement.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

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
@dataclass
class SubtitleStyle:
    font: str = "Arial Black"
    font_size: int = 84
    primary_color: str = "&H00FFFFFF"      # blanc (ABGR)
    highlight_color: str = "&H0000E5FF"    # jaune/orange sur le mot actif
    outline_color: str = "&H00000000"      # contour noir
    outline: int = 6
    shadow: int = 2
    margin_v: int = 420                    # remonte le texte au-dessus du bas
    max_chars_per_line: int = 22
    max_words_per_line: int = 4
    animate: bool = True                   # effet "pop" sur chaque groupe


SUBTITLE_STYLE = SubtitleStyle()


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
    extra: dict = field(default_factory=dict)
