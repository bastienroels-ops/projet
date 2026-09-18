"""Caler les sous-titres sur ce que la voix dit vraiment.

edge-tts renvoie, avec l'audio, un `WordBoundary` par mot. On s'en servait
directement. Mesuré, ce minutage ne décrit pas le fichier livré :

    silences réels dans l'audio            minutage annoncé
      0.000 → 0.207  (avant le 1er mot)      'Trois'    0.000 → 0.415
      2.672 → 3.607  (après « saches. »)     'saches.'  3.324 → 3.905
      6.459 → 7.409  (après « lit. »)        'lit.'     6.813 → 7.146

Le minutage annoncé n'a aucun trou — chaque mot commence exactement où finit
le précédent — quand l'audio contient trois secondes de silence sur dix. Ce
ne sont pas deux horloges décalées, ce sont deux horloges différentes : aucune
constante ne peut les réconcilier. Le silence initial, seul corrigé jusqu'ici,
ne réglait rien du reste.

Sur quatre scripts et 167 mots, l'écart au mot réellement prononcé était de
533 ms en médiane et 946 ms au neuvième décile — de quoi gâcher une vidéo. Un
mot sur quatre s'affichait alors que rien n'était dit.

--- Ce qui marche -----------------------------------------------------------

Le son dit lui-même où l'on parle et où l'on se tait, et ffmpeg sait le lire.
Mesuré : les pauses de l'audio correspondent une à une aux ponctuations du
texte. Neuf pauses pour neuf ponctuations fortes sur un script ; sur un autre,
huit pauses pour quatre ponctuations fortes et quatre virgules — les fortes
durent autour de 0,95 s, les virgules autour de 0,30. Le son annonce la
structure de la phrase.

D'où la méthode : chaque silence est une coupure entre deux mots. Laquelle,
c'est une programmation dynamique qui le décide — la position proportionnelle
doit concorder, et une ponctuation est un indice fort. Entre deux coupures, on
répartit au prorata des durées annoncées par edge-tts : fausses dans l'absolu
puisqu'elles absorbent les pauses, mais justes les unes par rapport aux autres
à l'intérieur d'une portée parlée.

Résultat sur le même banc : 56 ms en médiane, 175 ms au neuvième décile, et
plus aucun mot affiché sur du silence.

--- Ce qui ne marche pas ----------------------------------------------------

Trois pistes ont été essayées et mesurées avant d'être écartées :

* Whisper, qui donne un minutage au mot. Il demande une installation que
  personne ne fait — et il est moins bon : il antidate le premier mot de
  chaque segment jusqu'au début du segment. Mesuré à −92,9 dB sur la tranche
  où il plaçait un mot, soit du silence numérique. Sur le critère qui ne
  dépend d'aucun modèle — un mot affiché alors que rien n'est prononcé — il
  laissait 4 % des mots dehors, contre 0 % ici.

* Répartir au prorata de l'énergie émise plutôt que du temps. Séduisant, et
  deux fois pire : 104 ms de médiane contre 57. L'énergie varie trop à
  l'intérieur d'un mot pour servir d'horloge.

* Pondérer par les lettres ou les syllabes plutôt que par la durée annoncée :
  67 ms et 103 ms de médiane. Les durées d'edge-tts, toutes fausses qu'elles
  soient, restent la meilleure mesure du poids relatif d'un mot.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

from .voice import Word

log = logging.getLogger(__name__)

PONCTUATION = ".!?:;…,"

# En dessous, ce n'est pas une pause mais une respiration ou une consonne
# sourde. Mesuré : une virgule produit 0,20 à 0,34 s, un point 0,93 à 0,99.
# Un balayage de 0,08 à 0,25 s et de −40 à −50 dB donne partout entre 54 et
# 60 ms de médiane : le réglage est sur un plateau, pas sur une pointe.
PAUSE_MINIMALE = 0.18
SEUIL_SILENCE_DB = -45

# Ce qu'une ponctuation vaut en détour sur la position proportionnelle. Au-delà,
# c'est que la coupure est ailleurs et la ponctuation ne doit pas l'emporter.
PRIME_PONCTUATION = 0.05


def caler(mots: list[Word], audio: Path | str) -> list[Word]:
    """Minute les mots sur les portées parlées du fichier.

    Rend le minutage d'origine si le son ne se laisse pas lire — jamais pire
    qu'avant, et sans autre dépendance que ffmpeg.
    """
    if len(mots) < 2:
        return mots
    silences, duree = pauses_du_son(audio)
    if not duree:
        return mots

    # Le silence de tête et celui de queue bornent la parole ; ceux du milieu
    # la découpent.
    debut = silences[0][1] if silences and silences[0][0] <= 0.05 else 0.0
    fin = duree
    if silences and silences[-1][1] >= duree - 0.05:
        fin = silences[-1][0]
    internes = [(a, b) for a, b in silences
                if a > debut + 0.01 and b < fin - 0.01]

    if fin - debut < 0.2:
        return mots

    portees = _portees(debut, fin, internes)
    coupures = _choisir_les_coupures(mots, portees)
    if coupures is None:
        log.info("Calage abandonné : %d pauses pour %d mots.",
                 len(portees) - 1, len(mots))
        return mots

    cales = _rendre_croissant(_repartir(mots, portees, coupures))
    log.info("Sous-titres calés sur la voix : %d portée(s) parlée(s).",
             len(portees))
    return cales


def pauses_du_son(audio: Path | str) -> tuple[list[tuple[float, float]], float]:
    """Les silences du fichier, et sa durée. Rien d'autre que ffmpeg."""
    from . import config
    from .media import MediaError, run

    try:
        proc = run([
            config.FFMPEG_BIN, "-hide_banner", "-nostdin", "-i", str(audio),
            "-af", f"silencedetect=noise={SEUIL_SILENCE_DB}dB:d={PAUSE_MINIMALE}",
            "-vn", "-f", "null", "-",
        ], timeout=180, capture_stderr=True)
    except (MediaError, OSError) as exc:
        log.info("Silences illisibles (%s) : minutage d'origine conservé.", exc)
        return [], 0.0

    sortie = proc.stderr or ""
    debuts = [float(m) for m in re.findall(r"silence_start:\s*(-?[0-9.]+)", sortie)]
    fins = [float(m) for m in re.findall(r"silence_end:\s*([0-9.]+)", sortie)]

    duree = 0.0
    horodatages = re.findall(r"time=(\d+):(\d+):([0-9.]+)", sortie)
    if horodatages:
        h, m, s = horodatages[-1]
        duree = int(h) * 3600 + int(m) * 60 + float(s)

    # `silence_start` sans `silence_end` : le fichier finit dans le silence.
    if len(debuts) > len(fins):
        fins = fins + [duree]
    return list(zip(debuts, fins)), duree


def _portees(debut: float, fin: float,
             internes: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """Les intervalles où l'on parle, dans l'ordre."""
    portees = []
    curseur = debut
    for a, b in internes:
        if a > curseur:
            portees.append((curseur, a))
        curseur = b
    portees.append((curseur, fin))
    return [p for p in portees if p[1] - p[0] > 0.05]


def _choisir_les_coupures(mots: list[Word],
                          portees: list[tuple[float, float]]) -> list[int] | None:
    """Où couper la suite de mots pour la répartir sur les portées.

    Programmation dynamique : on cherche les k coupures qui minimisent l'écart
    entre la position proportionnelle annoncée et la position proportionnelle
    réellement parlée, avec une prime aux coupures de ponctuation. Sur les
    scripts mesurés, elle retrouve exactement les points, virgules et
    deux-points.
    """
    besoin = len(portees) - 1
    if besoin <= 0:
        return []
    if besoin >= len(mots):
        return None                  # plus de pauses que de mots : on renonce

    total = mots[-1].end - mots[0].start
    if total <= 0:
        return None
    annonce = [(m.end - mots[0].start) / total for m in mots]

    parle = sum(b - a for a, b in portees)
    reel, cumule = [], 0.0
    for a, b in portees[:-1]:
        cumule += b - a
        reel.append(cumule / parle)

    prime = [PRIME_PONCTUATION if m.text.rstrip()[-1:] in PONCTUATION else 0.0
             for m in mots]

    # cout[i][j] : meilleur coût des i premières coupures, la i-ème placée
    # après le mot j. `trace` garde de quoi relire le chemin.
    INFINI = float("inf")
    dernier_mot = len(mots) - 1
    cout = [[INFINI] * dernier_mot for _ in range(besoin + 1)]
    trace = [[-1] * dernier_mot for _ in range(besoin + 1)]
    for j in range(dernier_mot):
        cout[1][j] = abs(annonce[j] - reel[0]) - prime[j]
    for i in range(2, besoin + 1):
        meilleur, ou = INFINI, -1
        for j in range(dernier_mot):
            if j >= 1 and cout[i - 1][j - 1] < meilleur:
                meilleur, ou = cout[i - 1][j - 1], j - 1
            if meilleur < INFINI:
                cout[i][j] = meilleur + abs(annonce[j] - reel[i - 1]) - prime[j]
                trace[i][j] = ou

    fin = min(range(dernier_mot), key=lambda j: cout[besoin][j])
    if cout[besoin][fin] == INFINI:
        return None
    coupures = [fin]
    for i in range(besoin, 1, -1):
        fin = trace[i][fin]
        if fin < 0:
            return None
        coupures.append(fin)
    return sorted(coupures)


def _repartir(mots: list[Word], portees: list[tuple[float, float]],
              coupures: list[int]) -> list[Word]:
    """Chaque groupe de mots occupe sa portée, au prorata des durées annoncées.

    Les durées d'edge-tts sont fausses dans l'absolu — elles absorbent les
    pauses — mais leur rapport entre deux mots d'une même portée tient : un mot
    long y occupe bien plus de place qu'un mot court. Mesuré meilleur que les
    lettres, les syllabes et l'énergie émise.
    """
    groupes, debut = [], 0
    for coupure in coupures:
        groupes.append(mots[debut:coupure + 1])
        debut = coupure + 1
    groupes.append(mots[debut:])

    cales: list[Word] = []
    for groupe, (ouverture, fermeture) in zip(groupes, portees):
        if not groupe:
            continue
        poids = [max(0.04, m.end - m.start) for m in groupe]
        total = sum(poids)
        curseur = ouverture
        largeur = fermeture - ouverture
        for mot, part in zip(groupe, poids):
            duree = largeur * part / total
            cales.append(Word(text=mot.text, start=curseur, end=curseur + duree))
            curseur += duree
    return cales


def _rendre_croissant(mots: list[Word]) -> list[Word]:
    """Un sous-titre qui recule d'un mot à l'autre casse le surlignage.

    libass exige un minutage qui avance ; un arrondi malheureux suffirait à
    produire deux mots qui se chevauchent.
    """
    propre: list[Word] = []
    curseur = 0.0
    for mot in mots:
        debut = max(curseur, mot.start)
        fin = max(debut + 0.04, mot.end)
        propre.append(Word(text=mot.text, start=debut, end=fin))
        curseur = fin
    return propre
