"""Ce que l'utilisateur lit quand un téléchargement échoue.

Les conseils s'adressaient à quelqu'un devant un terminal — « configure
FLAMBEE_COOKIES_FROM_BROWSER » — alors que Flambée se pilote depuis un
téléphone. La seule action possible de là, c'est d'enregistrer la vidéo dans
l'application puis de l'importer. Ces tests tiennent le message, pas le code.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from flambee.downloader import _clean_ydl_error, _sans_prefixe  # noqa: E402


@pytest.mark.parametrize("brut, attendu", [
    ("[TikTok] 123: Unable to extract webpage", "Unable to extract webpage"),
    ("[youtube] dQw4w9WgXcQ: Video unavailable", "Video unavailable"),
    # Pas un identifiant : il y a une espace avant le deux-points.
    ("[generic] Unsupported URL: https://x.fr/@moi",
     "Unsupported URL: https://x.fr/@moi"),
    # Pas un identifiant non plus : un vrai mot, sans chiffre et court.
    ("Warning: something happened", "Warning: something happened"),
])
def test_le_prefixe_technique_disparait(brut, attendu):
    """« [TikTok] 7234567890 : » n'apprend rien à qui lit — la liste des
    sources dit déjà quel lien a échoué."""
    assert _sans_prefixe(brut) == attendu


def test_une_video_privee_n_est_pas_renvoyee_vers_l_import():
    """« Private video. Sign in if you have been granted access » contient les
    deux mots-clés. Conseiller de l'enregistrer depuis l'application serait un
    mauvais conseil : elle ne s'y enregistre pas non plus."""
    message = _clean_ydl_error(
        "ERROR: [youtube] xyz9876: Private video. Sign in if granted")
    assert "privée" in message
    assert "Enregistre-la" not in message


@pytest.mark.parametrize("brut", [
    "ERROR: [youtube] abc123: Sign in to confirm you are not a bot. See https://…",
    "ERROR: unable to download video data: HTTP Error 403: Forbidden",
    "ERROR: [TikTok] 999: Login required",
])
def test_un_refus_de_plateforme_renvoie_vers_l_import(brut):
    """C'est la seule action possible depuis un téléphone, et elle marche
    toujours."""
    message = _clean_ydl_error(brut)
    assert "importer" in message.lower()


def test_aucun_conseil_ne_parle_de_variable_d_environnement():
    """Flambée se pilote depuis Safari sur un iPhone. Un conseil qu'on ne peut
    pas suivre est pire qu'un message brut : il fait croire à une solution."""
    from flambee import downloader

    for _, conseil in downloader._ERROR_HINTS:
        assert "FLAMBEE_" not in conseil, conseil
        assert "pip install" not in conseil, conseil


def test_le_lien_de_documentation_de_yt_dlp_est_coupe():
    """« See https://github.com/yt-dlp/… » n'aide personne ici."""
    message = _clean_ydl_error(
        "ERROR: [youtube] a1: Sign in to confirm you are not a bot. "
        "See https://github.com/yt-dlp/yt-dlp/wiki for more")
    assert "github" not in message


def test_un_message_inconnu_est_rendu_tel_quel_mais_propre():
    assert _clean_ydl_error("ERROR: [x] 42: quelque chose d'inattendu") == \
        "Quelque chose d'inattendu"
