"""Étape 5a — voix off via edge-tts (gratuit, voix Microsoft Edge).

edge-tts renvoie, en plus de l'audio, des évènements `WordBoundary` : on les
conserve pour caler les sous-titres animés au mot près, sans transcription.
"""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import asdict, dataclass
from pathlib import Path

from . import config
from .media import MediaError, probe

log = logging.getLogger(__name__)

# edge-tts exprime le temps en unités de 100 nanosecondes.
_TICKS_PER_SECOND = 10_000_000


class VoiceError(RuntimeError):
    """La synthèse vocale a échoué."""


@dataclass
class Word:
    text: str
    start: float
    end: float

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class VoiceTrack:
    path: str
    duration: float
    words: list[Word]
    voice: str

    def to_dict(self) -> dict:
        return {
            "path": self.path,
            "duration": self.duration,
            "voice": self.voice,
            "words": [w.to_dict() for w in self.words],
        }


def clean_script(text: str) -> str:
    """Nettoie le script avant synthèse (Markdown, didascalies, emojis)."""
    text = re.sub(r"[*_`#>]+", " ", text)
    text = re.sub(r"^\s*[-–•]\s*", "", text, flags=re.MULTILINE)
    text = re.sub(r"\[[^\]]*\]|\([^)]*:[^)]*\)", " ", text)   # [plan large], (ton: ...)
    text = re.sub(r"^\s*(hook|accroche|développement|developpement|chute|"
                  r"conclusion|intro|outro)\s*:\s*", "", text,
                  flags=re.MULTILINE | re.IGNORECASE)
    text = "".join(ch for ch in text if ch.isprintable() or ch in "\n\t")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{2,}", "\n", text)
    return text.strip()


async def list_voices(language: str = "fr") -> list[dict[str, str]]:
    """Liste les voix edge-tts disponibles pour une langue (fallback local)."""
    try:
        import edge_tts

        raw = await edge_tts.list_voices()
    except Exception as exc:  # pragma: no cover - dépend du réseau
        log.warning("Liste des voix indisponible (%s), fallback local.", exc)
        return config.FRENCH_VOICES

    voices = []
    for item in raw:
        locale = item.get("Locale", "")
        if not locale.lower().startswith(language.lower()):
            continue
        short = item.get("ShortName", "")
        gender = "féminine" if item.get("Gender") == "Female" else "masculine"
        name = short.split("-")[-1].replace("Neural", "").replace("Multilingual", "")
        voices.append({"id": short, "label": f"{name} ({locale}, {gender})"})
    voices.sort(key=lambda v: (not v["id"].startswith("fr-FR"), v["label"]))
    return voices or config.FRENCH_VOICES


async def synthesize_async(
    text: str,
    out_path: Path,
    *,
    voice: str = config.DEFAULT_VOICE,
    rate: str = "+0%",
    pitch: str = "+0Hz",
) -> VoiceTrack:
    """Génère la voix off et retourne le minutage mot à mot."""
    import edge_tts

    cleaned = clean_script(text)
    if not cleaned:
        raise VoiceError("Le script est vide.")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    communicate = edge_tts.Communicate(cleaned, voice, rate=rate, pitch=pitch)

    words: list[Word] = []
    audio_written = False
    try:
        with out_path.open("wb") as handle:
            async for chunk in communicate.stream():
                if chunk["type"] == "audio":
                    handle.write(chunk["data"])
                    audio_written = True
                elif chunk["type"] == "WordBoundary":
                    start = chunk["offset"] / _TICKS_PER_SECOND
                    end = start + chunk["duration"] / _TICKS_PER_SECOND
                    words.append(Word(text=chunk["text"], start=start, end=end))
    except Exception as exc:
        raise VoiceError(f"Synthèse vocale impossible : {exc}") from exc

    if not audio_written:
        raise VoiceError("edge-tts n'a renvoyé aucun audio (voix invalide ?).")

    try:
        duration = probe(out_path).duration
    except MediaError:
        duration = words[-1].end if words else 0.0

    if not words:
        words = _estimate_words(cleaned, duration)

    return VoiceTrack(path=str(out_path), duration=duration, words=words, voice=voice)


def synthesize(text: str, out_path: Path, **kwargs) -> VoiceTrack:
    """Version synchrone de `synthesize_async` (usage hors boucle asyncio)."""
    return asyncio.run(synthesize_async(text, out_path, **kwargs))


def _estimate_words(text: str, duration: float) -> list[Word]:
    """Minutage de secours quand edge-tts n'émet pas de WordBoundary."""
    tokens = [t for t in re.split(r"\s+", text) if t]
    if not tokens or duration <= 0:
        return []
    weights = [max(1, len(t)) for t in tokens]
    total = sum(weights)
    words: list[Word] = []
    cursor = 0.0
    for token, weight in zip(tokens, weights):
        span = duration * weight / total
        words.append(Word(text=token, start=cursor, end=cursor + span))
        cursor += span
    return words
