import json
import re
import time
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import TypeVar

from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)


class LlmError(Exception):
    """Ошибка LLM с сообщением на русском для показа в интерфейсе."""


def strip_think(text: str) -> str:
    return re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()


def extract_json(text: str) -> dict:
    text = strip_think(text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start == -1 or end <= start:
            raise ValueError("в ответе нет JSON") from None
        return json.loads(text[start : end + 1])


class LlmClient:
    def __init__(
        self,
        client,
        model: str,
        temperature: float,
        max_tokens: int,
        disable_thinking: bool,
        log_path: Path | None = None,
        retries: int = 2,
    ):
        self._client = client
        self._model = model
        self._temperature = temperature
        self._max_tokens = max_tokens
        self._disable_thinking = disable_thinking
        self._log_path = log_path
        self._retries = retries

    def get_model(self) -> str:
        if self._model != "auto":
            return self._model
        try:
            ids = [m.id for m in self._client.models.list().data]
        except Exception as exc:
            raise LlmError(
                "LM Studio не отвечает. Запустите LM Studio и включите Local Server (порт 1234)."
            ) from exc
        chat_models = [i for i in ids if "embed" not in i.lower()]
        if not chat_models:
            raise LlmError("В LM Studio не загружена модель. Загрузите чат-модель и повторите.")
        return chat_models[0]

    def chat_json(
        self,
        messages: list[dict],
        model_cls: type[T],
        temperature: float | None = None,
        on_progress: Callable[[int], None] | None = None,
    ) -> T:
        """С on_progress ответ читается потоком, и колбэк получает число уже написанных символов."""
        model = self.get_model()
        temperature = self._temperature if temperature is None else temperature
        working = [dict(m) for m in messages]
        response_format = {
            "type": "json_schema",
            "json_schema": {"name": model_cls.__name__, "strict": True, "schema": model_cls.model_json_schema()},
        }
        for _ in range(self._retries + 1):
            started = time.perf_counter()
            options = dict(
                model=model,
                messages=self._prepare(working),
                temperature=temperature,
                max_tokens=self._max_tokens,
                response_format=response_format,
            )
            try:
                if on_progress is None:
                    response = self._client.chat.completions.create(**options)
                    raw = response.choices[0].message.content or ""
                else:
                    response, raw = None, self._read_stream(options, on_progress)
            except Exception as exc:
                raise LlmError(f"LM Studio не ответил: {exc}") from exc
            self._log(model, working, raw, time.perf_counter() - started, response)
            try:
                return model_cls.model_validate(extract_json(raw))
            except ValueError as exc:
                working += [
                    {"role": "assistant", "content": raw},
                    {"role": "user", "content": f"Ответ не прошёл проверку JSON по схеме ({exc}). Верни только корректный JSON."},
                ]
        raise LlmError(f"Модель не вернула корректный JSON после {self._retries + 1} попыток.")

    def _read_stream(self, options: dict, on_progress: Callable[[int], None]) -> str:
        parts: list[str] = []
        written = 0
        for chunk in self._client.chat.completions.create(stream=True, **options):
            piece = chunk.choices[0].delta.content if chunk.choices else None
            if piece:
                parts.append(piece)
                written += len(piece)
                on_progress(written)
        return "".join(parts)

    def _prepare(self, messages: list[dict]) -> list[dict]:
        prepared = [dict(m) for m in messages]
        if self._disable_thinking:
            prepared[-1]["content"] += "\n/no_think"
        return prepared

    def _log(self, model: str, messages: list[dict], raw: str, elapsed: float, response) -> None:
        if self._log_path is None:
            return
        usage = getattr(response, "usage", None)
        record = {
            "time": datetime.now().isoformat(timespec="seconds"),
            "model": model,
            "messages": messages,
            "response": raw,
            "elapsed_s": round(elapsed, 2),
            "completion_tokens": getattr(usage, "completion_tokens", None),
        }
        self._log_path.parent.mkdir(parents=True, exist_ok=True)
        with self._log_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")


def build_llm(config) -> LlmClient:
    from openai import OpenAI

    from app.config import ROOT

    cfg = config.lmstudio
    client = OpenAI(base_url=cfg.base_url, api_key="lm-studio", timeout=cfg.timeout_s)
    return LlmClient(
        client,
        model=cfg.model,
        temperature=cfg.temperature,
        max_tokens=cfg.max_tokens,
        disable_thinking=cfg.disable_thinking,
        log_path=ROOT / config.paths.data_dir / "logs" / "llm.jsonl",
    )
