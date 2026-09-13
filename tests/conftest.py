"""Fixtures communes : espace de travail isolé et client authentifié."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from flambee import app as app_module  # noqa: E402
from flambee import config, media, samples, users, voicestudio  # noqa: E402
from flambee.project import store  # noqa: E402


@pytest.fixture()
def espace(monkeypatch, tmp_path):
    """Isole tout ce qui touche au disque dans un dossier temporaire."""
    for module in (config, users.config, voicestudio.config, samples.config):
        monkeypatch.setattr(module, "WORK_DIR", tmp_path, raising=False)
    monkeypatch.setattr(users._local, "db", None, raising=False)
    monkeypatch.setattr(config, "PASSWORD", "")
    # Les tests décrivent le parcours d'un client : sans cela, le premier
    # compte de chaque base neuve serait celui de l'administrateur, en Studio.
    monkeypatch.setattr(users, "PLAN_PROPRIETAIRE", "essai")
    store._cache.clear()
    yield tmp_path
    store._cache.clear()


@pytest.fixture()
def client(monkeypatch, espace):
    """Client sans compte : le site public et les pages de connexion."""
    monkeypatch.setattr(media, "ensure_tools", lambda: [])
    monkeypatch.setattr(app_module.media, "ensure_tools", lambda: [])
    with TestClient(app_module.app) as test_client:
        yield test_client


@pytest.fixture()
def compte(client):
    """Client déjà connecté, avec un compte à la formule Essai."""
    client.post("/inscription",
                data={"email": "essai@exemple.fr", "mot_de_passe": "motdepasse1",
                      "nom": "Testeur"},
                follow_redirects=False)
    return client


@pytest.fixture()
def compte_pro(compte):
    """Client connecté, formule Créateur : les rubriques réservées s'ouvrent."""
    compte.post("/studio/abonnement", data={"plan": "createur"},
                follow_redirects=False)
    return compte
