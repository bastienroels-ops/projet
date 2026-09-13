"""Ce que chaque formule ouvre — et refuse.

La page Tarifs promettait des différences qui n'existaient nulle part dans le
code. Ces tests tiennent lieu de contrat : si une promesse cesse d'être vraie,
ils le disent.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from flambee import account, config, users  # noqa: E402
from flambee.project import store  # noqa: E402


def _u(plan: str) -> users.Utilisateur:
    return users.Utilisateur(id=1, email="a@exemple.fr", plan=plan)


# --- Règles ----------------------------------------------------------------
def test_l_essai_n_ouvre_que_deux_styles():
    ouverts = account.styles_autorises(_u("essai"))
    assert len(ouverts) == 2
    assert set(ouverts) <= set(config.SUBTITLE_PRESETS)
    for plan in ("createur", "studio"):
        assert len(account.styles_autorises(_u(plan))) == len(config.SUBTITLE_PRESETS)


def test_le_filigrane_ne_concerne_que_l_essai():
    assert account.filigrane(_u("essai"))
    assert not account.filigrane(_u("createur"))
    assert not account.filigrane(_u("studio"))


def test_les_voix_et_le_debit_suivent_la_formule():
    assert account.nombre_de_voix(_u("essai")) == 2
    assert account.nombre_de_voix(_u("createur")) is None
    assert not account.debit_reglable(_u("essai"))
    assert account.debit_reglable(_u("createur"))


def test_la_conservation_des_projets_suit_la_formule():
    assert account.retention_jours(_u("essai")) == 7
    assert account.retention_jours(_u("createur")) == 90
    assert account.retention_jours(_u("studio")) is None


# --- Application -----------------------------------------------------------
def _projet(client):
    return client.post("/api/projects").json()["id"]


def _reglages(preset="punch", **extra):
    base = {
        "voice": config.DEFAULT_VOICE, "voice_rate": "+0%", "voice_pitch": "+0Hz",
        "music": None, "music_volume": 0.12, "subtitles": True,
        "subtitle_preset": preset, "mask_source_subtitles": False,
        "mask_mode": "blur", "mask_height_ratio": 0.18, "motion": False,
        "source_audio_volume": 0.0,
    }
    base.update(extra)
    return base


def test_un_style_reserve_est_refuse_a_l_essai(compte):
    """Le contrôle est côté serveur : l'interface seule ne protège rien."""
    reserve = next(nom for nom in config.SUBTITLE_PRESETS
                   if nom not in account.STYLES_ESSAI)
    projet = _projet(compte)
    reponse = compte.post(f"/api/projects/{projet}/settings",
                          json=_reglages(preset=reserve))
    assert reponse.status_code == 402
    assert "Créateur" in reponse.json()["detail"]

    # …et un style ouvert passe.
    ouvert = account.STYLES_ESSAI[0]
    assert compte.post(f"/api/projects/{projet}/settings",
                       json=_reglages(preset=ouvert)).status_code == 200


def test_un_style_reserve_passe_en_createur(compte_pro):
    reserve = next(nom for nom in config.SUBTITLE_PRESETS
                   if nom not in account.STYLES_ESSAI)
    projet = _projet(compte_pro)
    assert compte_pro.post(f"/api/projects/{projet}/settings",
                           json=_reglages(preset=reserve)).status_code == 200


def test_le_debit_choisi_est_ignore_en_essai(compte):
    projet = _projet(compte)
    compte.post(f"/api/projects/{projet}/settings",
                json=_reglages(preset=account.STYLES_ESSAI[0], voice_rate="+20%"))
    reglages = compte.get(f"/api/projects/{projet}").json()["settings"]
    assert reglages["voice_rate"] == config.RenderSettings().voice_rate


def test_la_liste_des_styles_marque_ce_qui_est_reserve(compte):
    donnees = compte.get("/api/presets").json()
    verrouilles = [p["id"] for p in donnees["subtitles"] if p["verrouille"]]
    ouverts = [p["id"] for p in donnees["subtitles"] if not p["verrouille"]]
    assert len(ouverts) == 2 and len(verrouilles) == 4
    assert donnees["debit_reglable"] is False
    # Les styles réservés restent listés : on choisit mal ce qu'on ne voit pas.
    assert len(donnees["subtitles"]) == len(config.SUBTITLE_PRESETS)


def test_l_essai_ne_se_voit_proposer_que_deux_voix(compte):
    donnees = compte.get("/api/voices").json()
    assert len(donnees["voices"]) == 2
    assert donnees["toutes"] is False


def test_le_createur_a_toutes_les_voix(compte_pro):
    donnees = compte_pro.get("/api/voices").json()
    assert len(donnees["voices"]) == len(config.FRENCH_VOICES)
    assert donnees["toutes"] is True


# --- Filigrane -------------------------------------------------------------
def test_le_filigrane_entre_dans_le_graphe_ffmpeg():
    """Un filigrane décidé mais jamais incrusté ne vaut rien : on vérifie
    qu'il atteint bien le filtre, et après les sous-titres."""
    from flambee import assembler
    from flambee.downloader import Source
    from flambee.trimmer import Segment

    source = Source(index=0, url="x", path="/tmp/x.mp4", title="x")
    source.duration, source.has_audio = 4.0, False
    segments = [Segment(source_index=0, start=0.0, duration=3.0)]
    reglages = config.RenderSettings(subtitles=False, music=None, motion=False)

    avec = assembler.build_graph(
        segments, {0: source}, voice_path=None, subtitle_path=None,
        music_path=None, settings=reglages, duration=3.0, fmt=config.FORMAT,
        watermark="Flambée")
    sans = assembler.build_graph(
        segments, {0: source}, voice_path=None, subtitle_path=None,
        music_path=None, settings=reglages, duration=3.0, fmt=config.FORMAT,
        watermark="")

    graphe_avec = ";".join(avec.filters)
    assert "drawtext" in graphe_avec and "Flamb" in graphe_avec
    assert "drawtext" not in ";".join(sans.filters)
    assert avec.video_label == "vmark"


def test_le_filigrane_est_pose_au_dessus_des_sous_titres(tmp_path):
    """Recouvert par un sous-titre, il ne remplirait pas son office."""
    from flambee import assembler
    from flambee.downloader import Source
    from flambee.trimmer import Segment

    ass = tmp_path / "s.ass"
    ass.write_text("[Script Info]\n")
    source = Source(index=0, url="x", path="/tmp/x.mp4", title="x")
    source.duration, source.has_audio = 4.0, False
    graphe = assembler.build_graph(
        [Segment(source_index=0, start=0.0, duration=3.0)], {0: source},
        voice_path=None, subtitle_path=ass, music_path=None,
        settings=config.RenderSettings(subtitles=True, music=None, motion=False),
        duration=3.0, fmt=config.FORMAT, watermark="Flambée")
    texte = ";".join(graphe.filters)
    assert texte.index("subtitles=") < texte.index("drawtext")


def test_le_texte_du_filigrane_est_echappe_pour_ffmpeg():
    """Deux-points et apostrophes séparent les arguments d'un filtre."""
    from flambee import assembler

    echappe = assembler._escape_drawtext("Essai : l'outil")
    assert "\\:" in echappe and "\\'" in echappe


def test_le_filigrane_depend_de_la_formule_du_proprietaire(espace, monkeypatch):
    from flambee import pipeline

    essai = users.creer("essai@exemple.fr", "motdepasse1")
    users.mettre_a_jour(essai.id, plan="essai")
    pro = users.creer("pro@exemple.fr", "motdepasse1")
    users.mettre_a_jour(pro.id, plan="createur")

    projet_essai = store.create(essai.id)
    projet_pro = store.create(pro.id)
    assert pipeline.filigrane_pour(projet_essai) == pipeline.FILIGRANE
    assert pipeline.filigrane_pour(projet_pro) == ""


def test_un_projet_sans_proprietaire_garde_le_filigrane(espace):
    """Mieux vaut un filigrane de trop qu'un rendu payant offert."""
    from flambee import pipeline

    projet = store.create(999_999)          # compte inexistant
    assert pipeline.filigrane_pour(projet) == pipeline.FILIGRANE


# --- Conservation ----------------------------------------------------------
def test_la_purge_efface_les_projets_trop_vieux(espace):
    import os
    import time as t

    compte = users.creer("vieux@exemple.fr", "motdepasse1")
    recent = store.create(compte.id)
    ancien = store.create(compte.id)

    # On vieillit le projet : la date doit être écrite sur le disque avant de
    # reculer celle du fichier, sinon la relecture retrouve la date du jour.
    vieux = t.time() - 40 * 86400
    ancien.created_at = vieux
    ancien.save()
    os.utime(ancien.dir / "project.json", (vieux, vieux))
    store._cache.clear()

    effaces = store.purge(compte.id, jours=30)
    assert effaces == [ancien.id]
    assert store.get(ancien.id, owner=compte.id) is None
    assert store.get(recent.id, owner=compte.id) is not None


def test_la_formule_studio_n_efface_rien(espace):
    import os
    import time as t

    compte = users.creer("studio@exemple.fr", "motdepasse1")
    projet = store.create(compte.id)
    vieux = t.time() - 3000 * 86400
    os.utime(projet.dir / "project.json", (vieux, vieux))
    store._cache.clear()

    assert store.purge(compte.id, jours=None) == []
    assert store.get(projet.id, owner=compte.id) is not None


def test_un_projet_rouvert_n_est_pas_efface(espace):
    """C'est la dernière activité qui compte, pas la date de création."""
    import time as t

    compte = users.creer("actif@exemple.fr", "motdepasse1")
    projet = store.create(compte.id)
    projet.created_at = t.time() - 300 * 86400     # créé il y a longtemps…
    projet.save()                                   # …mais modifié à l'instant
    store._cache.clear()

    assert store.purge(compte.id, jours=90) == []
    assert store.get(projet.id, owner=compte.id) is not None


def test_la_page_des_creations_applique_la_conservation(compte):
    page = compte.get("/studio/creations")
    assert page.status_code == 200
    assert "7 jours" in page.text          # formule Essai


# --- Musique ---------------------------------------------------------------
def test_la_musique_est_reservee_aux_formules_payantes(compte, compte_pro):
    ouverte = compte_pro.get("/api/music").json()
    assert ouverte["autorisee"] is True


def test_l_essai_ne_recoit_pas_la_bibliotheque_musicale(compte):
    fermee = compte.get("/api/music").json()
    assert fermee["autorisee"] is False
    assert fermee["tracks"] == []


def test_une_piste_choisie_sans_droit_est_ecartee(compte, monkeypatch):
    """Le contrôle est côté serveur, pas dans la liste déroulante."""
    from flambee import app as app_module

    monkeypatch.setattr(app_module.pipeline, "list_music",
                        lambda: [{"id": "nappe.mp3", "label": "nappe"}])
    projet = _projet(compte)
    compte.post(f"/api/projects/{projet}/settings",
                json=_reglages(preset=account.STYLES_ESSAI[0], music="nappe.mp3"))
    assert compte.get(f"/api/projects/{projet}").json()["settings"]["music"] is None


# --- La page Tarifs ne doit pas dériver -----------------------------------
def test_les_chiffres_annonces_sont_ceux_du_code():
    """Une page de tarifs qui promet ce que le produit ne fait pas est un
    mensonge vendu. Ce test lie chaque chiffre affiché à sa règle."""
    from flambee import plans

    par_id = {p.id: p for p in plans.PLANS}
    texte = {ident: " ".join(p.features + [p.note, p.videos]).lower()
             for ident, p in par_id.items()}

    assert len(account.STYLES_ESSAI) == 2, "l'Essai annonce deux styles"
    assert "deux styles" in texte["essai"]
    assert len(config.SUBTITLE_PRESETS) == 6, "le Créateur annonce six styles"
    assert "six styles" in texte["createur"]
    assert account.VOIX_ESSAI == 2 and "deux voix" in texte["essai"]
    assert len(config.FRENCH_VOICES) == 10 and "dix voix" in texte["createur"]

    assert str(account.RETENTION_JOURS["essai"]) in texte["essai"]
    assert str(account.RETENTION_JOURS["createur"]) in texte["createur"]
    assert account.RETENTION_JOURS["studio"] is None
    assert "sans limite" in texte["studio"]

    # Le filigrane n'est annoncé que là où il est appliqué.
    assert "filigrane" in texte["createur"] or "filigrane" in texte["essai"]
    assert account.filigrane(_u("essai")) and not account.filigrane(_u("createur"))


def test_la_formule_studio_n_annonce_rien_qui_n_existe_pas():
    """Les fonctions non écrites sont nommées comme telles, pas vendues."""
    from flambee import plans

    studio = next(p for p in plans.PLANS if p.id == "studio")
    promesses = " ".join(studio.features).lower()
    for absente in ("marque", "sur mesure", "lot", "api", "prioritaire"):
        assert absente not in promesses, (
            f"« {absente} » est annoncé sans être implémenté")
    # Elles peuvent figurer dans la note, à condition d'être données comme à venir.
    assert "développement" in studio.note.lower()
