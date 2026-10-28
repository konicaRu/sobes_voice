import io

import numpy as np
import soundfile as sf

from app.tts import TtsEngine, split_sentences


class FakeSynth:
    def __init__(self):
        self.calls = []

    def __call__(self, text):
        self.calls.append(text)
        return np.zeros(4800, dtype="float32")


def test_split_keeps_short_text_whole():
    assert split_sentences("Привет. Как дела?", max_len=100) == ["Привет. Как дела?"]


def test_split_breaks_at_sentence_boundaries():
    text = "Первое предложение. Второе предложение. Третье предложение."
    assert split_sentences(text, max_len=25) == [
        "Первое предложение.",
        "Второе предложение.",
        "Третье предложение.",
    ]


def test_split_cuts_overlong_sentence_by_words():
    parts = split_sentences("слово " * 30, max_len=50)
    assert all(len(p) <= 50 for p in parts)
    assert " ".join(parts).split() == ["слово"] * 30


def test_engine_returns_wav_normalizes_text_and_caches(tmp_path):
    synth = FakeSynth()
    engine = TtsEngine(synth, 48000, {"SQL": "эс-кью-эль"}, tmp_path)
    wav = engine.synthesize("Знаешь SQL?")
    data, sr = sf.read(io.BytesIO(wav))
    assert sr == 48000 and len(data) == 4800
    assert synth.calls == ["Знаешь эс-кью-эль?"]
    assert engine.synthesize("Знаешь SQL?") == wav
    assert len(synth.calls) == 1


def test_cache_depends_on_voice(tmp_path):
    synth = FakeSynth()
    TtsEngine(synth, 48000, {}, tmp_path, voice_id="eugene:slow").synthesize("Привет")
    TtsEngine(synth, 48000, {}, tmp_path, voice_id="xenia:slow").synthesize("Привет")
    assert len(synth.calls) == 2


def test_engine_joins_chunks_of_long_text(tmp_path):
    synth = FakeSynth()
    engine = TtsEngine(synth, 48000, {}, tmp_path, max_len=25)
    wav = engine.synthesize("Первое предложение. Второе предложение.")
    data, _ = sf.read(io.BytesIO(wav))
    assert len(synth.calls) == 2 and len(data) == 9600
