"""Repartir des mêmes vidéos pour essayer autre chose.

Changer de style après coup obligeait à recommencer depuis les liens :
retéléchargement, ré-analyse des plans, nouvelle synthèse vocale. Rien de tout
cela ne dépend du style.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from flambee import config  # noqa: E402
from flambee.downloader import Source  # noqa: E402
from flambee.project import store  # noqa: E402


def _projet_abouti(owner: int = 0):
    projet = store.create(owner=owner)
    projet.ensure_dirs()
    for i in (0, 1):
        fichier = projet.sources_dir / f"source_{i:02d}.mp4"
        fichier.write_bytes(b"\0" * 4096)
        accroche = projet.hooks_dir / f"hook_{i}.mp4"
        accroche.write_bytes(b"\0" * 512)
        source = Source(index=i, url=f"http://exemple/{i}",
                        path=str(fichier), title=f"vidéo {i}")
        source.hook_path = str(accroche)
        source.duration = 8.0
        projet.sources.append(source)
    projet.hook_index = 1
    projet.recommended_hook = 0
    projet.analyses = {0: {"scenes": [1.0]}, 1: {"scenes": [2.0]}}
    projet.topic = "le sommeil"
    projet.script = "Un script déjà écrit et validé."
    projet.settings = config.RenderSettings(subtitle_preset="neon",
                                            music="Braises.mp3")
    projet.voice_signature = "abc123"
    projet.voice_duration = 9.0
    projet.voice_lead_in = 0.2
    projet.voice_words = [{"text": "Un", "start": 0.0, "end": 0.3}]
    (projet.dir / "voice.mp3").write_bytes(b"ID3" + b"\0" * 2048)
    projet.output_path = str(projet.dir / "final.mp4")
    projet.preview_path = str(projet.dir / "apercu.mp4")
    projet.save()
    return projet


def test_la_variante_reprend_tout_le_travail_deja_fait(espace):
    original = _projet_abouti()
    variante = original.variante()

    assert [s.title for s in variante.sources] == ["vidéo 0", "vidéo 1"]
    assert all(Path(s.path).exists() for s in variante.sources)
    assert variante.hook_index == 1 and variante.recommended_hook == 0
    assert variante.analyses == original.analyses
    assert variante.script == original.script
    assert variante.topic == original.topic
    assert variante.settings.subtitle_preset == "neon"
    assert variante.settings.music == "Braises.mp3"


def test_la_voix_suit_quand_elle_reste_valable(espace):
    """Elle ne dépend que du script et des réglages de voix, que la variante
    ne change pas : la refaire serait du temps perdu."""
    original = _projet_abouti()
    variante = original.variante()

    assert variante.voice_signature == original.voice_signature
    assert variante.voice_words == original.voice_words
    assert variante.voice_lead_in == original.voice_lead_in
    assert Path(variante.voice_path).exists()
    assert Path(variante.voice_path).parent == variante.dir


def test_le_rendu_ne_suit_pas(espace):
    """La variante n'a pas encore de vidéo : c'est tout son objet. Hériter du
    rendu de l'original ferait croire qu'elle est déjà faite."""
    variante = _projet_abouti().variante()
    assert not variante.output_path
    assert not variante.preview_path


def test_les_chemins_pointent_vers_le_dossier_de_la_variante(espace):
    """Sinon deux projets écriraient dans les mêmes fichiers, et nettoyer
    l'un viderait l'autre."""
    original = _projet_abouti()
    variante = original.variante()
    for source in variante.sources:
        assert str(original.dir) not in source.path
        assert str(variante.dir) in source.path
        assert str(variante.dir) in source.hook_path


def test_les_fichiers_sont_lies_pas_copies(espace):
    """Deux noms pour les mêmes octets : une variante ne coûte pas un
    deuxième exemplaire des vidéos sources."""
    original = _projet_abouti()
    variante = original.variante()
    for source in variante.sources:
        assert os.stat(source.path).st_nlink >= 2, source.path


def test_supprimer_l_original_ne_casse_pas_la_variante(espace):
    """Un fichier ne disparaît qu'avec son dernier nom. La rétention efface
    les vieux projets : elle ne doit pas vider les variantes encore vivantes.
    """
    import shutil

    original = _projet_abouti()
    variante = original.variante()
    shutil.rmtree(original.dir)

    for source in variante.sources:
        assert Path(source.path).exists(), source.path
        assert Path(source.path).stat().st_size > 0


def test_la_variante_reste_au_meme_proprietaire(espace):
    original = _projet_abouti(owner=7)
    variante = original.variante()
    assert variante.owner == 7
    assert store.get(variante.id, 7) is not None
    assert store.get(variante.id, 8) is None


def test_modifier_la_variante_ne_touche_pas_a_l_original(espace):
    """`RenderSettings` se modifie sur place : partagé, il ferait changer le
    style de l'original quand on change celui de la variante.

    Le test écrit dans le champ plutôt que de remplacer l'objet — remplacer
    ne prouverait rien, c'est justement l'écriture sur place qui est le
    danger."""
    original = _projet_abouti()
    variante = original.variante()

    variante.settings.subtitle_preset = "punch"
    variante.settings.music = None
    assert original.settings.subtitle_preset == "neon"
    assert original.settings.music == "Braises.mp3"


def test_les_analyses_ne_sont_pas_partagees(espace):
    """Même raison : un dictionnaire partagé se modifie des deux côtés."""
    original = _projet_abouti()
    variante = original.variante()
    variante.analyses[0] = {"scenes": [99.0]}
    assert original.analyses[0] == {"scenes": [1.0]}


def test_le_minutage_de_la_voix_n_est_pas_partage(espace):
    original = _projet_abouti()
    variante = original.variante()
    variante.voice_words[0]["text"] = "Deux"
    assert original.voice_words[0]["text"] == "Un"


# --- Par l'API -------------------------------------------------------------
def test_la_route_rend_un_projet_neuf(compte, monkeypatch):
    from flambee import app as app_module

    projet = compte.post("/api/projects").json()
    original = store.get(projet["id"], compte.get(
        f"/api/projects/{projet['id']}").json()["owner"])
    original.ensure_dirs()
    fichier = original.sources_dir / "source_00.mp4"
    fichier.write_bytes(b"\0" * 2048)
    source = Source(index=0, url="http://x/0", path=str(fichier), title="v")
    source.duration = 8.0
    original.sources.append(source)
    original.save()

    reponse = compte.post(f"/api/projects/{projet['id']}/variante")
    assert reponse.status_code == 200
    neuf = reponse.json()
    assert neuf["id"] != projet["id"]
    assert [s["title"] for s in neuf["sources"]] == ["v"]


def test_un_projet_sans_video_ne_donne_pas_de_variante(compte):
    """Il n'y aurait rien à reprendre : le bouton mentirait."""
    projet = compte.post("/api/projects").json()["id"]
    assert compte.post(f"/api/projects/{projet}/variante").status_code == 400
