"""Tests du site public : pages, tarifs, liste d'attente."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from flambee import app as app_module  # noqa: E402
from flambee import media, plans, site  # noqa: E402


@pytest.fixture()
def client(monkeypatch, tmp_path):
    monkeypatch.setattr(media, "ensure_tools", lambda: [])
    monkeypatch.setattr(app_module.media, "ensure_tools", lambda: [])
    monkeypatch.setattr(site.config, "WORK_DIR", tmp_path)
    with TestClient(app_module.app) as test_client:
        yield test_client


PAGES = ["/", "/fonctionnalites", "/tarifs", "/faq", "/connexion", "/inscription",
         "/mentions-legales", "/conditions", "/confidentialite"]


@pytest.mark.parametrize("chemin", PAGES)
def test_chaque_page_repond(client, chemin):
    reponse = client.get(chemin)
    assert reponse.status_code == 200
    assert "Flambée" in reponse.text
    # La navigation et le pied de page sont présents partout.
    assert 'href="/tarifs"' in reponse.text
    assert 'href="/mentions-legales"' in reponse.text


def test_latelier_reste_accessible(client):
    """Une route attrape-tout masquerait /studio : on vérifie qu'elle n'existe pas."""
    reponse = client.get("/studio")
    assert reponse.status_code == 200
    assert 'id="urls"' in reponse.text          # c'est bien l'outil
    assert client.get("/page-qui-nexiste-pas").status_code == 404


def test_les_tarifs_affichent_les_trois_formules(client):
    texte = client.get("/tarifs").text
    for plan in plans.PLANS:
        assert plan.name in texte
        assert plan.videos in texte
    # Les deux périodicités sont dans la page, la bascule se fait côté client.
    assert 'data-mensuel="19"' in texte and 'data-annuel="15"' in texte


def test_pages_legales_signalent_ce_qui_manque(client):
    """Publier des mentions inventées serait pire que de montrer les trous."""
    texte = client.get("/mentions-legales").text
    assert "à compléter" in texte
    assert "Document à compléter" in texte


def test_inscription_enregistre_une_adresse(client, tmp_path):
    reponse = client.post("/inscription",
                          data={"email": "Test@Exemple.FR", "formule": "createur",
                                "usage": "sport"})
    assert reponse.status_code == 200
    assert "C'est noté" in reponse.text

    inscrits = site.entries()
    assert len(inscrits) == 1
    assert inscrits[0]["email"] == "test@exemple.fr"      # normalisée
    assert inscrits[0]["formule"] == "createur"


def test_inscription_refuse_une_adresse_invalide(client):
    reponse = client.post("/inscription", data={"email": "pas-une-adresse"})
    assert reponse.status_code == 400
    assert "ne semble pas valide" in reponse.text
    assert site.entries() == []


def test_inscription_ne_duplique_pas(client):
    for _ in range(3):
        client.post("/inscription", data={"email": "a@b.fr", "formule": "essai"})
    assert len(site.entries()) == 1


def test_la_formule_choisie_preselectionne_le_menu(client):
    texte = client.get("/inscription?formule=studio").text
    assert '<option value="studio" selected>' in texte.replace("\n", " ")


def test_demo_accueil_est_une_video(client):
    if media.ensure_tools():
        pytest.skip("ffmpeg requis")
    reponse = client.get("/api/demo")
    assert reponse.status_code == 200
    assert reponse.headers["content-type"] == "video/mp4"
    assert len(reponse.content) > 5000
