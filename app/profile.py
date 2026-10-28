"""Профиль кандидата: сжатая выжимка из резюме и кейсов, которую ревьюер использует как опору на факты."""
from pathlib import Path

from pydantic import BaseModel

PROMPT_PATH = Path(__file__).resolve().parent / "prompts" / "candidate_profile.md"
MAX_SOURCE_CHARS = 6000  # на раздел: резюме и кейсы вместе должны помещаться в контекст 8k вместе с ответом
MAX_PROFILE_CHARS = 6000


class ProfileDraft(BaseModel):
    profile: str


def _section(title: str, texts: list[str]) -> str:
    body = "\n\n".join(t.strip() for t in texts if t.strip())[:MAX_SOURCE_CHARS]
    return f"{title}:\n{body}\n\n" if body else ""


def build_profile_messages(resumes: list[str], cases: list[str]) -> list[dict]:
    user = _section("Резюме", resumes) + _section("Кейсы", cases)
    return [
        {"role": "system", "content": PROMPT_PATH.read_text(encoding="utf-8")},
        {"role": "user", "content": user.strip()},
    ]


def build_profile(llm, resumes: list[str], cases: list[str]) -> str:
    draft = llm.chat_json(build_profile_messages(resumes, cases), ProfileDraft, temperature=0.0)
    return draft.profile.strip()[:MAX_PROFILE_CHARS]
