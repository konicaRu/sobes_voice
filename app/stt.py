import io
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

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


MIN_SAMPLE_RATE, MAX_SAMPLE_RATE = 8000, 48000
TARGET_SAMPLE_RATE: Literal[16000] = 16000  # браузер шлёт 16 кГц; остальные частоты приводим к ней
RECORDING_MARGIN_S = 30  # запас сверх max_answer_seconds на задержку остановки записи


class AudioError(ValueError):
    """Непригодная запись; сообщение на русском для показа в интерфейсе."""


def decode_wav(data: bytes, max_seconds: float | None = None) -> tuple[np.ndarray, int]:
    """WAV → float32 mono в диапазоне [-1, 1]. На int16 GigaAM молча возвращает пустую строку.

    Заголовок проверяется до декодирования: формат, частота и длительность.
    """
    try:
        info = sf.info(io.BytesIO(data))
        if info.format != "WAV":
            raise AudioError("Поддерживается только запись в формате WAV.")
        if not MIN_SAMPLE_RATE <= info.samplerate <= MAX_SAMPLE_RATE:
            raise AudioError(f"Неподдерживаемая частота записи ({info.samplerate} Гц).")
        if info.frames == 0:
            raise AudioError("Запись пустая. Проверьте микрофон и попробуйте ещё раз.")
        if max_seconds is not None and info.frames / info.samplerate > max_seconds:
            raise AudioError("Запись слишком длинная.")
        waveform, sr = sf.read(io.BytesIO(data), dtype="float32", always_2d=True)
    except (sf.LibsndfileError, RuntimeError) as exc:
        raise AudioError("Не удалось прочитать запись. Попробуйте записать ответ ещё раз.") from exc
    return waveform.mean(axis=1).astype(np.float32), sr


def resample(waveform: np.ndarray, sr: int) -> np.ndarray:
    """Линейная передискретизация к 16 кГц (запасной путь: из браузера приходит уже 16 кГц)."""
    if sr == TARGET_SAMPLE_RATE:
        return waveform
    length = round(len(waveform) * TARGET_SAMPLE_RATE / sr)
    positions = np.linspace(0, len(waveform) - 1, length)
    return np.interp(positions, np.arange(len(waveform)), waveform).astype(np.float32)


def normalize_level(waveform: np.ndarray, target_peak: float = 0.9) -> np.ndarray:
    peak = float(np.abs(waveform).max()) if len(waveform) else 0.0
    if peak < 1e-6:
        return waveform
    return waveform * (target_peak / peak)


def split_at_pauses(waveform: np.ndarray, sr: int, target_s: float, search_s: float = 6.0, frame_ms: int = 50):
    """Режет запись на куски ~target_s; разрез в самом тихом кадре окна [target-search, target].

    Заменяет Silero VAD: на тихих записях VAD терял всё после первых ~25 с.
    """
    frame = max(1, int(sr * frame_ms / 1000))
    target, search = int(target_s * sr), int(search_s * sr)
    cuts, start = [], 0
    while len(waveform) - start > target:
        lo, hi = start + target - search, start + target
        frames = waveform[lo:hi][: (hi - lo) // frame * frame].reshape(-1, frame)
        if len(frames) == 0:
            cut = hi
        else:
            cut = lo + int(np.sqrt((frames ** 2).mean(axis=1)).argmin()) * frame + frame // 2
        cuts.append((start, cut))
        start = cut
    cuts.append((start, len(waveform)))
    return cuts


class SttEngine:
    def __init__(self, recognize: Recognize, chunk_seconds: int, stt_fix: dict[str, str], max_seconds: float = 600):
        self._recognize = recognize
        self._chunk_seconds = chunk_seconds
        self._stt_fix = stt_fix
        self._max_seconds = max_seconds

    def transcribe(self, wav: bytes) -> Transcription:
        started = time.perf_counter()
        waveform, sr = decode_wav(wav, self._max_seconds)
        waveform = normalize_level(resample(waveform, sr))
        cuts = split_at_pauses(waveform, TARGET_SAMPLE_RATE, self._chunk_seconds)
        parts = (self._recognize(waveform[a:b], TARGET_SAMPLE_RATE).strip() for a, b in cuts)
        text = apply_stt_fixes(" ".join(p for p in parts if p), self._stt_fix)
        seconds = len(waveform) / TARGET_SAMPLE_RATE
        return Transcription(text=text, seconds=seconds, elapsed=time.perf_counter() - started)


def build_stt(config: Config, glossary: Glossary) -> SttEngine:
    import onnx_asr

    model = onnx_asr.load_model(config.stt.model)
    return SttEngine(
        lambda chunk, sr: model.recognize(chunk, sample_rate=TARGET_SAMPLE_RATE),
        chunk_seconds=config.stt.chunk_seconds,
        stt_fix=glossary.stt_fix,
        max_seconds=config.stt.max_answer_seconds + RECORDING_MARGIN_S,
    )
