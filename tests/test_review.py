import pytest
from pydantic import ValidationError

from app.llm import LlmCancelled, LlmError
from app.models import Question
from app.review import (
    MAX_ITEMS,
    MAX_SPOKEN_WORDS,
    REVIEW_TEMPERATURE,
    PointCheck,
    ReviewDraft,
    TopicCheck,
    build_messages,
    quote_supported,
    review_answer,
    verdict_for,
)

QUESTION = Question(
    id="q01",
    text="Как вы проверяли результаты A/B-теста?",
    category="technical",
    what_to_check=["статистическая значимость", "размер выборки"],
    reference_answer="Проверяю SRM, размер выборки и интерпретирую p-value.",
)
TRANSCRIPT = "Сначала смотрю эс эр эм, потом проверяю размер выборки и смотрю пивали, а ещё доверительный интервал"

COVERED = [
    PointCheck(point="SRM", quote="смотрю эс эр эм"),
    PointCheck(point="p-value", quote="смотрю пивали"),
    PointCheck(point="выборка", quote="проверяю размер выборки"),
]
WITH_MISSING = [*COVERED, PointCheck(point="MDE", quote="нет")]


class FakeLlm:
    def __init__(self, result, same_topic: "str | Exception" = "да"):
        self.result = result
        self.same_topic = same_topic  # ответ арбитра темы; исключение будет брошено
        self.calls = []

    def chat_json(self, messages, model_cls, **kwargs):
        self.calls.append((messages, model_cls, kwargs))
        if model_cls is TopicCheck:
            if isinstance(self.same_topic, Exception):
                raise self.same_topic
            return TopicCheck(same_topic="да" if self.same_topic == "да" else "нет")
        return self.result


def make_draft(**overrides) -> ReviewDraft:
    data: dict = dict(
        question_topic="проверка результатов A/B-теста", answer_topic="проверка результатов A/B-теста", on_topic="да",
        coverage=COVERED, score=3, strengths=["есть p-value"], issues=["нет размера выборки"],
        how_to_fix=["назвать MDE"], improved_outline="1. гипотеза 2. метрика", spoken_summary="Нужно больше конкретики.",
    )
    data.update(overrides)
    return ReviewDraft(**data)


def review(draft, transcript=TRANSCRIPT):
    return review_answer(FakeLlm(draft), QUESTION, transcript, seconds=10)


def user_message(**kwargs) -> str:
    return build_messages(QUESTION, "Смотрел на p-value", 42, **kwargs)[1]["content"]


# ---------- сообщения ----------

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


def test_reference_answer_is_presented_as_one_possible_good_answer():
    user = user_message()
    assert "Проверяю SRM, размер выборки и интерпретирую p-value." in user
    assert "один из возможных" in user


def test_optional_sections_are_skipped_when_empty():
    bare = Question(id="q02", text="Что такое SRM?")
    user = build_messages(bare, "ответ", 10)[1]["content"]
    assert "Эталон" not in user and "Профиль" not in user and "Вакансия" not in user and "Термины" not in user


def test_profile_vacancy_and_terms_are_included_when_given():
    user = user_message(profile="Вёл A/B-тесты в А3.", vacancy="Нужен продуктовый аналитик.", terms=["A/B", "SQL"])
    assert "Профиль кандидата" in user and "Вёл A/B-тесты в А3." in user
    assert "Вакансия" in user and "Нужен продуктовый аналитик." in user
    assert "A/B" in user and "SQL" in user


def test_long_vacancy_is_cut():
    user = user_message(vacancy="в" * 10_000)
    assert len(user) < 6000


# ---------- вызов модели ----------

def test_review_asks_for_draft_schema_at_low_temperature():
    llm = FakeLlm(make_draft())
    review_answer(llm, QUESTION, TRANSCRIPT, seconds=10)
    _, model_cls, kwargs = llm.calls[0]
    assert model_cls is ReviewDraft
    assert kwargs["temperature"] == REVIEW_TEMPERATURE


def test_review_passes_context_through_to_prompt():
    llm = FakeLlm(make_draft())
    review_answer(llm, QUESTION, TRANSCRIPT, seconds=10, profile="Профиль X", vacancy="Вакансия Y", terms=["SQL"])
    user = llm.calls[0][0][1]["content"]
    assert "Профиль X" in user and "Вакансия Y" in user and "SQL" in user


# ---------- вердикт ----------

@pytest.mark.parametrize(("score", "verdict"), [(5, "ok"), (4, "ok"), (3, "improve"), (2, "redo"), (1, "redo")])
def test_verdict_is_derived_from_score(score, verdict):
    assert verdict_for(score) == verdict
    result = review(make_draft(score=score, issues=["замечание"], how_to_fix=["совет"]))
    assert result.verdict == verdict
    assert result.score == score


# ---------- ответ не по теме ----------

def test_off_topic_answer_gets_one_point_with_formulaic_feedback():
    draft = make_draft(
        score=3, on_topic="нет", question_topic="масштабирование пилота AI", answer_topic="механика A/B-теста",
        issues=["придирка"], how_to_fix=["совет"], spoken_summary="Хороший ответ.",
    )
    result = review(draft)
    assert result.score == 1 and result.verdict == "redo"
    assert result.issues == ["Ответ не по теме: вопрос про «масштабирование пилота AI», а вы говорили про «механика A/B-теста»."]
    assert result.how_to_fix == ["Ответьте на заданный вопрос: масштабирование пилота AI."]
    assert result.spoken_summary == "Ответ нужно переделать: вы ответили не на тот вопрос."


def partly(**overrides):
    return make_draft(
        on_topic="частично", question_topic="масштабирование пилота AI", answer_topic="механика A/B-теста", **overrides
    )


def test_partly_on_topic_is_arbitrated_and_different_topics_mean_off_topic():
    llm = FakeLlm(partly(score=3), same_topic="нет")
    result = review_answer(llm, QUESTION, TRANSCRIPT, seconds=10)
    assert result.score == 1 and result.verdict == "redo"
    assert result.spoken_summary == "Ответ нужно переделать: вы ответили не на тот вопрос."
    assert "масштабирование пилота AI" in result.issues[0] and "механика A/B-теста" in result.issues[0]


def test_arbiter_receives_question_and_both_topics():
    llm = FakeLlm(partly(score=3), same_topic="нет")
    review_answer(llm, QUESTION, TRANSCRIPT, seconds=10)
    topic_calls = [c for c in llm.calls if c[1] is TopicCheck]
    assert len(topic_calls) == 1
    user = topic_calls[0][0][1]["content"]
    assert QUESTION.text in user and "масштабирование пилота AI" in user and "механика A/B-теста" in user
    assert topic_calls[0][2]["temperature"] == 0.0


def test_partly_on_topic_with_same_topic_verdict_changes_nothing():
    llm = FakeLlm(partly(score=3), same_topic="да")
    result = review_answer(llm, QUESTION, TRANSCRIPT, seconds=10)
    assert result.score == 3 and result.verdict == "improve"
    assert result.issues == ["нет размера выборки"]


@pytest.mark.parametrize("on_topic", ["да", "нет"])
def test_arbiter_is_only_called_for_uncertain_topic(on_topic):
    llm = FakeLlm(make_draft(on_topic=on_topic), same_topic="да")
    review_answer(llm, QUESTION, TRANSCRIPT, seconds=10)
    assert not [c for c in llm.calls if c[1] is TopicCheck]


def test_arbiter_failure_does_not_break_the_review():
    llm = FakeLlm(partly(score=3), same_topic=LlmError("LM Studio не отвечает."))
    result = review_answer(llm, QUESTION, TRANSCRIPT, seconds=10)
    assert result.score == 3


def test_on_topic_answer_is_not_affected_by_topic_check():
    assert review(make_draft(score=3, on_topic="да")).score == 3


def test_on_topic_must_be_one_of_three_values():
    with pytest.raises(ValidationError):
        make_draft(on_topic="возможно")


# ---------- придирки к ошибкам распознавания ----------

@pytest.mark.parametrize(
    "nitpick",
    [
        "Неправильное использование термина 'Амба-тест' вместо A/B-тест.",
        "Ошибка распознавания слова «пивали».",
        "В транскрипции неверно написан термин SRM.",
    ],
)
def test_complaints_about_recognition_errors_are_dropped(nitpick):
    draft = make_draft(score=3, issues=[nitpick, "нет размера выборки"], how_to_fix=[nitpick, "назвать MDE"])
    result = review(draft)
    assert result.issues == ["нет размера выборки"]
    assert result.how_to_fix == ["назвать MDE"]


# ---------- проверка цитат ----------

def test_verbatim_and_mostly_matching_quotes_are_supported():
    assert quote_supported("смотрю эс эр эм", TRANSCRIPT)
    assert quote_supported("проверяю размер выборки заранее", TRANSCRIPT)  # 3 из 4 слов


def test_invented_or_empty_quotes_are_not_supported():
    assert not quote_supported("рассчитывается по когортам например первый день", TRANSCRIPT)
    assert not quote_supported("нет", TRANSCRIPT)
    assert not quote_supported("", TRANSCRIPT)
    assert not quote_supported("а и", TRANSCRIPT)


def test_fabricated_quote_counts_as_missing_point_and_creates_issue():
    coverage = [*COVERED, PointCheck(point="когорты", quote="считается по когортам пользователей")]
    result = review(make_draft(score=3, coverage=coverage, issues=[], how_to_fix=[]))
    assert result.issues == ["Не раскрыт пункт: когорты"]
    assert result.how_to_fix == ["Раскройте пункт: когорты"]


def test_score_is_capped_when_a_third_of_points_is_missing():
    coverage = [*COVERED[:2], PointCheck(point="когорты", quote="считается по когортам пользователей")]
    result = review(make_draft(score=4, coverage=coverage, issues=[], how_to_fix=[], spoken_summary="Хороший ответ."))
    assert result.score == 3 and result.verdict == "improve"
    assert result.spoken_summary.startswith("Неплохо")
    assert "когорты" in result.spoken_summary


def test_score_is_capped_at_four_when_any_point_is_missing():
    coverage = [*COVERED, PointCheck(point="MDE", quote="нет"), PointCheck(point="а", quote="смотрю пивали"),
                PointCheck(point="б", quote="смотрю пивали")]
    result = review(make_draft(score=5, coverage=coverage, issues=["нет MDE"], how_to_fix=["назвать MDE"]))
    assert result.score == 4 and result.verdict == "ok"


def test_cap_never_raises_the_score():
    result = review(make_draft(score=1, coverage=COVERED, issues=["мимо"], how_to_fix=["переделать"]))
    assert result.score == 1


# ---------- хорошие ответы без придирок ----------

@pytest.mark.parametrize(("score", "start"), [(5, "Отличный ответ"), (4, "Хороший ответ")])
def test_good_answer_with_every_point_supported_has_no_complaints(score, start):
    draft = make_draft(score=score, issues=["придирка"], how_to_fix=["совет"], spoken_summary="Добавьте формулу.")
    result = review(draft)
    assert result.issues == [] and result.how_to_fix == []
    assert result.spoken_summary.startswith(start)
    assert "Добавьте" not in result.spoken_summary


def test_good_answer_with_one_missing_point_keeps_one_issue_only():
    coverage = [*COVERED, PointCheck(point="MDE", quote="нет"), PointCheck(point="а", quote="смотрю пивали"),
                PointCheck(point="б", quote="смотрю пивали")]
    result = review(make_draft(score=4, coverage=coverage, issues=["нет MDE", "мелочь"], how_to_fix=["назвать MDE", "ещё"]))
    assert result.issues == ["нет MDE"] and result.how_to_fix == ["назвать MDE"]


def test_good_answer_without_coverage_info_keeps_one_issue_only():
    result = review(make_draft(score=4, coverage=[], issues=["раз", "два"], how_to_fix=["а", "б"]))
    assert result.issues == ["раз"] and result.how_to_fix == ["а"]


def test_weaker_answers_keep_all_items_and_their_own_summary():
    draft = make_draft(score=2, coverage=WITH_MISSING, issues=["раз", "два", "три"], spoken_summary="Ответ нужно переделать.")
    result = review(draft)
    assert result.issues == ["раз", "два", "три"]
    assert result.spoken_summary == "Ответ нужно переделать."


@pytest.mark.parametrize("quote", ["нет", "Нет.", " НЕТ ", "отсутствует", "—", ""])
def test_missing_quote_markers_count_as_not_covered(quote):
    coverage = [*COVERED, PointCheck(point="MDE", quote=quote), PointCheck(point="а", quote="смотрю пивали"),
                PointCheck(point="б", quote="смотрю пивали"), PointCheck(point="в", quote="смотрю пивали")]
    result = review(make_draft(score=4, coverage=coverage, issues=["нет MDE"], how_to_fix=["назвать MDE"]))
    assert result.issues == ["нет MDE"]


# ---------- ограничения черновика ----------

def test_coverage_is_trimmed_to_five_points():
    many = [PointCheck(point=f"п{i}", quote="цитата") for i in range(10)]
    assert len(make_draft(coverage=many).coverage) == 5


def test_draft_trims_lists_and_drops_blank_items():
    draft = make_draft(issues=["раз", "", "два", "  ", "три", "четыре", "пять"])
    assert draft.issues == ["раз", "два", "три"]
    assert len(draft.issues) == MAX_ITEMS


def test_strengths_are_trimmed_to_two():
    assert make_draft(strengths=["а", "б", "в", "г"]).strengths == ["а", "б"]


def test_spoken_summary_is_cut_at_sentence_boundary():
    sentence = " ".join(["слово"] * 10) + "."
    summary = " ".join([sentence] * 7)  # 70 слов
    cut = make_draft(spoken_summary=summary).spoken_summary
    assert cut.endswith(".")
    assert len(cut.split()) <= MAX_SPOKEN_WORDS
    assert cut == " ".join([sentence] * 4)


def test_spoken_summary_without_sentence_boundary_is_hard_cut():
    cut = make_draft(spoken_summary=" ".join(["слово"] * 100)).spoken_summary
    assert len(cut.split()) <= MAX_SPOKEN_WORDS + 1  # слова и многоточие
    assert cut.endswith("…")


def test_draft_rejects_bad_score():
    with pytest.raises(ValidationError):
        make_draft(score=9)


def test_should_cancel_is_passed_to_every_model_call():
    llm = FakeLlm(make_draft(on_topic="частично"), same_topic="да")
    stop = lambda: False  # noqa: E731
    review_answer(llm, QUESTION, TRANSCRIPT, seconds=10, should_cancel=stop)
    assert len(llm.calls) == 2 and all(call[2]["should_cancel"] is stop for call in llm.calls)


def test_cancel_in_topic_check_is_not_swallowed():
    llm = FakeLlm(make_draft(on_topic="частично"), same_topic=LlmCancelled())
    with pytest.raises(LlmCancelled):
        review_answer(llm, QUESTION, TRANSCRIPT, seconds=10)
