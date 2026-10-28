import re
from decimal import Decimal

from num2words import num2words


def _term_pattern(term: str) -> str:
    return rf"(?<!\w){re.escape(term)}(?!\w)"


def apply_stt_fixes(text: str, fixes: dict[str, str]) -> str:
    """Исправляет типичные ошибки распознавания по таблице glossary.stt_fix."""
    for wrong in sorted(fixes, key=len, reverse=True):
        text = re.sub(_term_pattern(wrong), lambda _: fixes[wrong], text, flags=re.IGNORECASE)
    return text


def _percent_word(n: int) -> str:
    if 11 <= n % 100 <= 14:
        return "процентов"
    return {1: "процент", 2: "процента", 3: "процента", 4: "процента"}.get(n % 10, "процентов")


LETTER_NAMES = {
    "a": "эй", "b": "би", "c": "си", "d": "ди", "e": "и", "f": "эф", "g": "джи", "h": "эйч", "i": "ай",
    "j": "джей", "k": "кей", "l": "эль", "m": "эм", "n": "эн", "o": "оу", "p": "пи", "q": "кью",
    "r": "эр", "s": "эс", "t": "ти", "u": "ю", "v": "ви", "w": "дабл-ю", "x": "икс", "y": "вай", "z": "зет",
}
DIGRAPHS = {"sh": "ш", "ch": "ч", "zh": "ж", "ph": "ф", "th": "т", "ck": "к", "ee": "и", "oo": "у"}
LETTERS = {
    "a": "а", "b": "б", "c": "к", "d": "д", "e": "е", "f": "ф", "g": "г", "h": "х", "i": "и", "j": "дж",
    "k": "к", "l": "л", "m": "м", "n": "н", "o": "о", "p": "п", "q": "к", "r": "р", "s": "с", "t": "т",
    "u": "у", "v": "в", "w": "в", "x": "кс", "y": "й", "z": "з",
}
MAX_SPELLED_ACRONYM = 4


def _transliterate(word: str) -> str:
    lower, result, i = word.lower(), [], 0
    while i < len(lower):
        if lower[i : i + 2] in DIGRAPHS:
            result.append(DIGRAPHS[lower[i : i + 2]])
            i += 2
        else:
            result.append(LETTERS[lower[i]])
            i += 1
    return "".join(result)


def _latin_to_russian(match: re.Match) -> str:
    """Запасной вариант для латиницы вне глоссария: Silero на ней падает."""
    word = match[0]
    if len(word) == 1 or (word.isupper() and len(word) <= MAX_SPELLED_ACRONYM):
        return "-".join(LETTER_NAMES[c] for c in word.lower())
    return _transliterate(word)


MAX_NUMBER_DIGITS = 15  # длиннее читаем по цифрам: num2words на огромных числах тяжёлый


def _integer_to_words(match: re.Match) -> str:
    digits = match[0]
    if len(digits) > MAX_NUMBER_DIGITS:
        return " ".join(num2words(int(d), lang="ru") for d in digits)
    return num2words(int(digits), lang="ru")


def normalize_for_tts(text: str, glossary: dict[str, str]) -> str:
    """Подготовка текста для Silero: термины по глоссарию, латиница, числа и проценты словами."""
    for term in sorted(glossary, key=len, reverse=True):
        text = re.sub(_term_pattern(term), lambda _: glossary[term], text, flags=re.IGNORECASE)
    text = re.sub(r"[A-Za-z]+", _latin_to_russian, text)
    text = re.sub(
        rf"(\d{{1,{MAX_NUMBER_DIGITS}}})\s*%", lambda m: f"{num2words(int(m[1]), lang='ru')} {_percent_word(int(m[1]))}", text
    )
    text = re.sub(
        rf"\d{{1,{MAX_NUMBER_DIGITS}}}[.,]\d{{1,{MAX_NUMBER_DIGITS}}}",
        lambda m: num2words(Decimal(m[0].replace(",", ".")), lang="ru"),
        text,
    )
    text = re.sub(r"\d+", _integer_to_words, text)
    return text.replace("%", " процентов")
