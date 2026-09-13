#!/usr/bin/env python3
"""Fabrique les musiques de fond de Flambée, à partir de rien.

Pourquoi les calculer plutôt que les télécharger : une musique trouvée en
ligne, même annoncée « libre de droits », engage celui qui publie la vidéo.
Les licences changent, les catalogues se revendent, et un ayant droit peut
réclamer des années plus tard sur une vidéo déjà en ligne. Ici, chaque note
sort d'une formule écrite dans ce fichier : il n'y a aucun ayant droit.

Cinq ambiances, pensées pour passer *sous* une voix off — c'est leur seul
métier. Elles sont donc volontairement sans mélodie marquante, sans percussion
sèche et sans montée : tout ce qui attirerait l'oreille loin du propos.

    python outils/engendrer_musiques.py

Les fichiers obtenus sont identiques d'une machine à l'autre, et commités :
personne n'a besoin de relancer ce script pour que l'application fonctionne.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

RACINE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RACINE))

DUREE = 32.0          # une boucle entière ; l'application la répète à l'infini
ECHANTILLONNAGE = 44100

# La gamme, en hertz. Tout reste dans la même tonalité d'une piste à l'autre :
# un montage qui change de musique ne change pas de couleur harmonique.
NOTES = {
    "do2": 65.41, "sol2": 98.00, "do3": 130.81, "mib3": 155.56,
    "fa3": 174.61, "sol3": 196.00, "sib3": 233.08, "do4": 261.63,
    "mib4": 311.13, "fa4": 349.23, "sol4": 392.00, "sib4": 466.16,
    "do5": 523.25, "mib5": 622.25,
}


def _choix(valeurs: list[float], pas: float) -> str:
    """Une expression ffmpeg qui parcourt ces valeurs, une par pas de temps."""
    index = f"mod(floor(t/{pas}),{len(valeurs)})"
    expression = f"{valeurs[-1]:.2f}"
    for i in range(len(valeurs) - 2, -1, -1):
        expression = f"if(eq({index},{i}),{valeurs[i]:.2f},{expression})"
    return expression


def _pincee(notes: list[float], pas: float, decroissance: float,
            harmoniques: tuple[float, ...], amplitude: float) -> str:
    """Une note pincée qui s'éteint, répétée le long de la séquence.

    L'enveloppe part de zéro à chaque note : c'est elle qui masque le saut de
    phase quand la fréquence change, sans quoi chaque changement claquerait.
    """
    frequence = _choix(notes, pas)
    enveloppe = f"exp(-mod(t,{pas})*{decroissance})"
    voix = "+".join(
        f"{poids:.3f}*sin(2*PI*({frequence})*{rang}*t)"
        for rang, poids in enumerate(harmoniques, start=1)
    )
    return f"{amplitude}*{enveloppe}*({voix})"


def _nappe(accord: list[float], amplitude: float, battement: float) -> str:
    """Un accord tenu, très légèrement désaccordé pour qu'il respire.

    Le désaccord de quelques centièmes de hertz crée un battement lent entre
    les deux voix : sans lui, une somme de sinus pures sonne comme une alarme.
    """
    voix = []
    for i, frequence in enumerate(accord):
        voix.append(f"sin(2*PI*{frequence:.2f}*t)")
        voix.append(f"sin(2*PI*{frequence + 0.12 + i * 0.07:.2f}*t)")
    respiration = f"(0.82+0.18*sin(2*PI*{battement}*t))"
    return f"{amplitude}*{respiration}*({'+'.join(voix)})"


N = NOTES

PISTES: dict[str, dict] = {
    "Braises": {
        "description": "Nappe chaude et immobile, pour laisser toute la place au propos.",
        "gauche": _nappe([N["do3"], N["mib3"], N["sol3"]], 0.085, 0.055),
        "droite": _nappe([N["do3"], N["sol3"], N["sib3"]], 0.085, 0.041),
        "filtres": "lowpass=f=1100,aecho=0.7:0.75:420|760:0.28|0.2",
    },
    "Nocturne": {
        "description": "Notes rares et graves, beaucoup de silence entre elles.",
        "gauche": _pincee([N["do3"], N["sol2"], N["mib3"], N["do3"]],
                          2.0, 1.6, (1.0, 0.32, 0.11), 0.30),
        "droite": _pincee([N["sol3"], N["do3"], N["sib3"], N["sol3"]],
                          2.0, 1.9, (1.0, 0.26, 0.08), 0.24),
        "filtres": "lowpass=f=1600,aecho=0.8:0.85:600|1100:0.35|0.24",
    },
    "Clair": {
        "description": "Arpège léger et aéré, qui avance sans presser.",
        "gauche": _pincee([N["do4"], N["mib4"], N["sol4"], N["sib4"],
                           N["sol4"], N["mib4"]],
                          0.5, 5.5, (1.0, 0.22, 0.09, 0.04), 0.20),
        "droite": _pincee([N["sol4"], N["do5"], N["sib4"], N["mib5"],
                           N["sib4"], N["do5"]],
                          0.5, 6.5, (1.0, 0.18, 0.06), 0.15),
        "filtres": "highpass=f=180,lowpass=f=4200,aecho=0.8:0.8:270|410:0.3|0.22",
    },
    "Pulsation": {
        "description": "Battement sourd et régulier, pour une montée de tension.",
        "gauche": (f"0.34*exp(-mod(t,0.6)*7)*sin(2*PI*{N['do2']:.2f}*t)"
                   f"+0.10*exp(-mod(t,0.6)*3)*sin(2*PI*{N['do3']:.2f}*t)"),
        "droite": (f"0.30*exp(-mod(t,0.6)*7)*sin(2*PI*{N['do2']:.2f}*t)"
                   f"+0.07*exp(-mod(t,1.2)*2)*sin(2*PI*{N['sol3']:.2f}*t)"),
        "filtres": "lowpass=f=900,aecho=0.8:0.7:340:0.22",
    },
    "Ressort": {
        "description": "Motif court et rebondi, utile sur un rythme rapide.",
        "gauche": _pincee([N["do4"], N["sol3"], N["sib3"], N["fa4"],
                           N["mib4"], N["sol3"], N["do4"], N["sib3"]],
                          0.32, 9.0, (1.0, 0.30, 0.14, 0.06), 0.19),
        "droite": _pincee([N["sol4"], N["mib4"], N["fa4"], N["do4"],
                           N["sib4"], N["mib4"], N["sol4"], N["fa4"]],
                          0.32, 11.0, (1.0, 0.24, 0.10), 0.14),
        "filtres": "highpass=f=200,lowpass=f=3600,aecho=0.8:0.8:190|290:0.26|0.18",
    },
}


def engendrer(nom: str, piste: dict, destination: Path) -> Path:
    """Calcule une piste et l'écrit en mp3."""
    sortie = destination / f"{nom}.mp3"
    # `alimiter` en fin de chaîne : les sommes de sinus peuvent dépasser 0 dB
    # sur une coïncidence de phases, et la saturation s'entend immédiatement.
    graphe = (
        f"aevalsrc='{piste['gauche']}|{piste['droite']}'"
        f":s={ECHANTILLONNAGE}:d={DUREE:.2f}:c=stereo,"
        f"{piste['filtres']},"
        f"alimiter=level_in=1:level_out=0.85:limit=0.9,"
        # Même cible que la voix (-14 LUFS) : c'est ce qui donne son sens au
        # curseur « volume musique » de l'application, qui n'est qu'une
        # atténuation relative. Mastérisées douze décibels plus bas, ces
        # pistes se retrouvaient trente décibels sous la voix — inaudibles.
        f"loudnorm=I=-14:TP=-1.5:LRA=7"
    )
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", graphe,
         "-t", f"{DUREE:.2f}", "-c:a", "libmp3lame", "-b:a", "128k",
         "-metadata", f"title={nom}",
         "-metadata", "artist=Flambée",
         "-metadata", "comment=Engendré par outils/engendrer_musiques.py "
                      "— aucun ayant droit.",
         str(sortie)],
        check=True,
    )
    return sortie


def main() -> int:
    from flambee import config

    config.MUSIC_DIR.mkdir(parents=True, exist_ok=True)
    for nom, piste in PISTES.items():
        chemin = engendrer(nom, piste, config.MUSIC_DIR)
        poids = chemin.stat().st_size / 1024
        print(f"  {nom:12s} {poids:6.0f} ko   {piste['description']}")
    print(f"\n✓ {len(PISTES)} musiques dans {config.MUSIC_DIR}/")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
