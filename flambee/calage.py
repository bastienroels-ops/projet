"""Caler les sous-titres sur ce que la voix dit vraiment.

edge-tts renvoie, avec l'audio, un `WordBoundary` par mot. On s'en servait
directement. Mesuré, ce minutage ne décrit pas le fichier livré :

    silences réels dans l'audio      minutage edge-tts
      0.000 → 0.207  (début)           'Trois'    0.000 → 0.415
      2.672 → 3.607  (après « saches. »)  'saches.' 3.324 → 3.905
      6.459 → 7.409  (après « lit. »)     'lit.'    6.813 → 7.146

Le minutage d'edge-tts n'a aucun trou : chaque mot commence exactement où
finit le précédent, et les pauses de ponctuation sont absorbées dans la durée
des mots qui les précèdent. L'audio, lui, contient trois secondes de silence
sur dix. Les deux échelles de temps ne sont donc pas la même, et aucun
décalage constant ne peut les réconcilier — mesuré ici entre −0,22 et +0,30 s
selon l'endroit du script, ce qui s'entend et se voit.

D'où ce module. On connaît le texte exact, et on a le fichier audio : il ne
reste qu'à retrouver où chaque mot tombe dedans. Whisper, déjà embarqué pour
Script Viral, donne un minutage au mot calé sur le son. Il peut mal entendre
un mot — mais nous savons lequel aurait dû être dit, et une comparaison de
séquences remet chaque mot connu en face du mot entendu.

Sans moteur de transcription, on rend le minutage d'origine : c'est le
comportement d'avant, jamais pire.
"""

from __future__ import annotations

import logging
import re
import unicodedata
from difflib import SequenceMatcher
from pathlib import Path

from .voice import Word

log = logging.getLogger(__name__)

# En dessous, le calage n'a rien reconnu : on garde le minutage d'origine
# plutôt que d'imposer une correspondance inventée.
ACCORD_MINIMUM = 0.55


def disponible() -> bool:
    """Le calage précis demande le moteur de transcription."""
    from . import transcribe
    return transcribe.available()


def _clef(mot: str) -> str:
    """Forme comparable d'un mot : sans accent, sans ponctuation, en bas de casse.

    Whisper écrit « mélatonine », edge-tts « mélatonine. » ; l'un peut rendre
    « c'est » quand l'autre donne « c'est ». La comparaison se fait donc sur
    une forme dépouillée, pas sur le texte affiché — qui, lui, reste celui du
    script, seul à être sûr.
    """
    plat = unicodedata.normalize("NFD", mot.lower())
    plat = "".join(c for c in plat if unicodedata.category(c) != "Mn")
    return re.sub(r"[^a-z0-9]", "", plat)


def caler(attendus: list[Word], audio: Path | str,
          *, langue: str = "fr") -> list[Word]:
    """Rend les mots attendus, minutés sur l'audio. Le texte n'est pas touché."""
    if not attendus:
        return attendus
    from . import transcribe

    try:
        entendu = transcribe.transcribe(audio, language=langue, with_words=True)
    except Exception as exc:                 # moteur absent, audio illisible…
        log.info("Calage impossible (%s) : minutage d'origine conservé.", exc)
        return attendus

    return caler_sur(attendus, entendu.words)


def caler_sur(attendus: list[Word], entendus: list[Word]) -> list[Word]:
    """Le cœur du calage, sans dépendance au moteur — donc testable seul."""
    if not attendus or not entendus:
        return attendus

    clefs_a = [_clef(m.text) for m in attendus]
    clefs_e = [_clef(m.text) for m in entendus]

    correspondance = SequenceMatcher(None, clefs_a, clefs_e, autojunk=False)
    ancres: dict[int, Word] = {}
    reconnus = 0
    for bloc in correspondance.get_matching_blocks():
        for decalage in range(bloc.size):
            ancres[bloc.a + decalage] = entendus[bloc.b + decalage]
            reconnus += 1

    part = reconnus / len(attendus)
    if part < ACCORD_MINIMUM:
        log.info("Calage abandonné : %.0f %% des mots reconnus seulement.",
                 part * 100)
        return attendus

    cales = _interpoler(attendus, ancres)
    log.info("Sous-titres calés sur la voix : %d mots sur %d reconnus.",
             reconnus, len(attendus))
    return cales


def _interpoler(attendus: list[Word], ancres: dict[int, Word]) -> list[Word]:
    """Minute les mots non reconnus entre les ancres qui les entourent.

    Un mot que Whisper n'a pas entendu ne doit pas faire trou : on répartit
    l'intervalle entre les deux mots sûrs qui l'encadrent, au prorata du
    nombre de lettres — un mot long occupe plus de temps qu'un mot court.
    """
    resultat: list[Word] = []
    indices = sorted(ancres)
    premier, dernier = indices[0], indices[-1]

    for position, mot in enumerate(attendus):
        if position in ancres:
            ancre = ancres[position]
            resultat.append(Word(text=mot.text, start=ancre.start, end=ancre.end))
            continue
        resultat.append(Word(text=mot.text, start=0.0, end=0.0))  # comblé après

    # Avant la première ancre et après la dernière, on ne peut qu'extrapoler à
    # partir du minutage d'origine, en le recalant sur l'ancre voisine.
    _extrapoler_debut(resultat, attendus, premier, ancres[premier])
    _extrapoler_fin(resultat, attendus, dernier, ancres[dernier])

    # Entre deux ancres, on répartit.
    for gauche, droite in zip(indices, indices[1:]):
        if droite - gauche <= 1:
            continue
        debut, fin = resultat[gauche].end, resultat[droite].start
        trous = list(range(gauche + 1, droite))
        poids = [max(1, len(attendus[i].text)) for i in trous]
        total = sum(poids)
        curseur = debut
        for i, p in zip(trous, poids):
            duree = max(0.0, (fin - debut)) * p / total
            resultat[i] = Word(text=attendus[i].text, start=curseur,
                               end=curseur + duree)
            curseur += duree

    return _rendre_croissant(resultat)


def _extrapoler_debut(resultat: list[Word], attendus: list[Word],
                      premier: int, ancre: Word) -> None:
    """Les mots d'avant la première ancre gardent leurs durées d'origine,
    posées en reculant depuis elle."""
    curseur = ancre.start
    for i in range(premier - 1, -1, -1):
        duree = max(0.05, attendus[i].end - attendus[i].start)
        debut = max(0.0, curseur - duree)
        resultat[i] = Word(text=attendus[i].text, start=debut, end=curseur)
        curseur = debut


def _extrapoler_fin(resultat: list[Word], attendus: list[Word],
                    dernier: int, ancre: Word) -> None:
    curseur = ancre.end
    for i in range(dernier + 1, len(attendus)):
        duree = max(0.05, attendus[i].end - attendus[i].start)
        resultat[i] = Word(text=attendus[i].text, start=curseur,
                           end=curseur + duree)
        curseur += duree


def _rendre_croissant(mots: list[Word]) -> list[Word]:
    """Un sous-titre qui recule d'un mot à l'autre casse le surlignage.

    Whisper rend parfois deux mots qui se chevauchent d'un ou deux
    centièmes ; libass, lui, exige un minutage qui avance.
    """
    propre: list[Word] = []
    curseur = 0.0
    for mot in mots:
        debut = max(curseur, mot.start)
        fin = max(debut + 0.04, mot.end)
        propre.append(Word(text=mot.text, start=debut, end=fin))
        curseur = fin
    return propre
