"""Живая озвучка вопроса: модель переписывает письменную формулировку в разговорную речь."""
from pathlib import Path

from pydantic import BaseModel

from app.llm import LlmError

PROMPT_PATH = Path(__file__).resolve().parent / "prompts" / "speak_question.md"
SPOKEN_TEMPERATURE = 0.5
MAX_GROWTH = 2  # озвученная фраза не длиннее исходной больше чем вдвое (плюс запас на связки)
GROWTH_MARGIN_CHARS = 80


class SpokenDraft(BaseModel):
    spoken: str


def build_messages(text: str) -> list[dict]:
    return [
        {"role": "system", "content": PROMPT_PATH.read_text(encoding="utf-8")},
        {"role": "user", "content": f"Вопрос: {text}"},
    ]


def humanize_question(llm, text: str) -> str:
    """Не получилось или модель разболталась: читаем вопрос как написан, интервью не должно ломаться."""
    try:
        draft = llm.chat_json(build_messages(text), SpokenDraft, temperature=SPOKEN_TEMPERATURE)
    except LlmError:
        return text
    spoken = " ".join(draft.spoken.split())
    if not spoken or len(spoken) > len(text) * MAX_GROWTH + GROWTH_MARGIN_CHARS:
        return text
    return spoken
