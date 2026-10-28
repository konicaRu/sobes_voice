"""Генерация вопросов из вакансии и профиля кандидата (PLAN.md, 5.2)."""
import re
from collections import Counter
from collections.abc import Callable
from pathlib import Path
from typing import Literal

from pydantic import BaseModel

from app.models import MAX_CRITERIA, MAX_CRITERION_CHARS, MAX_QUESTION_CHARS, Question

PROMPT_PATH = Path(__file__).resolve().parent / "prompts" / "generate_questions.md"
GENERATION_TEMPERATURE = 0.7  # вопросы должны быть разнообразными, в отличие от оценки ответов
MAX_TOTAL_QUESTIONS = 30
MAX_PER_CATEGORY = 10
MAX_CRITERIA_PER_QUESTION = 4
EXPECTED_CHARS_PER_QUESTION = 350  # вопрос с критериями в JSON; для расчёта процентов
MAX_VACANCY_CHARS = 3000
MAX_PROFILE_CHARS = 4500
MAX_EXISTING = 30
MAX_EXISTING_CHARS = 120

Category = Literal["experience", "technical", "product_case", "behavioral", "motivation"]
CATEGORIES: dict[str, str] = {
    "experience": "опыт и кейсы кандидата",
    "technical": "технические: SQL, статистика, A/B-тесты, метрики",
    "product_case": "продуктовые кейсы и оценка",
    "behavioral": "поведенческие",
    "motivation": "мотивация",
}


class GeneratedQuestion(BaseModel):
    text: str
    category: Category
    what_to_check: list[str]


class QuestionBatch(BaseModel):
    questions: list[GeneratedQuestion]


def validate_counts(counts: dict[str, int]) -> dict[str, int]:
    cleaned: dict[str, int] = {}
    for category, number in counts.items():
        if category not in CATEGORIES:
            raise ValueError(f"Неизвестная категория: {category}.")
        if not 0 <= number <= MAX_PER_CATEGORY:
            raise ValueError(f"В категории можно запросить от 0 до {MAX_PER_CATEGORY} вопросов.")
        if number:
            cleaned[category] = number
    if not cleaned:
        raise ValueError("Укажите хотя бы один вопрос.")
    if sum(cleaned.values()) > MAX_TOTAL_QUESTIONS:
        raise ValueError(f"Всего можно запросить не больше {MAX_TOTAL_QUESTIONS} вопросов.")
    return cleaned


def generation_percent(written_chars: int, total_questions: int) -> int:
    expected = max(1, total_questions) * EXPECTED_CHARS_PER_QUESTION
    return min(95, 10 + int(85 * written_chars / expected))


def _section(title: str, body: str) -> str:
    return f"{title}:\n{body.strip()}\n\n" if body.strip() else ""


def build_generation_messages(vacancy: str, profile: str, counts: dict[str, int], existing: list[str]) -> list[dict]:
    wanted = "\n".join(f"- {category} ({CATEGORIES[category]}): {number}" for category, number in counts.items())
    listed = "\n".join(f"- {text.strip()[:MAX_EXISTING_CHARS]}" for text in existing[:MAX_EXISTING] if text.strip())
    user = (
        _section("Вакансия", vacancy[:MAX_VACANCY_CHARS])
        + _section("Профиль кандидата (факты из резюме и кейсов)", profile[:MAX_PROFILE_CHARS])
        + _section("Нужно сгенерировать (категория: сколько)", wanted)
        + _section("Уже есть, не повторяй", listed)
    )
    return [
        {"role": "system", "content": PROMPT_PATH.read_text(encoding="utf-8")},
        {"role": "user", "content": user.strip()},
    ]


def _key(text: str) -> str:
    return re.sub(r"\W+", " ", text.lower()).strip()


def generate_questions(
    llm,
    vacancy: str,
    profile: str,
    counts: dict[str, int],
    existing: list[str],
    on_progress: Callable[[int], None] | None = None,
) -> list[Question]:
    counts = validate_counts(counts)
    messages = build_generation_messages(vacancy, profile, counts, existing)
    batch = llm.chat_json(messages, QuestionBatch, temperature=GENERATION_TEMPERATURE, on_progress=on_progress)

    seen = {_key(text) for text in existing}
    taken: Counter[str] = Counter()
    questions: list[Question] = []
    for generated in batch.questions:
        text = generated.text.strip()
        if not text or generated.category not in counts or taken[generated.category] >= counts[generated.category]:
            continue
        if _key(text) in seen:
            continue
        seen.add(_key(text))
        taken[generated.category] += 1
        criteria = [c.strip()[:MAX_CRITERION_CHARS] for c in generated.what_to_check if c.strip()]
        questions.append(
            Question(
                id=f"q{len(questions) + 1:02d}",
                text=text[:MAX_QUESTION_CHARS],
                category=generated.category,
                what_to_check=criteria[: min(MAX_CRITERIA_PER_QUESTION, MAX_CRITERIA)],
            )
        )
    return questions
