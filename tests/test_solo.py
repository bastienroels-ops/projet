"""Mode une seule vidéo : un lien suffit, et c'est un parcours complet.

Avec une seule source, Flambée ne monte rien : la vidéo garde son son, et ses
paroles — écoutées puis corrigées — donnent les sous-titres. À partir de deux
sources, le montage reste tel qu'il était.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from flambee import app as app_module  # noqa: E402
from flambee import (config, downloader, media, pipeline, solo,  # noqa: E402
                     transcribe)
from flambee.downloader import Source, validate_urls  # noqa: E402
from flambee.project import Project, store  # noqa: E402
from flambee.voice import Word  # noqa: E402

needs_ffmpeg = pytest.mark.skipif(bool(media.ensure_tools()),
                                  reason="ffmpeg/ffprobe requis")


def mots(*paires):
    return [Word(text=t, start=d, end=f) for t, d, f in paires]


# --- Les bornes -------------------------------------------------------------
def test_un_seul_lien_est_valide():
    validate_urls(["https://a.test/1"])
    validate_urls([f"https://a.test/{i}" for i in range(5)])


def test_zero_lien_et_six_liens_restent_refuses():
    with pytest.raises(downloader.DownloadError):
        validate_urls([])
    with pytest.raises(downloader.DownloadError):
        validate_urls([f"https://a.test/{i}" for i in range(6)])


def test_une_video_courte_n_est_pas_un_probleme_quand_elle_est_seule():
    source = Source(index=1, url="x", path="/tmp/x.mp4", duration=12.0,
                    width=1080, height=1920)
    assert downloader._check_source(source)                  # montage : signalé
    assert not downloader._check_source(source, solo=True)   # seule : rien à dire


def test_une_video_horizontale_reste_signalee_meme_seule():
    source = Source(index=1, url="x", path="/tmp/x.mp4", duration=60.0,
                    width=1920, height=1080)
    assert any("horizontale" in m
               for m in downloader._check_source(source, solo=True))


# --- Réaligner un texte corrigé --------------------------------------------
ORIGINE = mots(("Salut", 0.0, 0.4), ("les", 0.5, 0.7), ("amis", 0.8, 1.2),
               ("bienvenue", 1.5, 2.1), ("ici", 2.2, 2.5))


def test_un_texte_inchange_garde_son_minutage():
    nouveau = solo.realigner(ORIGINE, "Salut les amis bienvenue ici", 3.0)
    assert [(m.text, m.start, m.end) for m in nouveau] == \
        [(m.text, m.start, m.end) for m in ORIGINE]


def test_la_casse_et_la_ponctuation_ne_changent_pas_le_minutage():
    nouveau = solo.realigner(ORIGINE, "Salut, les amis ! Bienvenue ici.", 3.0)
    # « ! » est un mot de plus : les autres gardent leur place.
    par_texte = {m.text: m for m in nouveau}
    assert par_texte["Bienvenue"].start == 1.5
    assert par_texte["ici."].end == 2.5
    assert par_texte["amis"].start == 0.8


def test_un_mot_corrige_prend_la_place_de_l_ancien():
    nouveau = solo.realigner(ORIGINE, "Salut les copains bienvenue ici", 3.0)
    copains = nouveau[2]
    assert copains.text == "copains"
    assert (copains.start, copains.end) == (0.8, 1.2)
    assert len(nouveau) == 5


def test_plusieurs_mots_se_partagent_la_place_d_un_seul():
    nouveau = solo.realigner(ORIGINE, "Salut les très bons copains bienvenue ici",
                             3.0)
    milieu = nouveau[2:5]
    assert [m.text for m in milieu] == ["très", "bons", "copains"]
    # Trois mots sur l'intervalle de « amis » (0,8 → 1,2), au prorata de leur
    # longueur, sans trou ni chevauchement.
    assert milieu[0].start == pytest.approx(0.8)
    assert milieu[-1].end == pytest.approx(1.2)
    assert milieu[0].end == pytest.approx(milieu[1].start)
    assert milieu[1].end == pytest.approx(milieu[2].start)
    # Ce qui n'a pas changé garde sa place exacte.
    assert nouveau[5].start == 1.5


def test_un_mot_supprime_disparait():
    nouveau = solo.realigner(ORIGINE, "Salut amis bienvenue ici", 3.0)
    assert [m.text for m in nouveau] == ["Salut", "amis", "bienvenue", "ici"]


def test_un_mot_ajoute_prend_l_intervalle_libre_entre_ses_voisins():
    nouveau = solo.realigner(ORIGINE, "Salut les amis et bienvenue ici", 3.0)
    et = next(m for m in nouveau if m.text == "et")
    assert 1.2 <= et.start and et.end <= 1.5 + 1e-6


def test_le_resultat_est_toujours_dans_l_ordre_et_lisible():
    serre = mots(("a", 0.0, 0.1), ("b", 0.1, 0.2))
    nouveau = solo.realigner(serre, "a x y z b", 1.0)
    for avant, apres in zip(nouveau, nouveau[1:]):
        assert apres.start >= avant.end
    assert all(m.end - m.start >= solo.MIN_MOT - 1e-9 for m in nouveau)


def test_sans_paroles_le_texte_est_reparti_sur_toute_la_video():
    nouveau = solo.realigner([], "un deux trois quatre", 8.0)
    assert nouveau[0].start == 0.0
    assert nouveau[-1].end == pytest.approx(8.0)
    assert [m.text for m in nouveau] == ["un", "deux", "trois", "quatre"]


def test_un_texte_vide_supprime_les_sous_titres():
    assert solo.realigner(ORIGINE, "   ", 3.0) == []


# --- Écouter la vidéo -------------------------------------------------------
def test_une_video_muette_n_a_rien_a_sous_titrer(tmp_path):
    assert solo.paroles_de("/tmp/x.mp4", tmp_path, has_audio=False) == []


def test_sans_moteur_l_erreur_dit_quoi_faire(monkeypatch, tmp_path):
    monkeypatch.setattr(transcribe, "available", lambda: False)
    monkeypatch.setattr(transcribe, "raison_indisponible", lambda: "absent")
    with pytest.raises(transcribe.TranscriptionError, match="à la main"):
        solo.paroles_de("/tmp/x.mp4", tmp_path)


# --- Le cadrage -------------------------------------------------------------
def test_le_cadrage_par_defaut_est_inchange():
    filtre = media.vertical_filter(width=1080, height=1920)
    assert "crop=1080:1920," in filtre + ","
    assert "x='" not in filtre


def test_le_point_de_cadrage_decale_le_recadrage():
    filtre = media.vertical_filter(width=1080, height=1920, focus_x=0.0)
    assert "crop=1080:1920:x='(iw-ow)*0.000'" in filtre


def test_la_video_entiere_passe_sur_un_fond_flou():
    filtre = media.vertical_filter(width=1080, height=1920, framing="fit",
                                   tag="t")
    assert "overlay=(W-w)/2:(H-h)/2" in filtre
    assert "force_original_aspect_ratio=decrease" in filtre


@needs_ffmpeg
@pytest.mark.parametrize("framing,focus", [("fill", 0.0), ("fill", 1.0),
                                           ("fit", 0.5)])
def test_ffmpeg_accepte_chaque_cadrage(tmp_path, framing, focus):
    """Une vidéo horizontale sort en 9:16, quel que soit le cadrage."""
    source = tmp_path / "large.mp4"
    media.ffmpeg(["-f", "lavfi", "-i", "testsrc=size=640x360:rate=30:duration=1",
                  "-pix_fmt", "yuv420p", str(source)])
    sortie = tmp_path / f"{framing}.mp4"
    filtre = media.vertical_filter(width=270, height=480, fps=30,
                                   framing=framing, focus_x=focus, tag="z")
    media.ffmpeg(["-i", str(source), "-filter_complex", f"[0:v]{filtre}[v]",
                  "-map", "[v]", "-pix_fmt", "yuv420p", str(sortie)])
    info = media.probe(sortie)
    assert (info.width, info.height) == (270, 480)


@needs_ffmpeg
def test_le_cadrage_avec_masque_reste_un_graphe_valide(tmp_path):
    source = tmp_path / "large.mp4"
    media.ffmpeg(["-f", "lavfi", "-i", "testsrc=size=640x360:rate=30:duration=1",
                  "-pix_fmt", "yuv420p", str(source)])
    sortie = tmp_path / "masque.mp4"
    filtre = media.vertical_filter(width=270, height=480, fps=30, framing="fit",
                                   mask="blur", tag="q")
    media.ffmpeg(["-i", str(source), "-filter_complex", f"[0:v]{filtre}[v]",
                  "-map", "[v]", "-pix_fmt", "yuv420p", str(sortie)])
    assert media.probe(sortie).height == 480


# --- Le parcours par l'API --------------------------------------------------
@pytest.fixture()
def une_video(monkeypatch):
    """Seul le réseau et ffmpeg sont simulés : `run_download` est le vrai."""

    def fake_download_all(urls, dest_dir, *, on_progress=None, cancel=None,
                          parallel=True):
        return [Source(index=i, url=u, path=f"/tmp/source_{i}.mp4", title=f"V{i}",
                       duration=30.0, width=1080, height=1920, has_audio=True)
                for i, u in enumerate(urls, start=1)]

    monkeypatch.setattr(downloader, "download_all", fake_download_all)
    monkeypatch.setattr(pipeline.trimmer, "extract_hooks",
                        lambda sources, out_dir: sources)
    monkeypatch.setattr(pipeline.analyzer, "analyze_all", lambda sources: {})
    monkeypatch.setattr(app_module, "_spawn", lambda cible, *a: cible(*a))


def test_un_lien_donne_un_parcours_complet(compte, une_video):
    identifiant = compte.post("/api/projects").json()["id"]
    corps = compte.post(f"/api/projects/{identifiant}/sources",
                        json={"urls": "https://www.tiktok.com/@a/video/1"})
    assert corps.status_code == 200
    projet = corps.json()
    assert projet["solo"] is True
    assert projet["hook_index"] == 1               # pas d'étape Accroche
    assert projet["step"] >= 3                     # on va droit au style
    assert projet["estimated_duration"] == 30.0    # la durée de la vidéo
    assert not projet["sources"][0]["warnings"]


def test_plusieurs_liens_restent_un_montage(compte, une_video):
    identifiant = compte.post("/api/projects").json()["id"]
    projet = compte.post(f"/api/projects/{identifiant}/sources", json={
        "urls": "https://a.test/1\nhttps://b.test/2"}).json()
    assert projet["solo"] is False
    assert projet["step"] == 2                     # l'étape Accroche demeure


def test_le_rendu_d_une_video_seule_ne_demande_pas_de_script(
        compte, une_video, monkeypatch):
    identifiant = compte.post("/api/projects").json()["id"]
    compte.post(f"/api/projects/{identifiant}/sources",
                json={"urls": "https://a.test/1"})
    lances = []
    monkeypatch.setattr(
        app_module.pipeline, "run_render",
        lambda projet, *, fast=False: (lances.append(projet.id),
                                       projet.set_job("render", "done")))
    reponse = compte.post(f"/api/projects/{identifiant}/render",
                          json={"fast": True})
    assert reponse.status_code == 200 and lances == [identifiant]


def test_le_rendu_d_un_montage_demande_toujours_un_script(compte, une_video):
    identifiant = compte.post("/api/projects").json()["id"]
    compte.post(f"/api/projects/{identifiant}/sources",
                json={"urls": "https://a.test/1\nhttps://b.test/2"})
    assert compte.post(f"/api/projects/{identifiant}/render").status_code == 400


def test_les_options_suivent_le_mode(compte, une_video):
    """Seule, la vidéo garde son son et ne tremble pas ; à plusieurs, les
    réglages d'un montage reviennent — sans écraser un choix qui n'a pas à
    l'être."""
    identifiant = compte.post("/api/projects").json()["id"]
    seule = compte.post(f"/api/projects/{identifiant}/sources",
                        json={"urls": "https://a.test/1"}).json()["settings"]
    assert seule["motion"] is False and seule["scene_aware"] is False
    assert seule["keep_source_audio"] is True

    plusieurs = compte.post(f"/api/projects/{identifiant}/sources", json={
        "urls": "https://a.test/1\nhttps://b.test/2"}).json()["settings"]
    assert plusieurs["motion"] is True and plusieurs["scene_aware"] is True
    assert plusieurs["keep_source_audio"] is True   # le son d'origine reste, partout


def test_le_texte_corrige_est_realigne_et_enregistre(compte, une_video):
    identifiant = compte.post("/api/projects").json()["id"]
    compte.post(f"/api/projects/{identifiant}/sources",
                json={"urls": "https://a.test/1"})
    projet = store.get(identifiant, owner=1)
    projet.transcript_words = [m.to_dict() for m in ORIGINE]
    projet.transcript_done = True
    projet.save()

    corps = compte.post(f"/api/projects/{identifiant}/transcription/texte",
                        json={"texte": "Salut les copains bienvenue ici"}).json()
    assert corps["transcript_text"] == "Salut les copains bienvenue ici"
    assert corps["step"] == 5
    assert [m["start"] for m in corps["transcript_words"]][2] == 0.8


def test_le_texte_ne_se_corrige_pas_sur_un_montage(compte, une_video):
    identifiant = compte.post("/api/projects").json()["id"]
    compte.post(f"/api/projects/{identifiant}/sources",
                json={"urls": "https://a.test/1\nhttps://b.test/2"})
    assert compte.post(f"/api/projects/{identifiant}/transcription/texte",
                       json={"texte": "x"}).status_code == 400
    assert compte.post(
        f"/api/projects/{identifiant}/transcription").status_code == 400


def test_la_transcription_est_lancee_en_tache_de_fond(
        compte, une_video, monkeypatch):
    identifiant = compte.post("/api/projects").json()["id"]
    compte.post(f"/api/projects/{identifiant}/sources",
                json={"urls": "https://a.test/1"})
    monkeypatch.setattr(transcribe, "available", lambda: True)
    monkeypatch.setattr(solo, "paroles_de", lambda *a, **k: ORIGINE)

    corps = compte.post(f"/api/projects/{identifiant}/transcription").json()
    assert corps["job"]["name"] == "transcribe" and corps["job"]["state"] == "done"
    assert corps["transcript_text"] == "Salut les amis bienvenue ici"


def test_sans_moteur_la_transcription_dit_pourquoi(compte, une_video, monkeypatch):
    identifiant = compte.post("/api/projects").json()["id"]
    compte.post(f"/api/projects/{identifiant}/sources",
                json={"urls": "https://a.test/1"})
    monkeypatch.setattr(transcribe, "available", lambda: False)
    monkeypatch.setattr(transcribe, "raison_indisponible", lambda: "absent")
    reponse = compte.post(f"/api/projects/{identifiant}/transcription")
    assert reponse.status_code == 503 and "à la main" in reponse.json()["detail"]


def test_le_cadrage_est_borne_par_le_serveur(compte, une_video):
    identifiant = compte.post("/api/projects").json()["id"]
    corps = compte.post(f"/api/projects/{identifiant}/settings", json={
        "framing": "zzz", "focus_x": 7}).json()
    assert corps["settings"]["framing"] == "fill"
    assert corps["settings"]["focus_x"] == 1.0


def test_une_variante_garde_les_paroles(compte, une_video, tmp_path):
    identifiant = compte.post("/api/projects").json()["id"]
    compte.post(f"/api/projects/{identifiant}/sources",
                json={"urls": "https://a.test/1"})
    projet = store.get(identifiant, owner=1)
    projet.transcript_words = [m.to_dict() for m in ORIGINE]
    projet.transcript_done = True
    neuf = projet.variante()
    assert neuf.transcript_done and len(neuf.transcript_words) == 5
    assert neuf.solo


# --- Le rendu, pour de vrai -------------------------------------------------
def _projet_avec_une_video(tmp_path, *, son=True, secondes=4,
                           taille="640x360") -> Project:
    projet = store.create(owner=0)
    projet.ensure_dirs()
    chemin = projet.sources_dir / "source_01.mp4"
    entrees = ["-f", "lavfi", "-i",
               f"testsrc=size={taille}:rate=30:duration={secondes}"]
    if son:
        entrees += ["-f", "lavfi", "-i",
                    f"sine=frequency=440:duration={secondes}"]
    media.ffmpeg([*entrees, "-c:v", "libx264", "-preset", "ultrafast",
                  "-pix_fmt", "yuv420p", *(["-c:a", "aac"] if son else []),
                  str(chemin)])
    info = media.probe(chemin)
    projet.sources = [Source(index=1, url="x", path=str(chemin), title="Ma vidéo",
                             duration=info.duration, width=info.width,
                             height=info.height, has_audio=info.has_audio)]
    projet.hook_index = 1
    projet.settings = config.RenderSettings(
        subtitles=True, motion=False, keep_source_audio=True, framing="fit")
    return projet


@needs_ffmpeg
def test_rendu_d_une_video_seule_avec_sous_titres_et_son(espace, tmp_path):
    """Un lien, aucun script : la vidéo ressort en 9:16, sous-titrée, avec son
    son d'origine."""
    projet = _projet_avec_une_video(tmp_path)
    projet.transcript_words = [m.to_dict() for m in mots(
        ("Bonjour", 0.2, 0.7), ("à", 0.8, 0.9), ("tous", 1.0, 1.5))]
    projet.transcript_done = True

    pipeline.run_render(projet, fast=True)

    assert projet.job.state == "done", projet.job.error
    assert projet.subtitle_path and Path(projet.subtitle_path).exists()
    sortie = media.probe(projet.preview_path)
    assert (sortie.width, sortie.height) == (config.PREVIEW_FORMAT.width,
                                             config.PREVIEW_FORMAT.height)
    assert sortie.has_audio
    assert sortie.duration == pytest.approx(4, abs=0.5)     # entière, sans coupe
    assert len(projet.segments) == 1 and projet.segments[0].start == 0.0


@needs_ffmpeg
def test_rendu_d_une_video_sans_son_ni_parole(espace, tmp_path):
    """Rien n'est dit : pas de sous-titres, et le rendu aboutit quand même."""
    projet = _projet_avec_une_video(tmp_path, son=False, taille="360x640")
    pipeline.run_render(projet, fast=True)      # écoute → vidéo muette → aucun mot
    assert projet.job.state == "done", projet.job.error
    assert projet.transcript_done and projet.subtitle_path == ""


@needs_ffmpeg
def test_le_rendu_ecoute_la_video_si_l_etape_texte_a_ete_sautee(
        espace, tmp_path, monkeypatch):
    projet = _projet_avec_une_video(tmp_path)
    appels = []

    def ecoute(*a, **k):
        appels.append(1)
        return mots(("Coucou", 0.3, 0.8))

    monkeypatch.setattr(solo, "paroles_de", ecoute)
    pipeline.run_render(projet, fast=True)
    assert projet.job.state == "done", projet.job.error
    assert appels == [1] and projet.transcript_done
    assert Path(projet.subtitle_path).exists()


@needs_ffmpeg
def test_les_sous_titres_sans_moteur_font_echouer_le_rendu_clairement(
        espace, tmp_path, monkeypatch):
    """Sous-titres demandés, texte jamais écrit, aucun moteur : on le dit,
    plutôt que de livrer une vidéo sans ce qu'on avait demandé."""
    projet = _projet_avec_une_video(tmp_path)
    monkeypatch.setattr(transcribe, "available", lambda: False)
    monkeypatch.setattr(transcribe, "raison_indisponible", lambda: "absent")
    pipeline.run_render(projet, fast=True)
    assert projet.job.state == "error" and "à la main" in projet.job.error


@needs_ffmpeg
def test_un_projet_a_deux_videos_est_toujours_monte(espace, tmp_path):
    """Le montage n'a pas bougé : deux sources, un script, une voix."""
    projet = _projet_avec_une_video(tmp_path)
    assert projet.solo
    second = projet.sources_dir / "source_02.mp4"
    second.write_bytes(Path(projet.sources[0].path).read_bytes())
    projet.sources.append(Source(index=2, url="y", path=str(second),
                                 duration=4.0, width=640, height=360))
    assert not projet.solo
    pipeline.run_render(projet, fast=True)
    assert projet.job.state == "error" and "script" in projet.job.error.lower()
