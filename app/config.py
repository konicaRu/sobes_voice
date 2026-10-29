from pathlib import Path

import yaml
from pydantic import BaseModel

ROOT = Path(__file__).resolve().parent.parent


class ServerConfig(BaseModel):
    host: str = "127.0.0.1"
    port: int = 8000
    stop_when_closed: bool = True  # сервер завершается, когда страницу закрыли
    idle_timeout_s: int = 150


class LmStudioConfig(BaseModel):
    base_url: str = "http://localhost:1234/v1"
    model: str = "auto"
    temperature: float = 0.3
    max_tokens: int = 1200
    disable_thinking: bool = True
    recommended_model: str = "qwen2.5-14b-instruct-1m"  # только подсказка пользователю, что загрузить в LM Studio
    timeout_s: int = 120


class SttConfig(BaseModel):
    engine: str = "gigaam"
    model: str = "gigaam-v3-e2e-rnnt"
    chunk_seconds: int = 20
    max_answer_seconds: int = 300


class TtsConfig(BaseModel):
    engine: str = "silero"
    model: str = "v5_6_ru"
    speaker: str = "eugene"
    rate: str = "slow"
    sample_rate: int = 48000
    speak_feedback: bool = True


class PathsConfig(BaseModel):
    data_dir: str = "data"


class Config(BaseModel):
    server: ServerConfig = ServerConfig()
    lmstudio: LmStudioConfig = LmStudioConfig()
    stt: SttConfig = SttConfig()
    tts: TtsConfig = TtsConfig()
    paths: PathsConfig = PathsConfig()


class Glossary(BaseModel):
    tts: dict[str, str] = {}
    stt_fix: dict[str, str] = {}


def lmstudio_hint(config: Config) -> str:
    return (
        f"Откройте LM Studio, загрузите модель {config.lmstudio.recommended_model} (контекст 8192, "
        "выгрузка на GPU на максимум) и включите Local Server (порт 1234)."
    )


def load_config(path: Path = ROOT / "config.yaml") -> Config:
    return Config.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")) or {})


def load_glossary(path: Path = ROOT / "glossary.yaml") -> Glossary:
    return Glossary.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")) or {})
