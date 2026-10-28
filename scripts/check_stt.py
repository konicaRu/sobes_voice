"""Проверка STT: распознать WAV-файл или записать с микрофона N секунд."""
import argparse
import sys
import time
from pathlib import Path

import numpy as np
import onnx_asr
import sounddevice as sd
import soundfile as sf
import yaml

ROOT = Path(__file__).resolve().parent.parent
SAMPLE_RATE = 16000


def record(seconds: int, out: Path) -> Path:
    print(f"Говорите {seconds} с… (запись началась)")
    audio = sd.rec(seconds * SAMPLE_RATE, samplerate=SAMPLE_RATE, channels=1, dtype="float32")
    sd.wait()
    out.parent.mkdir(parents=True, exist_ok=True)
    sf.write(out, audio, SAMPLE_RATE, subtype="PCM_16")
    print(f"Запись сохранена: {out}")
    return out


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


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--wav", type=Path, help="путь к WAV-файлу")
    group.add_argument("--record", type=int, metavar="N", help="записать N секунд с микрофона")
    args = parser.parse_args()

    config = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))["stt"]
    wav = args.wav or record(args.record, ROOT / "data" / "check" / "stt_record.wav")

    # float32 в диапазоне [-1, 1]: на int16 GigaAM молча возвращает пустую строку
    waveform, sr = sf.read(wav, dtype="float32", always_2d=False)
    if waveform.ndim > 1:
        waveform = waveform.mean(axis=1)
    seconds = len(waveform) / sr
    print(f"Файл: {wav.name}, {seconds:.1f} с, {sr} Hz, peak={np.abs(waveform).max():.2f}")

    t0 = time.perf_counter()
    model = onnx_asr.load_model(config["model"])
    print(f"Модель {config['model']} загружена за {time.perf_counter() - t0:.1f} с")

    t0 = time.perf_counter()
    cuts = split_at_pauses(waveform, sr, config["chunk_seconds"])
    text = " ".join(model.recognize(waveform[a:b], sample_rate=sr) for a, b in cuts)
    elapsed = time.perf_counter() - t0

    print(f"\nТекст: {text}\n")
    print(f"Распознано {seconds:.1f} с речи за {elapsed:.1f} с (x{seconds / elapsed:.1f} к реальному времени, кусков: {len(cuts)})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
