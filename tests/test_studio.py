"""Tests de l'application connectée : coquille, rubriques, crédits, voix."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from flambee import account, app as app_module, media, plans, voicestudio  # noqa: E402


@pytest.fixture()
def client(monkeypatch, tmp_path):
    monkeypatch.setattr(media, "ensure_tools", lambda: [])
    monkeypatch.setattr(app_module.media, "ensure_tools", lambda: [])
    monkeypatch.setattr(account.config, "WORK_DIR", tmp_path)
    monkeypatch.setattr(voicestudio.config, "WORK_DIR", tmp_path)
    with TestClient(app_module.app) as test_client:
        yield test_client


RUBRIQUES = ["/studio", "/studio/creations", "/studio/script-viral", "/studio/voix",
             "/studio/tutoriel", "/studio/communaute", "/studio/abonnement",
             "/studio/credits", "/studio/parametres", "/studio/profil"]


@pytest.mark.parametrize("chemin", RUBRIQUES)
def test_chaque_rubrique_repond(client, chemin):
    reponse = client.get(chemin)
    assert reponse.status_code == 200
    # Le menu latéral est présent sur toutes les pages.
    assert 'href="/studio/abonnement"' in reponse.text
    assert 'class="entree creer' in reponse.text


def test_latelier_garde_tous_ses_rouages(client):
    """L'outil a changé de gabarit : ses identifiants doivent survivre."""
    texte = client.get("/studio").text
    for identifiant in ("urls", "btn-download", "hooks", "subtitle_preset",
                        "script", "btn-render", "result-video", "preset-video"):
        assert f'id="{identifiant}"' in texte, identifiant
    assert "/static/app.js" in texte


def test_la_rubrique_active_est_signalee(client):
    assert 'class="entree active" href="/studio/credits"' in \
        client.get("/studio/credits").text.replace("\n", " ")


# --- Crédits et formules ---------------------------------------------------
def test_le_quota_depend_de_la_formule(client):
    assert "3" in client.get("/studio/credits").text          # essai : 3 rendus

    reponse = client.post("/studio/abonnement", data={"plan": "studio"},
                          follow_redirects=True)
    assert reponse.status_code == 200
    assert account.charger().plan == "studio"
    assert account.charger().quota is None                    # illimité


def test_un_rendu_consomme_un_credit(client):
    depart = account.charger().restants()
    account.noter("rendu", "video.mp4")
    assert account.charger().restants() == depart - 1
    assert "Vidéo rendue" in client.get("/studio/credits").text


def test_rendu_refuse_sans_credit(client, monkeypatch):
    """Les aperçus restent ouverts, le rendu définitif non."""
    for _ in range(3):
        account.noter("rendu", "x")
    assert account.charger().peut_rendre() is False

    projet = client.post("/api/projects").json()
    reponse = client.post(f"/api/projects/{projet['id']}/render", json={"fast": False})
    assert reponse.status_code == 402
    assert "Crédits épuisés" in reponse.json()["detail"]


def test_les_apercus_ne_consomment_rien(client, monkeypatch):
    for _ in range(5):
        account.noter("apercu", "x")
    assert account.charger().restants() == 3


# --- Rubriques réservées ---------------------------------------------------
def test_script_viral_verrouille_en_essai(client):
    texte = client.get("/studio/script-viral").text
    assert "Disponible à partir de" in texte
    assert "PRO" in texte

    reponse = client.post("/studio/script-viral", data={"url": "https://a.test/1"})
    assert reponse.status_code == 402


def test_script_viral_ouvert_en_createur(client):
    account.changer_de_formule("createur")
    texte = client.get("/studio/script-viral").text
    assert "Disponible à partir de" not in texte


def test_voice_studio_refuse_un_format_inconnu(client):
    account.changer_de_formule("createur")
    reponse = client.post("/studio/voix",
                          files={"fichier": ("note.txt", b"abc", "text/plain")})
    assert reponse.status_code == 400
    assert "Format non reconnu" in reponse.text


def test_profil_enregistre_les_informations(client):
    client.post("/studio/profil", data={"nom": "Bastien", "email": "b@exemple.fr"},
                follow_redirects=True)
    compte = account.charger()
    assert compte.nom == "Bastien" and compte.email == "b@exemple.fr"
    assert compte.initiales == "BA"


# --- Voix importée ---------------------------------------------------------
def test_la_voix_importee_apparait_dans_la_liste(client, monkeypatch):
    assert all(v["id"] != voicestudio.VOICE_ID
               for v in client.get("/api/voices").json()["voices"])

    monkeypatch.setattr(voicestudio, "charger", lambda: {"nom": "moi.m4a", "mots": []})
    voix = client.get("/api/voices").json()["voices"]
    assert voix[0]["id"] == voicestudio.VOICE_ID     # proposée en premier


def test_fichier_de_voix_absent(client):
    assert client.get("/studio/voix/fichier").status_code == 404
