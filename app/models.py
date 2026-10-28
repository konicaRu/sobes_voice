from typing import Annotated

from pydantic import BaseModel, Field, StringConstraints

SLOTS = ("questions", "vacancy", "resume", "cases")

MAX_QUESTION_CHARS = 1000
MAX_CRITERION_CHARS = 500
MAX_CRITERIA = 20
MAX_QUESTIONS = 200
MAX_REFERENCE_CHARS = 3000

Criterion = Annotated[str, StringConstraints(max_length=MAX_CRITERION_CHARS)]


class Question(BaseModel):
    id: str = ""
    text: str = Field(min_length=1, max_length=MAX_QUESTION_CHARS)
    category: str = "general"
    reference_answer: str = Field(default="", max_length=MAX_REFERENCE_CHARS)
    what_to_check: list[Criterion] = Field(default_factory=list, max_length=MAX_CRITERIA)
    expected_points: list[Criterion] = Field(default_factory=list, max_length=MAX_CRITERIA)


class Interview(BaseModel):
    id: str
    title: str
    created_at: str


class Attempt(BaseModel):
    q: int
    n: int
    text: str
    seconds: float
    audio: str | None = None
    review: dict | None = None


class Session(BaseModel):
    sid: str
    interview_id: str
    started_at: str
    questions: list[Question]
    attempts: list[Attempt] = Field(default_factory=list)
    skipped: list[int] = Field(default_factory=list)
    finished_at: str | None = None
