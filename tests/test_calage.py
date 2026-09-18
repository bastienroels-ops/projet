"""Caler les sous-titres sur la voix.

edge-tts annonce un minutage qui ne décrit pas le fichier qu'il livre : il n'a
aucune pause, là où l'audio en a trois secondes sur dix. Mesuré sur quatre
scripts et 167 mots, l'écart au mot réellement prononcé était de 533 ms en
médiane, et un mot sur quatre s'affichait alors que rien n'était dit.

Le son, lui, dit où l'on parle. Ces tests portent sur la mécanique qui s'en
sert — sans ffmpeg, en lui donnant les silences directement.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from flambee import calage  # noqa: E402
from flambee.voice import Word  # noqa: E402


def _mots(paires) -> list[Word]:
    return [Word(text=t, start=d, end=f) for t, d, f in paires]


def _contigus(textes, duree=0.4) -> list[Word]:
    """Le minutage tel qu'edge-tts le rend : sans le moindre trou."""
    mots, curseur = [], 0.0
    for texte in textes:
        mots.append(Word(text=texte, start=curseur, end=curseur + duree))
        curseur += duree
    return mots


# --- Les portées parlées ---------------------------------------------------
def test_les_portees_encadrent_la_parole():
    portees = calage._portees(0.2, 10.0, [(3.0, 4.0), (6.0, 7.0)])
    assert portees == [(0.2, 3.0), (4.0, 6.0), (7.0, 10.0)]


def test_une_portee_vide_n_est_pas_gardee():
    """Deux silences collés ne doivent pas créer d'intervalle de zéro seconde,
    qui ferait ensuite une division par zéro."""
    portees = calage._portees(0.0, 5.0, [(1.0, 2.0), (2.0, 3.0)])
    assert all(b - a > 0.05 for a, b in portees)


# --- Le choix des coupures -------------------------------------------------
def test_les_coupures_tombent_sur_la_ponctuation():
    """Le son annonce la structure de la phrase : une pause par ponctuation.

    « Regarde bien. Ça change tout. Tu vas comprendre. » — deux pauses, donc
    deux coupures, et elles doivent tomber après « bien. » et après « tout. ».
    """
    mots = _contigus(["Regarde", "bien.", "Ça", "change", "tout.",
                      "Tu", "vas", "comprendre."])
    portees = [(0.2, 1.05), (2.02, 2.91), (3.82, 4.78)]
    assert calage._choisir_les_coupures(mots, portees) == [1, 4]


def test_la_ponctuation_ne_l_emporte_pas_sur_une_position_absurde():
    """La prime est un indice, pas une règle : une ponctuation à l'autre bout
    du script ne doit pas attirer une coupure."""
    mots = _contigus(["un.", "deux", "trois", "quatre", "cinq", "six",
                      "sept", "huit", "neuf", "dix"])
    # Une seule pause, aux quatre cinquièmes du texte.
    portees = [(0.0, 8.0), (8.5, 10.0)]
    coupures = calage._choisir_les_coupures(mots, portees)
    assert coupures is not None and coupures[0] >= 6, coupures


def test_sans_pause_il_n_y_a_pas_de_coupure():
    mots = _contigus(["un", "deux", "trois"])
    assert calage._choisir_les_coupures(mots, [(0.0, 3.0)]) == []


def test_plus_de_pauses_que_de_mots_fait_renoncer():
    """Mieux vaut le minutage d'origine qu'un découpage impossible."""
    mots = _contigus(["un", "deux"])
    portees = [(0.0, 1.0), (1.5, 2.0), (2.5, 3.0), (3.5, 4.0)]
    assert calage._choisir_les_coupures(mots, portees) is None


# --- La répartition --------------------------------------------------------
def test_chaque_groupe_remplit_sa_portee():
    mots = _contigus(["a", "b", "c", "d"])
    portees = [(1.0, 2.0), (5.0, 6.0)]
    cales = calage._repartir(mots, portees, [1])
    assert cales[0].start == 1.0
    assert abs(cales[1].end - 2.0) < 1e-9
    assert cales[2].start == 5.0
    assert abs(cales[3].end - 6.0) < 1e-9


def test_un_mot_long_prend_plus_de_place_qu_un_mot_court():
    """Les durées annoncées sont fausses dans l'absolu, mais leur rapport
    tient : c'est ce qui sert à répartir."""
    mots = _mots([("court", 0.0, 0.2), ("interminable", 0.2, 1.0)])
    cales = calage._repartir(mots, [(0.0, 6.0)], [])
    assert (cales[1].end - cales[1].start) > 3 * (cales[0].end - cales[0].start)


# --- Bout en bout, sans ffmpeg ---------------------------------------------
def test_aucun_mot_ne_tombe_dans_un_silence(monkeypatch):
    """Le critère qui ne dépend d'aucun modèle : un sous-titre affiché alors
    que rien n'est prononcé est faux, sans discussion possible."""
    mots = _contigus(["Regarde", "bien.", "Ça", "change", "tout.",
                      "Tu", "vas", "comprendre."])
    silences = [(0.0, 0.19), (1.05, 2.02), (2.91, 3.82), (4.78, 5.1)]
    monkeypatch.setattr(calage, "pauses_du_son", lambda a: (silences, 5.1))

    cales = calage.caler(mots, "peu importe.mp3")
    for mot in cales:
        milieu = (mot.start + mot.end) / 2
        dans_le_vide = [(a, b) for a, b in silences if a < milieu < b]
        assert not dans_le_vide, f"{mot.text!r} s'affiche sur du silence {dans_le_vide}"


def test_le_texte_n_est_jamais_touche(monkeypatch):
    mots = _contigus(["Voici", "l'astuce.", "Regarde"])
    monkeypatch.setattr(calage, "pauses_du_son",
                        lambda a: ([(0.0, 0.2), (1.0, 1.6), (3.0, 3.2)], 3.2))
    assert [m.text for m in calage.caler(mots, "x.mp3")] == \
        ["Voici", "l'astuce.", "Regarde"]


def test_un_son_illisible_rend_le_minutage_d_origine(monkeypatch):
    """Jamais pire qu'avant : c'est la seule promesse qui compte quand ffmpeg
    ne répond pas."""
    mots = _contigus(["un", "deux"])
    monkeypatch.setattr(calage, "pauses_du_son", lambda a: ([], 0.0))
    assert calage.caler(mots, "x.mp3") is mots


def test_aucun_sous_titre_ne_dure_moins_de_quarante_millisecondes(monkeypatch):
    """Un mot très court dans un groupe dense recevrait quelques
    millisecondes : à l'écran, il clignote au lieu de s'allumer.

    C'est le seul office de la dernière passe — la répartition, elle, rend
    déjà un minutage croissant par construction."""
    mots = [Word(text="a", start=0.0, end=0.01)] + \
        [Word(text=f"m{i}", start=0.01 + i, end=1.01 + i) for i in range(12)]
    monkeypatch.setattr(calage, "pauses_du_son",
                        lambda a: ([(0.0, 0.05), (1.2, 1.4)], 1.4))
    for mot in calage.caler(mots, "x.mp3"):
        # 0.09 - 0.05 vaut 0.039999999999999994 : comparer au strict fait
        # échouer un code juste.
        assert mot.end - mot.start >= 0.04 - 1e-9, mot


def test_le_minutage_rendu_avance_toujours(monkeypatch):
    """libass exige un minutage croissant : un sous-titre qui recule casse le
    surlignage au mot. La répartition le garantit ; ce test le retiendra si
    elle change."""
    mots = _contigus(["un", "deux", "trois", "quatre"])
    monkeypatch.setattr(calage, "pauses_du_son",
                        lambda a: ([(0.0, 0.1), (1.0, 1.4), (2.6, 2.8)], 2.8))
    cales = calage.caler(mots, "x.mp3")
    for precedent, suivant in zip(cales, cales[1:]):
        assert suivant.start >= precedent.end, (precedent, suivant)
        assert suivant.end > suivant.start


def test_un_seul_mot_ne_declenche_rien():
    assert calage.caler([Word(text="Bonjour", start=0.0, end=1.0)], "x.mp3") \
        == [Word(text="Bonjour", start=0.0, end=1.0)]


# --- Lecture de la sortie de ffmpeg ----------------------------------------
def test_un_silence_final_sans_fin_est_refermé(monkeypatch):
    """ffmpeg n'écrit pas `silence_end` quand le fichier finit dans le
    silence : sans ce rattrapage, la dernière pause serait perdue."""
    from flambee import calage as module

    sortie = ("[silencedetect] silence_start: 0\n"
              "[silencedetect] silence_end: 0.21 | silence_duration: 0.21\n"
              "[silencedetect] silence_start: 4.8\n"
              "size=N/A time=00:00:05.30 bitrate=N/A speed=1x\n")

    class Faux:
        stderr = sortie

    # `pauses_du_son` importe `run` au moment de l'appel : c'est le module
    # media qu'il faut remplacer, pas une référence copiée.
    import flambee.media as media
    monkeypatch.setattr(media, "run", lambda *a, **k: Faux())
    silences, duree = module.pauses_du_son("x.mp3")
    assert duree == 5.30
    assert silences[-1] == (4.8, 5.30)
