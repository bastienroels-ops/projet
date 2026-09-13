"""Tests de l'application connectée : coquille, rubriques, crédits, voix."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from flambee import account, app as app_module, media, plans, users  # noqa: E402
from flambee import voicestudio  # noqa: E402


RUBRIQUES = ["/studio", "/studio/creations", "/studio/script-viral", "/studio/voix",
             "/studio/tutoriel", "/studio/communaute", "/studio/abonnement",
             "/studio/credits", "/studio/parametres", "/studio/profil"]


@pytest.mark.parametrize("chemin", RUBRIQUES)
def test_chaque_rubrique_repond(compte, chemin):
    reponse = compte.get(chemin)
    assert reponse.status_code == 200
    # Le menu latéral est présent sur toutes les pages.
    assert 'href="/studio/abonnement"' in reponse.text
    assert 'class="entree creer' in reponse.text


def test_latelier_garde_tous_ses_rouages(compte):
    """L'outil a changé de gabarit : ses identifiants doivent survivre."""
    texte = compte.get("/studio").text
    for identifiant in ("urls", "btn-download", "hooks", "subtitle_preset",
                        "script", "btn-render", "result-video", "preset-video"):
        assert f'id="{identifiant}"' in texte, identifiant
    assert "/static/app.js" in texte


def test_la_rubrique_active_est_signalee(compte):
    assert 'class="entree active" href="/studio/credits"' in \
        compte.get("/studio/credits").text.replace("\n", " ")


# --- Crédits et formules ---------------------------------------------------
def test_le_quota_depend_de_la_formule(compte):
    assert "3" in compte.get("/studio/credits").text          # essai : 3 rendus

    reponse = compte.post("/studio/abonnement", data={"plan": "studio"},
                          follow_redirects=True)
    assert reponse.status_code == 200
    utilisateur = users.par_email("essai@exemple.fr")
    assert utilisateur.plan == "studio"
    assert account.quota(utilisateur) is None                 # illimité


def test_un_rendu_consomme_un_credit(compte):
    utilisateur = users.par_email("essai@exemple.fr")
    depart = account.restants(utilisateur)
    account.noter(utilisateur, "rendu", "video.mp4")
    assert account.restants(users.par_email("essai@exemple.fr")) == depart - 1
    assert "Vidéo rendue" in compte.get("/studio/credits").text


def test_rendu_refuse_sans_credit(compte, monkeypatch):
    """Les aperçus restent ouverts, le rendu définitif non."""
    utilisateur = users.par_email("essai@exemple.fr")
    for _ in range(3):
        account.noter(utilisateur, "rendu", "x")
    assert account.peut_rendre(users.par_email("essai@exemple.fr")) is False

    projet = compte.post("/api/projects").json()
    reponse = compte.post(f"/api/projects/{projet['id']}/render", json={"fast": False})
    assert reponse.status_code == 402
    assert "Crédits épuisés" in reponse.json()["detail"]


def test_les_apercus_ne_consomment_rien(compte, monkeypatch):
    utilisateur = users.par_email("essai@exemple.fr")
    for _ in range(5):
        account.noter(utilisateur, "apercu", "x")
    assert account.restants(users.par_email("essai@exemple.fr")) == 3


# --- Rubriques réservées ---------------------------------------------------
def test_script_viral_verrouille_en_essai(compte):
    texte = compte.get("/studio/script-viral").text
    assert "Disponible à partir de" in texte
    assert "PRO" in texte

    reponse = compte.post("/studio/script-viral", data={"url": "https://a.test/1"})
    assert reponse.status_code == 402


def test_script_viral_ouvert_en_createur(compte_pro):
    texte = compte_pro.get("/studio/script-viral").text
    assert "Disponible à partir de" not in texte


def test_voice_studio_refuse_un_format_inconnu(compte_pro):
    reponse = compte_pro.post("/studio/voix",
                              files={"fichier": ("note.txt", b"abc", "text/plain")})
    assert reponse.status_code == 400
    assert "Format non reconnu" in reponse.text


def test_profil_enregistre_le_nom(compte):
    compte.post("/studio/profil", data={"nom": "Bastien"}, follow_redirects=True)
    utilisateur = users.par_email("essai@exemple.fr")
    assert utilisateur.nom == "Bastien"
    assert utilisateur.initiales == "BA"


# --- Voix importée ---------------------------------------------------------
def test_la_voix_importee_apparait_dans_la_liste(compte, monkeypatch):
    assert all(v["id"] != voicestudio.VOICE_ID
               for v in compte.get("/api/voices").json()["voices"])

    monkeypatch.setattr(voicestudio, "charger",
                        lambda owner=0: {"nom": "moi.m4a", "mots": []})
    voix = compte.get("/api/voices").json()["voices"]
    assert voix[0]["id"] == voicestudio.VOICE_ID     # proposée en premier


def test_fichier_de_voix_absent(compte):
    assert compte.get("/studio/voix/fichier").status_code == 404
