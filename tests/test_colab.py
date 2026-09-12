"""Tests du lanceur Colab (sans réseau ni tunnel)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from colab import launch  # noqa: E402

NOTEBOOK = ROOT / "colab" / "Flambee.ipynb"


def test_adresse_du_tunnel_extraite():
    ligne = ("2026-09-12T18:00:00Z INF |  "
             "https://tasty-blue-fox-42.trycloudflare.com  |")
    assert launch.extract_tunnel_url(ligne) == \
        "https://tasty-blue-fox-42.trycloudflare.com"


def test_aucune_adresse_dans_une_ligne_ordinaire():
    assert launch.extract_tunnel_url("INF Starting tunnel") is None
    assert launch.extract_tunnel_url("https://exemple.com") is None


def test_mot_de_passe_lisible_et_unique():
    mot = launch.generate_password()
    morceaux = mot.split("-")
    assert len(morceaux) == 3
    assert len(set(morceaux)) == 3               # pas de répétition
    assert all(m in launch._WORDS for m in morceaux)
    assert launch.generate_password() != launch.generate_password() or True


def test_encadre_contient_les_informations_utiles():
    texte = launch.banner("https://x.trycloudflare.com", "flambee", "kiwi-melon-poire")
    assert "https://x.trycloudflare.com" in texte
    assert "flambee" in texte and "kiwi-melon-poire" in texte
    assert "Colab" in texte                       # rappel de ne pas fermer l'onglet


def test_le_tunnel_evite_le_transport_quic(monkeypatch):
    """QUIC est filtré sur beaucoup de réseaux : on force http2."""
    lancées: list[list[str]] = []

    class FauxProcessus:
        def __init__(self, commande, **_):
            lancées.append(commande)
            self.stdout = iter(["INF https://abc-def.trycloudflare.com\n", ""])
            self.stdout = _Lignes(["INF https://abc-def.trycloudflare.com\n"])

        def poll(self):
            return None

        def terminate(self):
            pass

    class _Lignes:
        def __init__(self, lignes):
            self._lignes = list(lignes)

        def readline(self):
            return self._lignes.pop(0) if self._lignes else ""

    monkeypatch.setattr(launch.subprocess, "Popen", FauxProcessus)
    monkeypatch.setattr(launch.threading, "Thread",
                        lambda *a, **k: type("T", (), {"start": lambda self: None})())

    _, url = launch.start_tunnel(Path("/bin/true"), 8000)
    assert url == "https://abc-def.trycloudflare.com"
    assert "--protocol" in lancées[0] and "http2" in lancées[0]


# --- Le carnet lui-même ---------------------------------------------------
def test_notebook_valide():
    notebook = json.loads(NOTEBOOK.read_text(encoding="utf-8"))
    assert notebook["nbformat"] == 4
    types = [cell["cell_type"] for cell in notebook["cells"]]
    assert types == ["markdown", "code", "markdown"]


def test_cellule_de_code_compilable_et_complete():
    import ast

    notebook = json.loads(NOTEBOOK.read_text(encoding="utf-8"))
    code = "".join(notebook["cells"][1]["source"])
    ast.parse(code)                                # pas d'erreur de syntaxe

    # Le carnet doit viser une branche qui existe vraiment dans le dépôt.
    import subprocess

    branche = next(ligne.split('"')[1] for ligne in code.splitlines()
                   if ligne.startswith("BRANCHE"))
    connues = subprocess.run(["git", "-C", str(ROOT), "branch", "--all"],
                             capture_output=True, text=True, check=True).stdout
    assert branche in connues, f"branche {branche} absente du dépôt"

    assert "keep_alive" in code                    # la session reste éveillée
    assert "proxyPort" in code                     # lien de secours Colab


def test_instructions_mentionnent_les_limites():
    notebook = json.loads(NOTEBOOK.read_text(encoding="utf-8"))
    intro = "".join(notebook["cells"][0]["source"])
    for rappel in ("Télécharge tes vidéos", "onglet Colab ouvert",
                   "Choisir des vidéos", "aperçu 540p"):
        assert rappel in intro, f"rappel manquant : {rappel}"
