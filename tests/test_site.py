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


def test_la_navigation_change_selon_la_connexion(client):
    """Un visiteur voit « Créer un compte » ; un membre voit son nom."""
    visiteur = client.get("/").text
    assert 'href="/inscription" class="button primary petit"' in visiteur
    assert "Connexion" in visiteur

    client.post("/inscription",
                data={"email": "nav@exemple.fr", "mot_de_passe": "motdepasse1",
                      "nom": "Bastien"}, follow_redirects=False)
    membre = client.get("/").text
    assert "Bastien" in membre
    assert 'href="/studio" class="button primary petit"' in membre


def test_affiche_de_la_demonstration(client):
    if media.ensure_tools():
        pytest.skip("ffmpeg requis")
    reponse = client.get("/api/demo/poster")
    assert reponse.status_code == 200
    assert reponse.headers["content-type"] == "image/jpeg"
    assert len(reponse.content) > 1000


def test_robots_ferme_latelier_aux_moteurs(client):
    texte = client.get("/robots.txt").text
    assert "Disallow: /studio" in texte
    assert "Disallow: /api/" in texte
    assert "Sitemap:" in texte


def test_sitemap_liste_les_pages_publiques(client):
    reponse = client.get("/sitemap.xml")
    assert reponse.status_code == 200
    assert "application/xml" in reponse.headers["content-type"]
    for page in ("/tarifs", "/fonctionnalites", "/inscription"):
        assert f"<loc>http://testserver{page}</loc>" in reponse.text


def test_page_introuvable_reste_dans_lidentite(client):
    reponse = client.get("/une-page-qui-nexiste-pas")
    assert reponse.status_code == 404
    assert "Erreur 404" in reponse.text          # l'apostrophe est échappée
    assert "existe pas" in reponse.text
    assert 'href="/tarifs"' in reponse.text          # la navigation est là

    # Côté API, on garde du JSON.
    api = client.get("/api/inconnu")
    assert api.status_code == 404
    assert api.json()["detail"]


def test_apercu_de_partage(client):
    texte = client.get("/").text
    assert '<meta property="og:image" content="http://testserver/api/demo/poster">' in texte
    assert '<meta property="og:title"' in texte
    assert '<link rel="canonical" href="http://testserver/">' in texte
