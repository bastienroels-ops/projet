"""Écouter avant de choisir : les extraits de voix et de musique.

Une voix ne se choisit pas sur un prénom, ni une musique sur un nom de
fichier. Ces deux routes servent de quoi entendre — et, parce qu'elles
servent des fichiers, ce sont aussi deux portes qu'il faut tenir fermées.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from flambee import account, config, samples, voice


# --- La voix ---------------------------------------------------------------
def test_une_voix_inconnue_ne_declenche_aucune_synthese(compte_pro, monkeypatch):
    """Sans ce contrôle, n'importe quelle chaîne partirait chez edge-tts."""
    appels = []
    monkeypatch.setattr(voice, "audition", _piege(appels))
    assert compte_pro.get("/api/voices/fr-XX-Personne/sample").status_code == 404
    assert appels == []


def test_une_voix_reservee_ne_s_ecoute_pas_a_l_essai(compte, monkeypatch):
    appels = []
    monkeypatch.setattr(voice, "audition", _piege(appels))
    reservee = next(v["id"] for v in config.FRENCH_VOICES
                    if v["id"] not in account.VOIX_ESSAI)
    assert compte.get(f"/api/voices/{reservee}/sample").status_code == 402
    assert appels == []


def test_une_voix_ouverte_s_ecoute(compte, monkeypatch, tmp_path):
    extrait = tmp_path / "voix.mp3"
    extrait.write_bytes(b"ID3" + b"\0" * 400)

    async def rendre(voix):
        return extrait

    monkeypatch.setattr(voice, "audition", rendre)
    reponse = compte.get(f"/api/voices/{account.VOIX_ESSAI[0]}/sample")
    assert reponse.status_code == 200
    assert reponse.content.startswith(b"ID3")


def test_l_audition_ne_publie_pas_un_fichier_tronque(monkeypatch, espace):
    """Une synthèse interrompue laissait sinon un mp3 coupé, servi ensuite
    depuis le cache sans que rien ne le signale."""
    import asyncio

    async def synthese_qui_echoue(texte, chemin, **kw):
        Path(chemin).write_bytes(b"ID3" + b"\0" * 10)   # début d'écriture
        raise voice.VoiceError("coupure réseau")

    monkeypatch.setattr(voice, "synthesize_async", synthese_qui_echoue)
    with pytest.raises(voice.VoiceError):
        asyncio.run(voice.audition("fr-FR-DeniseNeural"))
    assert not voice.audition_path("fr-FR-DeniseNeural").exists()


def test_le_nom_de_fichier_d_audition_reste_sur_le_disque(espace):
    """Un identifiant de voix entre dans un nom de fichier : il ne doit pas
    pouvoir en sortir."""
    chemin = voice.audition_path("../../etc/passwd")
    assert chemin.parent == config.WORK_DIR / ".samples"
    assert ".." not in chemin.name


# --- La musique ------------------------------------------------------------
def test_une_musique_inconnue_est_refusee(compte_pro):
    assert compte_pro.get("/api/music/absente.mp3/sample").status_code == 404


def test_un_chemin_qui_remonte_est_refuse(compte_pro):
    """`music_path` garde la bibliothèque ; la route ne doit pas la contourner."""
    reponse = compte_pro.get("/api/music/..%2F..%2Fsecret.mp3/sample")
    assert reponse.status_code == 404


def test_l_extrait_suit_le_fichier_et_pas_seulement_son_nom():
    """Remplacer une musique sans la renommer doit regénérer l'extrait."""
    avant = samples.extrait_musique_path("fond.mp3", 1000, 111)
    apres = samples.extrait_musique_path("fond.mp3", 2000, 222)
    assert avant != apres


def test_la_duree_des_pistes_est_mise_en_cache(monkeypatch, tmp_path):
    """ffprobe coûte un processus : la bibliothèque ne doit pas en lancer un
    par piste à chaque ouverture de l'étape Style."""
    from flambee import pipeline

    piste = tmp_path / "fond.mp3"
    piste.write_bytes(b"\0" * 64)
    appels = []

    class Info:
        duration = 42.0

    def faux_probe(chemin):
        appels.append(chemin)
        return Info()

    monkeypatch.setattr(pipeline, "probe", faux_probe)
    monkeypatch.setattr(pipeline, "_DUREES_MUSIQUE", {})
    assert pipeline.duree_musique(piste) == 42.0
    assert pipeline.duree_musique(piste) == 42.0
    assert len(appels) == 1


def _piege(appels):
    async def refuser(*args, **kwargs):
        appels.append(args)
        raise AssertionError("la synthèse n'aurait pas dû être demandée")
    return refuser
