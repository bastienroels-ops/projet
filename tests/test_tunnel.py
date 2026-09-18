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


def _dormeur(restant, tours):
    """Remplace `time.sleep` : n'attend rien, et met fin à la boucle.

    Deux sorties, et la seconde compte autant que la première : quand les
    réponses préparées sont épuisées, et au bout d'un nombre de tours fixé.
    Sans cette borne, une surveillance qui cesserait de sonder ne ferait pas
    échouer le test — elle le ferait tourner sans fin.
    """
    passages = [0]

    def dormir(_secondes):
        passages[0] += 1
        if not restant or passages[0] > tours + 2:
            raise KeyboardInterrupt        # sortie propre de la boucle

    return dormir


def _surveiller(monkeypatch, reponses, tours):
    """Fait tourner `keep_alive` un nombre fixe de tours, sans attendre."""
    serveur, tunnel = _Faux(), _Faux()
    ouvertures = []

    restant = list(reponses)

    monkeypatch.setattr(launch.time, "sleep", _dormeur(restant, tours))
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


# --- L'autre porte ---------------------------------------------------------
# Rejouer un appel ne sert à rien quand le tunnel est mort pour de bon :
# l'utilisateur voit alors « Error 530 » ou « Error 1033 », deux pages
# anglaises qui ne disent pas quoi faire. Sur Colab, un second chemin existe
# pourtant — le lien direct de Google, qui ne traverse pas Cloudflare. Ces
# tests vérifient qu'il arrive jusqu'à la page, et qu'il y arrive seul.
import json                                                   # noqa: E402
import os                                                     # noqa: E402
import types                                                  # noqa: E402

from flambee import app as app_module                         # noqa: E402

STATIQUE = RACINE / "flambee" / "static"


def _config_avec(valeur):
    """Importe `flambee.config` dans un processus neuf, avec cette valeur.

    La constante est lue une seule fois, à l'import : la recharger dans le
    processus de test contaminerait tous les suivants.
    """
    environnement = {**os.environ, "FLAMBEE_PORTE_DIRECTE": valeur}
    sortie = subprocess.run(
        [sys.executable, "-c",
         "from flambee import config; print(config.PORTE_DIRECTE)"],
        cwd=str(RACINE), env=environnement, capture_output=True, text=True,
        check=True)
    return sortie.stdout.strip()


def test_une_adresse_https_est_retenue():
    assert _config_avec("https://exemple.colab.googleusercontent.com/") == \
        "https://exemple.colab.googleusercontent.com/"


@pytest.mark.parametrize("valeur", [
    "javascript:alert(1)",          # finit dans un href : elle s'exécuterait
    "http://exemple.fr",            # en clair, depuis une page en https
    "//exemple.fr",
    "pas une adresse",
])
def test_une_adresse_douteuse_est_ecartee(valeur):
    """La valeur atterrit dans un attribut `href` du gabarit."""
    assert _config_avec(valeur) == ""


def test_la_page_porte_l_adresse_quand_elle_existe(compte, monkeypatch):
    monkeypatch.setattr(app_module.config, "PORTE_DIRECTE",
                        "https://direct.exemple.fr")
    page = compte.get("/studio").text
    assert 'data-porte-directe="https://direct.exemple.fr"' in page
    assert 'id="porte-secours"' in page


def test_la_page_n_invente_pas_d_adresse(compte, monkeypatch):
    """Hors Colab il n'y a pas de second chemin : proposer un bouton mort
    serait pire que de ne rien proposer."""
    monkeypatch.setattr(app_module.config, "PORTE_DIRECTE", "")
    page = compte.get("/studio").text
    assert "data-porte-directe" not in page


def test_le_bandeau_part_cache(compte, monkeypatch):
    """Il ne doit paraître qu'au premier appel qui échoue."""
    monkeypatch.setattr(app_module.config, "PORTE_DIRECTE",
                        "https://direct.exemple.fr")
    page = compte.get("/studio").text
    assert 'class="porte-secours hidden" id="porte-secours"' in page


# --- Le cadre du carnet ----------------------------------------------------
def test_le_cadre_montre_l_adresse_directe_en_premier(monkeypatch):
    """Présenter la fragile en tête envoyait l'utilisateur droit sur la seule
    des deux qui puisse afficher un mur en anglais."""
    monkeypatch.setattr(launch, "_SECOURS", "https://direct.googleusercontent.com")
    monkeypatch.setattr(launch, "_COMPTE", None)
    monkeypatch.setattr(launch, "etat_transcription", lambda: "ok")
    cadre = launch.banner("https://tunnel.trycloudflare.com", "flambee", "kiwi")
    assert cadre.index("direct.googleusercontent.com") < \
        cadre.index("tunnel.trycloudflare.com")


def test_le_cadre_dit_quoi_faire_devant_une_1033(monkeypatch):
    monkeypatch.setattr(launch, "_SECOURS", "https://direct.googleusercontent.com")
    monkeypatch.setattr(launch, "_COMPTE", None)
    monkeypatch.setattr(launch, "etat_transcription", lambda: "ok")
    cadre = launch.banner("https://tunnel.trycloudflare.com", "flambee", "kiwi")
    assert "1033" in cadre and "l'autre" in cadre


def test_le_cadre_reste_lisible_sans_lien_direct(monkeypatch):
    """Hors Colab, `_SECOURS` est None : le cadre ne doit pas parler d'une
    porte qui n'existe pas."""
    monkeypatch.setattr(launch, "_SECOURS", None)
    monkeypatch.setattr(launch, "_COMPTE", None)
    monkeypatch.setattr(launch, "etat_transcription", lambda: "ok")
    cadre = launch.banner("https://tunnel.trycloudflare.com", "flambee", "kiwi")
    assert "1033" not in cadre
    assert "tunnel.trycloudflare.com" in cadre


def _boutons_affiches(monkeypatch, url, direct):
    """Fait croire à `launch` qu'il tourne dans un carnet, et récolte le HTML."""
    rendu = []
    faux = types.ModuleType("IPython.display")
    faux.HTML = lambda html: html
    faux.display = rendu.append
    monkeypatch.setitem(sys.modules, "IPython", types.ModuleType("IPython"))
    monkeypatch.setitem(sys.modules, "IPython.display", faux)
    monkeypatch.setattr(launch, "_SECOURS", direct)
    launch.afficher_le_lien(url, direct)
    return rendu[0] if rendu else ""


def test_deux_chemins_donnent_deux_boutons(monkeypatch):
    html = _boutons_affiches(monkeypatch, "https://tunnel.trycloudflare.com",
                             "https://direct.googleusercontent.com")
    assert html.count("<a href=") == 2
    assert html.index("direct.googleusercontent.com") < \
        html.index("tunnel.trycloudflare.com")


def test_une_seule_adresse_ne_donne_qu_un_bouton(monkeypatch):
    """Cas du tunnel qui ne s'est pas ouvert : deux boutons identiques
    n'aideraient personne à choisir."""
    html = _boutons_affiches(monkeypatch, "https://direct.googleusercontent.com",
                             "https://direct.googleusercontent.com")
    assert html.count("<a href=") == 1


# --- La cellule que Colab garde en mémoire ---------------------------------
def test_la_surveillance_retrouve_l_adresse_toute_seule(monkeypatch):
    """La cellule gardée en mémoire par le navigateur appelle `keep_alive`
    sans lui passer `url`. Sans repli, la surveillance du tunnel ne
    s'exécutait jamais chez qui avait déjà lancé le carnet une fois."""
    vues = []
    serveur, tunnel = _Faux(), _Faux()
    restant = [True, True]

    def sonder(url, **_k):
        vues.append(url)
        return restant.pop(0)

    monkeypatch.setattr(launch.time, "sleep", _dormeur(restant, 2))
    monkeypatch.setattr(launch, "tunnel_repond", sonder)
    monkeypatch.setattr(launch, "log", lambda *a: None)
    monkeypatch.setattr(launch, "_ADRESSE", "https://memoire.trycloudflare.com")

    launch.keep_alive(serveur, tunnel, port=8000)      # sans url, comme la cellule
    assert vues == ["https://memoire.trycloudflare.com"] * 2


def test_la_cellule_du_carnet_passe_bien_l_adresse():
    """Le carnet du dépôt, lui, n'a plus à s'en remettre au repli."""
    carnet = json.loads((RACINE / "colab" / "Flambee.ipynb").read_text())
    cellule = "".join(carnet["cells"][1]["source"])
    assert "launch.keep_alive(" in cellule
    assert "url=adresse" in cellule
    assert "launch.porte_directe()" in cellule


# --- Le bandeau, côté navigateur -------------------------------------------
def test_le_bandeau_se_retire_des_qu_un_appel_repasse():
    """Sinon il resterait affiché sur une application qui remarche."""
    source = (STATIQUE / "app.js").read_text()
    assert "tunnelRevenu();" in source
    assert source.count("tunnelCoupe();") == 2      # les deux abandons


def test_le_bandeau_ne_se_propose_pas_a_lui_meme():
    """Déjà passé par la porte directe, la proposer serait proposer de
    rester."""
    source = (STATIQUE / "app-shell.js").read_text()
    assert "adresse === window.location.origin" in source


def test_le_bandeau_garde_le_chemin_de_la_page():
    """Renvoyer à l'accueil ferait perdre l'écran où l'on était."""
    source = (STATIQUE / "app-shell.js").read_text()
    assert "window.location.pathname" in source
