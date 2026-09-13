"""Voice Studio — utiliser sa propre voix à la place de la synthèse.

Un enregistrement importé n'apporte aucun minutage : sans lui, impossible de
caler les sous-titres. On le retrouve par transcription locale, puis la piste
se comporte exactement comme une voix `edge-tts` dans la suite du montage.
"""

from __future__ import annotations

import json
import logging
import shutil
import threading
from pathlib import Path

from . import config, transcribe
from .media import MediaError, probe
from .voice import VoiceTrack, Word, measure_lead_in

log = logging.getLogger(__name__)

VOICE_ID = "importee"      # identifiant de la voix dans la liste des voix
EXTENSIONS = {".mp3", ".m4a", ".wav", ".aac", ".ogg", ".flac", ".mp4", ".mov", ".webm"}
MAX_BYTES = 80 * 1024 * 1024
_LOCK = threading.Lock()


def dossier() -> Path:
    chemin = config.WORK_DIR / "voix"
    chemin.mkdir(parents=True, exist_ok=True)
    return chemin


def chemin_audio() -> Path:
    return dossier() / "voix.wav"


def chemin_infos() -> Path:
    return dossier() / "voix.json"


def enregistrer(source: Path, nom_origine: str) -> dict:
    """Analyse un enregistrement et le retient comme voix de l'utilisateur."""
    with _LOCK:
        audio = transcribe.ensure_audio(source, chemin_audio())
        try:
            mots = transcribe.words_for_audio(audio)
        except transcribe.TranscriptionError:
            audio.unlink(missing_ok=True)
            raise

        infos = {
            "nom": nom_origine[:120],
            "duree": round(probe(audio).duration, 2),
            "mots": [mot.to_dict() for mot in mots],
            "texte": " ".join(mot.text for mot in mots),
        }
        chemin_infos().write_text(
            json.dumps(infos, ensure_ascii=False), encoding="utf-8"
        )
        log.info("Voix importée : %s (%s mots)", nom_origine, len(mots))
        return infos


def charger() -> dict | None:
    """Infos de la voix enregistrée, ou None."""
    if not chemin_audio().exists() or not chemin_infos().exists():
        return None
    try:
        return json.loads(chemin_infos().read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


def supprimer() -> None:
    with _LOCK:
        shutil.rmtree(dossier(), ignore_errors=True)


def piste() -> VoiceTrack:
    """Retourne la voix importée sous la forme attendue par le montage."""
    infos = charger()
    if not infos:
        raise MediaError("Aucune voix importée.")
    audio = chemin_audio()
    return VoiceTrack(
        path=str(audio),
        duration=float(infos.get("duree") or probe(audio).duration),
        words=[Word(**mot) for mot in infos.get("mots", [])],
        voice="importée",
        # Le minutage vient de la transcription, donc déjà aligné sur l'audio :
        # aucun décalage à corriger, contrairement à edge-tts.
        lead_in=0.0,
    )
