"""Caler les sous-titres sur la voix.

Le minutage d'edge-tts n'a aucun trou : chaque mot commence exactement où
finit le précédent. L'audio livré, lui, contient des pauses de ponctuation —
mesuré sur un script de dix secondes : trois secondes de silence, et dix-sept
mots sur soixante-dix affichés alors que rien n'était prononcé.

Ces tests portent sur le cœur du calage, qui ne dépend pas du moteur de
transcription : on lui donne ce que Whisper aurait entendu.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from flambee import calage  # noqa: E402
from flambee.voice import Word  # noqa: E402


def _mots(paires) -> list[Word]:
    return [Word(text=t, start=d, end=f) for t, d, f in paires]


def test_le_minutage_entendu_remplace_le_minutage_annonce():
    attendus = _mots([("Bonjour", 0.0, 0.5), ("tout", 0.5, 0.8),
                      ("le", 0.8, 1.0), ("monde", 1.0, 1.6)])
    entendus = _mots([("bonjour", 0.2, 0.7), ("tout", 1.5, 1.8),
                      ("le", 1.8, 2.0), ("monde", 2.0, 2.6)])
    cales = calage.caler_sur(attendus, entendus)
    assert [m.start for m in cales] == [0.2, 1.5, 1.8, 2.0]
    # Le texte affiché reste celui du script, jamais celui de la transcription.
    assert [m.text for m in cales] == ["Bonjour", "tout", "le", "monde"]


def test_la_ponctuation_et_les_accents_n_empechent_pas_la_reconnaissance():
    """Whisper écrit « mélatonine », le script « mélatonine. » — et parfois
    l'inverse pour les accents. La comparaison se fait sur une forme
    dépouillée."""
    attendus = _mots([("La", 0.0, 0.2), ("mélatonine.", 0.2, 1.0)])
    entendus = _mots([("la", 0.5, 0.7), ("Melatonine", 0.7, 1.5)])
    cales = calage.caler_sur(attendus, entendus)
    assert [m.start for m in cales] == [0.5, 0.7]
    assert cales[1].text == "mélatonine."


def test_un_mot_non_entendu_est_reparti_entre_ses_voisins():
    """Whisper avale parfois un mot court. Il ne doit pas faire trou : on lui
    donne sa part de l'intervalle entre les deux mots sûrs qui l'encadrent."""
    attendus = _mots([("un", 0.0, 0.3), ("deux", 0.3, 0.6), ("trois", 0.6, 1.0)])
    entendus = _mots([("un", 1.0, 1.3), ("trois", 2.0, 2.4)])
    cales = calage.caler_sur(attendus, entendus)
    assert cales[0].start == 1.0 and cales[2].start == 2.0
    assert 1.3 <= cales[1].start < cales[1].end <= 2.0


def test_une_transcription_qui_ne_correspond_pas_est_ecartee():
    """Mieux vaut le minutage d'origine qu'un calage sur un autre texte : si
    Whisper a transcrit autre chose, on ne lui fait pas confiance."""
    attendus = _mots([("un", 0.0, 0.3), ("deux", 0.3, 0.6), ("trois", 0.6, 1.0),
                      ("quatre", 1.0, 1.4), ("cinq", 1.4, 1.8)])
    entendus = _mots([("chien", 5.0, 5.3), ("chat", 5.3, 5.6),
                      ("cheval", 5.6, 6.0), ("poule", 6.0, 6.4)])
    assert calage.caler_sur(attendus, entendus) is attendus


def test_le_minutage_rendu_avance_toujours():
    """Whisper rend parfois deux mots qui se chevauchent. libass exige un
    minutage croissant : un sous-titre qui recule casse le surlignage."""
    attendus = _mots([("un", 0.0, 0.3), ("deux", 0.3, 0.6), ("trois", 0.6, 1.0)])
    entendus = _mots([("un", 1.0, 1.5), ("deux", 1.4, 1.45), ("trois", 1.2, 1.8)])
    cales = calage.caler_sur(attendus, entendus)
    for precedent, suivant in zip(cales, cales[1:]):
        assert suivant.start >= precedent.end, (precedent, suivant)
    for mot in cales:
        assert mot.end > mot.start


def test_sans_transcription_le_minutage_est_rendu_tel_quel():
    attendus = _mots([("un", 0.0, 0.3)])
    assert calage.caler_sur(attendus, []) is attendus
    assert calage.caler_sur([], _mots([("un", 0.0, 0.3)])) == []


def test_les_mots_avant_la_premiere_ancre_reculent_depuis_elle():
    """Si Whisper rate le tout premier mot, il ne doit pas rester à zéro
    pendant que le reste part à la seconde."""
    attendus = _mots([("Alors", 0.0, 0.4), ("écoute", 0.4, 0.9),
                      ("bien", 0.9, 1.3)])
    entendus = _mots([("écoute", 1.0, 1.5), ("bien", 1.5, 1.9)])
    cales = calage.caler_sur(attendus, entendus)
    assert cales[1].start == 1.0
    assert 0.0 <= cales[0].start < cales[0].end <= 1.0
