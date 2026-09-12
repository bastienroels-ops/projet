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

    def fake_render(project: Project) -> None:
        rendered["id"] = project.id
        project.set_job("render", "done", progress=1.0, message="fini")

    monkeypatch.setattr(app_module.pipeline, "run_render", fake_render)
    assert client.post(f"/api/projects/{project_id}/render").status_code == 200
    assert rendered["id"] == project_id


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
