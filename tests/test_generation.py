import pytest

from app.generation import (
    CATEGORIES,
    Category,
    EXPECTED_CHARS_PER_QUESTION,
    MAX_TOTAL_QUESTIONS,
    GeneratedQuestion,
    QuestionBatch,
    build_generation_messages,
    generate_questions,
    generation_percent,
    validate_counts,
)


class FakeLlm:
    def __init__(self, batch):
        self.batch = batch
        self.calls = []

    def chat_json(self, messages, model_cls, **kwargs):
        self.calls.append((messages, model_cls, kwargs))
        return self.batch


def item(text, category: Category = "technical", checks=("пункт 1", "пункт 2")):
    return GeneratedQuestion(text=text, category=category, what_to_check=list(checks))


def user_text(**kwargs) -> str:
    defaults: dict = dict(vacancy="Нужен аналитик", profile="Вёл A/B-тесты", counts={"technical": 2, "motivation": 1}, existing=[])
    defaults.update(kwargs)
    return build_generation_messages(**defaults)[1]["content"]


# ---------- проверка количества ----------

def test_validate_counts_drops_zeros_and_keeps_order():
    assert validate_counts({"technical": 3, "behavioral": 0, "motivation": 1}) == {"technical": 3, "motivation": 1}


@pytest.mark.parametrize(
    "counts",
    [{}, {"technical": 0}, {"technical": -1}, {"unknown": 2}, {"technical": MAX_TOTAL_QUESTIONS + 1}, {"technical": 11}],
)
def test_validate_counts_rejects_bad_input(counts):
    with pytest.raises(ValueError):
        validate_counts(counts)


def test_total_may_reach_the_maximum():
    assert sum(validate_counts({"technical": 10, "experience": 10, "motivation": 10}).values()) == MAX_TOTAL_QUESTIONS


# ---------- сообщения ----------

def test_messages_contain_vacancy_profile_and_requested_counts():
    user = user_text()
    assert "Нужен аналитик" in user and "Вёл A/B-тесты" in user
    assert "technical" in user and "motivation" in user
    assert "2" in user and CATEGORIES["technical"] in user


def test_only_requested_categories_are_listed():
    user = user_text(counts={"technical": 2})
    assert "behavioral" not in user


def test_empty_sections_are_skipped():
    user = user_text(vacancy="", profile="", existing=[])
    assert "Вакансия" not in user and "Профиль" not in user and "Уже есть" not in user


def test_existing_questions_are_listed_so_they_are_not_repeated():
    user = user_text(existing=["Что такое SRM?", "Расскажите о себе"])
    assert "Уже есть" in user and "Что такое SRM?" in user and "Расскажите о себе" in user


def test_long_inputs_are_cut():
    user = user_text(vacancy="в" * 20_000, profile="п" * 20_000, existing=[f"вопрос {i}" * 20 for i in range(200)])
    assert len(user) < 14_000


def test_system_prompt_forbids_inventing_facts():
    system = build_generation_messages("в", "п", {"technical": 1}, [])[0]["content"]
    assert "не выдумывай" in system.lower()


# ---------- генерация ----------

def test_generated_questions_get_ids_categories_and_criteria():
    llm = FakeLlm(QuestionBatch(questions=[item("Что такое SRM?"), item("Почему к нам?", "motivation")]))
    questions = generate_questions(llm, "в", "п", {"technical": 1, "motivation": 1}, [])
    assert [(q.id, q.category, q.text) for q in questions] == [
        ("q01", "technical", "Что такое SRM?"), ("q02", "motivation", "Почему к нам?"),
    ]
    assert questions[0].what_to_check == ["пункт 1", "пункт 2"] and questions[0].reference_answer == ""
    assert llm.calls[0][2]["temperature"] > 0


def test_extra_questions_beyond_requested_count_per_category_are_dropped():
    llm = FakeLlm(QuestionBatch(questions=[item("Раз?"), item("Два?"), item("Три?"), item("Почему к нам?", "motivation")]))
    questions = generate_questions(llm, "в", "п", {"technical": 2, "motivation": 1}, [])
    assert [q.text for q in questions] == ["Раз?", "Два?", "Почему к нам?"]


def test_categories_that_were_not_requested_are_dropped():
    llm = FakeLlm(QuestionBatch(questions=[item("Раз?"), item("Как вы ладите с командой?", "behavioral")]))
    assert [q.text for q in generate_questions(llm, "в", "п", {"technical": 2}, [])] == ["Раз?"]


def test_duplicates_of_existing_and_inside_the_batch_are_dropped():
    llm = FakeLlm(QuestionBatch(questions=[item("Что такое SRM?"), item("что такое srm"), item("Что такое MDE?"), item("Что такое MDE?")]))
    questions = generate_questions(llm, "в", "п", {"technical": 4}, existing=["Что такое SRM?"])
    assert [q.text for q in questions] == ["Что такое MDE?"]


def test_blank_texts_are_dropped_and_criteria_are_trimmed():
    llm = FakeLlm(QuestionBatch(questions=[
        item("  ", "technical"), item("Вопрос с критериями?", "technical", ("а", " ", "б", "в", "г", "д", "е")),
    ]))
    questions = generate_questions(llm, "в", "п", {"technical": 2}, [])
    assert [q.text for q in questions] == ["Вопрос с критериями?"]
    assert questions[0].what_to_check == ["а", "б", "в", "г"]


def test_progress_callback_is_passed_to_llm():
    llm = FakeLlm(QuestionBatch(questions=[item("Раз?")]))
    callback = lambda chars: None  # noqa: E731
    generate_questions(llm, "в", "п", {"technical": 1}, [], on_progress=callback)
    assert llm.calls[0][2]["on_progress"] is callback


def test_generation_percent_scales_with_requested_total_and_is_capped():
    total = 6
    assert generation_percent(0, total) == 10
    assert generation_percent(total * EXPECTED_CHARS_PER_QUESTION // 2, total) < generation_percent(total * EXPECTED_CHARS_PER_QUESTION, total)
    assert generation_percent(10 ** 6, total) == 95
