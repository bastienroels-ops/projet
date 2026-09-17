#!/usr/bin/env python3
"""Fabrique les fonds d'écran scindé de Flambée, à partir de rien.

Même raison que pour les musiques : une capture de jeu, même trouvée « libre
de droits », engage celui qui publie la vidéo. Un éditeur de jeu peut faire
retirer une vidéo des années plus tard, et la chaîne qui l'a mise en ligne
avec elle. Ici, chaque image sort d'une formule écrite dans ce fichier — il
n'y a aucun ayant droit, et rien à créditer.

Ces boucles ne racontent rien : c'est leur métier. Elles occupent le bas du
cadre, tiennent l'œil pendant que le haut parle, et ne doivent surtout pas
demander d'attention. Pas de visage, pas de texte, pas de coupe franche : du
mouvement continu et lisible en un coup d'œil.

    python outils/engendrer_fonds.py

Les fichiers obtenus sont identiques d'une machine à l'autre, et commités :
personne n'a besoin de relancer ce script pour que l'application fonctionne.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

RACINE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RACINE))

# Une boucle courte, répétée à l'infini par l'assemblage : trente secondes
# suffisent, et pèsent dix fois moins qu'une minute.
DUREE = 30.0
CADENCE = 30
# La bande du bas fait environ 730 pixels de haut sur 1080 de large dans le
# cadre final. On rend un peu plus large pour garder de la marge au recadrage.
LARGEUR, HAUTEUR = 1080, 760

# La palette de Flambée, en hexadécimal ffmpeg. Les fonds appartiennent au
# site : ils n'ont pas à ressembler à une capture d'un autre logiciel.
INDIGO = "0x6c5ce7"
VIOLET = "0xa78bfa"
NUIT = "0x1c1440"
ENCRE = "0x07070c"


# Pas de `noise` sur ces fonds. Le grain qui donne sa matière à une image fixe
# est ici un poison : il change chaque pixel à chaque image, l'encodeur ne peut
# plus rien prédire d'une image à l'autre, et une boucle de trente secondes
# pèse trois cents mégaoctets au lieu de deux. Mesuré.


def _remous(vitesse: float, flou: int) -> str:
    """Un champ de couleur qui dérive, sans rien à suivre.

    `gradients` anime quatre points de couleur ; le flou efface les bandes de
    quantification et ne laisse qu'une matière qui bouge.
    """
    return (
        f"gradients=size={LARGEUR}x{HAUTEUR}:d={DUREE}:r={CADENCE}"
        f":c0={ENCRE}:c1={NUIT}:c2={INDIGO}:c3={VIOLET}"
        f":x0=0:y0=0:x1={LARGEUR}:y1={HAUTEUR}"
        f":nb_colors=4:speed={vitesse},"
        f"gblur=sigma={flou},format=yuv420p"
    )


def _vagues(vitesse: float, periode: int) -> str:
    """Des ondes qui traversent le cadre en biais.

    Le motif le plus hypnotique du lot : aucun objet à suivre, juste une
    texture qui avance. `geq` calcule la luminance de chaque pixel par une
    sinusoïde dont la phase dépend du temps.
    """
    onde = (f"128+110*sin((X+Y*0.6)/{periode}-T*{vitesse})")
    return (
        f"color=c={ENCRE}:size={LARGEUR}x{HAUTEUR}:d={DUREE}:r={CADENCE},"
        f"format=gray,"
        f"geq=lum='{onde}',"
        f"format=yuv420p,"
        # La teinte vient d'ici : la sinusoïde ne produit que du gris.
        f"colorchannelmixer=rr=0.46:gg=0.38:bb=0.93,"
        f"gblur=sigma=6,format=yuv420p"
    )


def _damier(vitesse: float) -> str:
    """Un damier qui glisse en diagonale, très légèrement flou."""
    case = HAUTEUR // 8
    return (
        f"color=c={ENCRE}:size={LARGEUR * 2}x{HAUTEUR * 2}:d={DUREE}:r={CADENCE},"
        f"geq=lum='if(eq(mod(floor(X/{case})+floor(Y/{case}),2),0),210,28)'"
        f":cb=128:cr=128,"
        f"format=yuv420p,"
        f"crop={LARGEUR}:{HAUTEUR}:"
        f"x='mod(t*{vitesse:.0f},{case * 2})':y='mod(t*{vitesse:.0f},{case * 2})',"
        f"colorchannelmixer=rr=0.42:gg=0.36:bb=0.90,"
        f"gblur=sigma=3,format=yuv420p"
    )


FONDS: dict[str, dict] = {
    "Remous": {
        "filtre": lambda: _remous(vitesse=0.05, flou=30),
        "quoi": "un champ de couleur qui dérive",
    },
    "Vagues": {
        "filtre": lambda: _vagues(vitesse=1.8, periode=34),
        "quoi": "des ondes qui traversent le cadre en biais",
    },
    "Damier": {
        "filtre": lambda: _damier(vitesse=26),
        "quoi": "une texture qui glisse en diagonale",
    },
}


def engendrer(nom: str, fond: dict, destination: Path) -> Path:
    sortie = destination / f"{nom}.mp4"
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-v", "error", "-y",
         "-f", "lavfi", "-i", fond["filtre"](),
         "-t", f"{DUREE}", "-an",
         "-c:v", "libx264", "-preset", "medium", "-crf", "26",
         "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(sortie)],
        check=True,
    )
    return sortie


def main() -> int:
    from flambee import config

    destination = config.FONDS_DIR
    destination.mkdir(parents=True, exist_ok=True)
    for nom, fond in FONDS.items():
        chemin = engendrer(nom, fond, destination)
        poids = chemin.stat().st_size / 1e6
        print(f"  ✓ {nom:<10} {poids:5.1f} Mo — {fond['quoi']}")
    print(f"\n  {len(FONDS)} fonds dans {destination}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
