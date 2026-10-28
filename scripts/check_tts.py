"""Проверка TTS: озвучить фразу с числами и терминами, сохранить WAV, замерить время."""
import argparse
import re
import sys
import time
from pathlib import Path

import soundfile as sf
import torch
import yaml
from num2words import num2words

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_TEXT = (
    "Ретеншн второго дня вырос с тридцати двух до тридцати семи процентов. "
    "Мы запустили A/B тест на 12 тысяч пользователей и посчитали p-value в ClickHouse."
)


def normalize(text: str, glossary: dict) -> str:
    """Минимальная нормализация для проверки: глоссарий + числа словами."""
    for term, spoken in glossary.items():
        text = re.sub(re.escape(term), spoken, text, flags=re.IGNORECASE)
    return re.sub(r"\d+", lambda m: num2words(int(m.group()), lang="ru"), text)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--text", default=DEFAULT_TEXT)
    parser.add_argument("--speaker", help="aidar | baya | kseniya | xenia | eugene")
    parser.add_argument("--rate", help="темп (SSML): x-slow | slow | medium | fast | x-fast")
    parser.add_argument("--out", type=Path, default=ROOT / "data" / "check" / "tts.wav")
    args = parser.parse_args()

    config = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))["tts"]
    glossary = yaml.safe_load((ROOT / "glossary.yaml").read_text(encoding="utf-8"))["tts"]
    speaker = args.speaker or config["speaker"]

    torch.set_num_threads(4)
    from silero import silero_tts

    t0 = time.perf_counter()
    model = silero_tts(language="ru", speaker=config["model"])[0]
    model.to(torch.device("cpu"))
    print(f"Модель {config['model']} загружена за {time.perf_counter() - t0:.1f} с")

    text = normalize(args.text, glossary)
    print(f"Текст для синтеза: {text}")

    t0 = time.perf_counter()
    rate = args.rate or config["rate"]
    ssml = f'<speak><prosody rate="{rate}">{text}</prosody></speak>'
    audio = model.apply_tts(ssml_text=ssml, speaker=speaker, sample_rate=config["sample_rate"])
    elapsed = time.perf_counter() - t0
    seconds = len(audio) / config["sample_rate"]

    args.out.parent.mkdir(parents=True, exist_ok=True)
    sf.write(args.out, audio.numpy(), config["sample_rate"])
    print(f"Голос {speaker}: {seconds:.1f} с аудио синтезировано за {elapsed:.2f} с")
    print(f"Сохранено: {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
