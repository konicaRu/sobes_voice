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
