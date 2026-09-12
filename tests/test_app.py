"""Tests de l'API FastAPI (sans réseau : le téléchargement est simulé)."""

from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from flambee import app as app_module  # noqa: E402
from flambee import media, pipeline  # noqa: E402
from flambee.downloader import Source  # noqa: E402
from flambee.project import Project  # noqa: E402


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setattr(media, "ensure_tools", lambda: [])
    monkeypatch.setattr(app_module.media, "ensure_tools", lambda: [])
    with TestClient(app_module.app) as test_client:
        yield test_client


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


def _create(client) -> str:
    return client.post("/api/projects").json()["id"]


def test_health(client):
    body = client.get("/api/health").json()
    assert body["ffmpeg"] and "output_dir" in body


def test_page_daccueil(client):
    page = client.get("/")
    assert page.status_code == 200 and "Flambée" in page.text


def test_parcours_complet(client, fake_download, monkeypatch):
    project_id = _create(client)

    # Étape 1 : moins de 2 liens → refus
    refus = client.post(f"/api/projects/{project_id}/sources",
                        json={"urls": "https://a.test/1"})
    assert refus.status_code == 400

    body = client.post(f"/api/projects/{project_id}/sources", json={
        "urls": "https://a.test/1\nhttps://b.test/2"}).json()
    assert len(body["sources"]) == 2
    assert body["sources"][0]["hook_url"].endswith("/hooks/1")
    assert "path" not in body["sources"][0]

    # Étape 2
    body = client.post(f"/api/projects/{project_id}/hook",
                       json={"hook_index": 2}).json()
    assert body["hook_index"] == 2 and body["step"] >= 3
    assert client.post(f"/api/projects/{project_id}/hook",
                       json={"hook_index": 9}).status_code == 400
    assert client.get(f"/api/projects/{project_id}/hooks/1").status_code == 200

    # Étape 3 : une musique inconnue est ignorée, les bornes sont appliquées
    body = client.post(f"/api/projects/{project_id}/settings", json={
        "voice": "fr-FR-HenriNeural", "music": "inexistante.mp3",
        "music_volume": 5, "mask_source_subtitles": True, "mask_mode": "zzz",
    }).json()
    assert body["settings"]["music"] is None
    assert body["settings"]["music_volume"] == 1.0
    assert body["settings"]["mask_mode"] == "blur"
    assert body["settings"]["voice"] == "fr-FR-HenriNeural"

    # Étape 4 : mode manuel (prompt à copier) puis script validé
    prompt = client.post(f"/api/projects/{project_id}/script/prompt",
                         json={"topic": "le sommeil"}).json()["prompt"]
    assert "le sommeil" in prompt and "accroche" in prompt.lower()
    assert client.post(f"/api/projects/{project_id}/script/prompt",
                       json={"topic": "  "}).status_code == 400

    body = client.post(f"/api/projects/{project_id}/script", json={
        "script": "```\nVoici le script de test.\n```", "topic": "le sommeil"}).json()
    assert body["script"] == "Voici le script de test."
    assert body["step"] == 5
    assert client.post(f"/api/projects/{project_id}/script",
                       json={"script": "   "}).status_code == 400

    # Étape 5 : le rendu est lancé (pipeline simulé)
    rendered = {}

    def fake_render(project: Project, *, fast: bool = False) -> None:
        rendered["id"] = project.id
        rendered["fast"] = fast
        project.set_job("render", "done", progress=1.0, message="fini")

    monkeypatch.setattr(app_module.pipeline, "run_render", fake_render)
    assert client.post(f"/api/projects/{project_id}/render",
                       json={"fast": True}).status_code == 200
    assert rendered["id"] == project_id and rendered["fast"] is True


def test_render_refuse_sans_script(client, fake_download):
    project_id = _create(client)
    client.post(f"/api/projects/{project_id}/sources",
                json={"urls": "https://a.test/1\nhttps://b.test/2"})
    assert client.post(f"/api/projects/{project_id}/render").status_code == 400


def test_projet_introuvable(client):
    assert client.get("/api/projects/inconnu").status_code == 404


def test_tache_concurrente_refusee(client, monkeypatch):
    project_id = _create(client)
    project = app_module.store.get(project_id)
    project.set_job("download", "running", progress=0.3)
    response = client.post(f"/api/projects/{project_id}/sources",
                           json={"urls": "https://a.test/1\nhttps://b.test/2"})
    assert response.status_code == 409


def test_projet_persiste_sur_disque(client):
    project_id = _create(client)
    app_module.store._cache.clear()
    reloaded = app_module.store.get(project_id)
    assert reloaded is not None and reloaded.id == project_id


def test_musique_hors_bibliotheque_ignoree():
    assert pipeline._resolve_music("../secret.mp3") is None
    assert pipeline._resolve_music("") is None


def test_suppression_projet(client):
    project_id = _create(client)
    assert client.delete(f"/api/projects/{project_id}").json()["deleted"] is True
    assert client.get(f"/api/projects/{project_id}").status_code == 404


def test_liste_des_projets(client):
    project_id = _create(client)
    ids = [p["id"] for p in client.get("/api/projects").json()["projects"]]
    assert project_id in ids
    assert time.time() > 0


def test_message_derreur_yt_dlp_donne_une_piste():
    from flambee.downloader import _clean_ydl_error

    message = _clean_ydl_error(
        "ERROR: [youtube] abc: Sign in to confirm you're not a bot. See https://x"
    )
    assert "FLAMBEE_COOKIES_FROM_BROWSER" in message
    assert "https://x" not in message


def test_presets_de_sous_titres(client):
    body = client.get("/api/presets").json()
    ids = {p["id"] for p in body["subtitles"]}
    assert {"punch", "impact", "neon", "studio", "signature", "minimal"} <= ids
    assert body["default"] in ids
    # Chaque style s'accompagne d'un nom et d'une description pour l'interface.
    for preset in body["subtitles"]:
        assert preset["label"] and preset["description"]


def test_preset_inconnu_retombe_sur_le_defaut(client):
    project_id = _create(client)
    body = client.post(f"/api/projects/{project_id}/settings",
                       json={"subtitle_preset": "nimportequoi"}).json()
    assert body["settings"]["subtitle_preset"] == "punch"


def test_annulation_dune_tache(client, monkeypatch):
    project_id = _create(client)
    # Sans tâche en cours, rien à annuler.
    assert client.post(f"/api/projects/{project_id}/cancel").json()["cancelled"] is False

    token = pipeline.cancel_token(project_id)
    assert client.post(f"/api/projects/{project_id}/cancel").json()["cancelled"] is True
    assert token.is_set()
    pipeline._clear_cancel(project_id)


def test_health_annonce_lencodeur(client):
    body = client.get("/api/health").json()
    assert body["encoder"]
    assert isinstance(body["hardware_encoder"], bool)


def test_accroche_recommandee_exposee(client, fake_download):
    project_id = _create(client)
    body = client.post(f"/api/projects/{project_id}/sources", json={
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


def test_import_de_fichiers(client, sync_spawn, tmp_path):
    """Une vidéo envoyée depuis l'appareil suit le même chemin qu'un téléchargement."""
    if media.ensure_tools():
        pytest.skip("ffmpeg requis")
    payload = _tiny_video(tmp_path / "clip.mp4")
    project_id = _create(client)

    body = client.post(
        f"/api/projects/{project_id}/uploads",
        files=[("files", ("ma video.mp4", payload, "video/mp4")),
               ("files", ("autre.mov", payload, "video/quicktime"))],
    ).json()

    assert len(body["sources"]) == 2
    assert body["sources"][0]["title"] == "ma video"
    assert body["sources"][0]["duration"] > 0
    assert body["sources"][0]["hook_url"]          # accroche extraite
    assert body["step"] >= 2


def test_import_refuse_les_formats_inconnus(client, tmp_path):
    project_id = _create(client)
    response = client.post(
        f"/api/projects/{project_id}/uploads",
        files=[("files", ("script.txt", b"pas une video", "text/plain"))],
    )
    assert response.status_code == 400
    assert "Format non reconnu" in response.json()["detail"]


def test_import_refuse_les_fichiers_trop_gros(client, monkeypatch, tmp_path):
    monkeypatch.setattr(app_module.config, "MAX_UPLOAD_BYTES", 1024)
    project_id = _create(client)
    response = client.post(
        f"/api/projects/{project_id}/uploads",
        files=[("files", ("gros.mp4", b"x" * 5000, "video/mp4"))],
    )
    assert response.status_code == 413


def test_import_refuse_un_fichier_illisible(client, sync_spawn):
    project_id = _create(client)
    body = client.post(
        f"/api/projects/{project_id}/uploads",
        files=[("files", ("faux.mp4", b"ceci n'est pas une video", "video/mp4"))],
    ).json()
    assert body["job"]["state"] == "error"
    assert body["sources"][0]["error"]


# --- Mot de passe ---------------------------------------------------------
def test_acces_protege_par_mot_de_passe(monkeypatch):
    import base64 as _b64

    from fastapi import FastAPI

    from flambee.auth import install_auth

    monkeypatch.setattr(app_module.config, "PASSWORD", "secret")
    monkeypatch.setattr(app_module.config, "USERNAME", "flambee")

    protected = FastAPI()
    install_auth(protected)

    @protected.get("/ping")
    async def ping():
        return {"ok": True}

    with TestClient(protected) as guarded:
        assert guarded.get("/ping").status_code == 401
        assert "Basic" in guarded.get("/ping").headers["www-authenticate"]

        def header(user, password):
            token = _b64.b64encode(f"{user}:{password}".encode()).decode()
            return {"Authorization": f"Basic {token}"}

        assert guarded.get("/ping", headers=header("flambee", "faux")).status_code == 401
        assert guarded.get("/ping", headers=header("autre", "secret")).status_code == 401
        assert guarded.get("/ping", headers={"Authorization": "Bearer x"}).status_code == 401
        assert guarded.get("/ping", headers=header("flambee", "secret")).status_code == 200


def test_sans_mot_de_passe_aucun_controle(client):
    """Comportement local par défaut : pas d'authentification."""
    assert client.get("/api/health").status_code == 200


def test_health_signale_absence_de_cle_api(client, monkeypatch):
    """L'interface s'appuie dessus pour proposer le mode manuel."""
    monkeypatch.setattr(app_module.scriptgen, "api_key_available", lambda: False)
    assert client.get("/api/health").json()["anthropic_key"] is False
    monkeypatch.setattr(app_module.scriptgen, "api_key_available", lambda: True)
    assert client.get("/api/health").json()["anthropic_key"] is True


def test_generation_sans_cle_explique_le_mode_manuel(client, monkeypatch):
    monkeypatch.setattr(app_module.scriptgen, "api_key_available", lambda: False)
    project_id = _create(client)
    response = client.post(f"/api/projects/{project_id}/script/generate",
                           json={"topic": "le sommeil"})
    assert response.status_code == 400
    assert "mode manuel" in response.json()["detail"]
