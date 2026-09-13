"""L'essayage public des sous-titres.

Un formulaire ouvert qui déclenche un encodage ffmpeg et écrit un fichier de
sous-titres à partir d'un texte inconnu : les deux méritent des garde-fous.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from flambee import app as app_module, config, media, samples  # noqa: E402


# --- Assainissement du texte ----------------------------------------------
def test_les_directives_ass_sont_neutralisees():
    """Accolades et barres obliques inversées sont des commandes dans un
    fichier ASS : telles quelles, le visiteur choisirait le rendu."""
    sale = "Salut{\\p1}m 0 0 l 999 999{\\p0}"
    propre = samples.nettoyer_texte(sale)
    assert "{" not in propre and "}" not in propre and "\\" not in propre


def test_un_saut_de_ligne_ne_peut_pas_injecter_un_evenement():
    """Une nouvelle ligne termine l'événement : la suite serait lue comme du
    balisage. Elle devient un espace, pas une disparition qui collerait les
    mots."""
    sale = "Bonjour\nDialogue: 0,0:00:00.00,0:00:09.00,Flambee,,0,0,0,,PIRATE"
    propre = samples.nettoyer_texte(sale)
    assert "\n" not in propre
    assert propre.startswith("Bonjour Dialogue")


def test_le_texte_est_borne_et_normalise():
    assert len(samples.nettoyer_texte("a" * 500)) == samples.TEXTE_MAX
    assert samples.nettoyer_texte("  deux   espaces  ") == "deux espaces"
    assert samples.nettoyer_texte("   ") == ""
    # Les accents doivent survivre : c'est un site français.
    assert samples.nettoyer_texte("éàü çœ") == "éàü çœ"


def test_le_minutage_suit_le_nombre_de_mots():
    mots = samples.mots_du_texte("un deux trois")
    assert [m.text for m in mots] == ["un", "deux", "trois"]
    assert mots[0].start == 0.0
    assert mots[-1].end == pytest.approx(3 * samples.WORD_DURATION)


# --- L'endpoint ------------------------------------------------------------
def test_l_essayage_est_accessible_sans_compte(client):
    """C'est l'argument de la page d'accueil : il ne peut pas demander de
    s'inscrire d'abord."""
    if media.ensure_tools():
        pytest.skip("ffmpeg requis")
    reponse = client.get("/api/essayage",
                         params={"texte": "Une phrase à essayer",
                                 "style": "minimal"})
    assert reponse.status_code == 200
    assert reponse.headers["content-type"] == "video/mp4"
    assert len(reponse.content) > 2000


def test_un_texte_vide_ou_un_style_inconnu_sont_refuses(client):
    assert client.get("/api/essayage", params={"texte": "  "}).status_code == 400
    assert client.get("/api/essayage",
                      params={"texte": "bonjour",
                              "style": "inexistant"}).status_code == 404


def test_le_cache_evite_de_reencoder(client, monkeypatch):
    if media.ensure_tools():
        pytest.skip("ffmpeg requis")
    rendus = []
    vrai = samples._rendre_clip
    monkeypatch.setattr(samples, "_rendre_clip",
                        lambda *a, **k: (rendus.append(1), vrai(*a, **k))[1])

    params = {"texte": "Toujours la même phrase", "style": "punch"}
    client.get("/api/essayage", params=params)
    client.get("/api/essayage", params=params)
    assert len(rendus) == 1


def test_un_essayage_en_cache_ne_consomme_pas_de_quota(client, monkeypatch):
    """Sans cela, recharger la page épuiserait le quota sans rien encoder."""
    if media.ensure_tools():
        pytest.skip("ffmpeg requis")
    params = {"texte": "Phrase deja rendue", "style": "minimal"}
    assert client.get("/api/essayage", params=params).status_code == 200

    appels = []
    monkeypatch.setattr(app_module, "_peut_essayer",
                        lambda adresse: appels.append(adresse) or True)
    client.get("/api/essayage", params=params)
    assert appels == [], "le quota ne doit pas être consulté pour un cache"


def test_le_debit_est_limite(client, monkeypatch):
    """Un encodage par frappe ouvrirait la porte à saturer la machine."""
    monkeypatch.setattr(app_module, "_essais", {})
    monkeypatch.setattr(app_module, "_ESSAIS_PAR_MINUTE", 3)
    for _ in range(3):
        assert app_module._peut_essayer("1.2.3.4")
    assert not app_module._peut_essayer("1.2.3.4")
    # …mais une autre adresse n'est pas pénalisée.
    assert app_module._peut_essayer("5.6.7.8")


def test_la_page_d_accueil_porte_le_bloc_d_essayage(client):
    page = client.get("/").text
    assert 'id="essayage"' in page
    assert 'id="essayage-texte"' in page
    # Les six styles doivent être proposés.
    assert page.count('class="puce') == len(config.SUBTITLE_PRESETS)


def test_le_cache_des_essayages_est_plafonne(espace, monkeypatch):
    """Il est alimenté par des inconnus : sans plafond, le disque se remplit."""
    monkeypatch.setattr(samples, "ESSAYAGES_MAX", 3)
    dossier = espace / ".essayages"
    dossier.mkdir(parents=True, exist_ok=True)
    import os
    import time as t
    for i in range(6):
        fichier = dossier / f"clip{i}.mp4"
        fichier.write_bytes(b"x")
        os.utime(fichier, (t.time() + i, t.time() + i))   # du plus ancien au plus récent

    samples._limiter_cache_essayages()
    restants = sorted(f.name for f in dossier.glob("*.mp4"))
    assert restants == ["clip3.mp4", "clip4.mp4", "clip5.mp4"]
