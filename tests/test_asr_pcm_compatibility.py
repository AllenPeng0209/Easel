"""ASR recovery at the installed decoder/model boundary; no model downloads."""

import argparse
import importlib.util
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
from types import SimpleNamespace
import wave

import numpy as np
import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "skills/shared/scripts/asr.py"


@pytest.fixture
def asr(monkeypatch):
    spec = importlib.util.spec_from_file_location("asr_pcm_compatibility", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "_ensure_proxy", lambda: None)
    return module


def args(**changes):
    return argparse.Namespace(**{
        "model": "offline-fixture-model", "device": "cpu", "compute_type": "int8",
        "language": "zh", "beam_size": 5, "max_line_chars": 18, **changes,
    })


def write_wave(path, *, sample_rate=32000, stereo=False):
    samples = (np.sin(np.arange(sample_rate // 2) * (2 * np.pi * 440 / sample_rate)) * 16384).astype("<i2")
    if stereo:
        samples = np.column_stack((samples, -samples))
    with wave.open(str(path), "wb") as output:
        output.setnchannels(2 if stereo else 1)
        output.setsampwidth(2)
        output.setframerate(sample_rate)
        output.writeframes(samples.tobytes())


def install_whisper(monkeypatch, transcribe):
    class Model:
        def __init__(self, model, *, device, compute_type):
            pass

        def transcribe(self, audio, **options):
            return transcribe(audio, **options)

    monkeypatch.setitem(sys.modules, "faster_whisper", SimpleNamespace(WhisperModel=Model))


def result():
    return iter([SimpleNamespace(start=.125, end=.375, text=" 一起往下写。 ")]), SimpleNamespace(language="zh", duration=.5)


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="PCM normalization requires ffmpeg")
def test_metadata_errors_decoder_incompatibility_recovers_with_real_normalized_pcm(asr, monkeypatch, tmp_path):
    source = tmp_path / "语音 with spaces.wav"
    write_wave(source)
    decode_audio = pytest.importorskip("faster_whisper.audio").decode_audio

    def transcribe(audio, *, language, vad_filter, beam_size):
        if isinstance(audio, str):
            audio = decode_audio(audio)
        assert isinstance(audio, np.ndarray)
        assert audio.dtype == np.float32 and audio.shape == (8000,)
        assert np.isfinite(audio).all()
        assert .49 < float(audio.max()) < .51
        assert -.51 < float(audio.min()) < -.49
        assert language == "zh" and vad_filter is True and beam_size == 5
        return result()

    install_whisper(monkeypatch, transcribe)
    cues, meta = asr._transcribe_audio(source, args())
    assert cues == [(.125, .375, "一起往下写。")]
    assert meta["duration_sec"] == .5 and meta["language"] == "zh"
    assert source.is_file()


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="Video decoding requires ffmpeg")
def test_invalid_video_cleans_the_temporary_extraction_directory(asr, monkeypatch, tmp_path, capsys):
    source = tmp_path / "broken video.mp4"
    source.write_bytes(b"not a media file")
    temporary = tmp_path / "temporary work"
    temporary.mkdir()
    monkeypatch.setattr(asr.tempfile, "tempdir", str(temporary))
    options = args(input=str(source), output=str(tmp_path / "never.srt"), format="srt", res=None)

    with pytest.raises(SystemExit):
        asr.cmd_transcribe(options)

    assert list(temporary.iterdir()) == []
    assert source.read_bytes() == b"not a media file"
    assert not (tmp_path / "never.srt").exists()
    assert "ffmpeg 提取音轨失败" in capsys.readouterr().err


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="PCM normalization requires ffmpeg")
def test_recovery_downmixes_stereo_and_resamples_before_transcription(asr, monkeypatch, tmp_path):
    source = tmp_path / "opposite stereo.wav"
    write_wave(source, sample_rate=44100, stereo=True)

    def transcribe(audio, **_options):
        if isinstance(audio, str):
            raise TypeError("open() got an unexpected keyword argument 'metadata_errors'")
        assert audio.dtype == np.float32 and audio.shape == (8000,)
        # The right channel is the exact negative of the left channel.
        assert float(np.max(np.abs(audio))) <= 1 / 32768
        return result()

    install_whisper(monkeypatch, transcribe)
    cues, _ = asr._transcribe_audio(source, args())
    assert cues == [(.125, .375, "一起往下写。")]


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="PCM normalization requires ffmpeg")
def test_recovery_keeps_whispers_first_audio_stream_in_multitrack_media(asr, monkeypatch, tmp_path):
    first = tmp_path / "first.wav"
    second = tmp_path / "second.wav"
    write_wave(first)
    write_wave(second, stereo=True)
    source = tmp_path / "multiple tracks.mka"
    subprocess.run(["ffmpeg", "-v", "error", "-i", str(first), "-i", str(second), "-map", "0:a",
        "-map", "1:a", "-c:a", "pcm_s16le", "-disposition:a:0", "0", "-disposition:a:1", "default", str(source)],
        check=True, capture_output=True, timeout=20)

    def transcribe(audio, **_options):
        if isinstance(audio, str):
            raise TypeError("open() got an unexpected keyword argument 'metadata_errors'")
        # Whisper decodes audio=0. The default second track cancels to silence.
        assert .49 < float(audio.max()) < .51
        return result()

    install_whisper(monkeypatch, transcribe)
    cues, _ = asr._transcribe_audio(source, args())
    assert cues == [(.125, .375, "一起往下写。")]


def test_working_decoder_keeps_existing_path_without_requiring_ffmpeg(asr, monkeypatch, tmp_path):
    source = tmp_path / "normal.wav"
    write_wave(source)
    monkeypatch.setattr(asr, "_has_ffmpeg", lambda: False)

    def transcribe(audio, *, language, vad_filter, beam_size):
        assert audio == str(source)
        assert language is None and vad_filter is True and beam_size == 3
        return result()

    install_whisper(monkeypatch, transcribe)
    cues, _ = asr._transcribe_audio(source, args(language="auto", beam_size=3))
    assert cues == [(.125, .375, "一起往下写。")]


@pytest.mark.parametrize("message", [
    "transcribe() got an unexpected keyword argument 'other_option'",
    "metadata_errors has an invalid value",
])
def test_unrelated_type_errors_are_not_disguised_as_decode_recovery(asr, monkeypatch, tmp_path, message):
    source = tmp_path / "normal.wav"
    write_wave(source)
    monkeypatch.setattr(asr, "_has_ffmpeg", lambda: False)

    def transcribe(_audio, **_options):
        raise TypeError(message)

    install_whisper(monkeypatch, transcribe)
    with pytest.raises(TypeError, match=re.escape(message)):
        asr._transcribe_audio(source, args())


def test_decoder_recovery_reports_missing_ffmpeg_and_preserves_source(asr, monkeypatch, tmp_path, capsys):
    source = tmp_path / "source.wav"
    write_wave(source)
    original = source.read_bytes()
    monkeypatch.setattr(asr, "_has_ffmpeg", lambda: False)

    def transcribe(_audio, **_options):
        raise TypeError("open() got an unexpected keyword argument 'metadata_errors'")

    install_whisper(monkeypatch, transcribe)
    with pytest.raises(SystemExit) as failure:
        asr._transcribe_audio(source, args())
    assert failure.value.code == 3
    assert "ffmpeg" in capsys.readouterr().err
    assert source.read_bytes() == original


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="PCM normalization requires ffmpeg")
def test_corrupt_audio_cannot_reach_the_model_as_empty_samples(asr, monkeypatch, tmp_path, capsys):
    source = tmp_path / "broken.wav"
    source.write_bytes(b"corrupt audio")

    def transcribe(audio, **_options):
        if isinstance(audio, str):
            raise TypeError("open() got an unexpected keyword argument 'metadata_errors'")
        pytest.fail("Corrupt audio reached inference")

    install_whisper(monkeypatch, transcribe)
    with pytest.raises(SystemExit):
        asr._transcribe_audio(source, args())
    assert "ffmpeg 解码音轨失败" in capsys.readouterr().err
    assert source.read_bytes() == b"corrupt audio"


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="PCM normalization requires ffmpeg")
@pytest.mark.parametrize("format", ["srt", "ass", "txt", "json"])
def test_cli_recovery_preserves_caption_formats_and_timestamps(asr, monkeypatch, tmp_path, format):
    source = tmp_path / "源语音 with spaces.wav"
    write_wave(source)
    output = tmp_path / "字幕 with spaces" / f"result.{format}"

    def transcribe(audio, **_options):
        if isinstance(audio, str):
            raise TypeError("open() got an unexpected keyword argument 'metadata_errors'")
        assert isinstance(audio, np.ndarray)
        return result()

    install_whisper(monkeypatch, transcribe)
    monkeypatch.setattr(sys, "argv", ["asr.py", "transcribe", "-i", str(source), "-o", str(output),
        "--format", format, "--model", "offline-fixture-model", "--language", "zh", "--res", "640x360"])
    assert asr.main() == 0
    content = output.read_text(encoding="utf-8")
    if format == "srt":
        assert content == "1\n00:00:00,125 --> 00:00:00,375\n一起往下写。\n"
    elif format == "txt":
        assert content == "一起往下写。\n"
    elif format == "ass":
        assert "PlayResX: 640\nPlayResY: 360" in content
        assert "Dialogue: 0,0:00:00.12,0:00:00.38,Default,,0,0,0,,一起往下写。\n" in content
    else:
        value = json.loads(content)
        assert value == {"source": str(source), "model": "offline-fixture-model", "language": "zh",
            "duration_sec": .5, "num_segments": 1,
            "segments": [{"index": 1, "start": .125, "end": .375, "text": "一起往下写。"}]}


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="Video decoding requires ffmpeg")
@pytest.mark.parametrize("inference_fails", [False, True])
def test_real_video_extraction_is_cleaned_after_success_or_model_failure(asr, monkeypatch, tmp_path, inference_fails):
    audio = tmp_path / "tone.wav"
    write_wave(audio)
    source = tmp_path / "视频 with spaces.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "color=black:s=16x16:r=24:d=0.5",
        "-i", str(audio), "-t", "0.5", "-c:v", "libx264", "-c:a", "aac", str(source)],
        check=True, capture_output=True, timeout=20)
    original = source.read_bytes()
    temporary = tmp_path / "temporary work"
    temporary.mkdir()
    monkeypatch.setattr(asr.tempfile, "tempdir", str(temporary))
    output = tmp_path / "result.srt"

    def transcribe(samples, **_options):
        if inference_fails:
            raise RuntimeError("fixture inference failed")
        if isinstance(samples, str):
            raise TypeError("open() got an unexpected keyword argument 'metadata_errors'")
        assert isinstance(samples, np.ndarray) and samples.ndim == 1
        return result()

    install_whisper(monkeypatch, transcribe)
    options = args(input=str(source), output=str(output), format="srt", res=None)
    if inference_fails:
        with pytest.raises(RuntimeError, match="fixture inference failed"):
            asr.cmd_transcribe(options)
        assert not output.exists()
    else:
        assert asr.cmd_transcribe(options) == 0
        assert output.read_text(encoding="utf-8") == "1\n00:00:00,125 --> 00:00:00,375\n一起往下写。\n"
    assert list(temporary.iterdir()) == []
    assert source.read_bytes() == original
