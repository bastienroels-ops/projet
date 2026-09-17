"""L'écran scindé : le montage en haut, une boucle de jeu en bas.

Le format le plus répandu du moment. Deux choses doivent tenir : la
géométrie — deux bandes paires dont la somme fait exactement la hauteur — et
la place des sous-titres, qui ne doivent pas tomber dans la vidéo du bas.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from flambee import assembler, config, media, pipeline, subtitles  # noqa: E402
from flambee.downloader import Source  # noqa: E402
from flambee.trimmer import Segment  # noqa: E402


# --- Géométrie -------------------------------------------------------------
@pytest.mark.parametrize("hauteur", [1920, 960, 1280])
@pytest.mark.parametrize("ratio", [0.25, 0.4, 0.5, 0.62, 0.85])
def test_les_deux_bandes_sont_paires_et_font_la_hauteur(hauteur, ratio):
    """Une bande de hauteur impaire fait échouer l'encodage en yuv420p, et une
    somme fausse d'un pixel fait échouer `vstack`."""
    haut, bas = media.split_bands(hauteur, ratio)
    assert haut % 2 == 0 and bas % 2 == 0
    assert haut + bas == hauteur
    assert haut > 0 and bas > 0


@pytest.mark.parametrize("ratio", [-1.0, 0.0, 0.05, 0.99, 5.0])
def test_un_partage_absurde_est_ramene_dans_les_clous(ratio):
    """Une bande de deux pixels ne montre rien : le réglage est borné."""
    haut, bas = media.split_bands(1920, ratio)
    assert haut + bas == 1920
    assert min(haut, bas) >= 1920 * 0.15


# --- Graphe ffmpeg ---------------------------------------------------------
def _graphe(**extra):
    source = Source(index=0, url="x", path="/tmp/x.mp4", title="x")
    source.duration, source.has_audio = 10.0, False
    segments = [Segment(source_index=0, start=0.0, duration=6.0)]
    reglages = config.RenderSettings(subtitles=False, music=None, motion=False,
                                     **extra)
    return assembler.build_graph(
        segments, {0: source}, voice_path=None, subtitle_path=None,
        music_path=None, settings=reglages, duration=6.0, fmt=config.FORMAT,
        split_path=Path("/tmp/jeu.mp4") if extra.get("split_clip") else None)


def test_sans_fond_le_montage_occupe_tout_le_cadre():
    graphe = _graphe()
    assert "vstack" not in ";".join(graphe.filters)
    assert f"crop={config.FORMAT.width}:{config.FORMAT.height}" in \
        ";".join(graphe.filters)


def test_avec_un_fond_les_deux_bandes_sont_empilees():
    graphe = _graphe(split_clip="jeu.mp4", split_ratio=0.62)
    filtres = ";".join(graphe.filters)
    haut, bas = media.split_bands(config.FORMAT.height, 0.62)
    assert f"crop={config.FORMAT.width}:{haut}" in filtres, "bande du montage"
    assert f"crop={config.FORMAT.width}:{bas}" in filtres, "bande du compagnon"
    assert "vstack=inputs=2" in filtres


def test_le_fond_est_boucle_et_coupe_a_la_duree_du_montage():
    """Une boucle de jeu de trente secondes doit tenir sous un script d'une
    minute sans qu'on ait à s'en occuper."""
    graphe = _graphe(split_clip="jeu.mp4")
    entrees = " ".join(graphe.inputs)
    assert "-stream_loop -1" in entrees
    assert "-t 6.000 -i /tmp/jeu.mp4" in entrees


def test_l_ordre_d_empilement_suit_le_reglage():
    bas = ";".join(_graphe(split_clip="jeu.mp4", split_bottom=True).filters)
    haut = ";".join(_graphe(split_clip="jeu.mp4", split_bottom=False).filters)
    assert "[v0][vcmp]vstack" in bas, "le compagnon en bas"
    assert "[vcmp][v0]vstack" in haut, "le compagnon en haut"


def test_le_son_du_fond_n_entre_pas_dans_le_mixage():
    """Deux ambiances sous une voix off, c'en est une de trop."""
    graphe = _graphe(split_clip="jeu.mp4", keep_source_audio=True)
    filtres = ";".join(graphe.filters)
    entree_fond = graphe.inputs.index("/tmp/jeu.mp4")
    numero = graphe.inputs[:entree_fond].count("-i")
    assert f"[{numero}:a]" not in filtres


# --- Sous-titres -----------------------------------------------------------
def test_les_sous_titres_remontent_au_dessus_de_la_couture():
    """Laissés à leur marge d'origine, ils tomberaient au milieu de la vidéo
    du bas : illisibles, et posés sur ce qui bouge le plus dans l'image."""
    style = config.subtitle_style("punch")
    reglages = config.RenderSettings(split_clip="jeu.mp4", split_ratio=0.62)
    place = subtitles.style_pour_ecran_scinde(style, reglages, config.FORMAT)
    _, compagnon = media.split_bands(config.FORMAT.height, 0.62)
    assert place.margin_v > compagnon, "le texte est dans la bande du jeu"
    assert place.margin_v < compagnon + style.font_size, "trop haut, il décolle"


def test_le_compagnon_en_haut_ne_deplace_pas_les_sous_titres():
    """Le montage occupe alors le bas du cadre, là où la marge d'origine
    place déjà le texte."""
    style = config.subtitle_style("punch")
    reglages = config.RenderSettings(split_clip="jeu.mp4", split_bottom=False)
    assert subtitles.style_pour_ecran_scinde(
        style, reglages, config.FORMAT) is style


def test_sans_ecran_scinde_le_style_n_est_pas_touche():
    style = config.subtitle_style("neon")
    assert subtitles.style_pour_ecran_scinde(
        style, config.RenderSettings(), config.FORMAT) is style


# --- Bibliothèque ----------------------------------------------------------
def test_un_chemin_qui_sort_de_la_bibliotheque_est_refuse():
    assert pipeline.fond_path("../../etc/passwd") is None
    assert pipeline.fond_path("") is None
    assert pipeline.fond_path(None) is None


def test_le_fond_du_projet_demande_un_projet():
    """L'identifiant réservé ne désigne rien sans le projet qui le porte."""
    assert pipeline.fond_path(pipeline.FOND_DU_PROJET) is None
