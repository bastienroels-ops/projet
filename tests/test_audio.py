"""Le son d'origine survit au rendu, seul ou sous une voix off.

Les autres tests de rendu posent une voix factice *silencieuse* : ils vérifient
qu'un fichier audio existe, pas ce qu'il contient. Ici chaque piste est une
sinusoïde de fréquence propre — le son d'origine à 440 Hz, celui de la seconde
vidéo à 660 Hz, la voix à 1 000 Hz — et l'on mesure, dans le fichier final,
l'énergie à chacune de ces fréquences. Une piste absente se lit tout de suite.
"""

from __future__ import annotations

import math
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from flambee import assembler, config, media, pipeline, trimmer  # noqa: E402
from flambee.downloader import Source  # noqa: E402
from flambee.project import store  # noqa: E402
from flambee.voice import VoiceTrack, Word  # noqa: E402

pytestmark = pytest.mark.skipif(bool(media.ensure_tools()),
                                reason="ffmpeg/ffprobe requis")

SOURCE_HZ, SECONDE_HZ, VOIX_HZ = 440, 660, 1000
SECONDES = 5


# --- Fabrication et mesure ---------------------------------------------------
def _video(path: Path, *, hz: int | None, amplitude: float = 0.25,
           secondes: int = SECONDES, taille: str = "360x640") -> Source:
    entrees = ["-f", "lavfi", "-i",
               f"testsrc=size={taille}:rate=30:duration={secondes}"]
    if hz:
        entrees += ["-f", "lavfi", "-i",
                    f"aevalsrc={amplitude}*sin(2*PI*{hz}*t):s=48000:d={secondes}"]
    media.ffmpeg([*entrees, "-c:v", "libx264", "-preset", "ultrafast",
                  "-pix_fmt", "yuv420p", *(["-c:a", "aac"] if hz else []),
                  str(path)])
    info = media.probe(path)
    return Source(index=1, url="x", path=str(path), title="v",
                  duration=info.duration, width=info.width, height=info.height,
                  has_audio=info.has_audio)


def _voix(path: Path, secondes: float = 3.0) -> VoiceTrack:
    media.ffmpeg(["-f", "lavfi", "-i",
                  f"aevalsrc=0.3*sin(2*PI*{VOIX_HZ}*t):s=48000:d={secondes}",
                  str(path)])
    mots = [Word(text="Bonjour", start=0.2, end=0.8),
            Word(text="tout", start=0.9, end=1.3),
            Word(text="le monde", start=1.4, end=2.4)]
    return VoiceTrack(path=str(path), duration=secondes, words=mots,
                      voice="test", lead_in=0.0)


def _niveau_db(chemin, hz: int, debut: float = 0.8, fin: float = 2.6) -> float:
    """Niveau (dBFS) de la sinusoïde `hz` dans le fichier, sur [debut, fin]."""
    brut = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(chemin), "-ac", "1", "-ar", "48000",
         "-f", "f32le", "-"], capture_output=True, check=True).stdout
    signal = np.frombuffer(brut, dtype=np.float32)[int(debut * 48000):int(fin * 48000)]
    assert signal.size, "aucun échantillon audio dans le rendu"
    t = np.arange(signal.size) / 48000
    # Détection synchrone : insensible aux autres fréquences du mixage.
    c = np.sum(signal * np.cos(2 * math.pi * hz * t)) * 2 / signal.size
    s = np.sum(signal * np.sin(2 * math.pi * hz * t)) * 2 / signal.size
    amplitude = math.hypot(c, s)
    return 20 * math.log10(max(amplitude, 1e-9))


def _rendre(source: Source, tmp_path, *, voix: VoiceTrack | None = None,
            **reglages) -> Path:
    """Rend `source` seule (un extrait) via l'assemblage, comme le fait le pipeline."""
    segment = trimmer.Segment(source_index=source.index, start=0.0,
                              duration=float(SECONDES), is_hook=True)
    sortie = tmp_path / f"sortie_{len(list(tmp_path.glob('sortie_*')))}.mp4"
    assembler.render(
        [segment], [source], sortie,
        voice_path=Path(voix.path) if voix else None,
        subtitle_path=None, music_path=None,
        settings=config.RenderSettings(subtitles=False, motion=False, **reglages),
        duration=float(SECONDES), fast=True)
    return sortie


@pytest.fixture()
def source(tmp_path):
    return _video(tmp_path / "s.mp4", hz=SOURCE_HZ)


@pytest.fixture()
def voix(tmp_path):
    return _voix(tmp_path / "voix.wav")


# --- Test A : audio d'origine seul --------------------------------------------
def test_A_sans_voix_off_le_son_d_origine_reste_a_100_pourcent(source, tmp_path):
    original = _niveau_db(source.path, SOURCE_HZ)
    sortie = _rendre(source, tmp_path)
    assert _niveau_db(sortie, SOURCE_HZ) == pytest.approx(original, abs=1.5)
    assert _niveau_db(sortie, VOIX_HZ) < -60


def test_A_le_curseur_ne_baisse_pas_un_son_sans_voix_off(source, tmp_path):
    """Sans voix off, 100 % de l'original : le curseur « sous la voix » ne joue pas."""
    original = _niveau_db(source.path, SOURCE_HZ)
    sortie = _rendre(source, tmp_path, source_audio_volume=0.1)
    assert _niveau_db(sortie, SOURCE_HZ) == pytest.approx(original, abs=1.5)


def test_A_les_reglages_par_defaut_gardent_le_son(source, tmp_path):
    """Le défaut est de garder le son d'origine — c'était le contraire."""
    assert config.RenderSettings().keep_source_audio is True
    segment = trimmer.Segment(source_index=1, start=0.0, duration=float(SECONDES))
    sortie = tmp_path / "defaut.mp4"
    assembler.render([segment], [source], sortie, voice_path=None,
                     subtitle_path=None, music_path=None,
                     settings=config.RenderSettings(subtitles=False, motion=False),
                     duration=float(SECONDES), fast=True)
    assert _niveau_db(sortie, SOURCE_HZ) > -25


# --- Test B / D : voix off + son d'origine ------------------------------------
def test_B_voix_off_et_son_d_origine_sont_tous_deux_presents(source, voix, tmp_path):
    original = _niveau_db(source.path, SOURCE_HZ)
    sortie = _rendre(source, tmp_path, voix=voix)
    assert _niveau_db(sortie, VOIX_HZ) > -30          # la voix s'entend
    niveau = _niveau_db(sortie, SOURCE_HZ)
    assert niveau > -45                               # l'original aussi
    # Réglage par défaut : sous la voix, à 35 % (≈ -9 dB), pas à 5 %.
    assert niveau - original == pytest.approx(20 * math.log10(0.35), abs=2.5)


def test_D_un_son_asmr_discret_reste_audible_derriere_la_voix(tmp_path, voix):
    asmr = _video(tmp_path / "asmr.mp4", hz=SOURCE_HZ, amplitude=0.06)
    sortie = _rendre(asmr, tmp_path, voix=voix)
    niveau_asmr = _niveau_db(sortie, SOURCE_HZ)
    niveau_voix = _niveau_db(sortie, VOIX_HZ)
    assert niveau_asmr > -50, "l'ASMR a disparu du rendu"
    assert niveau_voix - niveau_asmr < 30, "l'ASMR est noyé sous la voix"


def test_les_curseurs_de_volume_agissent_separement(source, voix, tmp_path):
    base = _rendre(source, tmp_path, voix=voix)
    fort = _rendre(source, tmp_path, voix=voix, source_audio_volume=0.7)
    voix_basse = _rendre(source, tmp_path, voix=voix, voice_volume=0.5)
    coupe = _rendre(source, tmp_path, voix=voix, source_audio_volume=0.0)
    sans = _rendre(source, tmp_path, voix=voix, keep_source_audio=False)

    # Volume de la vidéo : 0,7 / 0,35 = +6 dB, la voix ne bouge pas.
    assert (_niveau_db(fort, SOURCE_HZ) - _niveau_db(base, SOURCE_HZ)
            == pytest.approx(6.0, abs=1.0))
    assert (_niveau_db(fort, VOIX_HZ)
            == pytest.approx(_niveau_db(base, VOIX_HZ), abs=1.0))
    # Volume de la voix : -6 dB, l'original ne bouge pas.
    assert (_niveau_db(voix_basse, VOIX_HZ) - _niveau_db(base, VOIX_HZ)
            == pytest.approx(-6.0, abs=1.0))
    assert (_niveau_db(voix_basse, SOURCE_HZ)
            == pytest.approx(_niveau_db(base, SOURCE_HZ), abs=1.0))
    # Volume à 0 ou son d'origine décoché : muet — c'est un choix explicite.
    assert _niveau_db(coupe, SOURCE_HZ) < -60
    assert _niveau_db(sans, SOURCE_HZ) < -60
    assert _niveau_db(coupe, VOIX_HZ) > -30


def test_la_musique_garde_son_propre_curseur(source, voix, tmp_path):
    musique = tmp_path / "musique.wav"
    media.ffmpeg(["-f", "lavfi", "-i", "aevalsrc=0.3*sin(2*PI*220*t):s=48000:d=5",
                  str(musique)])
    segment = trimmer.Segment(source_index=1, start=0.0, duration=float(SECONDES))
    niveaux = []
    for volume in (0.1, 0.4):
        sortie = tmp_path / f"musique_{volume}.mp4"
        assembler.render(
            [segment], [source], sortie, voice_path=Path(voix.path),
            subtitle_path=None, music_path=musique,
            settings=config.RenderSettings(subtitles=False, motion=False,
                                           music_volume=volume),
            duration=float(SECONDES), fast=True)
        niveaux.append((_niveau_db(sortie, 220), _niveau_db(sortie, SOURCE_HZ),
                        _niveau_db(sortie, VOIX_HZ)))
    assert niveaux[1][0] - niveaux[0][0] == pytest.approx(12.0, abs=2.0)  # 0,4 / 0,1
    assert all(n[1] > -45 and n[2] > -30 for n in niveaux)   # le reste ne bouge pas


# --- Test B / C : le parcours complet d'une vidéo seule -----------------------
def _projet_solo(tmp_path, monkeypatch, voix: VoiceTrack | None, **reglages):
    projet = store.create(owner=0)
    projet.ensure_dirs()
    src = _video(projet.sources_dir / "source_01.mp4", hz=SOURCE_HZ)
    projet.sources = [src]
    projet.hook_index = 1
    projet.settings = config.RenderSettings(motion=False, **reglages)
    if voix is not None:
        projet.script = "Bonjour tout le monde"
        monkeypatch.setattr(pipeline, "_voice_track",
                            lambda *a, **k: voix)
    return projet


def test_B_video_seule_avec_voix_off_via_le_pipeline(espace, tmp_path, monkeypatch, voix):
    projet = _projet_solo(tmp_path, monkeypatch, voix, subtitles=False)
    assert projet.solo and pipeline.voix_off_active(projet)

    pipeline.run_render(projet, fast=True)

    assert projet.job.state == "done", projet.job.error
    assert projet.voice_path == voix.path
    assert _niveau_db(projet.preview_path, VOIX_HZ) > -30
    assert _niveau_db(projet.preview_path, SOURCE_HZ) > -45
    assert media.probe(projet.preview_path).duration == pytest.approx(SECONDES, abs=0.5)


def test_A_video_seule_sans_voix_off_via_le_pipeline(espace, tmp_path, monkeypatch):
    projet = _projet_solo(tmp_path, monkeypatch, None, subtitles=False)
    assert not pipeline.voix_off_active(projet)
    pipeline.run_render(projet, fast=True)
    assert projet.job.state == "done", projet.job.error
    assert projet.voice_path == ""
    assert _niveau_db(projet.preview_path, SOURCE_HZ) > -25


def test_C_sous_titres_et_voix_off_et_son_d_origine_ensemble(
        espace, tmp_path, monkeypatch, voix):
    projet = _projet_solo(tmp_path, monkeypatch, voix, subtitles=True)
    pipeline.run_render(projet, fast=True)

    assert projet.job.state == "done", projet.job.error
    # Les sous-titres suivent la voix off …
    assert projet.subtitle_path and Path(projet.subtitle_path).exists()
    ass = Path(projet.subtitle_path).read_text(encoding="utf-8")
    assert "Bonjour" in ass.replace("\\N", " ") or "BONJOUR" in ass.upper()
    # … et le rendu porte les deux sons.
    assert _niveau_db(projet.preview_path, VOIX_HZ) > -30
    assert _niveau_db(projet.preview_path, SOURCE_HZ) > -45


def test_une_voix_off_plus_longue_que_la_video_est_signalee(
        espace, tmp_path, monkeypatch):
    longue = _voix(tmp_path / "longue.wav", secondes=8.0)
    projet = _projet_solo(tmp_path, monkeypatch, longue, subtitles=False)
    pipeline.run_render(projet, fast=True)
    assert projet.job.state == "done", projet.job.error
    assert "coupée" in projet.job.message
    assert media.probe(projet.preview_path).duration == pytest.approx(SECONDES, abs=0.5)


# --- Test E : le montage de 2 vidéos ou plus -----------------------------------
def _deux_sources(tmp_path) -> list[Source]:
    a = _video(tmp_path / "a.mp4", hz=SOURCE_HZ)
    b = _video(tmp_path / "b.mp4", hz=SECONDE_HZ)
    b.index = 2
    return [a, b]


def test_E_montage_de_deux_videos_avec_voix_off(tmp_path, voix):
    sources = _deux_sources(tmp_path)
    segments = [trimmer.Segment(source_index=1, start=0.0, duration=2.5, is_hook=True),
                trimmer.Segment(source_index=2, start=0.0, duration=2.5)]
    sortie = tmp_path / "montage.mp4"
    assembler.render(segments, sources, sortie, voice_path=Path(voix.path),
                     subtitle_path=None, music_path=None,
                     settings=config.RenderSettings(subtitles=False, motion=False),
                     duration=5.0, fast=True)
    # Première moitié : source 1 ; seconde : source 2 ; la voix (3 s) couvre le début.
    assert _niveau_db(sortie, SOURCE_HZ, 0.5, 2.2) > -45
    assert _niveau_db(sortie, SECONDE_HZ, 3.0, 4.8) > -45
    assert _niveau_db(sortie, VOIX_HZ, 0.5, 2.5) > -30


def test_E_le_montage_garde_son_choix_de_couper_le_son_d_origine(tmp_path, voix):
    """Décocher « garder le son d'origine » reste possible en montage."""
    sources = _deux_sources(tmp_path)
    segments = [trimmer.Segment(source_index=1, start=0.0, duration=2.5, is_hook=True),
                trimmer.Segment(source_index=2, start=0.0, duration=2.5)]
    sortie = tmp_path / "muet.mp4"
    assembler.render(segments, sources, sortie, voice_path=Path(voix.path),
                     subtitle_path=None, music_path=None,
                     settings=config.RenderSettings(subtitles=False, motion=False,
                                                    keep_source_audio=False),
                     duration=5.0, fast=True)
    assert _niveau_db(sortie, SOURCE_HZ, 0.5, 2.2) < -60
    assert _niveau_db(sortie, VOIX_HZ, 0.5, 2.5) > -30


def test_E_le_repli_multi_passes_n_applique_le_volume_qu_une_fois(
        source, voix, tmp_path):
    """Le chemin de secours baissait le son deux fois (par extrait, puis au
    mixage) : 35 % devenaient 12 %."""
    reglages = config.RenderSettings(subtitles=False, motion=False)
    segment = trimmer.Segment(source_index=1, start=0.0, duration=float(SECONDES))
    direct = _rendre(source, tmp_path, voix=voix)

    clip = trimmer.render_segment(segment, source, tmp_path / "clip.mp4",
                                  settings=reglages)
    replie = tmp_path / "replie.mp4"
    assembler.finalize(clip, replie, voice_path=Path(voix.path),
                       subtitle_path=None, music_path=None, settings=reglages,
                       duration=float(SECONDES))
    assert (_niveau_db(replie, SOURCE_HZ)
            == pytest.approx(_niveau_db(direct, SOURCE_HZ), abs=1.5))
