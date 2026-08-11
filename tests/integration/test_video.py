"""Integration test: build a real (small) video and validate it. Needs ffmpeg
(always available via imageio-ffmpeg)."""
from __future__ import annotations

import pytest
from PIL import Image

from src.core.settings import load_settings
from src.music.tones import synth_pad, write_wav
from src.quality import MediaValidator
from src.video import VideoBuilder, probe_media

pytestmark = pytest.mark.integration


def test_build_and_validate_video(tmp_path, project_paths):
    s = load_settings(project_paths, load_dotenv=False)
    img = tmp_path / "img.png"
    Image.new("RGB", (1080, 1350), (230, 228, 224)).save(img)
    music = tmp_path / "m.wav"
    write_wav(music, synth_pad("calm", duration=3.0))

    vb = VideoBuilder(s)
    out = tmp_path / "v.mp4"
    res = vb.build(image_path=img, out_path=out, aspect="feed", duration=2.0,
                   music_path=music, ken_burns=False)
    assert out.exists()
    assert res.has_audio is True

    info = probe_media(str(out))
    assert (info.width, info.height) == (1080, 1350)
    assert (info.video_codec or "").lower() == "h264"
    assert info.audio_sample_rate == 48000

    val = MediaValidator(s).validate_video(out, aspect="feed", expected_duration=2.0)
    assert val.passed is True, (val.issues, val.checks)


def test_build_video_without_music(tmp_path, project_paths):
    s = load_settings(project_paths, load_dotenv=False)
    img = tmp_path / "img2.png"
    Image.new("RGB", (1080, 1920), (30, 30, 34)).save(img)
    vb = VideoBuilder(s)
    out = tmp_path / "v2.mp4"
    # music_path=None + silent_audio=False => no audio stream at all
    res = vb.build(image_path=img, out_path=out, aspect="story", duration=2.0,
                   music_path=None, ken_burns=False, silent_audio=False)
    assert res.has_audio is False
    info = probe_media(str(out))
    assert (info.width, info.height) == (1080, 1920)
