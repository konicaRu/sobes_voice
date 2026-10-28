import hashlib
import io
import re
from collections.abc import Callable
from pathlib import Path
from xml.sax.saxutils import escape

import numpy as np
import soundfile as sf

from app.config import Config, Glossary
from app.text_norm import normalize_for_tts

Synthesize = Callable[[str], np.ndarray]


def split_sentences(text: str, max_len: int) -> list[str]:
    """Режет текст по предложениям на куски не длиннее max_len (Silero ограничивает вход)."""
    pieces: list[str] = []
    for sentence in re.split(r"(?<=[.!?…])\s+", text.strip()):
        words, current = sentence.split(), ""
        for word in words:
            candidate = f"{current} {word}".strip()
            if len(candidate) <= max_len:
                current = candidate
            else:
                pieces.append(current)
                current = word
        if current:
            pieces.append(current)

    chunks: list[str] = []
    for piece in pieces:
        if chunks and len(chunks[-1]) + 1 + len(piece) <= max_len:
            chunks[-1] = f"{chunks[-1]} {piece}"
        else:
            chunks.append(piece)
    return chunks


class TtsEngine:
    def __init__(
        self,
        synthesize: Synthesize,
        sample_rate: int,
        glossary: dict[str, str],
        cache_dir: Path,
        voice_id: str = "",
        max_len: int = 500,
    ):
        self._synthesize = synthesize
        self._sample_rate = sample_rate
        self._glossary = glossary
        self._cache_dir = cache_dir
        self._voice_id = voice_id
        self._max_len = max_len

    def synthesize(self, text: str) -> bytes:
        spoken = normalize_for_tts(text, self._glossary)
        key = hashlib.sha256(f"{self._voice_id}|{self._sample_rate}|{spoken}".encode()).hexdigest()
        cached = self._cache_dir / f"{key}.wav"
        if cached.exists():
            return cached.read_bytes()

        audio = np.concatenate([self._synthesize(part) for part in split_sentences(spoken, self._max_len)])
        buffer = io.BytesIO()
        sf.write(buffer, audio, self._sample_rate, format="WAV", subtype="PCM_16")
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        cached.write_bytes(buffer.getvalue())
        return buffer.getvalue()


def build_tts(config: Config, glossary: Glossary, cache_dir: Path) -> TtsEngine:
    import torch
    from silero import silero_tts

    torch.set_num_threads(4)
    model, _ = silero_tts(language="ru", speaker=config.tts.model)
    model.to(torch.device("cpu"))

    def synthesize(text: str) -> np.ndarray:
        ssml = f'<speak><prosody rate="{config.tts.rate}">{escape(text)}</prosody></speak>'
        audio = model.apply_tts(ssml_text=ssml, speaker=config.tts.speaker, sample_rate=config.tts.sample_rate)
        return audio.numpy()

    return TtsEngine(
        synthesize,
        config.tts.sample_rate,
        glossary.tts,
        cache_dir,
        voice_id=f"{config.tts.model}:{config.tts.speaker}:{config.tts.rate}",
    )
