"""Tests de l'API FastAPI (sans réseau : le téléchargement est simulé)."""

from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from flambee import app as app_module  # noqa: E402
from flambee import config, media, pipeline  # noqa: E402
from flambee.downloader import Source  # noqa: E402
from flambee.project import Project  # noqa: E402


@pytest.fixture()
def fake_download(monkeypatch):
    """Remplace le téléchargement yt-dlp par deux sources factices."""

    def fake_run(project: Project, urls: list[str]) -> None:
        project.urls = urls
        project.ensure_dirs()
        project.sources = []
        for index, url in enumerate(urls, start=1):
            hook = project.hooks_dir / f"hook_{index:02d}.mp4"
            hook.write_bytes(b"fake-mp4")
            project.sources.append(Source(
                index=index, url=url, path=f"/tmp/source_{index}.mp4",
                title=f"Vidéo {index}", duration=60.0, width=1080, height=1920,
                view_count=1000 * index, hook_path=str(hook),
            ))
        project.step = 2
        project.set_job("download", "done", progress=1.0, message="ok")

    monkeypatch.setattr(app_module.pipeline, "run_download", fake_run)
    monkeypatch.setattr(
        app_module, "_spawn", lambda target, *args: target(*args)
    )


def _create(compte) -> str:
    return compte.post("/api/projects").json()["id"]


def test_health(compte):
    body = compte.get("/api/health").json()
    assert body["ffmpeg"] and "output_dir" in body


def test_page_daccueil(compte):
    page = compte.get("/")
    assert page.status_code == 200 and "Flambée" in page.text


def test_parcours_complet(compte, fake_download, monkeypatch):
    project_id = _create(compte)

    # Étape 1 : moins de 2 liens → refus
    refus = compte.post(f"/api/projects/{project_id}/sources",
                        json={"urls": "https://a.test/1"})
    assert refus.status_code == 400

    body = compte.post(f"/api/projects/{project_id}/sources", json={
        "urls": "https://a.test/1\nhttps://b.test/2"}).json()
    assert len(body["sources"]) == 2
    assert body["sources"][0]["hook_url"].endswith("/hooks/1")
    assert "path" not in body["sources"][0]

    # Étape 2
    body = compte.post(f"/api/projects/{project_id}/hook",
                       json={"hook_index": 2}).json()
    assert body["hook_index"] == 2 and body["step"] >= 3
    assert compte.post(f"/api/projects/{project_id}/hook",
                       json={"hook_index": 9}).status_code == 400
    assert compte.get(f"/api/projects/{project_id}/hooks/1").status_code == 200

    # Étape 3 : une musique inconnue est ignorée, les bornes sont appliquées
    body = compte.post(f"/api/projects/{project_id}/settings", json={
        "voice": "fr-FR-HenriNeural", "music": "inexistante.mp3",
        "music_volume": 5, "mask_source_subtitles": True, "mask_mode": "zzz",
    }).json()
    assert body["settings"]["music"] is None
    assert body["settings"]["music_volume"] == 1.0
    assert body["settings"]["mask_mode"] == "blur"
    assert body["settings"]["voice"] == "fr-FR-HenriNeural"

    # Étape 4 : mode manuel (prompt à copier) puis script validé
    prompt = compte.post(f"/api/projects/{project_id}/script/prompt",
                         json={"topic": "le sommeil"}).json()["prompt"]
    assert "le sommeil" in prompt and "accroche" in prompt.lower()
    assert compte.post(f"/api/projects/{project_id}/script/prompt",
                       json={"topic": "  "}).status_code == 400

    body = compte.post(f"/api/projects/{project_id}/script", json={
        "script": "```\nVoici le script de test.\n```", "topic": "le sommeil"}).json()
    assert body["script"] == "Voici le script de test."
    assert body["step"] == 5
    assert compte.post(f"/api/projects/{project_id}/script",
                       json={"script": "   "}).status_code == 400

    # Étape 5 : le rendu est lancé (pipeline simulé)
    rendered = {}

    def fake_render(project: Project, *, fast: bool = False) -> None:
        rendered["id"] = project.id
        rendered["fast"] = fast
        project.set_job("render", "done", progress=1.0, message="fini")

    monkeypatch.setattr(app_module.pipeline, "run_render", fake_render)
    assert compte.post(f"/api/projects/{project_id}/render",
                       json={"fast": True}).status_code == 200
    assert rendered["id"] == project_id and rendered["fast"] is True


def test_render_refuse_sans_script(compte, fake_download):
    project_id = _create(compte)
    compte.post(f"/api/projects/{project_id}/sources",
                json={"urls": "https://a.test/1\nhttps://b.test/2"})
    assert compte.post(f"/api/projects/{project_id}/render").status_code == 400


def test_projet_introuvable(compte):
    assert compte.get("/api/projects/inconnu").status_code == 404


def test_tache_concurrente_refusee(compte, monkeypatch):
    from flambee import users

    project_id = _create(compte)
    proprietaire = users.par_email("essai@exemple.fr").id
    project = app_module.store.get(project_id, owner=proprietaire)
    project.set_job("download", "running", progress=0.3)
    response = compte.post(f"/api/projects/{project_id}/sources",
                           json={"urls": "https://a.test/1\nhttps://b.test/2"})
    assert response.status_code == 409


def test_projet_persiste_sur_disque(compte):
    from flambee import users

    project_id = _create(compte)
    proprietaire = users.par_email("essai@exemple.fr").id
    app_module.store._cache.clear()
    reloaded = app_module.store.get(project_id, owner=proprietaire)
    assert reloaded is not None and reloaded.id == project_id


def test_musique_hors_bibliotheque_ignoree():
    assert pipeline.music_path("../secret.mp3") is None
    assert pipeline.music_path("") is None


def test_suppression_projet(compte):
    project_id = _create(compte)
    assert compte.delete(f"/api/projects/{project_id}").json()["deleted"] is True
    assert compte.get(f"/api/projects/{project_id}").status_code == 404


def test_liste_des_projets(compte):
    project_id = _create(compte)
    ids = [p["id"] for p in compte.get("/api/projects").json()["projects"]]
    assert project_id in ids
    assert time.time() > 0


def test_message_derreur_yt_dlp_donne_une_piste():
    from flambee.downloader import _clean_ydl_error

    message = _clean_ydl_error(
        "ERROR: [youtube] abc: Sign in to confirm you're not a bot. See https://x"
    )
    assert "FLAMBEE_COOKIES_FROM_BROWSER" in message
    assert "https://x" not in message


def test_presets_de_sous_titres(compte):
    body = compte.get("/api/presets").json()
    ids = {p["id"] for p in body["subtitles"]}
    assert {"punch", "impact", "neon", "studio", "signature", "minimal"} <= ids
    assert body["default"] in ids
    # Chaque style s'accompagne d'un nom et d'une description pour l'interface.
    for preset in body["subtitles"]:
        assert preset["label"] and preset["description"]


def test_preset_inconnu_retombe_sur_le_defaut(compte):
    project_id = _create(compte)
    body = compte.post(f"/api/projects/{project_id}/settings",
                       json={"subtitle_preset": "nimportequoi"}).json()
    assert body["settings"]["subtitle_preset"] == "punch"


def test_annulation_dune_tache(compte, monkeypatch):
    project_id = _create(compte)
    # Sans tâche en cours, rien à annuler.
    assert compte.post(f"/api/projects/{project_id}/cancel").json()["cancelled"] is False

    token = pipeline.cancel_token(project_id)
    assert compte.post(f"/api/projects/{project_id}/cancel").json()["cancelled"] is True
    assert token.is_set()
    pipeline._clear_cancel(project_id)


def test_health_annonce_lencodeur(compte):
    body = compte.get("/api/health").json()
    assert body["encoder"]
    assert isinstance(body["hardware_encoder"], bool)


def test_accroche_recommandee_exposee(compte, fake_download):
    project_id = _create(compte)
    body = compte.post(f"/api/projects/{project_id}/sources", json={
        "urls": "https://a.test/1\nhttps://b.test/2"}).json()
    assert "recommended_hook" in body


def test_telechargements_paralleles(monkeypatch, tmp_path):
    """Les téléchargements doivent se recouvrir, pas s'enchaîner."""
    import time as _time

    from flambee import downloader as dl

    def slow_download(url, dest_dir, index, cancel=None):
        _time.sleep(0.4)
        return dl.Source(index=index, url=url, path=f"/tmp/{index}.mp4", duration=60)

    monkeypatch.setattr(dl, "download_one", slow_download)
    urls = [f"https://a.test/{i}" for i in range(4)]

    started = _time.monotonic()
    sources = dl.download_all(urls, tmp_path)
    elapsed = _time.monotonic() - started

    assert [s.index for s in sources] == [1, 2, 3, 4]     # ordre préservé
    assert elapsed < 1.0, f"séquentiel ({elapsed:.1f}s pour 4 × 0,4 s)"


def test_telechargement_annule_avant_de_commencer(monkeypatch, tmp_path):
    import threading as _threading

    from flambee import downloader as dl

    monkeypatch.setattr(dl, "download_one",
                        lambda *a, **k: pytest.fail("ne doit pas être appelé"))
    token = _threading.Event()
    token.set()
    sources = dl.download_all(["https://a.test/1", "https://a.test/2"], tmp_path,
                              cancel=token)
    assert all(s.error for s in sources)


# --- Import de fichiers ---------------------------------------------------
def _tiny_video(path: Path, seconds: int = 3) -> bytes:
    from flambee import media

    media.ffmpeg([
        "-f", "lavfi", "-i", f"testsrc=size=320x568:rate=15:duration={seconds}",
        "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", str(path),
    ])
    return path.read_bytes()


@pytest.fixture()
def sync_spawn(monkeypatch):
    monkeypatch.setattr(app_module, "_spawn", lambda target, *args: target(*args))


def test_import_de_fichiers(compte, sync_spawn, tmp_path):
    """Une vidéo envoyée depuis l'appareil suit le même chemin qu'un téléchargement."""
    if media.ensure_tools():
        pytest.skip("ffmpeg requis")
    payload = _tiny_video(tmp_path / "clip.mp4")
    project_id = _create(compte)

    body = compte.post(
        f"/api/projects/{project_id}/uploads",
        files=[("files", ("ma video.mp4", payload, "video/mp4")),
               ("files", ("autre.mov", payload, "video/quicktime"))],
    ).json()

    assert len(body["sources"]) == 2
    assert body["sources"][0]["title"] == "ma video"
    assert body["sources"][0]["duration"] > 0
    assert body["sources"][0]["hook_url"]          # accroche extraite
    assert body["step"] >= 2


def test_import_refuse_les_formats_inconnus(compte, tmp_path):
    project_id = _create(compte)
    response = compte.post(
        f"/api/projects/{project_id}/uploads",
        files=[("files", ("script.txt", b"pas une video", "text/plain"))],
    )
    assert response.status_code == 400
    assert "Format non reconnu" in response.json()["detail"]


def test_import_refuse_les_fichiers_trop_gros(compte, monkeypatch, tmp_path):
    monkeypatch.setattr(app_module.config, "MAX_UPLOAD_BYTES", 1024)
    project_id = _create(compte)
    response = compte.post(
        f"/api/projects/{project_id}/uploads",
        files=[("files", ("gros.mp4", b"x" * 5000, "video/mp4"))],
    )
    assert response.status_code == 413


def test_import_refuse_un_fichier_illisible(compte, sync_spawn):
    project_id = _create(compte)
    body = compte.post(
        f"/api/projects/{project_id}/uploads",
        files=[("files", ("faux.mp4", b"ceci n'est pas une video", "video/mp4"))],
    ).json()
    assert body["job"]["state"] == "error"
    assert body["sources"][0]["error"]


# --- Verrou global hérité --------------------------------------------------
def test_verrou_global_precede_les_comptes(monkeypatch, espace):
    """FLAMBEE_PASSWORD ferme tout le service, y compris le site public."""
    import base64 as _b64

    from fastapi.testclient import TestClient

    monkeypatch.setattr(app_module.config, "PASSWORD", "secret")
    monkeypatch.setattr(app_module.config, "USERNAME", "flambee")

    with TestClient(app_module.app) as garde:
        assert garde.get("/").status_code == 401
        assert "Basic" in garde.get("/").headers["www-authenticate"]

        def entete(utilisateur, mot_de_passe):
            jeton = _b64.b64encode(f"{utilisateur}:{mot_de_passe}".encode()).decode()
            return {"Authorization": f"Basic {jeton}"}

        assert garde.get("/", headers=entete("flambee", "faux")).status_code == 401
        assert garde.get("/", headers=entete("autre", "secret")).status_code == 401
        assert garde.get("/", headers=entete("flambee", "secret")).status_code == 200


def test_sans_verrou_le_site_public_reste_ouvert(client):
    assert client.get("/api/health").status_code == 200


def test_echantillon_de_style_est_une_video(compte):
    if media.ensure_tools():
        pytest.skip("ffmpeg requis")
    response = compte.get("/api/presets/punch/sample")
    assert response.status_code == 200
    assert response.headers["content-type"] == "video/mp4"
    assert len(response.content) > 2000
    assert "max-age" in response.headers.get("cache-control", "")


def test_echantillon_style_inconnu(compte):
    assert compte.get("/api/presets/inexistant/sample").status_code == 404


def test_echantillon_vient_du_cache(compte):
    """Le second appel ne relance pas ffmpeg."""
    if media.ensure_tools():
        pytest.skip("ffmpeg requis")
    from flambee import samples

    samples.clear_cache()
    chemin = samples.sample_path("minimal")
    assert not chemin.exists()
    compte.get("/api/presets/minimal/sample")
    assert chemin.exists()
    horodatage = chemin.stat().st_mtime
    compte.get("/api/presets/minimal/sample")
    assert chemin.stat().st_mtime == horodatage


def test_affiche_de_style_montre_le_texte(compte):
    """L'affiche ne doit pas tomber entre deux lignes : elle serait vide."""
    if media.ensure_tools():
        pytest.skip("ffmpeg requis")
    from flambee import samples

    samples.clear_cache()
    for preset in config.SUBTITLE_PRESETS:
        reponse = compte.get(f"/api/presets/{preset}/poster")
        assert reponse.status_code == 200
        assert reponse.headers["content-type"] == "image/jpeg"
        # Une image de fond seul pèse environ 1 ko ; avec le texte, plusieurs.
        assert len(reponse.content) > 2500, f"affiche vide pour {preset}"

    assert compte.get("/api/presets/inexistant/poster").status_code == 404


def test_l_instant_de_l_affiche_tombe_dans_une_ligne(compte):
    from flambee import samples
    from flambee.subtitles import group_words

    for preset in config.SUBTITLE_PRESETS:
        instant = samples._poster_time(preset)
        lignes = group_words(samples.sample_words(), config.subtitle_style(preset))
        assert any(ligne.start <= instant <= ligne.end for ligne in lignes), preset


def test_cache_invalide_si_le_style_change(monkeypatch):
    """Modifier un réglage doit produire un nouveau fichier, pas réutiliser l'ancien."""
    from flambee import config as flambee_config
    from flambee import samples

    avant = samples.sample_path("punch")
    style = flambee_config.SUBTITLE_PRESETS["punch"]
    monkeypatch.setattr(style, "font_size", style.font_size + 10)
    assert samples.sample_path("punch") != avant


def test_copie_de_visionnage_est_plus_legere(tmp_path):
    """La lecture dans la page ne doit pas passer par le 1080p."""
    if media.ensure_tools():
        pytest.skip("ffmpeg requis")
    from flambee import samples

    source = tmp_path / "rendu.mp4"
    media.ffmpeg([
        "-f", "lavfi", "-i", "testsrc2=s=1080x1920:r=30:d=3",
        "-f", "lavfi", "-t", "3", "-i", "anullsrc=r=48000:cl=stereo",
        "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-shortest", str(source),
    ])
    legere = samples.viewing_copy(source, tmp_path / "cache")
    info = media.probe(legere)
    assert info.width == samples.VIEWING_WIDTH
    assert legere.stat().st_size < source.stat().st_size
    assert info.duration == pytest.approx(media.probe(source).duration, abs=0.2)

    # Deuxième appel : le fichier est réutilisé tel quel.
    horodatage = legere.stat().st_mtime_ns
    assert samples.viewing_copy(source, tmp_path / "cache").stat().st_mtime_ns == horodatage


def test_visionnage_absent_sans_rendu(compte):
    project_id = _create(compte)
    assert compte.get(f"/api/projects/{project_id}/viewing").status_code == 404


def test_health_signale_absence_de_cle_api(compte, monkeypatch):
    """L'interface s'appuie dessus pour proposer le mode manuel."""
    monkeypatch.setattr(app_module.scriptgen, "api_key_available", lambda: False)
    assert compte.get("/api/health").json()["anthropic_key"] is False
    monkeypatch.setattr(app_module.scriptgen, "api_key_available", lambda: True)
    assert compte.get("/api/health").json()["anthropic_key"] is True


def test_generation_sans_cle_explique_le_mode_manuel(compte, monkeypatch):
    monkeypatch.setattr(app_module.scriptgen, "api_key_available", lambda: False)
    project_id = _create(compte)
    response = compte.post(f"/api/projects/{project_id}/script/generate",
                           json={"topic": "le sommeil"})
    assert response.status_code == 400
    assert "mode manuel" in response.json()["detail"]


# --- Aperçu des styles de sous-titres -------------------------------------


def test_le_cache_suit_le_fond_et_le_cadrage(monkeypatch):
    """Changer le fond ou la bande doit produire un nouveau fichier."""
    from flambee import samples

    avant = samples.sample_path("punch")
    monkeypatch.setattr(samples, "BAND_HEIGHT", samples.BAND_HEIGHT + 20)
    assert samples.sample_path("punch") != avant

    monkeypatch.undo()
    monkeypatch.setattr(samples, "BACKDROP_COLORS", ("0x000000",) * 6)
    assert samples.sample_path("punch") != avant


def test_toutes_les_vignettes_ont_le_meme_format():
    """Des cartes de hauteurs différentes donneraient une grille bancale."""
    from flambee import config as flambee_config
    from flambee import samples

    hauteurs = {samples.text_band(flambee_config.subtitle_style(nom),
                                  flambee_config.FORMAT)[1]
                for nom in flambee_config.SUBTITLE_PRESETS}
    assert len(hauteurs) == 1, "la bande doit être identique pour tous les styles"

    # Le cadrage reste dans l'image, quel que soit le style.
    for nom in flambee_config.SUBTITLE_PRESETS:
        haut, hauteur = samples.text_band(flambee_config.subtitle_style(nom),
                                          flambee_config.FORMAT)
        assert 0 <= haut and haut + hauteur <= flambee_config.FORMAT.height
