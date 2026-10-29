from pathlib import Path

from app.config import load_config, load_glossary

ROOT = Path(__file__).resolve().parent.parent


def test_load_config_reads_repo_config():
    cfg = load_config(ROOT / "config.yaml")
    assert cfg.lmstudio.base_url == "http://localhost:1234/v1"
    assert cfg.lmstudio.model == "auto"
    assert cfg.tts.rate == "slow"
    assert cfg.stt.chunk_seconds == 20
    assert cfg.paths.data_dir == "data"


def test_load_glossary_returns_both_tables():
    glossary = load_glossary(ROOT / "glossary.yaml")
    assert glossary.tts["ClickHouse"] == "кликхаус"
    assert glossary.stt_fix["клик хаус"] == "ClickHouse"


def test_glossary_fixes_real_a_b_misrecognitions():
    from app.text_norm import apply_stt_fixes

    fixes = load_glossary(ROOT / "glossary.yaml").stt_fix
    assert apply_stt_fixes("Амба-тест проводится для того", fixes) == "A/B-тест проводится для того"
    assert apply_stt_fixes("запускали амба тест на группе", fixes) == "запускали A/B-тест на группе"


def test_lmstudio_hint_names_recommended_model_context_and_port():
    from app.config import Config, lmstudio_hint

    config = Config()
    config.lmstudio.recommended_model = "my-model"
    hint = lmstudio_hint(config)
    assert "my-model" in hint and "8192" in hint and "1234" in hint
