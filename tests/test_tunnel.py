"""Le tunnel qui lâche — erreur 530 dans le navigateur.

Cloudflare renvoie 530 quand il n'arrive pas à joindre l'origine, ici le
Colab. Flambée n'en sait rien et tourne toujours : le rendu continue, le
projet est enregistré. Deux choses doivent donc tenir — un point de contrôle
joignable de l'extérieur, et une surveillance qui distingue un tunnel mort
d'un processus mort.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

RACINE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RACINE))
sys.path.insert(0, str(RACINE / "colab"))

import launch  # noqa: E402


# --- Le point de contrôle --------------------------------------------------
def test_le_ping_repond_sans_compte(client):
    """Le carnet l'interroge de l'extérieur, par le tunnel : il ne peut pas
    demander de session."""
    reponse = client.get("/ping")
    assert reponse.status_code == 200
    assert reponse.text == "ok"


def test_le_ping_accepte_aussi_head(client):
    """C'est ce que demandent les outils de surveillance ; un 405 leur ferait
    conclure à une panne."""
    assert client.head("/ping").status_code == 200


def test_le_ping_n_est_pas_mis_en_cache(client):
    """Un cache rendrait la sonde aveugle : elle lirait une vieille réponse
    pendant que le tunnel est mort."""
    assert "no-store" in client.get("/ping").headers.get("cache-control", "")


def test_le_ping_ne_touche_a_rien(client, monkeypatch):
    """Sondé toutes les vingt secondes pendant des heures : il ne doit ni
    lire le disque, ni ouvrir la base."""
    from flambee import users

    monkeypatch.setattr(users, "par_id", _interdit("users.par_id"))
    assert client.get("/ping").status_code == 200


def _interdit(nom):
    def refuser(*a, **k):
        raise AssertionError(f"{nom} ne devrait pas être appelé par /ping")
    return refuser


# --- La sonde du carnet ----------------------------------------------------
class _Reponse:
    def __init__(self, status):
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _urlopen_qui_rend(status):
    def ouvrir(requete, timeout=None):
        return _Reponse(status)
    return ouvrir


def _urlopen_qui_leve(exception):
    def ouvrir(requete, timeout=None):
        raise exception
    return ouvrir


@pytest.mark.parametrize("code", [200, 302, 401, 403, 404])
def test_une_reponse_de_l_application_signifie_un_tunnel_vivant(monkeypatch, code):
    """Même un refus prouve que le tunnel achemine : c'est le serveur qui a
    répondu, donc Cloudflare l'a bien atteint."""
    from urllib.error import HTTPError

    if code < 400:
        monkeypatch.setattr("urllib.request.urlopen", _urlopen_qui_rend(code))
    else:
        monkeypatch.setattr("urllib.request.urlopen", _urlopen_qui_leve(
            HTTPError("http://x/ping", code, "non", {}, None)))
    assert launch.tunnel_repond("https://exemple.trycloudflare.com") is True


@pytest.mark.parametrize("code", sorted(launch.CODES_DE_TUNNEL_MORT))
def test_les_codes_de_cloudflare_signifient_un_tunnel_mort(monkeypatch, code):
    """530 en tête : « je n'arrive pas à joindre l'origine »."""
    from urllib.error import HTTPError

    monkeypatch.setattr("urllib.request.urlopen", _urlopen_qui_leve(
        HTTPError("http://x/ping", code, "non", {}, None)))
    assert launch.tunnel_repond("https://exemple.trycloudflare.com") is False


def test_une_connexion_impossible_signifie_un_tunnel_mort(monkeypatch):
    from urllib.error import URLError

    monkeypatch.setattr("urllib.request.urlopen",
                        _urlopen_qui_leve(URLError("refusée")))
    assert launch.tunnel_repond("https://exemple.trycloudflare.com") is False


def test_la_sonde_vise_bien_le_point_de_controle(monkeypatch):
    """Sonder la racine ramènerait toute la page d'accueil à chaque tour."""
    vues = []

    def ouvrir(requete, timeout=None):
        vues.append(requete.full_url)
        return _Reponse(200)

    monkeypatch.setattr("urllib.request.urlopen", ouvrir)
    launch.tunnel_repond("https://exemple.trycloudflare.com/")
    assert vues == ["https://exemple.trycloudflare.com/ping"]


# --- La surveillance -------------------------------------------------------
class _Faux:
    """Un processus qui vit, ou qui est mort depuis le début."""

    def __init__(self, vivant=True):
        self._vivant = vivant
        self.arrete = False

    def poll(self):
        return None if self._vivant else 0

    def terminate(self):
        self.arrete = True
        self._vivant = False

    def wait(self, timeout=None):
        return 0

    def kill(self):
        self._vivant = False


def _surveiller(monkeypatch, reponses, tours):
    """Fait tourner `keep_alive` un nombre fixe de tours, sans attendre."""
    serveur, tunnel = _Faux(), _Faux()
    ouvertures = []

    restant = list(reponses)

    def dormir(_secondes):
        if not restant:
            raise KeyboardInterrupt        # sortie propre de la boucle

    monkeypatch.setattr(launch.time, "sleep", dormir)
    monkeypatch.setattr(launch, "tunnel_repond", lambda url, **k: restant.pop(0))
    monkeypatch.setattr(launch, "log", lambda *a: None)
    monkeypatch.setattr(launch, "afficher_le_lien", lambda url: None)
    monkeypatch.setattr(launch, "banner", lambda *a: "")

    def rouvrir(binaire, port, **k):
        ouvertures.append(port)
        return _Faux(), "https://nouvelle.trycloudflare.com"

    monkeypatch.setattr(launch, "start_tunnel", rouvrir)
    launch.keep_alive(serveur, tunnel, port=8000,
                      url="https://ancienne.trycloudflare.com")
    return tunnel, ouvertures


def test_un_hoquet_isole_ne_rouvre_pas_le_tunnel(monkeypatch):
    """Rouvrir change l'adresse, ce qui dérange bien plus qu'une seconde
    d'interruption."""
    _, ouvertures = _surveiller(monkeypatch, [True, False, True, True], 4)
    assert ouvertures == []


def test_trois_sondages_muets_rouvrent_le_tunnel(monkeypatch):
    """Une minute sans réponse : ce n'est plus un hoquet."""
    _, ouvertures = _surveiller(monkeypatch, [False, False, False], 3)
    assert ouvertures == [8000]


def test_le_compteur_repart_de_zero_quand_le_tunnel_revient(monkeypatch):
    """Deux muets, un vivant, deux muets : jamais trois d'affilée."""
    _, ouvertures = _surveiller(
        monkeypatch, [False, False, True, False, False], 5)
    assert ouvertures == []


def test_l_ancien_tunnel_est_arrete_avant_la_reouverture(monkeypatch):
    """Il tourne encore mais n'achemine plus : le laisser vivant laisserait
    deux tunnels concurrents sur le même port."""
    tunnel, _ = _surveiller(monkeypatch, [False, False, False], 3)
    assert tunnel.arrete is True
