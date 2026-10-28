import io

import numpy as np
import pytest
import soundfile as sf

from app.stt import AudioError, SttEngine, decode_wav, normalize_level, split_at_pauses

SR = 16000


def wav_bytes(samples, sr=SR):
    buf = io.BytesIO()
    sf.write(buf, samples, sr, format="WAV", subtype="PCM_16")
    return buf.getvalue()


def speech(seconds):
    return np.random.default_rng(0).uniform(-0.5, 0.5, int(seconds * SR)).astype("float32")


def test_decode_wav_returns_float32_in_unit_range():
    wave, sr = decode_wav(wav_bytes(speech(1)))
    assert wave.dtype == np.float32 and sr == SR
    assert 0.4 < np.abs(wave).max() <= 1.0


def test_decode_wav_stereo_to_mono():
    stereo = np.stack([np.full(1600, 0.2), np.full(1600, 0.4)], axis=1).astype("float32")
    wave, _ = decode_wav(wav_bytes(stereo))
    assert wave.ndim == 1 and abs(wave.mean() - 0.3) < 0.01


def test_normalize_level_boosts_quiet_audio():
    quiet = np.full(1000, 0.05, dtype="float32")
    assert abs(np.abs(normalize_level(quiet)).max() - 0.9) < 1e-3


def test_normalize_level_keeps_silence():
    assert not np.any(normalize_level(np.zeros(1000, dtype="float32")))


def test_short_audio_is_single_chunk():
    assert split_at_pauses(speech(10), SR, target_s=20) == [(0, 10 * SR)]


def test_long_audio_is_cut_inside_pause():
    wave = speech(45)
    wave[int(17 * SR):int(17.5 * SR)] = 0
    first_end = split_at_pauses(wave, SR, target_s=20)[0][1]
    assert 17 * SR <= first_end <= 17.5 * SR


def test_chunks_are_contiguous_cover_audio_and_fit_target():
    wave = speech(65)
    cuts = split_at_pauses(wave, SR, target_s=20)
    assert cuts[0][0] == 0 and cuts[-1][1] == len(wave)
    assert all(a[1] == b[0] for a, b in zip(cuts, cuts[1:]))
    assert all(b - a <= 20 * SR for a, b in cuts)


def test_engine_chunks_audio_and_applies_stt_fix():
    calls = []

    def fake_recognize(chunk, sr):
        calls.append(len(chunk))
        return "Клик Хаус"

    engine = SttEngine(fake_recognize, chunk_seconds=20, stt_fix={"клик хаус": "ClickHouse"})
    result = engine.transcribe(wav_bytes(speech(45)))
    assert result.text == "ClickHouse ClickHouse ClickHouse"
    assert len(calls) == 3
    assert result.seconds == 45
    assert result.elapsed >= 0


@pytest.mark.parametrize("sample_rate", [1000, 100000])
def test_engine_rejects_unsupported_sample_rate(sample_rate):
    engine = SttEngine(lambda chunk, sr: "текст", chunk_seconds=20, stt_fix={})
    with pytest.raises(AudioError, match="частот"):
        engine.transcribe(wav_bytes(np.zeros(sample_rate, dtype="float32"), sr=sample_rate))


def test_engine_rejects_too_long_audio():
    engine = SttEngine(lambda chunk, sr: "текст", chunk_seconds=20, stt_fix={}, max_seconds=10)
    with pytest.raises(AudioError, match="длинн"):
        engine.transcribe(wav_bytes(speech(15)))


def test_engine_skips_empty_chunks():
    texts = iter(["Привет", "", "мир"])
    engine = SttEngine(lambda chunk, sr: next(texts), chunk_seconds=20, stt_fix={})
    assert engine.transcribe(wav_bytes(speech(45))).text == "Привет мир"
