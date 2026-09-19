"""Mode une seule vidéo : les paroles de la vidéo tiennent lieu de voix off.

Avec plusieurs sources, le montage suit une voix off que Flambée fabrique à
partir d'un script. Avec une seule, il n'y a rien à monter : la vidéo garde son
propre son, et ce sont ses paroles — écoutées, puis corrigées à la main — qui
donnent les sous-titres.

Deux services :

- `paroles_de` écoute la piste audio d'une vidéo et rend les mots minutés ;
- `realigner` reporte sur ces minutages un texte que l'utilisateur a corrigé,
  sans lui demander de recaler quoi que ce soit.
"""

from __future__ import annotations

import difflib
import logging
import re
from pathlib import Path

from . import transcribe
from .media import MediaError
from .voice import Word

log = logging.getLogger(__name__)

# Sous cette durée, un sous-titre passe trop vite pour être lu.
MIN_MOT = 0.08


def paroles_de(video: str | Path, dossier: Path, *, has_audio: bool = True,
               language: str | None = None) -> list[Word]:
    """Les mots prononcés dans une vidéo, avec leur minutage.

    Une liste vide est une réponse valable : une vidéo muette, ou faite de
    musique seule, n'a rien à sous-titrer. Ce qui échoue vraiment — moteur de
    transcription absent, fichier illisible — lève une erreur.

    `language=None` laisse le moteur reconnaître la langue : une vidéo trouvée
    sur TikTok n'est pas forcément en français.
    """
    if not has_audio:
        return []
    if not transcribe.available():
        raise transcribe.TranscriptionError(
            "Moteur de transcription indisponible : "
            f"{transcribe.raison_indisponible()}. Écris le texte à la main, "
            "ou désactive les sous-titres."
        )
    audio = dossier / "paroles.wav"
    try:
        transcribe.ensure_audio(Path(video), audio)
    except MediaError:
        return []                      # pas de piste exploitable : rien à dire
    try:
        return transcribe.transcribe(audio, language=language,
                                     with_words=True).words
    finally:
        audio.unlink(missing_ok=True)


def texte_des_mots(mots: list[Word] | list[dict]) -> str:
    """Le texte à afficher dans l'éditeur."""
    return " ".join(
        (m["text"] if isinstance(m, dict) else m.text) for m in mots
    ).strip()


def _forme(mot: str) -> str:
    """Un mot réduit à ce qui compte pour le comparer : ni casse, ni ponctuation.

    Corriger « salut, » en « Salut » ne change pas le mot : son minutage doit
    rester celui de l'original."""
    return re.sub(r"[^\w]+", "", mot.lower())


def realigner(anciens: list[Word], texte: str, duree: float) -> list[Word]:
    """Reporte un texte corrigé sur le minutage des mots d'origine.

    Les mots qui n'ont pas changé gardent leur minutage exact. Un mot corrigé
    prend la place de celui qu'il remplace ; plusieurs mots à la place d'un seul
    se partagent sa durée, au prorata de leur longueur. Un mot ajouté prend sa
    place dans l'intervalle libre entre ses voisins.

    Sans mots d'origine — vidéo sans parole, texte écrit de zéro — le texte est
    réparti sur toute la durée de la vidéo.
    """
    nouveaux = texte.split()
    if not nouveaux:
        return []
    if not anciens:
        return _repartir(nouveaux, 0.0, max(duree, MIN_MOT * len(nouveaux)))

    comparaison = difflib.SequenceMatcher(
        a=[_forme(m.text) for m in anciens],
        b=[_forme(m) for m in nouveaux],
        autojunk=False,
    )
    resultat: list[Word] = []
    for etiquette, i1, i2, j1, j2 in comparaison.get_opcodes():
        if j2 == j1:
            continue                                  # mots supprimés
        groupe = nouveaux[j1:j2]
        if etiquette == "equal":
            resultat += [Word(text=mot, start=anciens[i1 + k].start,
                              end=anciens[i1 + k].end)
                         for k, mot in enumerate(groupe)]
            continue
        if i2 > i1:                                   # remplacés
            debut, fin = anciens[i1].start, anciens[i2 - 1].end
        else:                                         # ajoutés
            debut = anciens[i1 - 1].end if i1 > 0 else 0.0
            fin = anciens[i1].start if i1 < len(anciens) else max(duree, debut)
        resultat += _repartir(groupe, debut, fin)

    return _sans_chevauchement(resultat)


def _repartir(mots: list[str], debut: float, fin: float) -> list[Word]:
    """Étale des mots sur un intervalle, au prorata de leur longueur."""
    fin = max(fin, debut + MIN_MOT * len(mots))
    poids = [max(1, len(mot)) for mot in mots]
    total = sum(poids)
    place, curseur = [], debut
    for mot, poids_du_mot in zip(mots, poids):
        duree = (fin - debut) * poids_du_mot / total
        place.append(Word(text=mot, start=curseur, end=curseur + duree))
        curseur += duree
    return place


def _sans_chevauchement(mots: list[Word]) -> list[Word]:
    """Garantit des mots dans l'ordre, chacun avec une durée lisible."""
    propres: list[Word] = []
    for mot in mots:
        debut = max(mot.start, propres[-1].end if propres else 0.0)
        propres.append(Word(text=mot.text, start=round(debut, 3),
                            end=round(max(mot.end, debut + MIN_MOT), 3)))
    return propres
