"""L'export du site public en pages statiques."""

from __future__ import annotations

import html as html_module
import re
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


def test_sans_atelier_les_boutons_menent_a_l_essayage():
    """Publiée seule, la vitrine n'a pas de page d'inscription : un bouton qui
    mène à un 404 est pire qu'un bouton qui montre le produit."""
    html = '<a href="/inscription" class="button primary">Créer un compte</a>'
    rendu = exporter_site.reecrire(html, MEDIAS, "", "")
    assert rendu.count('href="/#essayage"') == 1
    assert "Voir la démonstration" in rendu
    assert "/inscription" not in rendu
    # Les classes sont conservées : la vedette reste la vedette.
    assert 'class="button primary"' in rendu


def test_sans_atelier_les_formules_perdent_leur_bouton():
    """Trois boutons identiques sous trois prix feraient croire à un choix.

    Le bouton d'une formule invite à la prendre. Aucune n'étant ouverte, il ne
    se remplace pas : la carte se contente de décrire ce que la formule
    contiendra, et l'appel unique vit ailleurs sur la page.
    """
    rendu = exporter_site.reecrire(
        '<article class="tarif"><a href="/inscription?formule=createur"'
        ' class="button primary">Choisir</a></article>', MEDIAS, "", "")
    assert rendu == '<article class="tarif"></article>' 


def test_sans_atelier_les_liens_de_navigation_disparaissent():
    """« Connexion » dans un menu, sans rien derrière, n'aide personne.

    Un bouton se remplace — il y a une démonstration à montrer à la place.
    Un lien de menu, lui, s'enlève : le transformer en « Voir la démonstration »
    ferait un menu qui répète trois fois la même chose.
    """
    rendu = exporter_site.reecrire(
        '<nav><a href="/connexion">Connexion</a>'
        '<a href="/studio">Ouvrir l\'atelier</a></nav>', MEDIAS, "", "")
    assert rendu == "<nav></nav>"


def test_les_reglages_cloudflare_sont_ecrits(tmp_path):
    """Sans _redirects, un visiteur ayant gardé un lien vers /inscription
    tomberait sur une page d'erreur."""
    exporter_site.ecrire_reglages_cloudflare(tmp_path, "")
    redirections = (tmp_path / "_redirects").read_text()
    for lien in exporter_site.LIENS_ATELIER:
        assert f"{lien} / 302" in redirections

    entetes = (tmp_path / "_headers").read_text()
    # Les médias portent une empreinte : les garder un an évite de les
    # retélécharger. Les pages, elles, doivent être revalidées.
    assert "immutable" in entetes.split("/*")[0] or "/media/*" in entetes
    assert "must-revalidate" in entetes


def test_les_redirections_pointent_vers_l_atelier_quand_il_existe(tmp_path):
    exporter_site.ecrire_reglages_cloudflare(tmp_path, "https://atelier.fr/")
    redirections = (tmp_path / "_redirects").read_text()
    assert "/inscription https://atelier.fr/inscription 302" in redirections


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


# --- Le garde-fou de la publication ----------------------------------------
# L'hébergeur ne sert pas le code : il sert `export/`, un instantané commité.
# Modifier un gabarit sans relancer l'export laisserait donc le site publié
# sur son ancienne version, sans que rien ne le signale. Ces deux tests
# échouent dès que l'instantané s'écarte de la source.

EXPORT = RACINE / "export"
SOURCE_STATIQUE = RACINE / "flambee" / "static"


def _client():
    from fastapi.testclient import TestClient

    from flambee.app import app
    return TestClient(app)


@pytest.mark.skipif(not EXPORT.exists(), reason="aucun export commité")
def test_les_pages_publiees_sont_a_jour():
    """Chaque page d'`export/` doit être celle que l'application rend là, maintenant."""
    medias = {**exporter_site.correspondances_medias(),
              **exporter_site.empreintes_ressources(SOURCE_STATIQUE)}
    with _client() as client:
        for adresse, fichier in exporter_site.PAGES.items():
            reponse = client.get(adresse)
            assert reponse.status_code == 200, adresse
            attendu = exporter_site.reecrire(reponse.text, medias, "", "")
            publie = (EXPORT / fichier).read_text(encoding="utf-8")
            assert publie == attendu, (
                f"{fichier} ne correspond plus à {adresse} — "
                f"relance : python outils/exporter_site.py --sortie export")


@pytest.mark.skipif(not EXPORT.exists(), reason="aucun export commité")
def test_les_ressources_publiees_sont_a_jour():
    """Les ressources publiées sont celles du dépôt, octet pour octet.

    Les CSS et JS sont publiés sous un nom qui porte l'empreinte de leur
    contenu : retrouver ce nom dans `export/static` prouve à la fois que le
    fichier est là et qu'il n'a pas bougé depuis.
    """
    empreintes = exporter_site.empreintes_ressources(SOURCE_STATIQUE)
    for fichier in sorted(SOURCE_STATIQUE.rglob("*")):
        if not fichier.is_file():
            continue
        relatif = fichier.relative_to(SOURCE_STATIQUE)
        publie = EXPORT / "static" / relatif
        empreint = empreintes.get(f"/static/{relatif.as_posix()}")
        if empreint:
            publie = EXPORT / empreint.lstrip("/")
        assert publie.exists(), (
            f"{publie.name} manque dans l'export — "
            f"relance : python outils/exporter_site.py --sortie export")
        assert publie.read_bytes() == fichier.read_bytes(), (
            f"{publie.name} est périmé — "
            f"relance : python outils/exporter_site.py --sortie export")


@pytest.mark.skipif(not EXPORT.exists(), reason="aucun export commité")
def test_aucune_ressource_ne_traine_sous_son_ancien_nom():
    """Un nom d'empreinte périmé serait servi un an : il ne doit pas rester.

    Les CSS et JS publiés portent un cache d'un an. Si l'export gardait aussi
    la version précédente d'un fichier, une page ancienne encore en cache
    continuerait de la charger sans jamais expirer.
    """
    attendus = {Path(v).name
                for v in exporter_site.empreintes_ressources(SOURCE_STATIQUE).values()}
    trouves = {f.name for f in (EXPORT / "static").glob("*")
               if f.suffix in (".css", ".js")}
    assert trouves == attendus, f"en trop : {trouves - attendus}"


def test_les_boutons_de_compte_menent_a_une_ancre_qui_existe():
    """Sans atelier, les boutons descendent à l'essayage — encore faut-il y aller.

    L'essayage ne vit que sur l'accueil. Une ancre relative « #essayage »
    depuis /tarifs désigne une ancre absente de cette page : le navigateur ne
    bouge pas, le bouton ne fait rien. Un lien mort sur la page des prix est
    pire que l'erreur 404 qu'on cherchait à éviter.
    """
    html = exporter_site.reecrire(
        '<a href="/inscription" class="button">Créer un compte</a>',
        MEDIAS, "", "")
    assert 'href="/#essayage"' in html


@pytest.mark.skipif(not EXPORT.exists(), reason="aucun export commité")
def test_aucune_page_publiee_ne_pointe_vers_une_ancre_absente():
    """Vérifié sur les pages réellement publiées, pas seulement sur la règle."""
    for fichier in sorted(EXPORT.rglob("*.html")):
        html = fichier.read_text(encoding="utf-8")
        for ancre in set(re.findall(r'href="#([^"]+)"', html)):
            assert f'id="{ancre}"' in html, (
                f"{fichier.relative_to(EXPORT)} pointe vers #{ancre}, "
                f"qui n'existe pas sur cette page")


# Ce qu'une vitrine sans atelier n'a pas le droit de dire. Chaque phrase promet
# un compte, un essai ou un abonnement à quelqu'un qui ne pourra rien obtenir —
# et c'est la première impression laissée aux gens à qui on donne l'adresse.
PROMESSES_INTERDITES = (
    "Créer un compte", "Créer mon compte", "Créer ma première vidéo",
    "carte bancaire", "Essai gratuit", "Le plus choisi", "Ouvrir l'atelier",
    "Commence gratuitement", "Résiliable à tout moment",
    # Le champ de saisie est masqué sans serveur : l'inviter à écrire serait
    # promettre la seule chose qu'on demande vraiment au visiteur de faire.
    "Écris ta phrase", "Tape ce que tu veux",
)


@pytest.mark.skipif(not EXPORT.exists(), reason="aucun export commité")
def test_la_vitrine_ne_promet_pas_de_compte():
    """Aucune page publiée ne doit annoncer ce que la vitrine ne peut pas tenir.

    Si ce test tombe après une modification des gabarits, ce n'est pas lui
    qu'il faut assouplir : c'est `_en_vitrine` dans l'exportateur qui ne
    connaît pas encore la nouvelle formulation.
    """
    for fichier in sorted(EXPORT.rglob("*.html")):
        texte = html_module.unescape(fichier.read_text(encoding="utf-8"))
        for promesse in PROMESSES_INTERDITES:
            assert promesse not in texte, (
                f"{fichier.relative_to(EXPORT)} promet « {promesse} » alors "
                f"qu'aucune inscription n'est possible")


@pytest.mark.skipif(not EXPORT.exists(), reason="aucun export commité")
def test_les_prix_publies_sont_annonces_comme_a_venir():
    """Les montants restent affichés, mais jamais comme des tarifs en vigueur."""
    for page in ("index.html", "tarifs/index.html"):
        texte = (EXPORT / page).read_text(encoding="utf-8")
        if "tarifs-grille" not in texte:
            continue
        avis = texte.index('class="tarifs-avis"')
        assert avis < texte.index("tarifs-grille"), \
            f"{page} : l'avis doit précéder les prix, pas les suivre"
        assert "ne sont pas encore ouvertes" in texte, f"{page} : avis absent"
