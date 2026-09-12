"""Tests hors ligne : on fabrique des vidéos sources avec ffmpeg.

Aucun téléchargement ni appel réseau n'est nécessaire ; seule la synthèse
vocale (edge-tts) est remplacée par un minutage simulé.
"""

from __future__ import annotations

import sys
import threading
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from flambee import assembler, config, media, subtitles, trimmer  # noqa: E402
from flambee.downloader import Source, normalize_urls, validate_urls  # noqa: E402
from flambee.downloader import DownloadError  # noqa: E402
from flambee.voice import Word, _estimate_words, clean_script  # noqa: E402

pytestmark = pytest.mark.skipif(
    bool(media.ensure_tools()), reason="ffmpeg/ffprobe requis"
)


def make_video(path: Path, seconds: int = 12, size: str = "720x1280") -> Path:
    """Génère une vidéo de test (mire + bip) avec ffmpeg."""
    media.ffmpeg([
        "-f", "lavfi", "-i", f"testsrc=size={size}:rate=30:duration={seconds}",
        "-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}",
        "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-shortest", str(path),
    ])
    return path


@pytest.fixture(scope="module")
def sources(tmp_path_factory) -> list[Source]:
    folder = tmp_path_factory.mktemp("sources")
    built = []
    for index, (seconds, size) in enumerate(((14, "720x1280"), (20, "1280x720")), 1):
        path = make_video(folder / f"source_{index:02d}.mp4", seconds, size)
        info = media.probe(path)
        built.append(Source(
            index=index, url=f"https://example.test/{index}", path=str(path),
            title=f"Source {index}", duration=info.duration,
            width=info.width, height=info.height,
        ))
    return built


# --- Étape 1 --------------------------------------------------------------
def test_normalize_urls_dedupe_et_filtre():
    urls = normalize_urls("https://a.test/1\n  https://b.test/2 , https://a.test/1 blah")
    assert urls == ["https://a.test/1", "https://b.test/2"]


def test_validate_urls_bornes():
    with pytest.raises(DownloadError):
        validate_urls(["https://a.test/1"])
    with pytest.raises(DownloadError):
        validate_urls([f"https://a.test/{i}" for i in range(6)])
    validate_urls([f"https://a.test/{i}" for i in range(3)])


def test_probe_corrige_la_rotation(sources):
    assert media.probe(sources[0].path).is_vertical


# --- Étape 2 --------------------------------------------------------------
def test_extract_hook_produit_un_apercu_vertical(sources, tmp_path):
    hook = trimmer.extract_hook(sources[0], tmp_path, duration=2)
    info = media.probe(hook)
    assert 1.8 <= info.duration <= 2.4
    assert (info.width, info.height) == (trimmer.PREVIEW_WIDTH, trimmer.PREVIEW_HEIGHT)


def test_plan_segments_couvre_la_duree_cible(sources):
    segments = trimmer.plan_segments(sources, hook_index=2, target_duration=25, seed=7)
    assert segments[0].is_hook and segments[0].source_index == 2
    assert sum(s.duration for s in segments) == pytest.approx(25, abs=0.5)
    for segment in segments:
        source = next(s for s in sources if s.index == segment.source_index)
        assert segment.start + segment.duration <= source.duration + 0.01


# --- Étape 3 / 5 ----------------------------------------------------------
@pytest.mark.parametrize("mask", [None, "blur", "black"])
def test_render_segment_normalise_au_format_9_16(sources, tmp_path, mask):
    settings = config.RenderSettings(
        mask_source_subtitles=mask is not None, mask_mode=mask or "blur"
    )
    segment = trimmer.Segment(source_index=sources[1].index, start=1.0, duration=2.0)
    out = trimmer.render_segment(segment, sources[1], tmp_path / f"c_{mask}.mp4",
                                 settings=settings)
    info = media.probe(out)
    assert (info.width, info.height) == (config.FORMAT.width, config.FORMAT.height)
    assert info.has_audio
    assert 1.8 <= info.duration <= 2.3


def _fake_voice(path: Path, seconds: float) -> Path:
    """Une voix off factice : un silence, suffisant pour valider le mixage."""
    media.ffmpeg(["-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo",
                  "-t", f"{seconds}", "-c:a", "aac", str(path)])
    return path


def test_rendu_en_une_passe(sources, tmp_path):
    """Le chemin principal : un seul ffmpeg du découpage au fichier final."""
    settings = config.RenderSettings(subtitles=True, motion=True)
    segments = trimmer.plan_segments(sources, hook_index=1, target_duration=9, seed=3)
    words = _estimate_words("Voici une astuce simple et redoutable pour tout changer", 8)
    ass_path = subtitles.write_ass(words, tmp_path / "subs.ass", max_duration=9)

    result = assembler.render(
        segments, sources, tmp_path / "final.mp4",
        voice_path=_fake_voice(tmp_path / "voice.m4a", 8.5),
        subtitle_path=ass_path, music_path=None,
        settings=settings, duration=9, fast=True,
    )
    assert result.duration == pytest.approx(9, abs=0.4)
    assert (result.width, result.height) == (config.FORMAT.width, config.FORMAT.height)
    assert media.probe(Path(result.path)).has_audio


def test_rendu_une_passe_avec_musique_et_ambiance(sources, tmp_path):
    """Ducking de la musique sous la voix + ambiance des sources."""
    settings = config.RenderSettings(
        subtitles=False, keep_source_audio=True, music_volume=0.2, motion=False
    )
    music = tmp_path / "music.m4a"
    media.ffmpeg(["-f", "lavfi", "-i", "sine=frequency=220", "-t", "3",
                  "-c:a", "aac", str(music)])
    segments = [trimmer.Segment(source_index=1, start=0, duration=3, is_hook=True),
                trimmer.Segment(source_index=2, start=2, duration=3)]

    result = assembler.render(
        segments, sources, tmp_path / "final2.mp4",
        voice_path=_fake_voice(tmp_path / "voice2.m4a", 5.5),
        subtitle_path=None, music_path=music,
        settings=settings, duration=6, fast=True,
    )
    assert result.duration == pytest.approx(6, abs=0.4)
    assert media.probe(Path(result.path)).has_audio


def test_rendu_respecte_la_progression_et_l_annulation(sources, tmp_path):
    """La progression remonte, et un rendu annulé s'arrête sans fichier valable."""
    seen: list[float] = []
    segments = [trimmer.Segment(source_index=1, start=0, duration=4, is_hook=True)]
    assembler.render(
        segments, sources, tmp_path / "p.mp4", voice_path=None, subtitle_path=None,
        music_path=None, settings=config.RenderSettings(subtitles=False),
        duration=4, fast=True, on_progress=seen.append,
    )
    assert seen and seen[-1] == 1.0 and all(0 <= v <= 1 for v in seen)

    cancel = threading.Event()
    cancel.set()
    with pytest.raises(media.Cancelled):
        assembler.render(
            segments, sources, tmp_path / "annule.mp4", voice_path=None,
            subtitle_path=None, music_path=None,
            settings=config.RenderSettings(subtitles=False),
            duration=4, fast=True, cancel=cancel,
        )


def test_graphe_unique_declare_toutes_les_entrees(sources):
    """Le graphe numérote correctement ses entrées (vidéo, voix, musique)."""
    graph = assembler.build_graph(
        [trimmer.Segment(source_index=1, start=0, duration=3),
         trimmer.Segment(source_index=2, start=1, duration=3)],
        {s.index: s for s in sources},
        voice_path=Path("/tmp/v.mp3"), subtitle_path=None,
        music_path=Path("/tmp/m.mp3"),
        settings=config.RenderSettings(subtitles=False),
        duration=6, fmt=config.FORMAT,
    )
    assert graph.inputs.count("-i") == 4          # 2 extraits + voix + musique
    assert graph.video_label == "vcat"
    assert graph.audio_label == "aout"
    joined = ";".join(graph.filters)
    assert "sidechaincompress" in joined          # musique atténuée sous la voix
    assert assembler.VOICE_LOUDNESS in joined     # voix normalisée


def test_repli_multi_passes(sources, tmp_path):
    """Le chemin de secours (un fichier par extrait) reste fonctionnel."""
    settings = config.RenderSettings(subtitles=False)
    clips = [
        trimmer.render_segment(segment, next(s for s in sources
                                             if s.index == segment.source_index),
                               tmp_path / f"clip_{i}.mp4", settings=settings)
        for i, segment in enumerate(
            trimmer.plan_segments(sources, hook_index=1, target_duration=8, seed=3), 1
        )
    ]
    montage = assembler.concat_clips(clips, tmp_path / "montage.mp4")
    assert media.probe(montage).duration == pytest.approx(8, abs=0.6)

    result = assembler.finalize(
        montage, tmp_path / "final3.mp4",
        voice_path=_fake_voice(tmp_path / "voice3.m4a", 7.5), subtitle_path=None,
        music_path=None, settings=settings, duration=7.8,
    )
    assert result.duration == pytest.approx(7.8, abs=0.4)


def test_encodeur_detecte_et_mis_en_cache():
    first = media.detect_encoder()
    assert first.name and first.args()
    assert media.detect_encoder() is first          # cache mémoire
    forced = media.detect_encoder(force="libx264")
    assert forced.name == "libx264"


# --- Sous-titres ----------------------------------------------------------
def test_build_ass_respecte_la_duree_max():
    words = [Word(text=f"mot{i}", start=i * 0.5, end=i * 0.5 + 0.45) for i in range(20)]
    content = subtitles.build_ass(words, max_duration=4.0)
    assert "[Events]" in content
    times = [line.split(",")[2] for line in content.splitlines()
             if line.startswith("Dialogue")]
    assert times and all(t <= "0:00:04.00" for t in times)


def test_clean_script_retire_le_markdown():
    assert clean_script("**Accroche :** Voici [plan] un *test*") == "Voici un test"


def test_ass_declare_les_dix_champs_de_dialogue():
    """Un champ manquant dans l'en-tête décale le texte (virgule parasite)."""
    words = _estimate_words("bonjour tout le monde", 2)
    content = subtitles.build_ass(words)
    header = next(line for line in content.splitlines()
                  if line.startswith("Format: Layer"))
    dialogues = [line for line in content.splitlines() if line.startswith("Dialogue:")]
    assert header.count(",") + 1 == 10
    assert dialogues
    for line in dialogues:
        payload = line.split(",", 9)[9]          # le champ Text
        assert payload and not payload.startswith(",")
    # Le premier évènement porte les balises de style (fondu, animation).
    assert dialogues[0].split(",", 9)[9].startswith("{")


# --- Synchronisation des sous-titres --------------------------------------
def test_silence_initial_mesure(tmp_path):
    """edge-tts démarre son minutage au premier mot, pas au début du fichier."""
    from flambee.voice import measure_lead_in

    avec_silence = tmp_path / "avec.m4a"
    # `-t` doit précéder son `-i` : placé après, il borne l'entrée suivante et
    # anullsrc, infini, fait alors tourner ffmpeg sans fin.
    media.ffmpeg([
        "-f", "lavfi", "-t", "0.5", "-i", "anullsrc=r=48000:cl=mono",
        "-f", "lavfi", "-i", "sine=frequency=440:duration=1.5",
        "-filter_complex", "[0:a][1:a]concat=n=2:v=0:a=1[a]", "-map", "[a]",
        "-c:a", "aac", str(avec_silence),
    ])
    assert measure_lead_in(avec_silence) == pytest.approx(0.5, abs=0.1)

    sans_silence = tmp_path / "sans.m4a"
    media.ffmpeg(["-f", "lavfi", "-i", "sine=frequency=440:duration=1.5",
                  "-c:a", "aac", str(sans_silence)])
    assert measure_lead_in(sans_silence) == pytest.approx(0.0, abs=0.05)


def test_recalage_decale_tous_les_sous_titres():
    """Le décalage s'applique à l'ensemble des évènements, pas au premier seul."""
    words = _estimate_words("un deux trois quatre cinq six sept huit", 8)
    sans = subtitles.build_ass(words)
    avec = subtitles.build_ass(words, offset=0.25)

    def debuts(contenu):
        return [ligne.split(",")[1] for ligne in contenu.splitlines()
                if ligne.startswith("Dialogue")]

    a, b = debuts(sans), debuts(avec)
    assert len(a) == len(b) and a[0] < b[0] and a[-1] < b[-1]
    assert b[0] == "0:00:00.25"


def test_la_voix_en_cache_conserve_le_recalage(tmp_path, monkeypatch):
    """Un rendu final qui suit un aperçu doit rester aussi bien synchronisé."""
    from flambee import pipeline
    from flambee.project import Project

    projet = Project()
    monkeypatch.setattr(type(projet), "dir", property(lambda self: tmp_path))
    projet.script = "Bonjour tout le monde."
    projet.voice_signature = pipeline._voice_signature(projet)
    projet.voice_words = [{"text": "Bonjour", "start": 0.0, "end": 0.5}]
    projet.voice_duration = 1.0
    projet.voice_lead_in = 0.21
    (tmp_path / "voice.mp3").write_bytes(b"factice")

    piste = pipeline._voice_track(projet)
    assert piste.lead_in == 0.21
