"""Проверка LM Studio: модели, скорость, /no_think, json_schema, тестовый разбор ответа."""
import json
import re
import sys
import time
from pathlib import Path

import yaml
from openai import OpenAI

ROOT = Path(__file__).resolve().parent.parent

REVIEW_SCHEMA = {
    "type": "object",
    "properties": {
        "verdict": {"type": "string", "enum": ["ok", "improve", "redo"]},
        "score": {"type": "integer", "minimum": 1, "maximum": 5},
        "issues": {"type": "array", "items": {"type": "string"}},
        "spoken_summary": {"type": "string"},
    },
    "required": ["verdict", "score", "issues", "spoken_summary"],
}

QUESTION = "Расскажите, как вы проверяли результаты A/B-теста и что делали, если метрика не была значимой."
ANSWER = "Ну, мы запускали тест, смотрели на p-value, и если оно было меньше 0.05, то считали, что всё хорошо. Если нет, то просто ждали ещё."


def strip_think(text: str) -> str:
    return re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()


def chat(client, model, messages, **kwargs):
    t0 = time.perf_counter()
    resp = client.chat.completions.create(model=model, messages=messages, **kwargs)
    elapsed = time.perf_counter() - t0
    tokens = resp.usage.completion_tokens if resp.usage else 0
    return resp.choices[0].message.content or "", elapsed, tokens


def main() -> int:
    cfg = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))["lmstudio"]
    client = OpenAI(base_url=cfg["base_url"], api_key="lm-studio", timeout=cfg["timeout_s"])

    try:
        models = [m.id for m in client.models.list().data]
    except Exception as exc:
        print(f"ОШИБКА: LM Studio не отвечает на {cfg['base_url']} ({exc}).")
        print("Запустите LM Studio, загрузите модель и включите Local Server (вкладка Developer).")
        return 1
    print("Модели на сервере:", ", ".join(models) or "(пусто)")
    if not models:
        print("ОШИБКА: ни одна модель не загружена.")
        return 1
    model = models[0] if cfg["model"] == "auto" else cfg["model"]
    print(f"Используем: {model}\n")

    # 1. Скорость генерации
    _, elapsed, tokens = chat(
        client, model,
        [{"role": "user", "content": "/no_think Объясни в 5 предложениях, что такое retention в продукте."}],
        max_tokens=300, temperature=cfg["temperature"],
    )
    print(f"[1] Скорость: {tokens} токенов за {elapsed:.1f} с = {tokens / elapsed:.1f} ток/с")

    # 2. /no_think
    raw, _, _ = chat(client, model, [{"role": "user", "content": "/no_think Сколько будет 17*23?"}], max_tokens=400)
    has_think = "<think>" in raw
    print(f"[2] /no_think: блок <think> {'ПРИСУТСТВУЕТ (нужно вырезать)' if has_think else 'отсутствует'}; ответ: {strip_think(raw)[:80]!r}")

    # 3. json_schema
    messages = [
        {"role": "system", "content": "/no_think Ты строгий, но конструктивный интервьюер на позицию продуктового аналитика. Разбери ответ кандидата. Отвечай по-русски."},
        {"role": "user", "content": f"Вопрос: {QUESTION}\nОтвет кандидата (автоматическая расшифровка речи): {ANSWER}"},
    ]
    try:
        raw, elapsed, tokens = chat(
            client, model, messages,
            max_tokens=cfg["max_tokens"], temperature=cfg["temperature"],
            response_format={"type": "json_schema", "json_schema": {"name": "review", "strict": True, "schema": REVIEW_SCHEMA}},
        )
        data = json.loads(strip_think(raw))
        print(f"[3] json_schema: РАБОТАЕТ, разбор за {elapsed:.1f} с ({tokens} токенов)")
        print(json.dumps(data, ensure_ascii=False, indent=2))
    except Exception as exc:
        print(f"[3] json_schema: НЕ СРАБОТАЛ ({exc}). Нужен запасной путь: извлечение JSON из текста.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
