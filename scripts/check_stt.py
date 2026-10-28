"""Проверка STT: распознать WAV-файл или записать с микрофона N секунд.

Использует тот же SttEngine, что и приложение (нарезка по паузам, выравнивание громкости, глоссарий).
"""
import argparse
import sys
import time
from pathlib import Path

import sounddevice as sd
import soundfile as sf

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.config import load_config, load_glossary  # noqa: E402
from app.stt import TARGET_SAMPLE_RATE, build_stt  # noqa: E402


def record(seconds: int, out: Path) -> Path:
    print(f"Говорите {seconds} с… (запись началась)")
    audio = sd.rec(seconds * TARGET_SAMPLE_RATE, samplerate=TARGET_SAMPLE_RATE, channels=1, dtype="float32")
    sd.wait()
    out.parent.mkdir(parents=True, exist_ok=True)
    sf.write(out, audio, TARGET_SAMPLE_RATE, subtype="PCM_16")
    print(f"Запись сохранена: {out}")
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--wav", type=Path, help="путь к WAV-файлу")
    group.add_argument("--record", type=int, metavar="N", help="записать N секунд с микрофона")
    args = parser.parse_args()

    wav = args.wav or record(args.record, ROOT / "data" / "check" / "stt_record.wav")

    t0 = time.perf_counter()
    engine = build_stt(load_config(), load_glossary())
    print(f"Модель загружена за {time.perf_counter() - t0:.1f} с")

    result = engine.transcribe(wav.read_bytes())
    print(f"\nТекст: {result.text}\n")
    print(f"Распознано {result.seconds:.1f} с речи за {result.elapsed:.1f} с (x{result.seconds / result.elapsed:.1f} к реальному времени)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
