"""Tests du site public : pages, tarifs, liste d'attente."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from flambee import app as app_module  # noqa: E402
from flambee import media, plans, users  # noqa: E402


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


def test_latelier_reste_accessible(compte):
    """Une route attrape-tout masquerait /studio : on vérifie qu'elle n'existe pas."""
    reponse = compte.get("/studio")
    assert reponse.status_code == 200
    assert 'id="urls"' in reponse.text          # c'est bien l'outil
    assert compte.get("/page-qui-nexiste-pas").status_code == 404


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


def test_inscription_cree_un_compte(client):
    reponse = client.post("/inscription",
                          data={"email": "Nouveau@Exemple.FR",
                                "mot_de_passe": "motdepasse1", "nom": "Nouveau"},
                          follow_redirects=False)
    assert reponse.status_code == 303
    assert reponse.headers["location"] == "/studio"

    compte = users.par_email("nouveau@exemple.fr")
    assert compte is not None
    assert compte.email == "nouveau@exemple.fr"      # normalisée
    assert compte.nom == "Nouveau"


def test_inscription_refuse_une_adresse_invalide(client):
    reponse = client.post("/inscription",
                          data={"email": "pas-une-adresse",
                                "mot_de_passe": "motdepasse1"})
    assert reponse.status_code == 400
    assert "ne semble pas valide" in reponse.text
    assert users.compter() == 0


def test_la_formule_choisie_est_appliquee(client):
    """Le bouton d'une formule mène à l'inscription et la formule suit."""
    assert 'href="/inscription?formule=createur"' in client.get("/tarifs").text

    client.post("/inscription",
                data={"email": "c@exemple.fr", "mot_de_passe": "motdepasse1",
                      "formule": "createur"}, follow_redirects=False)
    assert users.par_email("c@exemple.fr").plan == "createur"


def test_demo_accueil_est_une_video(client):
    if media.ensure_tools():
        pytest.skip("ffmpeg requis")
    reponse = client.get("/api/demo")
    assert reponse.status_code == 200
    assert reponse.headers["content-type"] == "video/mp4"
    assert len(reponse.content) > 5000
