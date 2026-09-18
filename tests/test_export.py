"""L'export du site public en pages statiques."""

from __future__ import annotations

import html as html_module
import json
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


def test_l_essayage_bascule_en_apercu():
    """Sans serveur, le navigateur prend le relais et dessine lui-même.

    Le bloc doit être armé pour l'aperçu, savoir où trouver les polices du
    moteur, et recevoir le fond nu à la place des clips pré-calculés.
    """
    html = ('<div class="essayage" id="essayage">'
            '<video id="essayage-video" poster="/api/presets/punch/poster">'
            "</video></div>")
    rendu = exporter_site.reecrire(html, MEDIAS, "", "")
    assert 'data-apercu="1"' in rendu
    assert 'data-polices="/fonts"' in rendu
    assert 'src="/media/fond.mp4"' in rendu
    assert "data-fige" not in rendu


def test_les_polices_suivent_le_sous_chemin():
    """Publié sous /projet, l'aperçu doit chercher les polices au bon endroit.

    La passe de préfixe ne réécrit que href, src, poster et data-src : celle-ci
    porte donc le préfixe dès sa pose, sinon le navigateur irait chercher les
    polices à la racine du domaine et dessinerait avec autre chose.
    """
    rendu = exporter_site.reecrire(
        '<div class="essayage" id="essayage"></div>', MEDIAS, "", "/projet")
    assert 'data-polices="/projet/fonts"' in rendu
    assert 'data-polices="/projet/projet' not in rendu


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
    html = ('<link href="/static/style.css"><a href="/faq">Questions</a>'
            '<video src="/api/demo"></video>')
    rendu = exporter_site.reecrire(html, MEDIAS, "", "/projet")
    assert 'href="/projet/static/style.css"' in rendu
    assert 'href="/projet/faq"' in rendu
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
                "/api/demo", "/api/demo/poster", "/api/health", "/api/essayage",
                # Le site publié est libre et gratuit : il n'a pas de page de
                # tarifs, alors que l'application, elle, garde ses formules
                # pour le jour où un atelier tournerait quelque part.
                "/tarifs",
                # « /ping » n'est pas une page : c'est le point de contrôle que
                # le carnet Colab sonde de l'extérieur, par le tunnel, pour
                # savoir si celui-ci tient encore. L'exporter n'aurait aucun
                # sens — le site publié n'a pas de tunnel à surveiller.
                "/ping"}
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
            attendu = reponse.text
            if PORTE.exists():
                # L'export pose la porte avant de réécrire les adresses :
                # comparer sans elle accuserait l'export d'être périmé à
                # chaque fois. La porte est rendue avec le réglage commité,
                # donc elle sort identique.
                attendu = exporter_site.poser_la_porte(
                    attendu, _porte_rendue(), _reglage_porte()["empreinte"])
            attendu = exporter_site.reecrire(attendu, medias, "", "")
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
        attendu = fichier.read_bytes()
        if fichier.suffix in (".css", ".js", ".webmanifest"):
            # Ces fichiers citent des images, et l'export y remplace les
            # adresses par les noms empreints : comparer au fichier source tel
            # quel accuserait à tort l'export d'être périmé.
            attendu = exporter_site.reecrire_ressources(
                attendu.decode("utf-8"), empreintes).encode("utf-8")
        assert publie.read_bytes() == attendu, (
            f"{publie.name} est périmé — "
            f"relance : python outils/exporter_site.py --sortie export")


def test_calculer_les_empreintes_ne_touche_pas_au_depot():
    """Mesurer ne doit rien changer à ce qu'on mesure.

    Ce test et celui de fraîcheur appellent `empreintes_ressources` sur
    l'arborescence *source*, pas sur la copie exportée. Le jour où cette
    fonction a renommé les fichiers et réécrit les feuilles de style au
    passage, lancer la suite de tests suffisait à renommer les images du
    dépôt et à figer leur empreinte dans le CSS commité — sans un message,
    et avec une suite qui passait ensuite très bien.
    """
    avant = {f: f.stat().st_mtime_ns for f in sorted(SOURCE_STATIQUE.rglob("*"))}
    sommes = {f: f.read_bytes() for f in avant if f.is_file()}

    exporter_site.empreintes_ressources(SOURCE_STATIQUE)

    apres = {f: f.stat().st_mtime_ns for f in sorted(SOURCE_STATIQUE.rglob("*"))}
    assert set(apres) == set(avant), (
        f"fichiers apparus ou disparus : "
        f"{sorted(p.name for p in set(apres) ^ set(avant))}")
    modifies = [f.name for f, contenu in sommes.items()
                if f.read_bytes() != contenu]
    assert not modifies, f"contenu réécrit : {modifies}"


@pytest.mark.skipif(not EXPORT.exists(), reason="aucun export commité")
def test_aucune_adresse_publiee_ne_pointe_vers_un_nom_sans_empreinte():
    """Une adresse oubliée dans une feuille de style est un 404 silencieux.

    Les images citées par le CSS doivent être réécrites vers leur nom
    empreint : le fichier d'origine n'existe plus dans l'export, et le
    navigateur n'affiche rien — sans erreur visible, puisqu'une image de fond
    absente ne dit rien.
    """
    empreints = {Path(v).name
                 for v in exporter_site.empreintes_ressources(SOURCE_STATIQUE).values()}
    for fichier in sorted((EXPORT / "static").rglob("*")):
        if fichier.suffix not in (".css", ".js", ".webmanifest"):
            continue
        texte = fichier.read_text(encoding="utf-8")
        for adresse in re.findall(r"/static/[\w/.-]+", texte):
            nom = Path(adresse).name
            # Les polices gardent leur nom : leur contenu ne change jamais.
            assert nom in empreints or "/fonts/" in adresse, (
                f"{fichier.name} cite {adresse}, qui n'existe pas dans l'export")


@pytest.mark.skipif(not EXPORT.exists(), reason="aucun export commité")
def test_aucune_ressource_ne_traine_sous_son_ancien_nom():
    """Un nom d'empreinte périmé serait servi un an : il ne doit pas rester.

    Les ressources publiées portent un cache d'un an. Si l'export gardait aussi
    la version précédente d'un fichier, une page ancienne encore en cache
    continuerait de la charger sans jamais expirer. Les images comptent autant
    que les feuilles de style : elles portent le même cache.
    """
    empreintes = exporter_site.empreintes_ressources(SOURCE_STATIQUE)
    attendus = {Path(v).name for v in empreintes.values()}
    suffixes = {Path(v).suffix for v in empreintes.values()}
    trouves = {f.name for f in (EXPORT / "static").rglob("*")
               if f.suffix in suffixes}
    assert trouves == attendus, (
        f"en trop : {sorted(trouves - attendus)} ; "
        f"manquants : {sorted(attendus - trouves)}")


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
    # Le site est libre et gratuit : plus rien n'y est à vendre, et un prix
    # affiché renverrait à des formules qui n'existent plus.
    "19 €", "49 €", "Abonnement et résiliation", "par mois",
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
def test_aucune_page_de_tarifs_n_est_publiee():
    """Le site est libre : il n'a ni page de tarifs, ni lien vers elle."""
    assert not (EXPORT / "tarifs").exists()
    for fichier in sorted(EXPORT.rglob("*.html")):
        texte = fichier.read_text(encoding="utf-8")
        assert 'href="/tarifs"' not in texte, fichier.name
    # Derrière une porte, il n'y a pas de plan du site du tout — la promesse
    # tient donc déjà. Ailleurs, il doit être muet sur les tarifs.
    plan = EXPORT / "sitemap.xml"
    if plan.exists():
        assert "/tarifs" not in plan.read_text(encoding="utf-8"), (
            "le plan du site annonce encore les tarifs")


@pytest.mark.skipif(not EXPORT.exists(), reason="aucun export commité")
def test_les_conditions_disent_la_gratuite():
    """La clause d'abonnement est remplacée, pas simplement retirée.

    L'ôter sans rien mettre laisserait un texte muet sur le prix, là où la
    question se pose ; et la clause de gratuité n'a rien à faire sur les deux
    autres pages légales, où il n'est pas question de paiement.
    """
    conditions = (EXPORT / "conditions" / "index.html").read_text(encoding="utf-8")
    assert "<h2>Gratuité</h2>" in conditions
    assert "Abonnement et résiliation" not in conditions
    for autre in ("mentions-legales", "confidentialite"):
        texte = (EXPORT / autre / "index.html").read_text(encoding="utf-8")
        assert "Gratuité" not in texte, f"clause hors sujet sur {autre}"


@pytest.mark.skipif(not EXPORT.exists(), reason="aucun export commité")
def test_l_essayage_publie_laisse_taper_sa_phrase():
    """Le navigateur dessine le texte : la saisie doit rester ouverte.

    C'est le seul outil que le site met réellement entre les mains d'un
    visiteur. Le masquer, ou publier la page sans les polices du moteur, le
    ramènerait à un diaporama.
    """
    page = (EXPORT / "index.html").read_text(encoding="utf-8")
    assert 'data-apercu="1"' in page, "l'aperçu du navigateur n'est pas armé"
    assert 'id="essayage-texte"' in page, "le champ de saisie a disparu"
    assert 'id="styles-rendu"' in page, "les styles ne sont pas transmis"
    assert (EXPORT / "media" / "fond.mp4").exists(), "le fond du moteur manque"
    for police in ("Anton-Regular.ttf", "ArchivoBlack-Regular.ttf",
                   "BebasNeue-Regular.ttf", "PlayfairDisplay-Bold.ttf"):
        assert (EXPORT / "fonts" / police).exists(), f"{police} non publiée"
    # Les polices sont sous licence OFL : leur redistribution l'exige.
    assert (EXPORT / "fonts" / "LICENCES.md").exists(), "licences non publiées"


@pytest.mark.skipif(not EXPORT.exists(), reason="aucun export commité")
def test_le_site_s_installe_sur_l_ecran_d_accueil():
    """Ajouté à l'écran d'accueil, le site doit avoir son icône et son nom.

    iOS ignore le manifeste et lit `apple-touch-icon` ; Android fait l'inverse.
    Il faut donc les deux, et les fichiers qu'ils désignent doivent exister —
    sans quoi l'icône est une capture floue de la page, ce qui ne ressemble
    plus à une application du tout.
    """
    page = (EXPORT / "index.html").read_text(encoding="utf-8")
    assert 'rel="apple-touch-icon"' in page
    assert 'name="apple-mobile-web-app-title"' in page
    assert 'content="standalone"' not in page  # le mode vit dans le manifeste

    manifeste = re.search(r'rel="manifest" href="([^"]+)"', page)
    assert manifeste, "aucun manifeste déclaré"
    fichier = EXPORT / manifeste.group(1).lstrip("/")
    assert fichier.exists(), f"{fichier.name} déclaré mais absent"

    contenu = json.loads(fichier.read_text(encoding="utf-8"))
    assert contenu["display"] == "standalone", "le site s'ouvrirait dans Safari"
    for icone in contenu["icons"]:
        assert (EXPORT / icone["src"].lstrip("/")).exists(), icone["src"]
    # L'icône iOS est empreinte comme le reste : on suit l'adresse déclarée
    # plutôt que le nom d'origine, qui n'existe plus dans l'export.
    ios = re.search(r'rel="apple-touch-icon" href="([^"]+)"', page)
    assert ios, "aucune icône iOS déclarée"
    assert (EXPORT / ios.group(1).lstrip("/")).exists(), \
        f"{ios.group(1)} déclarée mais absente"


# --------------------------------------------------------------- La porte ---

PORTE = RACINE / "deploiement" / "porte.json"


def _reglage_porte() -> dict:
    return json.loads(PORTE.read_text(encoding="utf-8"))


def _porte_rendue() -> str:
    """La porte telle que l'export la rend, depuis le réglage commité."""
    from flambee.app import templates
    reglage = _reglage_porte()
    return templates.get_template("site/_porte.html").render(
        sel=reglage["sel"], empreinte=reglage["empreinte"],
        tours=reglage["tours"])


def test_le_code_n_est_jamais_ecrit_nulle_part():
    """Seule l'empreinte est publiée, jamais le code lui-même.

    C'est tout ce qui sépare une porte d'un écriteau : le code ne doit se
    trouver ni dans le réglage, ni dans les pages, ni dans le script.
    """
    if not PORTE.exists():
        pytest.skip("aucune porte posée")
    reglage = json.loads(PORTE.read_text(encoding="utf-8"))
    assert set(reglage) == {"sel", "tours", "empreinte"}, (
        f"le réglage porte autre chose que le sel, les tours et l'empreinte : "
        f"{sorted(reglage)}")
    assert reglage["tours"] >= 100_000, (
        "trop peu de tours : sans serveur pour limiter les tentatives, le prix "
        "d'un essai est la seule chose qui rende les essais coûteux")
    assert len(reglage["sel"]) >= 32, "sel trop court"


@pytest.mark.skipif(not EXPORT.exists(), reason="aucun export commité")
def test_toutes_les_pages_publiees_sont_derriere_la_porte():
    """Une seule page oubliée, et la porte ne sert plus à rien.

    Le réglage vit dans le dépôt justement pour cela : republier sans repasser
    l'option rouvrirait le site en grand, en silence. Ce test est le garde-fou
    qui le dit à voix haute.
    """
    if not PORTE.exists():
        pytest.skip("aucune porte posée")
    empreinte = json.loads(PORTE.read_text(encoding="utf-8"))["empreinte"]

    pages = sorted(EXPORT.glob("*.html")) + sorted(EXPORT.glob("*/index.html"))
    assert pages, "aucune page dans l'export"
    for page in pages:
        html = page.read_text(encoding="utf-8")
        relatif = page.relative_to(EXPORT)
        assert 'class="verrouille"' in html, f"{relatif} n'est pas verrouillée"
        assert 'id="porte"' in html, f"{relatif} n'a pas de porte"
        assert empreinte in html, f"{relatif} porte une autre empreinte"
        assert 'content="noindex, nofollow"' in html, (
            f"{relatif} serait indexée — le contenu part avec la page, et un "
            f"moteur de recherche publierait ce que la porte réserve")


@pytest.mark.skipif(not EXPORT.exists(), reason="aucun export commité")
def test_un_site_ferme_ne_s_annonce_pas():
    """Ni plan du site, ni invitation à explorer."""
    if not PORTE.exists():
        pytest.skip("aucune porte posée")
    assert not (EXPORT / "sitemap.xml").exists(), (
        "le plan du site est la liste de ce qu'on cherche à ne pas montrer")
    robots = (EXPORT / "robots.txt").read_text(encoding="utf-8")
    assert "Disallow: /" in robots and "Allow: /" not in robots, robots


def test_la_porte_s_efface_quand_elle_est_ouverte():
    """La feuille de style doit retirer la porte, pas seulement le voile.

    Sans cette règle, la porte restait en place — `position: fixed`, plein
    écran — chez tout visiteur déjà entré : le contenu s'affichait derrière et
    restait invisible. Le site paraissait verrouillé dès la deuxième visite.
    """
    css = (SOURCE_STATIQUE / "site.css").read_text(encoding="utf-8")
    assert re.search(r"html:not\(\.verrouille\)\s+\.porte\s*\{[^}]*display:\s*none",
                     css), "rien n'efface la porte une fois ouverte"
