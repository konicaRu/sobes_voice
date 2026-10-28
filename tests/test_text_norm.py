import re

from app.text_norm import apply_stt_fixes, normalize_for_tts

FIXES = {"клик хаус": "ClickHouse", "пивали": "p-value", "ему тест": "A/B-тест"}
GLOSSARY = {"ClickHouse": "кликхаус", "A/B": "эй-би", "SQL": "эс-кью-эль"}


def test_stt_fix_replaces_case_insensitively():
    assert apply_stt_fixes("Смотрел в Клик Хаус", FIXES) == "Смотрел в ClickHouse"


def test_stt_fix_matches_whole_words_only():
    assert apply_stt_fixes("пивалити", FIXES) == "пивалити"


def test_stt_fix_without_rules_keeps_text():
    assert apply_stt_fixes("Привет", {}) == "Привет"


def test_tts_glossary_replaces_terms():
    assert normalize_for_tts("Запрос в ClickHouse на SQL", GLOSSARY) == "Запрос в кликхаус на эс-кью-эль"


def test_tts_glossary_handles_slash_terms():
    assert normalize_for_tts("A/B тест", GLOSSARY) == "эй-би тест"


def test_tts_single_latin_letters_use_letter_names():
    assert normalize_for_tts("Z-тест и t-тест", {}) == "зет-тест и ти-тест"


def test_tts_short_uppercase_acronym_is_spelled_out():
    assert normalize_for_tts("проверка SRM", {}) == "проверка эс-эр-эм"


def test_tts_unknown_latin_word_is_transliterated():
    result = normalize_for_tts("guardrail-метрики и CUPED", {})
    assert not re.search("[A-Za-z]", result)
    assert result.startswith("гуардраил-метрики и ")


def test_tts_glossary_wins_over_transliteration():
    assert normalize_for_tts("SQL", GLOSSARY) == "эс-кью-эль"


def test_tts_numbers_to_words():
    assert normalize_for_tts("Выросло на 12 тысяч", {}) == "Выросло на двенадцать тысяч"


def test_tts_percent_declension():
    assert normalize_for_tts("32%", {}) == "тридцать два процента"
    assert normalize_for_tts("5%", {}) == "пять процентов"
    assert normalize_for_tts("1%", {}) == "один процент"
    assert normalize_for_tts("11%", {}) == "одиннадцать процентов"


def test_tts_decimal_number():
    assert normalize_for_tts("порог 0.05", {}) == "порог ноль целых пять сотых"
