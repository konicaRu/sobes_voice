import pytest

from app.models import Attempt, Question, Session
from app.report import (
    STATUS_LABELS,
    ReportDraft,
    build_history,
    build_report_messages,
    generate_report,
    question_status,
    render_markdown,
    session_rows,
    session_stats,
    weak_questions,
)

QUESTIONS = [
    Question(id="q01", text="Что такое SRM?", category="technical"),
    Question(id="q02", text="Что такое MDE?", category="technical"),
    Question(id="q03", text="Почему к нам?", category="motivation"),
    Question(id="q04", text="Расскажите о проекте", category="experience"),
]


def attempt(q, n=1, verdict=None, score=None, issues=()):
    review = None if verdict is None else {"verdict": verdict, "score": score, "issues": list(issues)}
    return Attempt(q=q, n=n, text="ответ", seconds=10, review=review)


def make_session(attempts=(), skipped=(), questions=QUESTIONS, sid="i__20261028-100000", started="2026-10-28T10:00:00"):
    return Session(
        sid=sid, interview_id="i", started_at=started, questions=list(questions),
        attempts=list(attempts), skipped=list(skipped),
    )


class FakeLlm:
    def __init__(self, draft):
        self.draft = draft
        self.calls = []

    def chat_json(self, messages, model_cls, **kwargs):
        self.calls.append((messages, model_cls, kwargs))
        return self.draft


# ---------- статусы ----------

def test_status_of_untouched_skipped_and_answered_questions():
    session = make_session(attempts=[attempt(1)], skipped=[2])
    assert [question_status(session, i) for i in range(4)] == ["new", "answered", "skipped", "new"]


def test_status_uses_verdict_of_latest_attempt():
    session = make_session(attempts=[attempt(0, 1, "redo", 1), attempt(0, 2, "ok", 5)])
    assert question_status(session, 0) == "ok"


def test_answer_after_skip_wins_over_skip():
    session = make_session(attempts=[attempt(0, 1, "improve", 3)], skipped=[0])
    assert question_status(session, 0) == "improve"


# ---------- строки и итоги ----------

def test_rows_and_stats_use_latest_reviewed_scores():
    session = make_session(
        attempts=[attempt(0, 1, "redo", 1), attempt(0, 2, "ok", 5), attempt(1, 1, "improve", 3), attempt(2, 1)],
        skipped=[3],
    )
    rows = session_rows(session)
    assert [(r["status"], r["score"], r["attempts"]) for r in rows] == [
        ("ok", 5, 2), ("improve", 3, 1), ("answered", None, 1), ("skipped", None, 0),
    ]
    stats = session_stats(rows)
    assert stats == {
        "total": 4, "answered": 3, "skipped": 1, "average": 4.0,
        "counts": {"ok": 1, "improve": 1, "redo": 0, "answered": 1, "skipped": 1, "new": 0},
    }


def test_average_is_none_without_reviewed_answers():
    stats = session_stats(session_rows(make_session(attempts=[attempt(0)])))
    assert stats["average"] is None and stats["answered"] == 1


def test_average_is_rounded_to_one_decimal():
    session = make_session(attempts=[attempt(0, 1, "ok", 4), attempt(1, 1, "improve", 3), attempt(2, 1, "redo", 1)])
    assert session_stats(session_rows(session))["average"] == 2.7


# ---------- слабые вопросы ----------

def test_weak_questions_are_those_whose_latest_verdict_is_improve_or_redo():
    first = make_session(
        attempts=[attempt(0, 1, "redo", 1), attempt(1, 1, "improve", 3), attempt(2, 1, "ok", 5)],
        sid="i__1", started="2026-10-28T10:00:00",
    )
    second = make_session(attempts=[attempt(0, 1, "ok", 5)], sid="i__2", started="2026-10-29T10:00:00")
    weak = weak_questions([second, first], QUESTIONS)  # порядок сессий не важен
    assert [q.text for q in weak] == ["Что такое MDE?"]


def test_weak_questions_follow_current_approved_list_and_use_its_version():
    old = make_session(attempts=[attempt(0, 1, "redo", 1), attempt(1, 1, "redo", 1)])
    approved = [
        Question(id="q01", text="Что такое MDE?", what_to_check=["новый критерий"]),
        Question(id="q02", text="Новый вопрос?"),
    ]  # «Что такое SRM?» пользователь удалил из списка
    weak = weak_questions([old], approved)
    assert [(q.id, q.text, q.what_to_check) for q in weak] == [("q01", "Что такое MDE?", ["новый критерий"])]


def test_weak_questions_ignore_skipped_and_unreviewed():
    session = make_session(attempts=[attempt(0)], skipped=[1])
    assert weak_questions([session], QUESTIONS) == []


# ---------- история ----------

def test_history_lists_sessions_and_builds_matrix_by_question_text():
    first = make_session(attempts=[attempt(0, 1, "redo", 1), attempt(1, 1, "ok", 5)], sid="i__1", started="2026-10-28T10:00:00")
    second = make_session(
        attempts=[attempt(0, 1, "improve", 3)], sid="i__2", started="2026-10-29T10:00:00",
        questions=[QUESTIONS[0], Question(id="q02", text="Новый вопрос?")],
    )
    history = build_history([second, first])
    assert [s["sid"] for s in history["sessions"]] == ["i__1", "i__2"]
    assert history["sessions"][0]["average"] == 3.0 and history["sessions"][1]["average"] == 3.0
    matrix = {row["text"]: row["cells"] for row in history["matrix"]}
    assert matrix["Что такое SRM?"] == [{"status": "redo", "score": 1}, {"status": "improve", "score": 3}]
    assert matrix["Что такое MDE?"] == [{"status": "ok", "score": 5}, None]
    assert matrix["Новый вопрос?"] == [None, {"status": "new", "score": None}]


def test_history_skips_sessions_without_any_activity():
    empty = make_session(sid="i__1")
    active = make_session(attempts=[attempt(0, 1, "ok", 5)], sid="i__2")
    assert [s["sid"] for s in build_history([empty, active])["sessions"]] == ["i__2"]


# ---------- модель отчёта ----------

def test_report_draft_trims_lists_and_blank_items():
    draft = ReportDraft(summary="  Итог.  ", weak_areas=["а", " ", "б", "в", "г"], recommendations=["1", "2", "3", "4", "5"])
    assert draft.summary == "Итог."
    assert draft.weak_areas == ["а", "б", "в"]
    assert len(draft.recommendations) == 4


def test_messages_contain_questions_statuses_scores_and_top_issues():
    session = make_session(attempts=[attempt(0, 1, "improve", 3, ["нет MDE", "мелочь", "ещё мелочь"]), attempt(1, 1, "ok", 5)], skipped=[2])
    messages = build_report_messages(session_rows(session), session)
    system, user = messages[0]["content"], messages[1]["content"]
    assert "не выдумывай" in system.lower()
    assert "Что такое SRM?" in user and "Доработать" in user and "3" in user
    assert "нет MDE" in user and "мелочь" in user and "ещё мелочь" not in user
    assert STATUS_LABELS["skipped"] in user


def test_report_percent_grows_and_is_capped():
    from app.report import EXPECTED_REPORT_CHARS, report_percent

    assert report_percent(0) == 10
    assert report_percent(EXPECTED_REPORT_CHARS // 2) < report_percent(EXPECTED_REPORT_CHARS)
    assert report_percent(10 ** 6) == 95


# ---------- сборка отчёта ----------

def build_data(draft=None):
    session = make_session(
        attempts=[attempt(0, 1, "redo", 1, ["мимо"]), attempt(1, 1, "improve", 3), attempt(2, 1, "ok", 5)], skipped=[3]
    )
    llm = FakeLlm(draft or ReportDraft(summary="Неплохо.", weak_areas=["нет цифр"], recommendations=["добавляйте цифры"]))
    return generate_report(llm, session, title="Т2"), llm


def test_report_combines_code_facts_and_model_text():
    data, llm = build_data()
    assert data["stats"]["average"] == 3.0 and data["stats"]["answered"] == 3
    assert (data["summary"], data["weak_areas"], data["recommendations"]) == ("Неплохо.", ["нет цифр"], ["добавляйте цифры"])
    assert llm.calls[0][1] is ReportDraft
    assert llm.calls[0][2]["temperature"] <= 0.3


def grounded_session():
    return make_session(attempts=[attempt(0, 1, "improve", 3, ["нет MDE"])])


def test_invented_latin_terms_are_dropped_from_model_text():
    draft = ReportDraft(
        summary="Хорошо знает SRM. Путает BETWEEN и HAVING. Нужны цифры.",
        weak_areas=["Нет понимания BETWEEN", "Пропуск MDE"],
        recommendations=["Повторите BETWEEN", "Называйте цифру результата"],
    )
    data = generate_report(FakeLlm(draft), grounded_session(), title="Т")
    assert data["summary"] == "Хорошо знает SRM. Нужны цифры."
    assert data["weak_areas"] == ["Пропуск MDE"]
    assert data["recommendations"] == ["Называйте цифру результата"]


def test_known_terms_and_star_are_allowed():
    draft = ReportDraft(summary="Итог.", weak_areas=[], recommendations=["Используйте STAR", "Повторите ClickHouse"])
    data = generate_report(FakeLlm(draft), grounded_session(), title="Т", known_terms=["ClickHouse"])
    assert data["recommendations"] == ["Используйте STAR", "Повторите ClickHouse"]


def test_summary_falls_back_when_every_sentence_is_ungrounded():
    draft = ReportDraft(summary="Путает BETWEEN.", weak_areas=[], recommendations=[])
    assert generate_report(FakeLlm(draft), grounded_session(), title="Т")["summary"] == "Подробности смотрите в таблице результатов."


def test_repeat_list_has_weakest_first_and_skipped_last():
    data, _ = build_data()
    assert data["repeat"] == ["Что такое SRM?", "Что такое MDE?", "Расскажите о проекте"]


def test_progress_callback_is_passed_to_llm():
    session = make_session(attempts=[attempt(0, 1, "ok", 5)])
    llm = FakeLlm(ReportDraft(summary="ок", weak_areas=[], recommendations=[]))
    callback = lambda chars: None  # noqa: E731
    generate_report(llm, session, title="Т", on_progress=callback)
    assert llm.calls[0][2]["on_progress"] is callback


def test_generate_report_without_answers_is_rejected():
    with pytest.raises(ValueError, match="ответ"):
        generate_report(FakeLlm(ReportDraft(summary="", weak_areas=[], recommendations=[])), make_session(skipped=[0]), title="Т")


# ---------- markdown ----------

def test_markdown_has_all_sections_and_table():
    data, _ = build_data()
    md = render_markdown("Т2 — аналитик", data, finished_at="2026-10-28T11:00:00")
    assert md.startswith("# Отчёт: Т2 — аналитик")
    for section in ("## Итог", "## Результаты по вопросам", "## Слабые зоны", "## Повторить", "## Рекомендации"):
        assert section in md
    assert "| 1 | Что такое SRM? | Переделать | 1 |" in md
    assert "Средняя оценка: 3.0 из 5" in md and "Неплохо." in md


def test_markdown_without_reviewed_answers_has_no_average_line():
    session = make_session(attempts=[attempt(0)])
    data = generate_report(FakeLlm(ReportDraft(summary="ок", weak_areas=[], recommendations=[])), session, title="Т")
    assert "Средняя оценка" not in render_markdown("Т", data, finished_at="2026-10-28T11:00:00")
