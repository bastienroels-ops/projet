"""Moteurs de transcription : sélection, diagnostic, adaptateurs."""

from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from flambee import transcribe  # noqa: E402
from flambee.voice import Word  # noqa: E402


# --- Diagnostic ------------------------------------------------------------
def test_un_moteur_absent_et_un_moteur_casse_ne_disent_pas_la_meme_chose(monkeypatch):
    """« absent » envoie installer ; « cassé » envoie réparer. Les confondre
    fait chercher une installation déjà faite."""
    monkeypatch.setattr(transcribe, "_dernier_etat", "")

    monkeypatch.setattr(transcribe, "_essayer_import", lambda module: "absent")
    assert transcribe.raison_indisponible() == "absent"
    assert not transcribe.available()

    def casse(module):
        return ("ImportError : numpy.core.multiarray failed to import"
                if module == "faster_whisper" else "absent")

    monkeypatch.setattr(transcribe, "_essayer_import", casse)
    raison = transcribe.raison_indisponible()
    assert raison != "absent"
    assert "numpy" in raison
    assert "faster-whisper" in raison


def test_une_erreur_qui_n_est_pas_un_import_ne_fait_pas_planter_la_page(monkeypatch):
    """Une bibliothèque partagée manquante lève OSError, pas ImportError."""
    import importlib

    def importer(module):
        raise OSError("libcudnn.so.8: cannot open shared object file")

    monkeypatch.setattr(importlib, "import_module", importer)
    monkeypatch.setattr(transcribe, "_dernier_etat", "")
    assert transcribe._essayer_import("faster_whisper").startswith("OSError")
    assert transcribe.available() is False


def test_le_moteur_prefere_est_le_plus_rapide(monkeypatch):
    monkeypatch.setattr(transcribe, "_dernier_etat", "")
    monkeypatch.setattr(transcribe, "_essayer_import", lambda module: None)
    assert transcribe.moteur_disponible() == "faster_whisper"
    assert transcribe.moteur_actif() == "faster-whisper"

    monkeypatch.setattr(
        transcribe, "_essayer_import",
        lambda module: "absent" if module == "faster_whisper" else None)
    assert transcribe.moteur_disponible() == "whisper"
    assert transcribe.moteur_actif() == "openai-whisper"


def test_l_installation_ne_se_relance_pas_si_un_moteur_repond(monkeypatch):
    monkeypatch.setattr(transcribe, "available", lambda: True)
    monkeypatch.setattr(transcribe, "moteur_actif", lambda: "faster-whisper")
    ok, journal = transcribe.installer()
    assert ok and "déjà en place" in journal


# --- Adaptateur du moteur de secours --------------------------------------
class _ModeleFactice:
    """Rend la structure que produit openai-whisper, sans le charger."""

    def __init__(self, resultat):
        self.resultat = resultat

    def transcribe(self, chemin, **kwargs):
        return self.resultat


def _resultat(mots):
    return {
        "text": " ".join(m["word"].strip() for m in mots),
        "language": "fr",
        "segments": [{"end": mots[-1]["end"], "words": mots}],
    }


def test_la_ponctuation_ne_devient_pas_un_sous_titre_a_elle_seule():
    """Ce moteur détache « ! » du mot. Tel quel, il serait surligné seul."""
    modele = _ModeleFactice(_resultat([
        {"word": " Bonjour", "start": 0.0, "end": 0.42},
        {"word": "!", "start": 0.42, "end": 0.80},
        {"word": " Ceci", "start": 0.80, "end": 1.22},
        {"word": " va", "start": 1.22, "end": 1.40},
        {"word": ".", "start": 1.40, "end": 1.55},
    ]))
    texte, mots, langue, duree = transcribe._avec_openai_whisper(
        modele, Path("x.wav"), "fr", True)

    assert [m.text for m in mots] == ["Bonjour!", "Ceci", "va."]
    # La ponctuation rallonge le mot auquel elle se rattache, sans trou.
    assert mots[0].start == 0.0 and mots[0].end == 0.80
    assert mots[2].end == 1.55
    assert langue == "fr" and duree == 1.55
    assert all(isinstance(m, Word) for m in mots)


def test_l_adaptateur_supporte_une_sortie_sans_mots():
    modele = _ModeleFactice({"text": "Bonjour", "language": "fr", "segments": []})
    texte, mots, langue, duree = transcribe._avec_openai_whisper(
        modele, Path("x.wav"), "fr", True)
    assert texte == "Bonjour" and mots == [] and duree == 0.0


def test_une_ponctuation_en_tete_ne_fait_pas_planter():
    """Sans mot précédent, il n'y a rien à quoi la rattacher."""
    modele = _ModeleFactice(_resultat([
        {"word": "!", "start": 0.0, "end": 0.2},
        {"word": " Bonjour", "start": 0.2, "end": 0.6},
    ]))
    _, mots, _, _ = transcribe._avec_openai_whisper(
        modele, Path("x.wav"), "fr", True)
    assert [m.text for m in mots] == ["!", "Bonjour"]


# --- Installation depuis l'interface --------------------------------------
def _proprietaire(client):
    client.post("/inscription", data={"email": "patron@exemple.fr",
                                      "mot_de_passe": "motdepasse1", "nom": "P"},
                follow_redirects=False)
    return client


def _sans_moteur(monkeypatch):
    from flambee import app as app_module

    monkeypatch.setattr(app_module.transcribe, "available", lambda: False)
    monkeypatch.setattr(app_module.transcribe, "raison_indisponible",
                        lambda: "absent")
    monkeypatch.setattr(app_module.transcribe, "moteur_actif", lambda: "")
    monkeypatch.setattr(app_module, "_installation",
                        {"etat": "repos", "ok": False, "journal": ""})


def test_l_administrateur_peut_installer_le_moteur_depuis_la_page(
        compte_pro, monkeypatch):
    from flambee import app as app_module

    _sans_moteur(monkeypatch)
    page = compte_pro.get("/studio/script-viral").text
    assert "Installer le moteur" in page

    appels = []
    monkeypatch.setattr(app_module.transcribe, "installer",
                        lambda *a, **k: (appels.append(1), (True, "ok"))[1])

    reponse = compte_pro.post("/studio/moteur?suite=/studio/voix",
                              follow_redirects=False)
    assert reponse.status_code == 303
    assert reponse.headers["location"] == "/studio/voix"

    for _ in range(200):                      # le fil d'installation est bref
        if app_module._installation["etat"] == "fini":
            break
        time.sleep(0.01)
    assert app_module._installation == {"etat": "fini", "ok": True, "journal": "ok"}
    assert appels == [1]


def test_l_installation_est_refusee_a_un_autre_compte(compte_pro, monkeypatch):
    """Lancer pip est une action d'administration, pas un service client."""
    _sans_moteur(monkeypatch)
    compte_pro.get("/deconnexion")
    compte_pro.post("/inscription", data={"email": "client@exemple.fr",
                                          "mot_de_passe": "motdepasse1"},
                    follow_redirects=False)
    compte_pro.post("/studio/abonnement", data={"plan": "createur"},
                    follow_redirects=False)

    assert compte_pro.post("/studio/moteur",
                           follow_redirects=False).status_code == 403
    # …et le bouton ne lui est même pas proposé.
    assert "Installer le moteur" not in compte_pro.get("/studio/script-viral").text


def test_l_installation_peut_etre_interdite_partout(compte_pro, monkeypatch):
    _sans_moteur(monkeypatch)
    monkeypatch.setenv("FLAMBEE_INSTALL_MOTEUR", "0")
    assert compte_pro.post("/studio/moteur",
                           follow_redirects=False).status_code == 403
    assert "Installer le moteur" not in compte_pro.get("/studio/script-viral").text


def test_un_moteur_casse_propose_de_reparer_et_montre_la_cause(compte_pro,
                                                               monkeypatch):
    from flambee import app as app_module

    _sans_moteur(monkeypatch)
    monkeypatch.setattr(app_module.transcribe, "raison_indisponible",
                        lambda: "faster-whisper — ImportError : numpy.core...")
    page = compte_pro.get("/studio/voix").text
    assert "hors d'état" in page
    assert "numpy.core" in page              # la cause est affichée, pas masquée
    assert "Réparer l'installation" in page
