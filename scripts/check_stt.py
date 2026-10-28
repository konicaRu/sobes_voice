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


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--wav", type=Path, help="путь к WAV-файлу")
    group.add_argument("--record", type=int, metavar="N", help="записать N секунд с микрофона")
    parser.add_argument("--no-vad", action="store_true", help="не резать по паузам (для коротких записей)")
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

    use_vad = config["use_vad"] and not args.no_vad
    if use_vad:
        model = model.with_vad(onnx_asr.load_vad("silero"))

    t0 = time.perf_counter()
    if use_vad:
        text = " ".join(seg.text for seg in model.recognize(waveform, sample_rate=sr))
    else:
        text = model.recognize(waveform, sample_rate=sr)
    elapsed = time.perf_counter() - t0

    print(f"\nТекст: {text}\n")
    print(f"Распознано {seconds:.1f} с речи за {elapsed:.1f} с (x{seconds / elapsed:.1f} к реальному времени, VAD={'вкл' if use_vad else 'выкл'})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
