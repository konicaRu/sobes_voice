from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from app.models import Question

PROMPT_PATH = Path(__file__).resolve().parent / "prompts" / "review.md"


class ReviewResult(BaseModel):
    verdict: Literal["ok", "improve", "redo"]
    score: int = Field(ge=1, le=5)
    strengths: list[str]
    issues: list[str]
    how_to_fix: list[str]
    improved_outline: str
    spoken_summary: str


def build_messages(question: Question, transcript: str, seconds: float) -> list[dict]:
    criteria = "\n".join(f"- {item}" for item in question.what_to_check) or "- не задано"
    user = (
        f"Вопрос: {question.text}\n\n"
        f"Что проверять:\n{criteria}\n\n"
        f"Длительность ответа: {seconds:.0f} с\n\n"
        f"Ответ кандидата (автоматическая расшифровка речи):\n{transcript}"
    )
    return [
        {"role": "system", "content": PROMPT_PATH.read_text(encoding="utf-8")},
        {"role": "user", "content": user},
    ]


def review_answer(llm, question: Question, transcript: str, seconds: float) -> ReviewResult:
    return llm.chat_json(build_messages(question, transcript, seconds), ReviewResult)
