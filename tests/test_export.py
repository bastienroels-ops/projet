"""L'export du site public en pages statiques."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

RACINE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RACINE))
sys.path.insert(0, str(RACINE / "outils"))

import exporter_site  # noqa: E402


MEDIAS = {
    "/api/demo": "/media/demo.mp4",
    "/api/demo/poster": "/media/demo.jpg",
    "/api/presets/punch/sample": "/media/punch-sample.mp4",
    "/api/presets/punch/poster": "/media/punch-poster.jpg",
}


def test_les_medias_les_plus_longs_sont_remplaces_en_premier():
    """« /api/demo » est un préfixe de « /api/demo/poster » : traité d'abord,
    il laisserait une adresse bâtarde « /media/demo.mp4/poster »."""
    html = '<video poster="/api/demo/poster" src="/api/demo"></video>'
    rendu = exporter_site.reecrire(html, MEDIAS, "", "")
    assert 'poster="/media/demo.jpg"' in rendu
    assert 'src="/media/demo.mp4"' in rendu
    assert "/media/demo.mp4/poster" not in rendu


def test_les_clips_de_style_sont_recables():
    html = ('<video poster="/api/presets/punch/poster" '
            'data-src="/api/presets/punch/sample"></video>')
    rendu = exporter_site.reecrire(html, MEDIAS, "", "")
    assert "/api/presets" not in rendu
    assert "/media/punch-poster.jpg" in rendu and "/media/punch-sample.mp4" in rendu


def test_l_essayage_bascule_en_mode_fige():
    """Sans serveur, l'essayage libre est impossible : les puces doivent
    quand même faire défiler les six clips déjà calculés."""
    html = '<div class="essayage" id="essayage">…</div>'
    rendu = exporter_site.reecrire(html, MEDIAS, "", "")
    assert 'data-fige="1"' in rendu
    assert 'data-echantillons="/media"' in rendu


def test_les_liens_de_compte_pointent_vers_l_atelier():
    html = ('<a href="/inscription">Créer</a><a href="/connexion">Entrer</a>'
            '<a href="/inscription?formule=createur">Choisir</a>'
            '<a href="/tarifs">Tarifs</a>')
    rendu = exporter_site.reecrire(html, MEDIAS, "https://atelier.exemple.fr/", "")
    assert 'href="https://atelier.exemple.fr/inscription"' in rendu
    assert 'href="https://atelier.exemple.fr/connexion"' in rendu
    assert 'href="https://atelier.exemple.fr/inscription?formule=createur"' in rendu
    # Les pages qui existent en statique ne bougent pas.
    assert 'href="/tarifs"' in rendu


def test_sans_atelier_les_liens_restent_tels_quels():
    html = '<a href="/inscription">Créer</a>'
    assert 'href="/inscription"' in exporter_site.reecrire(html, MEDIAS, "", "")


def test_un_sous_chemin_prefixe_toutes_les_adresses():
    """Publié sous « /projet », un chemin absolu « /static/x » pointerait à
    la racine du domaine, où il n'y a rien."""
    html = ('<link href="/static/style.css"><a href="/tarifs">T</a>'
            '<video src="/api/demo"></video>')
    rendu = exporter_site.reecrire(html, MEDIAS, "", "/projet")
    assert 'href="/projet/static/style.css"' in rendu
    assert 'href="/projet/tarifs"' in rendu
    assert 'src="/projet/media/demo.mp4"' in rendu


def test_les_adresses_externes_ne_sont_pas_touchees():
    html = '<a href="https://exemple.fr/page">Dehors</a><a href="//cdn/x">Autre</a>'
    rendu = exporter_site.reecrire(html, MEDIAS, "", "/projet")
    assert 'href="https://exemple.fr/page"' in rendu
    assert 'href="//cdn/x"' in rendu


def test_toutes_les_pages_publiques_sont_exportees():
    """Une page oubliée ici disparaîtrait du site publié sans prévenir."""
    from flambee import auth

    ignorees = {"/connexion", "/inscription", "/deconnexion", "/reinitialiser",
                "/mot-de-passe-oublie", "/robots.txt", "/sitemap.xml",
                "/api/demo", "/api/demo/poster", "/api/health", "/api/essayage"}
    attendues = {a for a in auth.PUBLIC if a not in ignorees}
    assert attendues == set(exporter_site.PAGES), (
        "l'export et les routes publiques ont divergé")
