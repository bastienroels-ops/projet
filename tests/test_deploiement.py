"""Ce qui sépare le code qui marche ici du service qui tourne là-bas.

Ces vérifications ne lancent aucun conteneur : elles lisent les fichiers de
déploiement et s'assurent que les promesses qu'ils portent tiennent. Une
erreur à ce niveau ne se voit pas en développement — elle se découvre en
production, souvent le jour où elle a déjà fait des dégâts.
"""

from __future__ import annotations

import re
from pathlib import Path

RACINE = Path(__file__).resolve().parent.parent
COMPOSE = RACINE / "docker-compose.yml"
DOCKERFILE = RACINE / "Dockerfile"
EXEMPLE = RACINE / ".env.exemple"


def _bloc_service(nom: str) -> str:
    """Le bloc d'un service de docker-compose.yml, découpé à l'indentation.

    Volontairement sans analyseur YAML : PyYAML n'est pas une dépendance
    déclarée du projet, et ce test doit tourner sur une installation propre.
    """
    lignes = COMPOSE.read_text(encoding="utf-8").splitlines()
    debut = next(i for i, l in enumerate(lignes) if l.strip() == f"{nom}:")
    marge = len(lignes[debut]) - len(lignes[debut].lstrip())
    for i in range(debut + 1, len(lignes)):
        if lignes[i].strip() and not lignes[i].startswith(" " * (marge + 1)):
            return "\n".join(lignes[debut:i])
    return "\n".join(lignes[debut:])


def test_les_comptes_survivent_a_une_reconstruction():
    """Les données doivent vivre dans le volume, jamais dans l'image.

    La base des comptes est écrite sous FLAMBEE_WORK_DIR. Si ce chemin sortait
    du volume monté, chaque `docker compose up --build` — c'est-à-dire chaque
    mise à jour du site — effacerait tous les comptes, sans un message.
    """
    dockerfile = DOCKERFILE.read_text(encoding="utf-8")
    volume = re.search(r'VOLUME \["([^"]+)"\]', dockerfile).group(1)

    for variable in ("FLAMBEE_WORK_DIR", "FLAMBEE_OUTPUT_DIR"):
        chemin = re.search(rf"{variable}=(\S+)", dockerfile).group(1)
        assert chemin.startswith(volume.rstrip("/") + "/"), (
            f"{variable} vaut {chemin}, hors du volume {volume}")

    assert f":{volume}" in _bloc_service("flambee"), (
        f"le service ne monte aucun volume sur {volume}")


def test_le_conteneur_recoit_tous_les_reglages():
    """Le service doit charger .env en entier.

    Les variables étaient énumérées une à une : celles qu'on documentait sans
    penser à les ajouter ici n'arrivaient jamais à l'application. Les réglages
    SMTP en ont fait les frais — la réinitialisation de mot de passe ne pouvait
    pas envoyer de courriel en production, et rien ne le signalait.
    """
    bloc = _bloc_service("flambee")
    assert "env_file:" in bloc, "le service ne charge pas .env"
    assert re.search(r"^\s+- \.env\s*$", bloc, re.M), \
        "env_file ne cite pas .env"


def test_env_exemple_ne_documente_que_des_reglages_lus():
    """Un réglage documenté que personne ne lit est un piège.

    Le renseigner ne produirait aucun effet, et la recherche de la panne
    commencerait ailleurs.
    """
    code = "\n".join(f.read_text(encoding="utf-8")
                     for f in sorted((RACINE / "flambee").glob("*.py")))
    lues = set(re.findall(r"FLAMBEE_[A-Z_]+|ANTHROPIC_API_KEY", code))
    documentees = set(re.findall(r"^([A-Z][A-Z_]*)=", EXEMPLE.read_text("utf-8"), re.M))
    # DOMAINE n'est pas lu par l'application : c'est Caddy qui s'en sert.
    inconnues = documentees - lues - {"DOMAINE"}
    assert not inconnues, f"documentés mais jamais lus : {sorted(inconnues)}"


def test_l_installation_automatique_ecrit_les_reglages_essentiels():
    """Le fichier cloud-init doit produire un .env complet.

    Sans clé de signature, l'application refuse de démarrer ; sans adresse de
    base, les liens de réinitialisation pointeraient vers nulle part.
    """
    init = (RACINE / "deploiement" / "oracle-cloud-init.yaml").read_text("utf-8")
    for reglage in ("FLAMBEE_SECRET_KEY=", "FLAMBEE_BASE_URL=",
                    "FLAMBEE_SIGNUP=", "FLAMBEE_SMTP_HOTE="):
        assert reglage in init, f"{reglage} absent du .env engendré"


def test_la_sauvegarde_ne_devine_pas_le_nom_du_volume():
    """Une archive vide qui se croit réussie est pire que pas de sauvegarde.

    Docker crée sans un mot un volume vide sous un nom inconnu : un nom écrit
    en dur produit alors une archive de quelques centaines d'octets, chaque
    nuit, jusqu'au jour où l'on en a besoin. Le script doit lire le nom sur le
    conteneur qui tourne, et refuser une archive sans la base des comptes.
    """
    init = (RACINE / "deploiement" / "oracle-cloud-init.yaml").read_text("utf-8")
    debut = init.index("flambee-sauvegarde")
    script = init[debut:init.index("- path:", debut)]

    assert "docker inspect" in script, "le nom du volume n'est pas demandé à Docker"
    assert 'Destination "/donnees"' in script, "le montage cherché n'est pas /donnees"
    assert "travail/flambee.db" in script, \
        "l'archive n'est pas vérifiée pour la base des comptes"
    assert re.search(r'-v "\$VOLUME":/d:ro', script), \
        "le volume devrait être monté en lecture seule pour une sauvegarde"


def test_l_installation_refuse_une_distribution_inattendue():
    """Oracle propose sa propre distribution par défaut, pas Ubuntu.

    Sans ce contrôle, l'installation partait et échouait bien plus loin, sur
    un paquet introuvable — une erreur dont la cause réelle, le choix de
    l'image, n'apparaissait nulle part.
    """
    init = (RACINE / "deploiement" / "oracle-cloud-init.yaml").read_text("utf-8")
    assert "command -v apt-get" in init, "la distribution n'est pas vérifiée"
    assert "Canonical Ubuntu" in init, "le message ne dit pas quoi choisir"
