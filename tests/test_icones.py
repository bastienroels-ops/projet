"""Le jeu d'icônes."""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from flambee import icones, plans  # noqa: E402


def test_chaque_icone_rend_un_svg_valide():
    for nom in icones.ICONES:
        rendu = str(icones.icone(nom))
        assert rendu.startswith("<svg") and rendu.endswith("</svg>")
        assert 'viewBox="0 0 24 24"' in rendu
        # Le dessin doit contenir de la géométrie, pas seulement un cadre vide.
        assert re.search(r"<(path|circle|rect)", rendu), nom


def test_les_icones_sont_masquees_aux_lecteurs_d_ecran():
    """Elles accompagnent toujours un libellé écrit : annoncées en plus, elles
    encombreraient la lecture à voix haute sans rien apporter."""
    for nom in icones.ICONES:
        assert 'aria-hidden="true"' in str(icones.icone(nom))


def test_une_icone_inconnue_se_signale():
    """Mieux vaut une erreur au rendu qu'un trou silencieux dans la page."""
    with pytest.raises(KeyError):
        icones.icone("nexistepas")


def test_la_taille_et_la_classe_sont_reportees():
    rendu = str(icones.icone("micro", taille=28, classe="grande"))
    assert 'width="28"' in rendu and 'height="28"' in rendu
    assert 'class="icone grande"' in rendu


def test_chaque_atout_designe_une_icone_existante():
    """Le champ `icon` porte un nom du jeu, plus un emoji : une faute de frappe
    doit tomber ici, pas sur la page d'accueil d'un visiteur."""
    for atout in plans.FEATURES:
        assert atout.icon in icones.ICONES, atout.icon


def test_les_gabarits_n_utilisent_plus_d_emoji_d_interface():
    """Le dessin d'un emoji appartient au système du visiteur : trois
    appareils donnaient trois interfaces."""
    racine = Path(__file__).resolve().parent.parent / "flambee" / "templates"
    suspects = "🔥📄🎙🎞📖🫂💳🪙⚙👤🔒⭐💎🎬✂⚡✍🎚📱"
    fautifs = []
    for gabarit in racine.rglob("*.html"):
        texte = gabarit.read_text(encoding="utf-8")
        for ligne in texte.splitlines():
            if any(c in ligne for c in suspects):
                fautifs.append(f"{gabarit.name}: {ligne.strip()[:70]}")
    assert not fautifs, "emoji restants :\n" + "\n".join(fautifs)
