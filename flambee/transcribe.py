"""Transcription locale.

Deux usages :

- récupérer le texte d'une vidéo source, pour s'en inspirer ou le retravailler ;
- retrouver le minutage mot à mot d'une voix enregistrée par l'utilisateur, ce
  qu'`edge-tts` fournit gratuitement mais qu'un fichier importé n'a pas.

Tout se passe sur la machine : aucun envoi vers un service tiers. Le modèle est
téléchargé au premier usage puis conservé.

Deux implémentations du même modèle Whisper sont acceptées, essayées dans cet
ordre :

- **faster-whisper** (CTranslate2) — environ quatre fois plus rapide et sans
  PyTorch. C'est le moteur du conteneur et du serveur, déclaré dans
  `requirements-optional.txt`.
- **openai-whisper** (PyTorch) — plus lourd, mais PyTorch est déjà installé sur
  Google Colab, où ses binaires restent cohérents avec le reste de
  l'environnement. C'est le filet lorsque le premier refuse de se charger, ce
  qui arrive : les roues de CTranslate2 et d'onnxruntime entrent en conflit avec
  la version de numpy que Colab impose.

Une rubrique verrouillée par un moteur absent est une rubrique morte. Le module
sait donc aussi dire *pourquoi* il est indisponible, et l'installer lui-même.
"""

from __future__ import annotations

import importlib
import logging
import os
import subprocess
import sys
import threading
from dataclasses import dataclass, field
from pathlib import Path

from .media import MediaError
from .voice import Word

log = logging.getLogger(__name__)

# « tiny » est rapide mais approximatif, « small » précis mais lent sur un
# processeur modeste. « base » tient le milieu et suffit aux deux usages.
MODEL_NAME = os.environ.get("FLAMBEE_WHISPER_MODEL", "base")

# Nom du module importable → nom lisible, dans l'ordre de préférence.
MOTEURS: dict[str, str] = {
    "faster_whisper": "faster-whisper",
    "whisper": "openai-whisper",
}

_model = None
_moteur_charge: str = ""
_lock = threading.Lock()
_lock_installation = threading.Lock()


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


# --- Disponibilité ---------------------------------------------------------
_dernier_etat: str = ""


def _essayer_import(module: str) -> str | None:
    """None si l'import passe, sinon la raison. Ne laisse rien s'échapper."""
    try:
        importlib.import_module(module)
        return None
    except ModuleNotFoundError:
        return "absent"
    except Exception as exc:
        # Paquet présent mais hors d'état : conflit de version avec numpy,
        # bibliothèque partagée manquante… La page doit l'afficher, pas planter.
        return f"{type(exc).__name__} : {exc}"


def etat_moteurs() -> dict[str, str | None]:
    """Pour chaque moteur : None s'il fonctionne, sinon la raison de l'échec.

    L'échec n'est jamais mis en cache : une installation faite pendant que le
    serveur tourne prend effet sans le redémarrer. `invalidate_caches` est
    indispensable pour cela, Python gardant en mémoire le contenu des dossiers
    de `sys.path`.
    """
    global _dernier_etat

    etat = {module: _essayer_import(module) for module in MOTEURS}
    if all(raison is not None for raison in etat.values()):
        importlib.invalidate_caches()
        etat = {module: _essayer_import(module) for module in MOTEURS}

    resume = " | ".join(f"{MOTEURS[m]}: {r or 'ok'}" for m, r in etat.items())
    if resume != _dernier_etat:      # une ligne de journal par cause, pas par appel
        if any(raison is None for raison in etat.values()):
            log.info("Transcription — %s", resume)
        else:
            log.warning("Transcription indisponible — %s", resume)
        _dernier_etat = resume
    return etat


def moteur_disponible() -> str | None:
    """Le premier moteur utilisable, dans l'ordre de préférence."""
    etat = etat_moteurs()
    for module in MOTEURS:
        if etat.get(module) is None:
            return module
    return None


def raison_indisponible() -> str | None:
    """None si la transcription est utilisable, sinon la raison, en clair."""
    etat = etat_moteurs()
    if any(raison is None for raison in etat.values()):
        return None
    if all(raison == "absent" for raison in etat.values()):
        return "absent"
    # Au moins un moteur est installé mais refuse de se charger : c'est cette
    # raison-là qui intéresse, pas le « absent » de l'autre.
    casses = [f"{MOTEURS[m]} — {r}" for m, r in etat.items() if r != "absent"]
    return " ; ".join(casses)


def available() -> bool:
    """Un moteur de transcription est-il utilisable ?"""
    return moteur_disponible() is not None


def moteur_actif() -> str:
    """Nom lisible du moteur qui serait utilisé, ou chaîne vide."""
    module = moteur_disponible()
    return MOTEURS.get(module, "") if module else ""


# --- Installation ----------------------------------------------------------
def _strategies() -> list[tuple[str, list[str]]]:
    """Tentatives d'installation, de la plus légère à la plus lourde."""
    racine = Path(__file__).resolve().parent.parent
    optionnel = racine / "requirements-optional.txt"
    return [
        ("faster-whisper, depuis requirements-optional.txt",
         ["-r", str(optionnel)] if optionnel.exists() else ["faster-whisper"]),
        ("faster-whisper, binaires réinstallés",
         ["--upgrade", "--force-reinstall", "--no-cache-dir",
          "faster-whisper", "ctranslate2", "av"]),
        ("openai-whisper, qui s'appuie sur PyTorch",
         ["openai-whisper"]),
    ]


def installer(timeout: int = 1800) -> tuple[bool, str]:
    """Installe un moteur de transcription. Retourne (réussite, journal).

    On s'arrête au premier succès vérifié par un import réel : pip peut très
    bien réussir et le module rester inutilisable, c'est même le cas qui nous
    occupe ici.
    """
    with _lock_installation:
        if available():
            return True, f"Le moteur {moteur_actif()} est déjà en place."

        journal: list[str] = []
        for intitule, arguments in _strategies():
            commande = [sys.executable, "-m", "pip", "install",
                        "--disable-pip-version-check", "--no-input", *arguments]
            journal.append(f"$ pip install {' '.join(arguments)}")
            log.info("Installation du moteur de transcription — %s", intitule)
            try:
                resultat = subprocess.run(
                    commande, capture_output=True, text=True, timeout=timeout,
                )
            except subprocess.TimeoutExpired:
                journal.append(f"  ⏱ dépassement de {timeout} s")
                continue
            except Exception as exc:                     # pip introuvable…
                journal.append(f"  ✗ {type(exc).__name__} : {exc}")
                continue

            if resultat.returncode != 0:
                journal.append(f"  ✗ pip a renvoyé {resultat.returncode}")
                journal.extend(f"    {ligne}" for ligne
                               in _lignes_utiles(resultat.stderr or resultat.stdout))
                continue

            importlib.invalidate_caches()
            if available():
                journal.append(f"  ✓ {moteur_actif()} opérationnel")
                log.info("Moteur de transcription installé : %s", moteur_actif())
                return True, "\n".join(journal)

            journal.append("  ✗ installé, mais l'import échoue encore :")
            for module, raison in etat_moteurs().items():
                if raison and raison != "absent":
                    journal.append(f"    {MOTEURS[module]} — {raison}")

        log.error("Aucun moteur de transcription n'a pu être installé.")
        return False, "\n".join(journal)


def _lignes_utiles(sortie: str, maximum: int = 12) -> list[str]:
    """Les dernières lignes parlantes de pip, sans le bruit de progression."""
    lignes = [ligne.rstrip() for ligne in (sortie or "").splitlines()
              if ligne.strip() and not ligne.startswith(("  Downloading",
                                                         "  Using cached",
                                                         "Requirement already"))]
    return lignes[-maximum:]


# --- Modèle ----------------------------------------------------------------
def _load():
    """Charge le modèle une seule fois pour tout le processus."""
    global _model, _moteur_charge
    if _model is not None:
        return _model
    with _lock:
        if _model is not None:
            return _model

        module = moteur_disponible()
        if module is None:
            raise TranscriptionError(
                "Aucun moteur de transcription n'est utilisable : "
                f"{raison_indisponible()}."
            )

        log.info("Chargement du modèle « %s » avec %s…",
                 MODEL_NAME, MOTEURS[module])
        try:
            if module == "faster_whisper":
                from faster_whisper import WhisperModel

                modele = WhisperModel(MODEL_NAME, device="cpu",
                                      compute_type="int8")
            else:
                import whisper

                modele = whisper.load_model(MODEL_NAME)
        except Exception as exc:  # pragma: no cover - dépend du téléchargement
            raise TranscriptionError(
                f"Modèle « {MODEL_NAME} » indisponible : {exc}"
            ) from exc
        _model, _moteur_charge = modele, module
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
        if _moteur_charge == "faster_whisper":
            texte, mots, langue, duree = _avec_faster_whisper(
                modele, path, language, with_words)
        else:
            texte, mots, langue, duree = _avec_openai_whisper(
                modele, path, language, with_words)
    except TranscriptionError:
        raise
    except Exception as exc:
        raise TranscriptionError(f"Transcription impossible : {exc}") from exc

    return Transcription(
        text=_repunctuate(texte), words=mots,
        language=langue, duration=duree, model=MODEL_NAME,
    )


def _avec_faster_whisper(modele, path: Path, language: str | None,
                         with_words: bool) -> tuple[str, list[Word], str, float]:
    segments, info = modele.transcribe(
        str(path), language=language, word_timestamps=with_words,
        vad_filter=True,
    )
    segments = list(segments)

    mots: list[Word] = []
    if with_words:
        for segment in segments:
            for mot in segment.words or []:
                texte = mot.word.strip()
                if texte:
                    mots.append(Word(text=texte, start=float(mot.start),
                                     end=float(mot.end)))

    texte = " ".join(segment.text.strip() for segment in segments).strip()
    return (texte, mots,
            getattr(info, "language", "") or "",
            float(getattr(info, "duration", 0.0) or 0.0))


_PONCTUATION = frozenset(",.;:!?…»)]\u201d\u2019")


def _avec_openai_whisper(modele, path: Path, language: str | None,
                         with_words: bool) -> tuple[str, list[Word], str, float]:
    resultat = modele.transcribe(str(path), language=language,
                                 word_timestamps=with_words, verbose=False)
    segments = resultat.get("segments") or []

    mots: list[Word] = []
    if with_words:
        for segment in segments:
            for mot in segment.get("words") or []:
                texte = str(mot.get("word", "")).strip()
                if not texte:
                    continue
                # Ce moteur détache la ponctuation : « Bonjour » puis « ! »,
                # chacun avec son minutage. Telle quelle, elle deviendrait un
                # sous-titre à elle seule, surlignée comme un mot. On la
                # recolle au mot précédent, comme le fait faster-whisper.
                if texte in _PONCTUATION and mots:
                    mots[-1] = Word(text=mots[-1].text + texte,
                                    start=mots[-1].start,
                                    end=float(mot["end"]))
                    continue
                mots.append(Word(text=texte, start=float(mot["start"]),
                                 end=float(mot["end"])))

    # Ce moteur ne renvoie pas la durée : la fin du dernier segment en tient lieu.
    duree = float(segments[-1].get("end", 0.0)) if segments else 0.0
    return (str(resultat.get("text", "")).strip(), mots,
            str(resultat.get("language", "") or ""), duree)


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
