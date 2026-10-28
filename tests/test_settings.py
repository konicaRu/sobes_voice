import json

import pytest
from fastapi.testclient import TestClient

from app.config import Config, Glossary
from app.main import create_app
from app.settings import SettingsUpdate, apply_overrides, load_overrides, save_overrides
from app.storage import Storage

VALID = {"speaker": "xenia", "rate": "medium", "speak_feedback": False, "model": "qwen-x", "disable_thinking": False}


class FakeTts:
    voice_id = ""

    def synthesize(self, text):
        return b"RIFFfake"


class FakeLlm:
    def __init__(self):
        self.configured = None

    def get_model(self):
        return "qwen-test"

    def list_models(self):
        return ["qwen-test", "other-model"]

    def configure(self, model, disable_thinking):
        self.configured = (model, disable_thinking)


@pytest.fixture
def parts(tmp_path):
    config, tts, llm = Config(), FakeTts(), FakeLlm()
    app = create_app(config=config, glossary=Glossary(), storage=Storage(tmp_path), stt=object(), tts=tts, llm=llm)
    return TestClient(app), config, tts, llm, tmp_path


def test_get_returns_current_values_and_choices(parts):
    client, *_ = parts
    data = client.get("/api/settings").json()
    assert data["values"] == {
        "speaker": "eugene", "rate": "slow", "speak_feedback": True, "model": "auto", "disable_thinking": True,
    }
    assert "xenia" in data["choices"]["speakers"] and "medium" in data["choices"]["rates"]
    assert data["choices"]["models"] == ["qwen-test", "other-model"]
    assert data["stt"]["engine"] == "gigaam"


def test_put_applies_to_config_tts_and_llm_and_saves_file(parts):
    client, config, tts, llm, tmp_path = parts
    response = client.put("/api/settings", json=VALID)
    assert response.status_code == 200 and response.json()["values"] == VALID
    assert (config.tts.speaker, config.tts.rate, config.tts.speak_feedback) == ("xenia", "medium", False)
    assert (config.lmstudio.model, config.lmstudio.disable_thinking) == ("qwen-x", False)
    assert llm.configured == ("qwen-x", False)
    assert tts.voice_id.endswith("xenia:medium")
    assert json.loads((tmp_path / "settings.json").read_text(encoding="utf-8")) == VALID


@pytest.mark.parametrize("field,value", [("speaker", "nobody"), ("rate", "turbo"), ("model", "")])
def test_put_rejects_invalid_values(parts, field, value):
    client, *_ = parts
    assert client.put("/api/settings", json={**VALID, field: value}).status_code == 422


def test_overrides_are_loaded_at_startup(tmp_path):
    save_overrides(tmp_path / "settings.json", SettingsUpdate(**VALID))
    config = Config()
    create_app(config=config, glossary=Glossary(), storage=Storage(tmp_path), stt=object(), tts=FakeTts(), llm=FakeLlm())
    assert config.tts.speaker == "xenia" and config.lmstudio.model == "qwen-x"


def test_broken_overrides_file_is_ignored(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text("{не json", encoding="utf-8")
    assert load_overrides(path) is None
    path.write_text(json.dumps({**VALID, "speaker": "nobody"}), encoding="utf-8")
    config = Config()
    apply_overrides(config, path)
    assert config.tts.speaker == "eugene"
