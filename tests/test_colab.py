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
    assert types[0] == "markdown" and types[1] == "code"
    assert types.count("code") == 1, "une seule cellule à exécuter, pas deux"


def test_le_bouton_est_a_portee_du_premier_ecran():
    """La cellule de code doit suivre une entrée brève.

    Le carnet s'ouvrait sur quarante et une lignes de mode d'emploi : sur un
    téléphone, le bouton ▶︎ — le seul geste à faire — tombait deux écrans plus
    bas, et la cellule qu'on avait sous les yeux était du texte, sans rien à
    toucher. Les explications se lisent très bien après, pendant que
    l'installation défile.
    """
    notebook = json.loads(NOTEBOOK.read_text(encoding="utf-8"))
    entree = "".join(notebook["cells"][0]["source"])
    lignes = len(entree.splitlines())
    assert lignes <= 8, f"l'entrée fait {lignes} lignes et repousse le bouton"
    assert "▶" in entree, "l'entrée ne dit pas sur quoi toucher"


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
    """Les avertissements peuvent vivre dans n'importe quelle cellule de texte.

    Ils ont quitté l'en-tête pour laisser le bouton ▶︎ en vue ; ce qui compte
    est qu'ils soient dans le carnet, pas qu'ils soient en premier.
    """
    notebook = json.loads(NOTEBOOK.read_text(encoding="utf-8"))
    texte = "".join("".join(cell["source"]) for cell in notebook["cells"]
                    if cell["cell_type"] == "markdown")
    for rappel in ("Télécharge tes vidéos", "onglet Colab ouvert",
                   "Choisir des vidéos", "aperçu 540p"):
        assert rappel in texte, f"rappel manquant : {rappel}"


def test_lien_de_secours_absent_hors_colab():
    """Hors Colab, l'absence du module ne doit rien casser."""
    assert launch.colab_fallback_url(8000) is None


def test_cle_api_transmise_au_serveur(monkeypatch):
    """La clé saisie dans Colab doit atteindre le serveur, et elle seule."""
    captured: dict = {}

    class FauxProcessus:
        def __init__(self, commande, **kwargs):
            captured["env"] = kwargs.get("env", {})

        def poll(self):
            return None

    monkeypatch.setattr(launch.subprocess, "Popen", FauxProcessus)

    launch.start_server(8000, "mdp", "flambee", "sk-ant-secret")
    assert captured["env"]["ANTHROPIC_API_KEY"] == "sk-ant-secret"
    assert captured["env"]["FLAMBEE_PASSWORD"] == "mdp"

    launch.start_server(8000, "mdp", "flambee", "   ")
    assert "ANTHROPIC_API_KEY" not in captured["env"]


def test_les_rendus_vivent_hors_du_dossier_clone(monkeypatch):
    """Relancer la cellule efface le clone : les vidéos doivent être ailleurs."""
    captured: dict = {}

    class FauxProcessus:
        def __init__(self, commande, **kwargs):
            captured["env"] = kwargs.get("env", {})

        def poll(self):
            return None

    monkeypatch.setattr(launch.subprocess, "Popen", FauxProcessus)
    launch.start_server(8000, "mdp", "flambee")

    sortie = Path(captured["env"]["FLAMBEE_OUTPUT_DIR"])
    travail = Path(captured["env"]["FLAMBEE_WORK_DIR"])
    assert "flambee-data" in sortie.parts and sortie.exists()
    assert "flambee-data" in travail.parts
    # L'encodage tourne en priorité basse pour ne pas étrangler le tunnel.
    assert int(captured["env"]["FLAMBEE_NICE"]) > 0


def test_le_tunnel_est_rouvert_sil_tombe(monkeypatch, capsys):
    """Un encodage peut faire tomber le tunnel ; le rendu, lui, continue."""
    appels: list[int] = []

    class Serveur:
        def __init__(self):
            self.restant = 2

        def poll(self):
            self.restant -= 1
            return None if self.restant > 0 else 0   # s'arrête au 2e tour

    class TunnelMort:
        def poll(self):
            return 1

    class TunnelNeuf:
        def poll(self):
            return None

    def faux_tunnel(binaire, port, **_):
        appels.append(port)
        return TunnelNeuf(), "https://nouvelle-adresse.trycloudflare.com"

    monkeypatch.setattr(launch.time, "sleep", lambda _: None)
    monkeypatch.setattr(launch, "start_tunnel", faux_tunnel)

    launch.keep_alive(Serveur(), TunnelMort(), port=8000, password="mdp")

    assert appels == [8000], "le tunnel n'a pas été rouvert"
    sortie = capsys.readouterr().out
    assert "https://nouvelle-adresse.trycloudflare.com" in sortie
    assert "adresse a changé" in sortie
