"""Tests hors ligne : on fabrique des vidéos sources avec ffmpeg.

Aucun téléchargement ni appel réseau n'est nécessaire ; seule la synthèse
vocale (edge-tts) est remplacée par un minutage simulé.
"""

from __future__ import annotations

import sys
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


def test_concat_et_rendu_final(sources, tmp_path):
    settings = config.RenderSettings(subtitles=True)
    clips = []
    for position, segment in enumerate(
        trimmer.plan_segments(sources, hook_index=1, target_duration=9, seed=3), 1
    ):
        source = next(s for s in sources if s.index == segment.source_index)
        clips.append(trimmer.render_segment(
            segment, source, tmp_path / f"clip_{position}.mp4", settings=settings
        ))

    montage = assembler.concat_clips(clips, tmp_path / "montage.mp4")
    assert media.probe(montage).duration == pytest.approx(9, abs=0.6)

    words = _estimate_words("Voici une astuce simple et redoutable pour tout changer", 8)
    ass_path = subtitles.write_ass(words, tmp_path / "subs.ass", max_duration=8.5)

    # Une voix off factice : un silence de 8 s, suffisant pour valider le mixage.
    voice_path = tmp_path / "voice.m4a"
    media.ffmpeg(["-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo", "-t", "8",
                  "-c:a", "aac", str(voice_path)])

    out = assembler.finalize(
        montage, tmp_path / "final.mp4",
        voice_path=voice_path, subtitle_path=ass_path, music_path=None,
        settings=settings, duration=8.5,
    )
    info = media.probe(out)
    assert info.duration == pytest.approx(8.5, abs=0.4)
    assert (info.width, info.height) == (config.FORMAT.width, config.FORMAT.height)
    assert info.has_audio


def test_finalize_avec_musique_et_audio_source(sources, tmp_path):
    settings = config.RenderSettings(
        subtitles=False, keep_source_audio=True, music_volume=0.2
    )
    segment = trimmer.Segment(source_index=1, start=0, duration=3, is_hook=True)
    clip = trimmer.render_segment(segment, sources[0], tmp_path / "clip.mp4",
                                  settings=settings)
    montage = assembler.concat_clips([clip], tmp_path / "montage2.mp4")

    music = tmp_path / "music.m4a"
    media.ffmpeg(["-f", "lavfi", "-i", "sine=frequency=220", "-t", "2",
                  "-c:a", "aac", str(music)])
    voice = tmp_path / "voice2.m4a"
    media.ffmpeg(["-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo", "-t", "3",
                  "-c:a", "aac", str(voice)])

    out = assembler.finalize(
        montage, tmp_path / "final2.mp4", voice_path=voice, subtitle_path=None,
        music_path=music, settings=settings, duration=3,
    )
    assert media.probe(out).has_audio


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
