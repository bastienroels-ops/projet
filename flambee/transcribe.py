"""Transcription locale (faster-whisper).

Deux usages :

- récupérer le texte d'une vidéo source, pour s'en inspirer ou le retravailler ;
- retrouver le minutage mot à mot d'une voix enregistrée par l'utilisateur, ce
  qu'`edge-tts` fournit gratuitement mais qu'un fichier importé n'a pas.

Tout se passe sur la machine : aucun envoi vers un service tiers. Le modèle est
téléchargé au premier usage puis conservé.
"""

from __future__ import annotations

import logging
import os
import threading
from dataclasses import dataclass, field
from pathlib import Path

from .media import MediaError
from .voice import Word

log = logging.getLogger(__name__)

# « tiny » est rapide mais approximatif, « small » précis mais lent sur un
# processeur modeste. « base » tient le milieu et suffit aux deux usages.
MODEL_NAME = os.environ.get("FLAMBEE_WHISPER_MODEL", "base")

_model = None
_lock = threading.Lock()


class TranscriptionError(RuntimeError):
    """La transcription a échoué."""


@dataclass
class Transcription:
    text: str
    words: list[Word] = field(default_factory=list)
    language: str = ""
    duration: float = 0.0
    model: str = ""

    def to_dict(self) -> dict:
        return {
            "text": self.text,
            "language": self.language,
            "duration": self.duration,
            "model": self.model,
            "words": [w.to_dict() for w in self.words],
        }


def available() -> bool:
    """faster-whisper est-il installé ?"""
    try:
        import faster_whisper  # noqa: F401

        return True
    except ImportError:
        return False


def _load():
    """Charge le modèle une seule fois pour tout le processus."""
    global _model
    if _model is not None:
        return _model
    with _lock:
        if _model is not None:
            return _model
        try:
            from faster_whisper import WhisperModel
        except ImportError as exc:
            raise TranscriptionError(
                "La transcription nécessite faster-whisper : "
                "`pip install -r requirements-optional.txt`."
            ) from exc

        log.info("Chargement du modèle de transcription « %s »…", MODEL_NAME)
        try:
            _model = WhisperModel(MODEL_NAME, device="cpu", compute_type="int8")
        except Exception as exc:  # pragma: no cover - dépend du téléchargement
            raise TranscriptionError(
                f"Modèle « {MODEL_NAME} » indisponible : {exc}"
            ) from exc
    return _model


def transcribe(
    path: str | Path,
    *,
    language: str | None = "fr",
    with_words: bool = True,
) -> Transcription:
    """Transcrit un fichier audio ou vidéo."""
    path = Path(path)
    if not path.exists():
        raise TranscriptionError(f"Fichier introuvable : {path.name}")

    modele = _load()
    try:
        segments, info = modele.transcribe(
            str(path), language=language, word_timestamps=with_words,
            vad_filter=True,
        )
        segments = list(segments)
    except Exception as exc:
        raise TranscriptionError(f"Transcription impossible : {exc}") from exc

    mots: list[Word] = []
    if with_words:
        for segment in segments:
            for mot in segment.words or []:
                texte = mot.word.strip()
                if texte:
                    mots.append(Word(text=texte, start=float(mot.start),
                                     end=float(mot.end)))

    texte = " ".join(segment.text.strip() for segment in segments).strip()
    return Transcription(
        text=_repunctuate(texte),
        words=mots,
        language=getattr(info, "language", "") or "",
        duration=float(getattr(info, "duration", 0.0) or 0.0),
        model=MODEL_NAME,
    )


def _repunctuate(texte: str) -> str:
    """Nettoie les espaces avant la ponctuation, fréquents en sortie de modèle."""
    import re

    texte = re.sub(r"\s+([,.;:!?…])", r"\1", texte)
    texte = re.sub(r"\s{2,}", " ", texte)
    return texte.strip()


def words_for_audio(path: str | Path, *, language: str | None = "fr") -> list[Word]:
    """Minutage mot à mot d'une voix enregistrée, pour caler les sous-titres."""
    resultat = transcribe(path, language=language, with_words=True)
    if not resultat.words:
        raise TranscriptionError(
            "Aucun mot détecté : le fichier est-il bien une voix parlée ?"
        )
    return resultat.words


def ensure_audio(source: Path, destination: Path) -> Path:
    """Extrait une piste audio exploitable (mono 16 kHz) d'un fichier quelconque."""
    from .media import ffmpeg, has_media_duration

    destination.parent.mkdir(parents=True, exist_ok=True)
    ffmpeg([
        "-i", str(source), "-vn", "-ac", "1", "-ar", "16000",
        "-c:a", "pcm_s16le", str(destination),
    ], timeout=900)
    if not has_media_duration(destination, minimum=0.2):
        raise MediaError("Aucune piste audio exploitable dans ce fichier.")
    return destination
