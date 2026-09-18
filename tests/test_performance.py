"""Ce qui rend le rendu plus court, et ce qui doit le rester.

Profilé : sur un rendu 1080×1920, l'encodage pèse 99 % du temps et la voix,
qui n'est plus en cache, plus du quart de l'aperçu. D'où deux leviers — le
preset d'encodage, mesuré, et la voix fabriquée d'avance.

Ces tests ne mesurent pas des secondes : un banc de vitesse dans une suite de
tests ment dès qu'une machine est chargée. Ils tiennent les propriétés qui,
elles, décident du résultat.
"""

from __future__ import annotations

import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from flambee import config, media, pipeline, voice  # noqa: E402
from flambee.project import store  # noqa: E402


# --- L'encodeur ------------------------------------------------------------
def test_le_preset_d_encodage_reste_celui_qui_a_ete_mesure():
    """`veryfast -crf 18` contre `fast -crf 20` : 2,16 s au lieu de 3,40 sur
    six secondes en 1080×1920, pour un SSIM de 0,99646 contre 0,99637.

    Ce test ne rejuge pas ce choix — il empêche qu'on revienne à un preset
    lent sans refaire la mesure."""
    args = media._LIBX264.args()
    assert "-preset" in args
    preset = args[args.index("-preset") + 1]
    lents = ("medium", "slow", "slower", "veryslow", "placebo")
    assert preset not in lents, (
        f"preset {preset!r} : plus lent que ce qui a été mesuré, "
        "refais le banc avant de le figer")


def test_l_apercu_encode_au_moins_aussi_vite_que_le_rendu():
    """Il est là pour être vu tout de suite : il ne peut pas coûter plus cher
    que le rendu définitif."""
    ordre = ["ultrafast", "superfast", "veryfast", "faster", "fast",
             "medium", "slow", "slower", "veryslow"]
    rapide = media._LIBX264.args(fast=True)
    qualite = media._LIBX264.args()
    assert ordre.index(rapide[rapide.index("-preset") + 1]) <= \
        ordre.index(qualite[qualite.index("-preset") + 1])


# --- La voix préparée d'avance ---------------------------------------------
def _projet(espace):
    projet = store.create(owner=0)
    projet.ensure_dirs()
    projet.script = "Voici l'astuce que personne ne connaît."
    projet.settings = config.RenderSettings()
    projet.save()
    return projet


def test_la_preparation_ne_touche_pas_a_l_etat_de_la_tache(espace, monkeypatch):
    """Elle tourne pendant que l'utilisateur lit son récapitulatif, sur une
    page où aucune barre de progression n'a de sens. Si elle écrivait dans
    l'état, le navigateur croirait un rendu en cours et se mettrait à sonder.
    """
    projet = _projet(espace)
    avant = (projet.job.name, projet.job.state, projet.job.message)

    monkeypatch.setattr(pipeline.voice, "synthesize", _fausse_synthese(projet))
    monkeypatch.setattr(pipeline.calage, "caler", lambda mots, audio: mots)
    pipeline.prechauffer_la_voix(projet)

    assert (projet.job.name, projet.job.state, projet.job.message) == avant
    assert projet.voice_words, "la voix aurait dû être mise en cache"


def test_un_echec_de_preparation_ne_remonte_pas(espace, monkeypatch):
    """Le rendu la refera et signalera l'erreur à un moment où l'utilisateur
    attend une réponse. En fond, il n'y a personne pour la lire."""
    projet = _projet(espace)

    def echouer(*a, **k):
        raise voice.VoiceError("réseau coupé")

    monkeypatch.setattr(pipeline.voice, "synthesize", echouer)
    pipeline.prechauffer_la_voix(projet)          # ne doit rien lever
    assert projet.job.state != "error"


def test_la_voix_importee_n_est_pas_preparee(espace, monkeypatch):
    """Il n'y a rien à synthétiser : le fichier existe déjà."""
    projet = _projet(espace)
    projet.settings = config.RenderSettings(
        voice=pipeline.voicestudio.VOICE_ID)
    appels = []
    monkeypatch.setattr(pipeline.voice, "synthesize",
                        lambda *a, **k: appels.append(1))
    pipeline.prechauffer_la_voix(projet)
    assert appels == []


def test_un_script_vide_ne_declenche_rien(espace, monkeypatch):
    projet = _projet(espace)
    projet.script = "   "
    appels = []
    monkeypatch.setattr(pipeline.voice, "synthesize",
                        lambda *a, **k: appels.append(1))
    pipeline.prechauffer_la_voix(projet)
    assert appels == []


def test_deux_demandes_simultanees_ne_synthetisent_qu_une_fois(espace, monkeypatch):
    """Un rendu lancé pendant la préparation doit l'attendre, pas la refaire :
    deux appels réseau et deux écritures dans le même fichier donneraient le
    minutage de celui qui finit second."""
    projet = _projet(espace)
    appels = []
    depart = threading.Barrier(2)

    def lente(texte, chemin, **kw):
        appels.append(1)
        piste = _fausse_synthese(projet)(texte, chemin, **kw)
        return piste

    monkeypatch.setattr(pipeline.voice, "synthesize", lente)
    monkeypatch.setattr(pipeline.calage, "caler", lambda mots, audio: mots)

    def travailler():
        depart.wait()
        pipeline._voice_track(projet, silencieux=True)

    fils = [threading.Thread(target=travailler) for _ in range(2)]
    for f in fils:
        f.start()
    for f in fils:
        f.join(timeout=20)

    assert len(appels) == 1, f"{len(appels)} synthèses au lieu d'une"


def test_le_rendu_reutilise_la_voix_preparee(espace, monkeypatch):
    projet = _projet(espace)
    appels = []

    def compter(texte, chemin, **kw):
        appels.append(1)
        return _fausse_synthese(projet)(texte, chemin, **kw)

    monkeypatch.setattr(pipeline.voice, "synthesize", compter)
    monkeypatch.setattr(pipeline.calage, "caler", lambda mots, audio: mots)

    pipeline.prechauffer_la_voix(projet)
    pipeline._voice_track(projet)                 # ce que fait le rendu
    assert len(appels) == 1, "la voix a été refaite alors qu'elle était prête"


def _fausse_synthese(projet):
    """Écrit un fichier plausible et rend un minutage, sans réseau."""
    def synthetiser(texte, chemin, **kw):
        Path(chemin).parent.mkdir(parents=True, exist_ok=True)
        Path(chemin).write_bytes(b"ID3" + b"\0" * 2048)
        mots = [voice.Word(text=m, start=i * 0.4, end=i * 0.4 + 0.35)
                for i, m in enumerate(texte.split())]
        return voice.VoiceTrack(path=str(chemin), duration=len(mots) * 0.4,
                                words=mots, voice=kw.get("voice", "x"),
                                lead_in=0.2)
    return synthetiser
