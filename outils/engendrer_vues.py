#!/usr/bin/env python3
"""Fabrique les images fixes du site, avec le moteur vidéo de Flambée.

Un site qui vend du montage vidéo et qui s'illustre avec des photos de banque
d'images se contredit lui-même. Les images fixes de la page d'accueil sortent
donc d'ici : les trois vignettes « Source » et les deux bandeaux pleine
largeur sont calculés par le même `sunset_filter` que la démonstration du
téléphone, à des moments différents du jour.

Pourquoi les calculer plutôt que les prendre ailleurs : aucune licence à
suivre, aucun ayant droit, et le site ressemble à ce qu'il produit.

    python outils/engendrer_vues.py

Les fichiers obtenus sont déterministes et commités : personne n'a besoin de
relancer ce script pour que le site s'affiche.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

RACINE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RACINE))

# Assez grand pour rester net sur un écran à deux pixels par point : les
# vignettes font environ 200 points de large au plus large des mises en page.
LARGEUR = 420
HAUTEUR = 746          # 9:16, pair des deux côtés

# Les bandeaux pleine largeur, très étirés.
BANDEAU = (1600, 620)

# Les couches des bandeaux sont peintes plus fin qu'à l'ordinaire : l'astre y
# est petit dans une image large, et à la définition par défaut son disque
# sortait en escalier.
BANDEAU_DEFINITION = 760

# Chaque pixel de chaque image passe par une formule. Pour une image fixe
# prise à la neuvième seconde, la cadence du cinéma en fait calculer deux cent
# vingt-cinq pour n'en garder qu'une — plusieurs minutes de calcul jetées.
# Quatre images par seconde suffisent, à condition que les instants demandés
# tombent sur cette grille : sinon ffmpeg sert l'image précédente et le choix
# de l'instant ne veut plus rien dire.
CADENCE = 4


def engendrer(nom: str, moment, destination: Path, *, instant: float,
              taille: tuple[int, int] = (LARGEUR, HAUTEUR),
              definition: int | None = None) -> Path:
    """Rend une image fixe du plan, à l'instant donné.

    L'instant compte : la mer et les nuages bougent avec le temps, et prendre
    les vues au même moment donnerait partout la même ondulation.
    """
    from flambee.samples import sunset_filter

    sortie = destination / f"{nom}.jpg"
    duree = instant + 1 / CADENCE
    graphe = sunset_filter(*taille, duree, tag="v", moment=moment,
                           definition=definition, fps=CADENCE)
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-filter_complex", graphe,
         "-map", "[v]", "-ss", f"{instant:.2f}", "-frames:v", "1",
         "-q:v", "4", str(sortie)],
        check=True,
    )
    return sortie


def main() -> int:
    from flambee import samples

    verticale = (LARGEUR, HAUTEUR)
    vues = (
        ("vue-aube", samples.AUBE, 1.50, verticale,
         "L'aube — bleus froids, mer d'étain.", None),
        ("vue-couchant", samples.COUCHANT, 3.25, verticale,
         "Le couchant — celui de la démonstration.", None),
        ("vue-nuit", samples.NUIT, 5.00, verticale,
         "La nuit — lune haute, chemin d'argent.", None),
        ("bandeau-couchant", samples.COUCHANT_BANDEAU, 7.25, BANDEAU,
         "Bandeau — le couchant en pleine largeur.", BANDEAU_DEFINITION),
        ("bandeau-nuit", samples.NUIT_BANDEAU, 9.25, BANDEAU,
         "Bandeau — la nuit en pleine largeur.", BANDEAU_DEFINITION),
    )
    destination = RACINE / "flambee" / "static" / "vues"
    destination.mkdir(parents=True, exist_ok=True)
    for nom, moment, instant, taille, description, definition in vues:
        assert abs(instant * CADENCE - round(instant * CADENCE)) < 1e-9, (
            f"{nom} : {instant}s ne tombe pas sur la grille de {CADENCE} i/s")
        chemin = engendrer(nom, moment, destination, instant=instant,
                           taille=taille, definition=definition)
        poids = chemin.stat().st_size / 1024
        print(f"  {nom:14s} {poids:5.0f} ko   {description}")
    print(f"\n✓ {len(vues)} vues dans {destination}/")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
