"""Le serveur permanent : ce qui s'installe tout seul doit être juste du
premier coup.

Ce fichier ne s'exécute jamais sur une vraie machine — je n'ai pas d'instance
Oracle. Il vérifie donc ce qui peut l'être sans en avoir une, et c'est déjà
l'essentiel des façons de se tromper : un YAML invalide, une faute de frappe
dans un nom de variable, une étape que la page d'attente ne connaît pas, un
ordre qui prendrait le port 80 à Caddy.
"""

from __future__ import annotations

import ast
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")

RACINE = Path(__file__).resolve().parent.parent
CLOUD_INIT = RACINE / "deploiement" / "oracle-cloud-init.yaml"


@pytest.fixture(scope="module")
def plan():
    return yaml.safe_load(CLOUD_INIT.read_text(encoding="utf-8"))


def _fichier(plan, fin):
    return next(f["content"] for f in plan["write_files"]
                if f["path"].endswith(fin))


# --- La forme --------------------------------------------------------------
def test_le_fichier_est_un_cloud_init_valide(plan):
    """Collé tel quel dans le formulaire d'Oracle : une erreur de syntaxe et
    la machine démarre sans rien installer, sans le dire."""
    assert CLOUD_INIT.read_text(encoding="utf-8").startswith("#cloud-config")
    assert plan["write_files"] and plan["runcmd"]


def test_tous_les_scripts_embarques_sont_syntaxiquement_justes(plan):
    """Ils ne sont exécutés qu'une fois, sur une machine neuve, sans personne
    devant l'écran : une faute de frappe y coûte une réinstallation."""
    vus = 0
    for fichier in plan["write_files"]:
        contenu = fichier["content"]
        if contenu.startswith("#!/usr/bin/env python3"):
            ast.parse(contenu)                  # lève en cas de faute
            vus += 1
        elif contenu.startswith("#!/usr/bin/env bash"):
            with tempfile.NamedTemporaryFile("w", suffix=".sh") as t:
                t.write(contenu)
                t.flush()
                r = subprocess.run(["bash", "-n", t.name],
                                   capture_output=True, text=True)
            assert r.returncode == 0, f"{fichier['path']} :\n{r.stderr}"
            vus += 1
    assert vus >= 4, "les scripts du déploiement ont disparu du fichier"


def test_aucun_reglage_ecrit_dans_le_vide():
    """Une variable mal orthographiée est écrite, transmise, et ignorée. Rien
    ne le signale : le réglage a simplement l'air de ne pas marcher."""
    code = " ".join(p.read_text(encoding="utf-8")
                    for p in (RACINE / "flambee").rglob("*.py"))
    lues = set(re.findall(r'["\'](FLAMBEE_[A-Z0-9_]+)["\']', code))

    ecrites = set()
    for fichier in ("deploiement/oracle-cloud-init.yaml",
                    "docker-compose.yml", "Dockerfile"):
        ecrites |= set(re.findall(r"\b(FLAMBEE_[A-Z0-9_]+)\b",
                                  (RACINE / fichier).read_text(encoding="utf-8")))

    assert not (ecrites - lues), (
        "réglages écrits par le déploiement que le code ne lit jamais : "
        + ", ".join(sorted(ecrites - lues)))


# --- La page d'attente -----------------------------------------------------
def test_les_etapes_annoncees_sont_celles_que_la_page_connait(plan):
    """Une étape inconnue laisse la barre de progression bloquée au début,
    pendant que l'installation avance : on croit qu'elle est morte."""
    temoin = _fichier(plan, "flambee-temoin")
    installateur = _fichier(plan, "flambee-installer")

    connues = re.findall(
        r'"([^"]+)"', re.search(r"ETAPES = \[(.*?)\]", temoin, re.S).group(1))
    ecrites = [e for e in re.findall(r'etape "([\w-]+)', installateur)
               if e != "Echec"]

    assert not set(ecrites) - set(connues), (
        f"étapes inconnues de la page : {set(ecrites) - set(connues)}")
    assert ecrites == sorted(ecrites, key=connues.index), (
        "l'installateur annonce ses étapes dans le désordre : la progression "
        "reculerait sous les yeux de l'utilisateur")


def test_la_page_d_attente_rend_le_port_avant_que_caddy_le_prenne(plan):
    """Les deux veulent le port 80. Caddy en a besoin pour obtenir le
    certificat ; s'il le trouve occupé, le HTTPS échoue et le site reste
    inaccessible — pour une page d'attente qu'on avait oublié d'éteindre."""
    installateur = _fichier(plan, "flambee-installer")
    arret = installateur.index("systemctl stop flambee-temoin")
    demarrage = installateur.index("docker compose up -d")
    assert arret < demarrage, (
        "le témoin doit rendre le port 80 avant que Caddy démarre")


def test_la_construction_precede_l_arret_du_temoin(plan):
    """C'est l'étape longue, donc la seule qu'on ait envie de suivre. La
    construire après avoir éteint la page, c'est éteindre la page pendant les
    dix minutes qui comptent."""
    installateur = _fichier(plan, "flambee-installer")
    assert installateur.index("docker compose build") < \
        installateur.index("systemctl stop flambee-temoin")


def test_un_arret_laisse_une_page_qui_l_explique(plan):
    """Sans ce filet, une installation morte ressemble à une installation
    lente : on attend devant un écran qui n'avancera plus."""
    installateur = _fichier(plan, "flambee-installer")
    assert "trap 'fin $?' EXIT" in installateur
    assert 'etape "Echec' in installateur


# --- La mise à jour nocturne -----------------------------------------------
def test_la_mise_a_jour_ne_coupe_jamais_un_rendu(plan):
    """Reconstruire redémarre le conteneur. Le faire pendant un rendu perdrait
    le travail en cours — et il tourne la nuit, justement, quand personne ne
    regarde."""
    maj = _fichier(plan, "flambee-mise-a-jour")
    verification = maj.index("travaux_en_cours")
    reconstruction = maj.index("docker compose up -d --build")
    assert verification < reconstruction
    assert 'if [ "$travaux" -gt 0 ]' in maj


def test_la_mise_a_jour_ne_reconstruit_pas_pour_rien(plan):
    """La construction dure plusieurs minutes et coupe le service : la faire
    chaque nuit sans raison serait une panne quotidienne."""
    maj = _fichier(plan, "flambee-mise-a-jour")
    assert 'if [ "$ici" = "$la_bas" ]' in maj


def test_la_mise_a_jour_lit_le_json_avec_un_analyseur(plan):
    """Une expression régulière sur du JSON tombe sur une espace après le
    deux-points. L'échec serait silencieux : le script conclurait « application
    muette » et ne mettrait plus jamais à jour."""
    maj = _fichier(plan, "flambee-mise-a-jour")
    assert "json.load" in maj
    assert not re.search(r'grep -o .*travaux_en_cours', maj)


def test_la_sonde_ignore_le_proxy_pour_joindre_la_machine_elle_meme(plan):
    """Un proxy sortant configuré sur la machine recevrait aussi les appels à
    127.0.0.1, qu'il ne sait pas router."""
    maj = _fichier(plan, "flambee-mise-a-jour")
    assert "127.0.0.1" in maj


def test_le_disque_ne_se_remplit_pas_d_anciennes_images(plan):
    """Docker garde chaque version construite. Le disque de l'offre gratuite
    se remplit en quelques semaines."""
    assert "docker image prune" in _fichier(plan, "flambee-mise-a-jour")


# --- Le compteur que la mise à jour interroge ------------------------------
def test_la_sonde_publie_les_travaux_en_cours(client):
    reponse = client.get("/api/health")
    assert reponse.status_code == 200
    assert reponse.json()["travaux_en_cours"] == 0


def test_un_rendu_en_cours_est_compte(espace, monkeypatch):
    from flambee.project import store
    _poser(espace, 1, "running", 5)
    assert store.travaux_en_cours() == 1


def test_un_rendu_mort_ne_bloque_pas_les_mises_a_jour_pour_toujours(espace):
    """Un rendu tué par un manque de mémoire reste inscrit « running » à
    jamais : sans borne de fraîcheur, une seule vidéo ratée empêcherait toute
    mise à jour, définitivement."""
    from flambee.project import store
    _poser(espace, 1, "running", 7200)
    assert store.travaux_en_cours() == 0


def test_les_travaux_termines_ne_sont_pas_comptes(espace):
    from flambee.project import store
    _poser(espace, 1, "done", 5)
    _poser(espace, 2, "error", 5)
    assert store.travaux_en_cours() == 0


def _poser(espace, compte, etat, age):
    import time
    dossier = espace / "utilisateurs" / str(compte) / "projets" / f"p{compte}"
    dossier.mkdir(parents=True)
    (dossier / "project.json").write_text(json.dumps(
        {"id": f"p{compte}", "owner": compte,
         "job": {"state": etat, "updated_at": time.time() - age}}),
        encoding="utf-8")
