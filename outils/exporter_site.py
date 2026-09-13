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
    "/tarifs": "tarifs/index.html",
    "/faq": "faq/index.html",
    "/mentions-legales": "mentions-legales/index.html",
    "/conditions": "conditions/index.html",
    "/confidentialite": "confidentialite/index.html",
}

# Les liens qui demandent un serveur : ils ne peuvent pas vivre en statique.
LIENS_ATELIER = ("/inscription", "/connexion", "/studio", "/mot-de-passe-oublie")


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

    for preset in config.SUBTITLE_PRESETS:
        print(f"→ Style « {preset} »…", flush=True)
        shutil.copy2(samples.build_sample(preset),
                     destination / f"{preset}-sample.mp4")
        shutil.copy2(samples.sample_poster(preset),
                     destination / f"{preset}-poster.jpg")

    return correspondances_medias()


def reecrire(html: str, medias: dict[str, str], atelier: str, prefixe: str) -> str:
    """Adapte une page servie par l'application à une publication statique."""
    # Les médias d'abord : les clés les plus longues en premier, sinon
    # « /api/demo » remplacerait le début de « /api/demo/poster ».
    # Le préfixe n'est pas posé ici : la passe finale s'en charge, et l'ajouter
    # aux deux endroits donnait « /projet/projet/media/… ».
    for source in sorted(medias, key=len, reverse=True):
        html = html.replace(f'"{source}"', f'"{medias[source]}"')

    # L'essayage libre demande ffmpeg : on bascule sur les clips pré-calculés.
    html = html.replace(
        '<div class="essayage" id="essayage">',
        f'<div class="essayage" id="essayage" data-fige="1" '
        f'data-echantillons="{prefixe}/media">', 1)

    if atelier:
        base = atelier.rstrip("/")
        for lien in LIENS_ATELIER:
            html = re.sub(rf'href="{lien}(["?])', rf'href="{base}{lien}\1', html)
    else:
        # Vitrine seule : ces adresses n'existent pas ici. Plutôt qu'un 404 au
        # premier clic, les boutons descendent à l'essayage — on montre le
        # produit au lieu de mener nulle part.
        for lien in LIENS_ATELIER:
            html = re.sub(rf'href="{lien}[^"]*"', 'href="#essayage"', html)
    if prefixe:
        # `poster` et `data-src` portent aussi des adresses de médias : les
        # oublier laisserait les clips pointer à la racine du domaine.
        html = re.sub(r'\b(href|src|poster|data-src)="/(?!/)',
                      rf'\1="{prefixe}/', html)
    return html


def ecrire_reglages_cloudflare(sortie: Path, atelier: str) -> None:
    """Deux fichiers que Cloudflare Pages lit à la racine du site.

    `_headers` fixe la durée de cache. Les médias portent une empreinte dans
    leur nom et ne changent jamais à contenu égal : les garder un an évite de
    les retélécharger à chaque visite. Les pages, elles, doivent être
    revalidées, sinon une correction publiée resterait invisible.

    `_redirects` rattrape les adresses de l'atelier : sans lui, un visiteur
    qui a gardé un lien vers /inscription tomberait sur une page d'erreur.
    """
    (sortie / "_headers").write_text(
        "/media/*\n"
        "  Cache-Control: public, max-age=31536000, immutable\n"
        "/static/*\n"
        "  Cache-Control: public, max-age=31536000, immutable\n"
        "/*\n"
        "  Cache-Control: public, max-age=0, must-revalidate\n"
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

    with TestClient(app) as client:
        for adresse, fichier in PAGES.items():
            reponse = client.get(adresse)
            if reponse.status_code != 200:
                raise SystemExit(f"{adresse} a répondu {reponse.status_code}")
            cible = sortie / fichier
            cible.parent.mkdir(parents=True, exist_ok=True)
            cible.write_text(
                reecrire(reponse.text, medias, atelier, prefixe), encoding="utf-8")
            print(f"   {adresse:22s} → {fichier}")

        for nom in ("robots.txt", "sitemap.xml"):
            reponse = client.get("/" + nom)
            if reponse.status_code == 200:
                (sortie / nom).write_text(reponse.text, encoding="utf-8")

    # Une page 404 : les hébergeurs statiques la servent d'eux-mêmes.
    with TestClient(app) as client:
        reponse = client.get("/adresse-qui-nexiste-pas")
        (sortie / "404.html").write_text(
            reecrire(reponse.text, medias, atelier, prefixe), encoding="utf-8")

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
