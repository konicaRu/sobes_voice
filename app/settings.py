"""Настройки, которые меняются со страницы «Настройки»: хранятся в data/settings.json поверх config.yaml."""
import json
import os
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, ValidationError

SPEAKERS = ("aidar", "baya", "kseniya", "xenia", "eugene")
RATES = ("x-slow", "slow", "medium", "fast", "x-fast")


class SettingsUpdate(BaseModel):
    speaker: Literal["aidar", "baya", "kseniya", "xenia", "eugene"]
    rate: Literal["x-slow", "slow", "medium", "fast", "x-fast"]
    speak_feedback: bool
    model: str = Field(min_length=1, max_length=200)  # "auto" = первая загруженная модель
    disable_thinking: bool


def current_values(config) -> SettingsUpdate:
    return SettingsUpdate(
        speaker=config.tts.speaker, rate=config.tts.rate, speak_feedback=config.tts.speak_feedback,
        model=config.lmstudio.model, disable_thinking=config.lmstudio.disable_thinking,
    )


def apply_to_config(config, values: SettingsUpdate) -> None:
    config.tts.speaker = values.speaker
    config.tts.rate = values.rate
    config.tts.speak_feedback = values.speak_feedback
    config.lmstudio.model = values.model
    config.lmstudio.disable_thinking = values.disable_thinking


def apply_to_engines(config, values: SettingsUpdate, tts, llm) -> None:
    """Применяет настройки к уже загруженным движкам без перезапуска сервера."""
    if tts is not None:
        tts.voice_id = f"{config.tts.model}:{values.speaker}:{values.rate}"
    if llm is not None:
        llm.configure(model=values.model, disable_thinking=values.disable_thinking)


def load_overrides(path: Path) -> SettingsUpdate | None:
    try:
        return SettingsUpdate.model_validate(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, ValueError, ValidationError):
        return None


def save_overrides(path: Path, values: SettingsUpdate) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(values.model_dump_json(indent=2), encoding="utf-8")
    os.replace(temporary, path)


def apply_overrides(config, path: Path) -> None:
    values = load_overrides(path)
    if values is not None:
        apply_to_config(config, values)
