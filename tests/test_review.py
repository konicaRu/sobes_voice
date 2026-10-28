import pytest
from pydantic import ValidationError

from app.models import Question
from app.review import ReviewResult, build_messages, review_answer

QUESTION = Question(
    id="q01",
    text="Как вы проверяли результаты A/B-теста?",
    category="technical",
    what_to_check=["статистическая значимость", "размер выборки"],
)


class FakeLlm:
    def __init__(self, result):
        self.result = result
        self.calls = []

    def chat_json(self, messages, model_cls):
        self.calls.append((messages, model_cls))
        return self.result


def valid_result(**overrides):
    data = dict(
        verdict="improve", score=3, strengths=["есть p-value"], issues=["нет размера выборки"],
        how_to_fix=["назвать MDE"], improved_outline="1. гипотеза 2. метрика", spoken_summary="Нужно больше конкретики.",
    )
    data.update(overrides)
    return ReviewResult(**data)


def test_messages_contain_question_criteria_and_transcript():
    messages = build_messages(QUESTION, "Смотрел на p-value", seconds=42)
    system, user = messages[0]["content"], messages[1]["content"]
    assert messages[0]["role"] == "system" and messages[1]["role"] == "user"
    assert "интервьюер" in system
    assert "автоматическ" in system
    assert QUESTION.text in user
    assert "размер выборки" in user
    assert "Смотрел на p-value" in user
    assert "42" in user


def test_review_answer_returns_llm_result():
    llm = FakeLlm(valid_result())
    result = review_answer(llm, QUESTION, "ответ", seconds=10)
    assert result.verdict == "improve"
    assert llm.calls[0][1] is ReviewResult


def test_review_result_rejects_bad_verdict_and_score():
    with pytest.raises(ValidationError):
        valid_result(verdict="отлично")
    with pytest.raises(ValidationError):
        valid_result(score=9)
