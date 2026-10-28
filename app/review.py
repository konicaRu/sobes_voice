import re
from collections.abc import Iterable
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from app.llm import LlmError
from app.models import Question

PROMPT_PATH = Path(__file__).resolve().parent / "prompts" / "review.md"
REVIEW_TEMPERATURE = 0.1  # низкая: вердикт на одном ответе должен быть стабильным
MAX_ITEMS = 3
MAX_STRENGTHS = 2
MAX_POINTS = 5
MAX_SPOKEN_WORDS = 45
MAX_VACANCY_CHARS = 1500
MIN_QUOTE_OVERLAP = 0.7  # доля слов цитаты, которые должны реально встречаться в расшифровке
MISSING_MARKERS = {"", "нет", "отсутствует", "не сказано", "не упомянуто"}
ALL_COVERED_SUMMARY = {5: "Отличный ответ. Все ключевые пункты раскрыты.", 4: "Хороший ответ. Все ключевые пункты раскрыты."}
OFF_TOPIC_SUMMARY = "Ответ нужно переделать: вы ответили не на тот вопрос."
# придирки к ошибкам распознавания («неправильный термин X вместо Y») модель пишет, несмотря на запрет в промпте
RECOGNITION_NITPICK = re.compile(r"термин\w*[^.]*вместо|распознав|транскрипц", re.IGNORECASE)
WORD = re.compile(r"[a-zа-яё0-9]+")


def limit_words(text: str, limit: int) -> str:
    """Обрезает по границе предложения, чтобы озвучка не обрывалась на полуслове."""
    words = text.split()
    if len(words) <= limit:
        return text
    cut = " ".join(words[:limit])
    last = max(cut.rfind(mark) for mark in ".!?")
    return cut[: last + 1] if last > 0 else cut + "…"


def verdict_for(score: int) -> Literal["ok", "improve", "redo"]:
    """Вердикт вычисляется из оценки, а не просится у модели: так они не расходятся."""
    if score >= 4:
        return "ok"
    return "improve" if score == 3 else "redo"


class PointCheck(BaseModel):
    point: str  # ключевой пункт (из «Что проверять», эталона или вопроса)
    quote: str  # фрагмент ответа кандидата, где это сказано, или «нет»


def is_missing(quote: str) -> bool:
    return quote.strip(" .—-–").lower() in MISSING_MARKERS


def _words(text: str) -> list[str]:
    return [w for w in WORD.findall(text.lower().replace("ё", "е")) if len(w) >= 3]


def quote_supported(quote: str, transcript: str) -> bool:
    """Цитата настоящая, если почти все её слова есть в расшифровке (модель иногда «цитирует» то, чего не было)."""
    if is_missing(quote):
        return False
    words = _words(quote)
    if not words:
        return False
    known = set(_words(transcript))
    return sum(word in known for word in words) / len(words) >= MIN_QUOTE_OVERLAP


class ReviewDraft(BaseModel):
    """То, что возвращает модель (схема для structured output). Порядок полей важен: сначала модель
    формулирует, о чём вопрос и о чём ответ, и решает, по теме ли он; потом coverage с цитатами, и только
    после этого оценка и замечания."""

    question_topic: str
    answer_topic: str
    on_topic: Literal["да", "частично", "нет"]
    coverage: list[PointCheck]
    score: int = Field(ge=1, le=5)
    strengths: list[str]
    issues: list[str]
    how_to_fix: list[str]
    improved_outline: str
    spoken_summary: str

    @field_validator("strengths", "issues", "how_to_fix", mode="before")
    @classmethod
    def trim_items(cls, value):
        if not isinstance(value, list):
            return value
        return [item.strip() for item in value if isinstance(item, str) and item.strip()][:MAX_ITEMS]

    @field_validator("strengths")
    @classmethod
    def trim_strengths(cls, value: list[str]) -> list[str]:
        return value[:MAX_STRENGTHS]

    @field_validator("coverage")
    @classmethod
    def trim_coverage(cls, value: list[PointCheck]) -> list[PointCheck]:
        return value[:MAX_POINTS]

    @field_validator("spoken_summary")
    @classmethod
    def limit_spoken_summary(cls, value: str) -> str:
        return limit_words(value.strip(), MAX_SPOKEN_WORDS)


class ReviewResult(ReviewDraft):
    verdict: Literal["ok", "improve", "redo"]


def _section(title: str, body: str) -> str:
    return f"{title}:\n{body.strip()}\n\n" if body.strip() else ""


def build_messages(
    question: Question,
    transcript: str,
    seconds: float,
    profile: str = "",
    vacancy: str = "",
    terms: Iterable[str] = (),
) -> list[dict]:
    criteria = "\n".join(f"- {item}" for item in question.what_to_check) or "- не задано"
    term_list = ", ".join(sorted(set(terms)))
    user = (
        f"Вопрос: {question.text}\n\n"
        f"Что проверять:\n{criteria}\n\n"
        + _section(
            "Эталонный ответ (один из возможных хороших ответов; другая формулировка с тем же смыслом тоже хороша)",
            question.reference_answer,
        )
        + _section("Профиль кандидата (факты из резюме и кейсов)", profile)
        + _section("Вакансия (кратко)", vacancy[:MAX_VACANCY_CHARS])
        + _section(
            "Термины, которые распознаются с ошибками (если слово в расшифровке похоже на термин, считай, что кандидат назвал его верно)",
            term_list,
        )
        + f"Длительность ответа: {seconds:.0f} с\n\n"
        f"Ответ кандидата (автоматическая расшифровка речи):\n{transcript}"
    )
    return [
        {"role": "system", "content": PROMPT_PATH.read_text(encoding="utf-8")},
        {"role": "user", "content": user},
    ]


def _ceiling(total: int, missing: int) -> int:
    """Потолок оценки по доле нераскрытых пунктов: треть и больше — не выше 3, хотя бы один — не выше 4."""
    if missing == 0 or total == 0:
        return 5
    return 3 if missing * 3 >= total else 4


class TopicCheck(BaseModel):
    same_topic: Literal["да", "нет"]


TOPIC_PROMPT = (
    "Сравни тему вопроса собеседования и тему ответа кандидата. Оценивай только тему, а не качество ответа.\n"
    "Ответь «да», если ответ посвящён тому, о чём спрашивали, пусть неполный или с ошибками.\n"
    "Ответь «нет», если кандидат говорит о другом: например, спрашивали про решение или выбор, "
    "а он объясняет устройство метода или инструмента.\n"
    "Верни JSON с одним полем same_topic: «да» или «нет»."
)


def _same_topic(llm, question: Question, draft: ReviewDraft) -> bool:
    """Арбитр для неуверенного «частично»: короткая отдельная проверка надёжнее, чем решение внутри большого разбора."""
    messages = [
        {"role": "system", "content": TOPIC_PROMPT},
        {
            "role": "user",
            "content": f"Вопрос: {question.text}\nТема вопроса: {draft.question_topic}\nТема ответа: {draft.answer_topic}",
        },
    ]
    try:
        return llm.chat_json(messages, TopicCheck, temperature=0.0).same_topic == "да"
    except LlmError:
        return True  # не получилось проверить: не наказываем кандидата


def review_answer(
    llm,
    question: Question,
    transcript: str,
    seconds: float,
    profile: str = "",
    vacancy: str = "",
    terms: Iterable[str] = (),
) -> ReviewResult:
    messages = build_messages(question, transcript, seconds, profile, vacancy, terms)
    draft = llm.chat_json(messages, ReviewDraft, temperature=REVIEW_TEMPERATURE)
    fields = draft.model_dump()

    off_topic = draft.on_topic == "нет" or (draft.on_topic == "частично" and not _same_topic(llm, question, draft))
    if off_topic:  # ответ про другое: оценка и текст формульные, модель тут легко ошибается в сторону мягкости
        fields.update(
            score=1,
            issues=[f"Ответ не по теме: вопрос про «{draft.question_topic}», а вы говорили про «{draft.answer_topic}»."],
            how_to_fix=[f"Ответьте на заданный вопрос: {draft.question_topic}."],
            spoken_summary=OFF_TOPIC_SUMMARY,
        )
        return ReviewResult(verdict="redo", **fields)

    missing = [p.point for p in draft.coverage if not quote_supported(p.quote, transcript)]
    score = min(draft.score, _ceiling(len(draft.coverage), len(missing)))
    issues = [text for text in draft.issues if not RECOGNITION_NITPICK.search(text)]
    fixes = [text for text in draft.how_to_fix if not RECOGNITION_NITPICK.search(text)]
    summary = draft.spoken_summary
    if missing and not issues:
        issues = [f"Не раскрыт пункт: {point}" for point in missing][:MAX_ITEMS]
    if missing and not fixes:
        fixes = [f"Раскройте пункт: {point}" for point in missing][:MAX_ITEMS]
    if score < 4 <= draft.score:
        if score <= 2:
            summary = "Ответ нужно переделать: он лишь отчасти отвечает на вопрос."
        else:
            summary = f"Неплохо, но стоит доработать. Не раскрыт пункт: {missing[0]}."

    if score >= 4:
        if draft.coverage and not missing:
            # хороший ответ, все пункты подтверждены цитатами: придирки модели («нет X», хотя X сказано) вредны
            issues, fixes, summary = [], [], ALL_COVERED_SUMMARY[score]
        else:
            issues, fixes = issues[:1], fixes[:1]

    fields.update(score=score, issues=issues, how_to_fix=fixes, spoken_summary=summary)
    return ReviewResult(verdict=verdict_for(score), **fields)
