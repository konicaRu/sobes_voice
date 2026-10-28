import io
import time
from collections.abc import Callable
from dataclasses import dataclass

import numpy as np
import soundfile as sf

from app.config import Config, Glossary
from app.text_norm import apply_stt_fixes

Recognize = Callable[[np.ndarray, int], str]


@dataclass
class Transcription:
    text: str
    seconds: float
    elapsed: float


def decode_wav(data: bytes) -> tuple[np.ndarray, int]:
    """WAV → float32 mono в диапазоне [-1, 1]. На int16 GigaAM молча возвращает пустую строку."""
    waveform, sr = sf.read(io.BytesIO(data), dtype="float32", always_2d=True)
    return waveform.mean(axis=1).astype(np.float32), sr


def normalize_level(waveform: np.ndarray, target_peak: float = 0.9) -> np.ndarray:
    peak = float(np.abs(waveform).max()) if len(waveform) else 0.0
    if peak < 1e-6:
        return waveform
    return waveform * (target_peak / peak)


def split_at_pauses(waveform: np.ndarray, sr: int, target_s: float, search_s: float = 6.0, frame_ms: int = 50):
    """Режет запись на куски ~target_s; разрез в самом тихом кадре окна [target-search, target].

    Заменяет Silero VAD: на тихих записях VAD терял всё после первых ~25 с.
    """
    frame = int(sr * frame_ms / 1000)
    target, search = int(target_s * sr), int(search_s * sr)
    cuts, start = [], 0
    while len(waveform) - start > target:
        lo, hi = start + target - search, start + target
        frames = waveform[lo:hi][: (hi - lo) // frame * frame].reshape(-1, frame)
        cut = lo + int(np.sqrt((frames ** 2).mean(axis=1)).argmin()) * frame + frame // 2
        cuts.append((start, cut))
        start = cut
    cuts.append((start, len(waveform)))
    return cuts


class SttEngine:
    def __init__(self, recognize: Recognize, chunk_seconds: int, stt_fix: dict[str, str]):
        self._recognize = recognize
        self._chunk_seconds = chunk_seconds
        self._stt_fix = stt_fix

    def transcribe(self, wav: bytes) -> Transcription:
        started = time.perf_counter()
        waveform, sr = decode_wav(wav)
        waveform = normalize_level(waveform)
        cuts = split_at_pauses(waveform, sr, self._chunk_seconds)
        parts = (self._recognize(waveform[a:b], sr).strip() for a, b in cuts)
        text = apply_stt_fixes(" ".join(p for p in parts if p), self._stt_fix)
        return Transcription(text=text, seconds=len(waveform) / sr, elapsed=time.perf_counter() - started)


def build_stt(config: Config, glossary: Glossary) -> SttEngine:
    import onnx_asr

    model = onnx_asr.load_model(config.stt.model)
    return SttEngine(
        lambda chunk, sr: model.recognize(chunk, sample_rate=sr),
        chunk_seconds=config.stt.chunk_seconds,
        stt_fix=glossary.stt_fix,
    )
