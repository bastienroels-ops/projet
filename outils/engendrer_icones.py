#!/usr/bin/env python3
"""Fabrique les icônes d'application de Flambée, à partir de la palette.

Ces trois PNG sont ce que l'on voit sur l'écran d'accueil d'un téléphone une
fois le site ajouté — c'est-à-dire la marque, en un centimètre. Ils étaient
dessinés à la main et déposés là : le jour où la couleur du site a changé, ils
sont restés orange, seuls de leur espèce, et rien ne l'a signalé.

Ils sortent donc d'ici désormais. La flamme est le même tracé que la favicone
de `templates/site/base.html` — un test vérifie que les deux ne divergent
pas — et les couleurs sont celles de la feuille de style.

    python outils/engendrer_icones.py

Les fichiers obtenus sont déterministes et commités : personne n'a besoin de
relancer ce script pour que le site s'affiche.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

RACINE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RACINE))

# Le tracé de la flamme, dans un carré de 24. Identique à celui de la favicone.
FLAMME = ("M12 21.6c3.7 0 6.7-2.8 6.7-6.4 0-4.8-4.2-6.7-4.2-11.2 0 0-3.1 1.7"
          "-3.1 5.5 0 1.7-1 2.4-1.9 1.7-.7-.6-.9-1.9-.9-1.9S5.3 11.2 5.3 15.2"
          "c0 3.6 3 6.4 6.7 6.4Z")

# Les mêmes valeurs que `:root` dans static/style.css.
ENCRE = "#07070c"
ENCRE_HAUT = "#15142a"      # le fond s'éclaircit vers le haut, comme une nuit
FLAMME_CLAIRE = "#a78bfa"
FLAMME_ACCENT = "#6c5ce7"
FLAMME_NUIT = "#3b2b8f"

TAILLES = (180, 192, 512)


def dessin() -> str:
    """L'icône entière, en SVG, dans un carré de 24.

    Trois couches : le fond, un halo derrière la flamme — sans lui l'icône est
    un autocollant posé sur du noir — et la flamme en dégradé.
    """
    return f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">
  <defs>
    <linearGradient id="fond" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0" stop-color="{ENCRE_HAUT}"/>
      <stop offset="1" stop-color="{ENCRE}"/>
    </linearGradient>
    <radialGradient id="halo" cx="0.5" cy="0.62" r="0.5">
      <stop offset="0" stop-color="{FLAMME_ACCENT}" stop-opacity="0.55"/>
      <stop offset="1" stop-color="{FLAMME_ACCENT}" stop-opacity="0"/>
    </radialGradient>
    <linearGradient id="flamme" x1="0.2" y1="1" x2="0.8" y2="0">
      <stop offset="0" stop-color="{FLAMME_NUIT}"/>
      <stop offset="0.45" stop-color="{FLAMME_ACCENT}"/>
      <stop offset="1" stop-color="{FLAMME_CLAIRE}"/>
    </linearGradient>
  </defs>
  <rect width="24" height="24" fill="url(#fond)"/>
  <rect width="24" height="24" fill="url(#halo)"/>
  <path d="{FLAMME}" fill="url(#flamme)"/>
</svg>
'''


def engendrer(taille: int, source: Path, destination: Path) -> Path:
    """Rasterise le dessin à une taille, sans coins arrondis.

    Les coins sont laissés carrés : iOS et Android découpent eux-mêmes la
    forme du système, et une icône déjà arrondie s'y retrouve rognée deux
    fois, avec un liseré sombre sur le tour.
    """
    sortie = destination / f"icone-{taille}.png"
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error",
         "-width", str(taille), "-height", str(taille), "-i", str(source),
         "-frames:v", "1", str(sortie)],
        check=True,
    )
    return sortie


def main() -> int:
    destination = RACINE / "flambee" / "static"
    source = destination / ".icone.svg"
    source.write_text(dessin(), encoding="utf-8")
    try:
        for taille in TAILLES:
            chemin = engendrer(taille, source, destination)
            poids = chemin.stat().st_size / 1024
            print(f"  {chemin.name:16s} {poids:5.1f} ko")
    finally:
        source.unlink(missing_ok=True)
    print(f"\n✓ {len(TAILLES)} icônes dans {destination}/")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
