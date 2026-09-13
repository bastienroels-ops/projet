#!/usr/bin/env python3
"""Exporte le site public de Flambée en pages statiques.

Pourquoi : l'atelier a besoin de Python, de ffmpeg et d'un disque — donc d'un
serveur. Le site public, lui, ne fait que montrer : une fois les clips de
démonstration calculés une bonne fois, il ne reste que du HTML, du CSS et des
fichiers. Un hébergement de pages statiques suffit, et ceux-là sont gratuits.

    python outils/exporter_site.py --sortie export
    python outils/exporter_site.py --sortie export \\
        --atelier https://flambee-bastien.duckdns.org

L'option `--atelier` fait pointer les boutons de compte vers l'application
quand elle existe quelque part. Sans elle, ces liens restent relatifs et
mèneront à une page absente : le script le dit plutôt que de le taire.
"""

from __future__ import annotations

import argparse
import hashlib
import re
import shutil
import sys
from pathlib import Path

RACINE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RACINE))

# Les pages à figer, et le chemin où les écrire. Un dossier par adresse, avec
# un index.html dedans : les adresses restent propres, sans « .html ».
PAGES: dict[str, str] = {
    "/": "index.html",
    "/fonctionnalites": "fonctionnalites/index.html",
    "/faq": "faq/index.html",
    "/mentions-legales": "mentions-legales/index.html",
    "/conditions": "conditions/index.html",
    "/confidentialite": "confidentialite/index.html",
}

# Les liens qui demandent un serveur : ils ne peuvent pas vivre en statique.
LIENS_ATELIER = ("/inscription", "/connexion", "/studio", "/mot-de-passe-oublie")


def empreintes_ressources(dossier: Path) -> dict[str, str]:
    """Nomme chaque feuille de style et chaque script d'après son contenu.

    Ces fichiers portaient un nom fixe et un cache d'un an : un visiteur déjà
    venu gardait l'ancienne feuille de style pendant des mois, sur une page
    neuve — donc une page cassée plutôt qu'une page à jour. Le nom changeant
    avec le contenu, l'ancien cache ne peut plus être servi, et le nouveau
    reste cachable aussi longtemps qu'on veut.

    Les polices gardent leur nom : leur contenu ne change jamais à nom égal.
    """
    def nommer(fichier: Path, contenu: bytes) -> tuple[str, str]:
        relatif = fichier.relative_to(dossier).as_posix()
        empreinte = hashlib.sha256(contenu).hexdigest()[:10]
        souche = relatif[:-len(fichier.suffix)]
        return (f"/static/{relatif}",
                f"/static/{souche}.{empreinte}{fichier.suffix}")

    # Les images d'abord. Elles sont citées par les feuilles de style, dont
    # l'empreinte doit donc se calculer sur le contenu *réécrit* : hachée
    # avant, une feuille changerait de contenu sans changer de nom, et le
    # cache d'un an servirait l'ancienne.
    table: dict[str, str] = {}
    for fichier in sorted(dossier.rglob("*")):
        if fichier.suffix in (".jpg", ".png", ".webp", ".svg"):
            origine, publie = nommer(fichier, fichier.read_bytes())
            table[origine] = publie

    for fichier in sorted(dossier.rglob("*")):
        # Le manifeste rejoint les feuilles et les scripts : il porte le même
        # cache d'un an, et un nom fixe l'y figerait aussi longtemps.
        if fichier.suffix not in (".css", ".js", ".webmanifest"):
            continue
        contenu = reecrire_ressources(fichier.read_text(encoding="utf-8"), table)
        origine, publie = nommer(fichier, contenu.encode("utf-8"))
        table[origine] = publie
    return table


def reecrire_ressources(texte: str, empreintes: dict[str, str]) -> str:
    """Remplace les adresses de ressources par leur nom empreint."""
    for origine, publie in empreintes.items():
        texte = texte.replace(origine, publie)
    return texte


def appliquer_empreintes(dossier: Path, empreintes: dict[str, str]) -> None:
    """Renomme les fichiers du dossier et met à jour ce qui les cite.

    Séparé du calcul : `empreintes_ressources` est appelée sur l'arborescence
    *source* par les tests et par le garde-fou de fraîcheur. Tant que les deux
    ne faisaient qu'un, les lancer renommait les fichiers du dépôt et
    réécrivait les feuilles de style au passage — le contenu du projet changeait
    parce qu'on l'avait mesuré.
    """
    for fichier in sorted(dossier.rglob("*")):
        if fichier.suffix in (".css", ".js", ".webmanifest"):
            texte = fichier.read_text(encoding="utf-8")
            remplace = reecrire_ressources(texte, empreintes)
            if remplace != texte:
                fichier.write_text(remplace, encoding="utf-8")

    for origine, publie in empreintes.items():
        source = dossier / origine[len("/static/"):]
        source.rename(dossier / publie[len("/static/"):])


def correspondances_medias() -> dict[str, str]:
    """Table des adresses de l'application vers les fichiers du site statique.

    Les noms portent une extension : un hébergeur statique choisit le type
    MIME d'après elle, et « /api/demo » sans extension serait servi comme un
    fichier à télécharger.

    Séparée du rendu : la table est déductible sans ffmpeg, et le garde-fou
    qui compare `export/` au code peut donc s'en servir sans rien calculer.
    """
    from flambee import config

    correspondances = {"/api/demo/poster": "/media/demo.jpg",
                       "/api/demo": "/media/demo.mp4"}
    for preset in config.SUBTITLE_PRESETS:
        correspondances[f"/api/presets/{preset}/sample"] = f"/media/{preset}-sample.mp4"
        correspondances[f"/api/presets/{preset}/poster"] = f"/media/{preset}-poster.jpg"
    return correspondances


def fabriquer_medias(destination: Path) -> dict[str, str]:
    """Calcule les clips et affiches une fois pour toutes, et les dépose."""
    from flambee import config, samples

    destination.mkdir(parents=True, exist_ok=True)

    print("→ Démonstration de l'accroche…", flush=True)
    shutil.copy2(samples.build_hero(), destination / "demo.mp4")
    shutil.copy2(samples.hero_poster(), destination / "demo.jpg")

    # Le fond nu : l'aperçu du navigateur y pose le texte du visiteur.
    print("→ Fond de l'essayage…", flush=True)
    shutil.copy2(samples.build_backdrop(), destination / "fond.mp4")

    # Les polices des sous-titres, pour que le navigateur dessine avec les
    # mêmes que ffmpeg. Elles sont toutes sous licence OFL, qui autorise leur
    # redistribution ; leur fichier de licence part avec elles.
    polices = destination.parent / "fonts"
    polices.mkdir(parents=True, exist_ok=True)
    for fichier in sorted(config.FONTS_DIR.iterdir()):
        if fichier.suffix in (".ttf", ".otf") or fichier.name == "LICENCES.md":
            shutil.copy2(fichier, polices / fichier.name)

    for preset in config.SUBTITLE_PRESETS:
        print(f"→ Style « {preset} »…", flush=True)
        shutil.copy2(samples.build_sample(preset),
                     destination / f"{preset}-sample.mp4")
        shutil.copy2(samples.sample_poster(preset),
                     destination / f"{preset}-poster.jpg")

    return correspondances_medias()


def reecrire(html: str, adresses: dict[str, str], atelier: str, prefixe: str) -> str:
    """Adapte une page servie par l'application à une publication statique.

    `adresses` fait correspondre ce que sert l'application — les points
    d'entrée des médias, les ressources à leur nom d'origine — aux fichiers
    publiés.
    """
    # Les adresses d'abord : les clés les plus longues en premier, sinon
    # « /api/demo » remplacerait le début de « /api/demo/poster ».
    # Le préfixe n'est pas posé ici : la passe finale s'en charge, et l'ajouter
    # aux deux endroits donnait « /projet/projet/media/… ».
    for source in sorted(adresses, key=len, reverse=True):
        html = html.replace(f'"{source}"', f'"{adresses[source]}"')

    # L'essayage libre demande ffmpeg à chaque phrase. Publié en statique, il
    # passe à l'aperçu dessiné par le navigateur : le visiteur tape toujours sa
    # phrase, le fond reste celui du moteur, seul le texte est peint à l'écran.
    #
    # `data-polices` porte le préfixe ici même : la passe finale ne réécrit que
    # href, src, poster et data-src. `src` du fond, lui, la laisse faire.
    html = html.replace(
        '<div class="essayage" id="essayage">',
        f'<div class="essayage" id="essayage" data-apercu="1" '
        f'data-polices="{prefixe}/fonts">', 1)
    html = re.sub(
        r'<video id="essayage-video"[^>]*>',
        '<video id="essayage-video" muted loop autoplay playsinline '
        'preload="auto" src="/media/fond.mp4">',
        html, count=1)

    if atelier:
        base = atelier.rstrip("/")
        for lien in LIENS_ATELIER:
            html = re.sub(rf'href="{lien}(["?])', rf'href="{base}{lien}\1', html)
    else:
        # Vitrine seule : ces adresses n'existent pas ici, et les promesses
        # qui y mènent non plus. Les boutons descendent à l'essayage — on
        # montre le produit au lieu de mener nulle part — et le texte cesse
        # d'annoncer des comptes que personne ne peut créer.
        #
        # L'ancre est absolue : l'essayage ne vit que sur l'accueil, et
        # « #essayage » depuis /tarifs cherche une ancre absente de la page,
        # donc ne fait rien — un bouton mort, pire que le 404 qu'on évitait.
        html = _en_vitrine(html)
    if prefixe:
        # `poster` et `data-src` portent aussi des adresses de médias : les
        # oublier laisserait les clips pointer à la racine du domaine.
        html = re.sub(r'\b(href|src|poster|data-src)="/(?!/)',
                      rf'\1="{prefixe}/', html)
    return html


# Les liens de compte, dans une page servie par l'application.
_ANCRE_ATELIER = re.compile(
    r'<a\s+([^>]*?)href="(/(?:inscription|connexion|studio|mot-de-passe-oublie)'
    r'[^"]*)"([^>]*?)>(.*?)</a>', re.S)
_CLASSE = re.compile(r'class="([^"]*)"')

# Ce que la vitrine seule ne peut pas tenir. Chaque phrase promet un compte,
# un essai ou un abonnement : sans atelier derrière, ce sont des promesses en
# l'air, et c'est la première impression qu'on laisse aux gens à qui on donne
# l'adresse.
_PROMESSES = {
    "Essai gratuit — aucune carte bancaire demandée.":
        "Libre d'accès, sans compte à créer.",
    # Un produit que personne n'a encore pu acheter n'a pas de formule
    # « la plus choisie » : le ruban invente une preuve sociale.
    '<span class="ruban">Le plus choisi</span>': "",
    "Oui, à tout moment et sans justification. L'accès reste ouvert "
    "jusqu'à la fin de la période déjà réglée.":
        "Il n'y a pas d'abonnement : le site est libre et gratuit, et rien "
        "n'y est à souscrire.",
    "Puis-je annuler mon abonnement ?": "Y a-t-il quelque chose à payer ?",
}

_APPEL = (
    '<section class="appel">\n'
    "  <h2>Sers-toi, <em>c'est gratuit</em>.</h2>\n"
    "  <p>Pas de compte, pas de formule, rien à payer. Les six écritures de\n"
    "     sous-titres et le test de l'accroche sont là, ouverts à tout le\n"
    "     monde, et le resteront.</p>\n"
    '  <a href="/#essayage" class="button primary grand">Essayer les sous-titres</a>\n'
    "</section>")

# Les blocs entiers que la vitrine gratuite n'a pas lieu de montrer.
# La clause d'abonnement renvoie à une page de tarifs qui n'existe plus et
# décrit une facturation qui n'a pas lieu : la laisser serait plus trompeur
# que de l'ôter. Une clause la remplace, qui dit ce qu'il en est vraiment.
_A_RETIRER = (
    r'<section class="section section-tarifs">.*?</section>',
    r'<a href="/tarifs"[^>]*>.*?</a>',
)

_CLAUSE_ABONNEMENT = r'<h2>Abonnement et résiliation</h2>(?:\s*<p>.*?</p>)+'


_CLAUSE_GRATUITE = (
    "<h2>Gratuité</h2><p>Le service est mis à disposition gratuitement. "
    "Aucun paiement n'est demandé, aucun abonnement n'est souscrit, et rien "
    "n'est donc à résilier.</p>")


def _en_vitrine(html: str) -> str:
    """Rend la page honnête quand il n'y a pas d'atelier derrière.

    Les gabarits ne connaissent pas ce mode, et c'est voulu : le jour où
    l'application tourne quelque part, l'export reçoit `--atelier`, cette
    fonction n'est pas appelée, et les vrais boutons reviennent d'eux-mêmes.
    """
    def remplacer(m):
        avant, apres, texte = m.group(1), m.group(3), m.group(4)
        # Le bouton d'une formule invite à la choisir. Aucune n'étant ouverte,
        # il ne se remplace pas non plus : trois boutons identiques sous trois
        # prix différents feraient croire à un choix qui n'existe pas. La
        # carte se contente alors de décrire ce que la formule contiendra.
        if "formule=" in m.group(2):
            return ""
        classes = _CLASSE.search(avant + apres)
        if classes and "button" in classes.group(1):
            return (f'<a href="/#essayage" class="{classes.group(1)}">'
                    f"Voir la démonstration</a>")
        # Un lien de navigation vers une page qui n'existe pas ne se remplace
        # pas : il s'enlève. « Connexion » sans rien derrière n'aide personne.
        return ""

    html = _ANCRE_ATELIER.sub(remplacer, html)
    # Toutes les occurrences : le lien vers les tarifs figure dans le menu et
    # dans le pied de page, et n'en retirer qu'un le laisserait dans l'autre.
    for motif in _A_RETIRER:
        html = re.sub(motif, "", html, flags=re.S)

    # La clause de gratuité ne remplace celle d'abonnement que sur la page qui
    # la portait : posée sur les trois pages légales, elle parlerait de
    # paiement là où il n'en était pas question.
    html, retiree = re.subn(_CLAUSE_ABONNEMENT, "", html, flags=re.S, count=1)
    if retiree:
        # Elle se pose à la fin du texte légal, et nulle part ailleurs : un
        # simple remplacement du premier « </div> » la collerait dans l'en-tête.
        html = re.sub(r'(<div class="texte-legal">.*?)</div>',
                      lambda m: m.group(1) + _CLAUSE_GRATUITE + "</div>",
                      html, count=1, flags=re.S)
    # Le navigateur dessine le texte : la saisie libre fonctionne, et
    # l'accroche peut de nouveau y inviter — en disant d'où vient quoi.
    html = re.sub(
        r'<p class="section-accroche">Six écritures\. Tape ce que tu veux.*?</p>',
        '<p class="section-accroche">Six écritures. Tape ce que tu veux : le '
        "fond est une vraie image du moteur vidéo, et ton navigateur y dessine "
        "le texte avec la police, la taille et les couleurs exactes du rendu.</p>",
        html, flags=re.S)
    for promesse, honnete in _PROMESSES.items():
        html = html.replace(promesse, honnete)
    html = re.sub(r'<section class="appel">.*?</section>', _APPEL, html, flags=re.S)
    return html


def _elaguer_sitemap(xml: str, atelier: str, prefixe: str) -> str:
    """Ne garde que les adresses réellement publiées."""
    publiees = {chemin.rstrip("/") or "/" for chemin in PAGES}

    def garder(bloc: str) -> bool:
        adresse = re.search(r"<loc>([^<]+)</loc>", bloc)
        if not adresse:
            return True
        chemin = re.sub(r"^https?://[^/]+", "", adresse.group(1)).rstrip("/") or "/"
        if prefixe and chemin.startswith(prefixe):
            chemin = chemin[len(prefixe):] or "/"
        return chemin in publiees

    return re.sub(r"\s*<url>.*?</url>",
                  lambda m: m.group(0) if garder(m.group(0)) else "",
                  xml, flags=re.S)


def ecrire_reglages_cloudflare(sortie: Path, atelier: str) -> None:
    """Deux fichiers que Cloudflare Pages lit à la racine du site.

    `_headers` fixe la durée de cache. Les feuilles de style et les scripts
    portent une empreinte de contenu dans leur nom : à nom égal leur contenu
    ne bouge plus, on peut les garder un an. Les médias, eux, gardent un nom
    fixe — le script d'essayage compose leurs adresses — donc ils sont
    revalidés à chaque visite : sinon la démonstration republiée resterait
    invisible pendant des mois pour qui est déjà venu.

    Aucune règle ne fixe le cache des pages : les deux hébergeurs les
    revalident par défaut, et une règle `/*` viendrait écraser les deux
    précédentes, qu'elle soit lue avant ou après elles.

    `_redirects` rattrape les adresses de l'atelier : sans lui, un visiteur
    qui a gardé un lien vers /inscription tomberait sur une page d'erreur.
    """
    (sortie / "_headers").write_text(
        "/media/*\n"
        "  Cache-Control: public, max-age=0, must-revalidate\n"
        "/static/*\n"
        "  Cache-Control: public, max-age=31536000, immutable\n"
        "/*\n"
        "  X-Content-Type-Options: nosniff\n"
        "  Referrer-Policy: strict-origin-when-cross-origin\n",
        encoding="utf-8")

    destination = atelier.rstrip("/") if atelier else ""
    lignes = []
    for lien in LIENS_ATELIER:
        cible = f"{destination}{lien}" if destination else "/"
        code = "302" if destination else "302"
        lignes.append(f"{lien} {cible} {code}")
        lignes.append(f"{lien}/* {cible} {code}")
    (sortie / "_redirects").write_text("\n".join(lignes) + "\n", encoding="utf-8")


def exporter(sortie: Path, atelier: str, prefixe: str) -> None:
    from fastapi.testclient import TestClient

    from flambee import media
    from flambee.app import app

    manquants = media.ensure_tools()
    if manquants:
        raise SystemExit(f"ffmpeg est requis pour calculer les médias : {manquants}")

    if sortie.exists():
        shutil.rmtree(sortie)
    sortie.mkdir(parents=True)

    medias = fabriquer_medias(sortie / "media")

    print("→ Ressources…", flush=True)
    shutil.copytree(RACINE / "flambee" / "static", sortie / "static")
    ressources = empreintes_ressources(sortie / "static")
    appliquer_empreintes(sortie / "static", ressources)
    adresses = {**medias, **ressources}

    with TestClient(app) as client:
        for adresse, fichier in PAGES.items():
            reponse = client.get(adresse)
            if reponse.status_code != 200:
                raise SystemExit(f"{adresse} a répondu {reponse.status_code}")
            cible = sortie / fichier
            cible.parent.mkdir(parents=True, exist_ok=True)
            cible.write_text(
                reecrire(reponse.text, adresses, atelier, prefixe), encoding="utf-8")
            print(f"   {adresse:22s} → {fichier}")

        for nom in ("robots.txt", "sitemap.xml"):
            reponse = client.get("/" + nom)
            if reponse.status_code != 200:
                continue
            texte = reponse.text
            if nom == "sitemap.xml":
                # Le plan du site est celui de l'application : il annonce des
                # adresses que la vitrine ne publie pas. Les y laisser enverrait
                # les moteurs de recherche sur des redirections, et ferait
                # figurer dans leurs résultats des pages qui n'existent plus.
                texte = _elaguer_sitemap(texte, atelier, prefixe)
            (sortie / nom).write_text(texte, encoding="utf-8")

    # Une page 404 : les hébergeurs statiques la servent d'eux-mêmes.
    with TestClient(app) as client:
        reponse = client.get("/adresse-qui-nexiste-pas")
        (sortie / "404.html").write_text(
            reecrire(reponse.text, adresses, atelier, prefixe), encoding="utf-8")

    ecrire_reglages_cloudflare(sortie, atelier)

    poids = sum(f.stat().st_size for f in sortie.rglob("*") if f.is_file())
    fichiers = sum(1 for f in sortie.rglob("*") if f.is_file())
    print(f"\n✓ {fichiers} fichiers, {poids / 1_048_576:.1f} Mo dans {sortie}/")
    if not atelier:
        print("\n⚠️  Vitrine seule : les boutons « Créer un compte » et")
        print("   « Connexion » descendent à l'essayage, faute d'atelier où")
        print("   les envoyer. Relance avec --atelier <adresse> le jour où")
        print("   l'application tourne, et ils y mèneront.")


def main() -> None:
    analyseur = argparse.ArgumentParser(description=__doc__)
    analyseur.add_argument("--sortie", default="export", type=Path)
    analyseur.add_argument("--atelier", default="",
                           help="adresse de l'application, pour les liens de compte")
    analyseur.add_argument("--prefixe", default="",
                           help="sous-chemin de publication, ex. /projet")
    arguments = analyseur.parse_args()
    exporter(arguments.sortie, arguments.atelier, arguments.prefixe.rstrip("/"))


if __name__ == "__main__":
    main()
