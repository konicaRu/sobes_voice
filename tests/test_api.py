import asyncio
import time

import pytest
from fastapi.testclient import TestClient

from app.config import Config, Glossary, SttConfig
from app.generation import GeneratedQuestion, QuestionBatch
from app.llm import LlmError
from app.main import create_app
from app.profile import ProfileDraft
from app.report import ReportDraft
from app.review import PointCheck, ReviewDraft
from app.storage import Storage
from app.stt import AudioError, Transcription

RESULT = ReviewDraft(
    question_topic="цифры результата", answer_topic="цифры результата", on_topic="да",
    coverage=[PointCheck(point="цифры", quote="нет")], score=3, strengths=["есть пример"], issues=["нет цифр"],
    how_to_fix=["назвать цифры"], improved_outline="1. контекст 2. цифры", spoken_summary="Добавьте цифры.",
)
QUESTIONS = [
    {"text": "Первый вопрос?", "what_to_check": ["критерий"]},
    {"text": "Второй вопрос?"},
    {"text": "Третий вопрос?"},
]


BATCH = QuestionBatch(questions=[
    GeneratedQuestion(text="Как вы проверяете SRM?", category="technical", what_to_check=["доли групп", "критерий"]),
    GeneratedQuestion(text="Почему вы хотите к нам?", category="motivation", what_to_check=["мотивы", "знание компании"]),
])


REPORT_DRAFT = ReportDraft(summary="Неплохо, но нужны цифры.", weak_areas=["нет цифр"], recommendations=["называйте цифру результата"])


class FakeStt:
    def __init__(self, texts=("мой ответ",)):
        self.texts = list(texts)

    def transcribe(self, wav):
        return Transcription(text=self.texts.pop(0), seconds=3.0, elapsed=0.1)


class FakeTts:
    def __init__(self):
        self.texts = []

    def synthesize(self, text):
        self.texts.append(text)
        return b"RIFFfake-wav"


class FakeLlm:
    def __init__(self, error=None):
        self.error = error
        self.calls = []

    def get_model(self):
        if self.error:
            raise self.error
        return "qwen-test"

    def chat_json(self, messages, model_cls, **kwargs):
        if self.error:
            raise self.error
        self.calls.append(messages)
        if model_cls is ProfileDraft:
            return ProfileDraft(profile="Профиль: вёл A/B-тесты, retention +5%.")
        if model_cls is QuestionBatch:
            return BATCH
        if model_cls is ReportDraft:
            return REPORT_DRAFT
        return RESULT


@pytest.fixture
def make_client(tmp_path):
    def factory(stt=None, tts=None, llm=None, config=None, glossary=None):
        app = create_app(
            config=config or Config(), glossary=glossary or Glossary(), storage=Storage(tmp_path),
            stt=stt or FakeStt(), tts=tts or FakeTts(), llm=llm or FakeLlm(),
        )
        return TestClient(app)

    return factory


def new_interview(client, title="Т2 — аналитик"):
    return client.post("/api/interviews", json={"title": title}).json()


def approve(client, interview_id, questions=QUESTIONS):
    return client.put(f"/api/interviews/{interview_id}/questions", json={"questions": questions})


def upload(client, interview_id, slot, filename, content):
    return client.post(
        f"/api/interviews/{interview_id}/documents", params={"slot": slot, "filename": filename}, content=content
    )


def new_session(client):
    interview = new_interview(client)
    approve(client, interview["id"])
    return client.post(f"/api/interviews/{interview['id']}/sessions").json()


# ---------- здоровье и озвучка ----------

def test_health_reports_model(make_client):
    body = make_client().get("/api/health").json()
    assert body["lmstudio"] == {"ok": True, "model": "qwen-test", "error": None}
    assert body["stt"]["ok"] and body["tts"]["ok"]


def test_health_reports_llm_error_without_failing(make_client):
    client = make_client(llm=FakeLlm(error=LlmError("LM Studio не отвечает.")))
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json()["lmstudio"] == {"ok": False, "model": None, "error": "LM Studio не отвечает."}


def test_tts_returns_wav(make_client):
    tts = FakeTts()
    response = make_client(tts=tts).post("/api/tts", json={"text": "Привет"})
    assert response.status_code == 200
    assert response.headers["content-type"] == "audio/wav"
    assert response.content == b"RIFFfake-wav"
    assert tts.texts == ["Привет"]


def test_tts_failure_is_500_with_russian_message(make_client):
    class BrokenTts:
        def synthesize(self, text):
            raise ValueError("Failed to parse SSML")

    response = make_client(tts=BrokenTts()).post("/api/tts", json={"text": "Привет"})
    assert response.status_code == 500
    assert response.json()["detail"] == "Не удалось озвучить текст."


def test_tts_rejects_overlong_text(make_client):
    assert make_client().post("/api/tts", json={"text": "а" * 2001}).status_code == 422


def test_tts_rejects_empty_text(make_client):
    assert make_client().post("/api/tts", json={"text": ""}).status_code == 422


def test_index_page_is_served(make_client):
    response = make_client().get("/")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]


# ---------- собеседования и документы ----------

def test_create_and_list_interviews(make_client):
    client = make_client()
    created = new_interview(client)
    assert created["title"] == "Т2 — аналитик"
    listed = client.get("/api/interviews").json()
    assert [i["id"] for i in listed] == [created["id"]]
    assert listed[0]["questions"] is None
    assert listed[0]["documents"] == {"questions": 0, "vacancy": 0, "resume": 0, "cases": 0}


def test_empty_interview_title_is_422(make_client):
    assert make_client().post("/api/interviews", json={"title": "  "}).status_code == 422


def test_unknown_or_malicious_interview_is_404(make_client):
    client = make_client()
    assert client.get("/api/interviews/nope").status_code == 404
    assert client.get("/api/interviews/..%2F..%2Fetc").status_code == 404


def test_upload_document_and_show_in_interview(make_client):
    client = make_client()
    interview = new_interview(client)
    response = upload(client, interview["id"], "vacancy", "вакансия.md", "Нужен аналитик".encode())
    assert response.status_code == 200
    assert response.json() == {"filename": "вакансия.md"}
    details = client.get(f"/api/interviews/{interview['id']}").json()
    assert details["documents"]["vacancy"] == ["вакансия.md"]
    assert details["questions"] is None


def test_upload_rejects_unsupported_file_unknown_slot_and_unreadable_file(make_client):
    client = make_client()
    interview_id = new_interview(client)["id"]
    assert upload(client, interview_id, "vacancy", "script.py", b"x").status_code == 422
    assert upload(client, interview_id, "secrets", "a.md", b"x").status_code == 422
    bad = upload(client, interview_id, "resume", "cv.docx", b"not a docx")
    assert bad.status_code == 422 and "прочитать" in bad.json()["detail"]
    assert client.get(f"/api/interviews/{interview_id}").json()["documents"]["resume"] == []


def test_upload_rejects_oversized_body_with_413(make_client):
    client = make_client()
    interview_id = new_interview(client)["id"]
    response = upload(client, interview_id, "cases", "big.txt", b"a" * (5 * 1024 * 1024 + 1))
    assert response.status_code == 413


def test_delete_document(make_client):
    client = make_client()
    interview_id = new_interview(client)["id"]
    upload(client, interview_id, "cases", "кейс.md", b"x")
    assert client.delete(f"/api/interviews/{interview_id}/documents/cases/кейс.md").status_code == 200
    assert client.get(f"/api/interviews/{interview_id}").json()["documents"]["cases"] == []
    assert client.delete(f"/api/interviews/{interview_id}/documents/cases/кейс.md").status_code == 404


# ---------- вопросы ----------

def test_parse_questions_from_uploaded_file(make_client):
    client = make_client()
    interview_id = new_interview(client)["id"]
    upload(client, interview_id, "questions", "q.md", "1. Что такое SRM?\n   - Критерии: пропорции".encode())
    response = client.post(f"/api/interviews/{interview_id}/questions/parse")
    assert response.status_code == 200
    body = response.json()
    assert body["files"] == ["q.md"]
    assert body["questions"][0]["text"] == "Что такое SRM?"
    assert body["questions"][0]["what_to_check"] == ["пропорции"]
    assert client.get(f"/api/interviews/{interview_id}/questions").json() == {"questions": None}  # ещё не утверждены


def test_parse_merges_several_files_without_duplicates(make_client):
    client = make_client()
    interview_id = new_interview(client)["id"]
    upload(client, interview_id, "questions", "a.md", "1. Что такое SRM?\n2. Что такое MDE?".encode())
    upload(client, interview_id, "questions", "b.txt", "Что такое srm?\nЧто такое CUPED?".encode())
    questions = client.post(f"/api/interviews/{interview_id}/questions/parse").json()["questions"]
    assert [q["text"] for q in questions] == ["Что такое SRM?", "Что такое MDE?", "Что такое CUPED?"]
    assert [q["id"] for q in questions] == ["q01", "q02", "q03"]


def test_parse_without_files_is_409_and_without_questions_is_422(make_client):
    client = make_client()
    interview_id = new_interview(client)["id"]
    assert client.post(f"/api/interviews/{interview_id}/questions/parse").status_code == 409
    upload(client, interview_id, "questions", "empty.md", "Просто текст.".encode())
    response = client.post(f"/api/interviews/{interview_id}/questions/parse")
    assert response.status_code == 422 and "вопрос" in response.json()["detail"]


def test_approve_questions_renumbers_ids_and_saves(make_client):
    client = make_client()
    interview_id = new_interview(client)["id"]
    response = approve(client, interview_id, [{"id": "zzz", "text": "  Первый?  ", "what_to_check": [" а ", " "]}, {"text": "Второй?"}])
    assert response.status_code == 200
    saved = response.json()["questions"]
    assert [(q["id"], q["text"]) for q in saved] == [("q01", "Первый?"), ("q02", "Второй?")]
    assert saved[0]["what_to_check"] == ["а"]
    assert client.get(f"/api/interviews/{interview_id}/questions").json()["questions"] == saved
    assert client.get("/api/interviews").json()[0]["questions"] == 2


def test_reference_answer_is_extracted_trimmed_and_saved(make_client):
    client = make_client()
    interview_id = new_interview(client)["id"]
    notes = "### 1. Что такое SRM?\n**Коротко:**\n> Проверка долей.\n### 2. Что такое MDE?\n### 3. Что такое CUPED?"
    upload(client, interview_id, "questions", "q.md", notes.encode())
    parsed = client.post(f"/api/interviews/{interview_id}/questions/parse").json()["questions"]
    assert parsed[0]["reference_answer"] == "Проверка долей."
    parsed[0]["reference_answer"] = "  Правка пользователя.  "
    saved = client.put(f"/api/interviews/{interview_id}/questions", json={"questions": parsed}).json()["questions"]
    assert saved[0]["reference_answer"] == "Правка пользователя."


def test_approve_rejects_empty_blank_and_too_many_questions(make_client):
    client = make_client()
    interview_id = new_interview(client)["id"]
    assert approve(client, interview_id, []).status_code == 422
    assert approve(client, interview_id, [{"text": "   "}]).status_code == 422
    assert approve(client, interview_id, [{"text": "в?"}] * 201).status_code == 422


# ---------- сессии ----------

def test_session_requires_approved_questions(make_client):
    client = make_client()
    interview_id = new_interview(client)["id"]
    response = client.post(f"/api/interviews/{interview_id}/sessions")
    assert response.status_code == 409 and "Утвердите" in response.json()["detail"]


def test_create_session_returns_questions_and_settings(make_client):
    session = new_session(make_client())
    assert session["sid"]
    assert [q["text"] for q in session["questions"]] == ["Первый вопрос?", "Второй вопрос?", "Третий вопрос?"]
    assert session["settings"]["max_answer_seconds"] == 300


def test_transcribe_saves_attempt_and_audio_on_disk(make_client, tmp_path):
    client = make_client()
    sid = new_session(client)["sid"]
    response = client.post(f"/api/sessions/{sid}/transcribe?q=0", content=b"wav-bytes")
    assert response.status_code == 200
    assert response.json()["text"] == "мой ответ"
    attempt = client.get(f"/api/sessions/{sid}").json()["attempts"][0]
    assert (attempt["q"], attempt["n"], attempt["text"]) == (0, 1, "мой ответ")
    assert attempt["audio"] == "audio/q01_a1.wav"
    audio_files = list(tmp_path.rglob("q01_a1.wav"))
    assert len(audio_files) == 1 and audio_files[0].read_bytes() == b"wav-bytes"


def test_session_survives_server_restart(make_client, tmp_path):
    first = make_client()
    sid = new_session(first)["sid"]
    first.post(f"/api/sessions/{sid}/transcribe?q=1", content=b"x")

    second = make_client()  # новый экземпляр приложения на тех же данных
    session = second.get(f"/api/sessions/{sid}").json()
    assert session["attempts"][0]["text"] == "мой ответ"
    assert len(session["questions"]) == 3
    assert len(second.get("/api/interviews").json()) == 1


def test_transcribe_unknown_session_is_404(make_client):
    assert make_client().post("/api/sessions/nope/transcribe?q=0", content=b"x").status_code == 404
    assert make_client().get("/api/sessions/..%2Fx").status_code == 404


def test_transcribe_unknown_question_is_404(make_client):
    client = make_client()
    sid = new_session(client)["sid"]
    assert client.post(f"/api/sessions/{sid}/transcribe?q=9", content=b"x").status_code == 404


def test_transcribe_empty_speech_is_422_with_russian_hint_and_saves_nothing(make_client):
    client = make_client(stt=FakeStt(texts=[""]))
    sid = new_session(client)["sid"]
    response = client.post(f"/api/sessions/{sid}/transcribe?q=0", content=b"x")
    assert response.status_code == 422
    assert "микрофон" in response.json()["detail"]
    assert client.get(f"/api/sessions/{sid}").json()["attempts"] == []


def test_retry_creates_second_attempt_and_review_uses_latest(make_client):
    llm = FakeLlm()
    client = make_client(stt=FakeStt(texts=["первая попытка", "вторая попытка"]), llm=llm)
    sid = new_session(client)["sid"]
    client.post(f"/api/sessions/{sid}/transcribe?q=0", content=b"x")
    client.post(f"/api/sessions/{sid}/transcribe?q=0", content=b"x")
    client.post(f"/api/sessions/{sid}/review", json={"q": 0})
    assert "вторая попытка" in llm.calls[0][1]["content"]
    assert "первая попытка" not in llm.calls[0][1]["content"]
    attempts = client.get(f"/api/sessions/{sid}").json()["attempts"]
    assert [a["n"] for a in attempts] == [1, 2]
    assert attempts[0]["review"] is None and attempts[1]["review"]["verdict"] == "improve"


def test_review_uses_question_criteria_and_transcript(make_client):
    llm = FakeLlm()
    client = make_client(llm=llm)
    sid = new_session(client)["sid"]
    client.post(f"/api/sessions/{sid}/transcribe?q=0", content=b"x")
    response = client.post(f"/api/sessions/{sid}/review", json={"q": 0})
    assert response.status_code == 200
    assert response.json()["verdict"] == "improve"
    user_message = llm.calls[0][1]["content"]
    assert "Первый вопрос?" in user_message and "критерий" in user_message and "мой ответ" in user_message


def wait_for_profile(client, interview_id, timeout=5.0):
    """Сборка профиля идёт в фоне: ждём, пока статус перестанет быть «running»."""
    deadline = time.time() + timeout
    while True:
        status = client.get(f"/api/interviews/{interview_id}/profile/status").json()
        if status["state"] != "running" or time.time() > deadline:
            return status
        time.sleep(0.02)


def test_profile_status_is_idle_before_any_build(make_client):
    client = make_client()
    interview_id = new_interview(client)["id"]
    assert client.get(f"/api/interviews/{interview_id}/profile/status").json()["state"] == "idle"


def test_build_profile_needs_resume_or_cases_runs_in_background_and_is_cached(make_client):
    llm = FakeLlm()
    client = make_client(llm=llm)
    interview_id = new_interview(client)["id"]
    assert client.post(f"/api/interviews/{interview_id}/profile").status_code == 409
    upload(client, interview_id, "resume", "cv.md", "Аналитик, A/B-тесты, SQL".encode())
    upload(client, interview_id, "cases", "кейс.md", "Retention вырос на 5%".encode())
    started = client.post(f"/api/interviews/{interview_id}/profile")
    assert started.status_code == 200
    assert started.json()["state"] in ("running", "done")
    status = wait_for_profile(client, interview_id)
    assert status["state"] == "done" and status["percent"] == 100
    assert status["profile"] == "Профиль: вёл A/B-тесты, retention +5%."
    user = llm.calls[0][1]["content"]
    assert "A/B-тесты, SQL" in user and "Retention вырос на 5%" in user
    assert client.get(f"/api/interviews/{interview_id}").json()["profile"] == status["profile"]


def test_profile_build_error_is_reported_in_status_and_can_be_retried(make_client):
    llm = FakeLlm(error=LlmError("LM Studio не отвечает."))
    client = make_client(llm=llm)
    interview_id = new_interview(client)["id"]
    upload(client, interview_id, "resume", "cv.md", "Аналитик".encode())
    client.post(f"/api/interviews/{interview_id}/profile")
    status = wait_for_profile(client, interview_id)
    assert status["state"] == "error" and status["message"] == "LM Studio не отвечает."
    llm.error = None
    client.post(f"/api/interviews/{interview_id}/profile")
    assert wait_for_profile(client, interview_id)["state"] == "done"


COUNTS = {"technical": 1, "motivation": 1}


def start_generation(client, interview_id, counts=None, existing=()):
    return client.post(
        f"/api/interviews/{interview_id}/questions/generate", json={"counts": counts or COUNTS, "existing": list(existing)}
    )


def wait_for_generation(client, interview_id, timeout=5.0):
    deadline = time.time() + timeout
    while True:
        status = client.get(f"/api/interviews/{interview_id}/questions/generate/status").json()
        if status["state"] != "running" or time.time() > deadline:
            return status
        time.sleep(0.02)


def test_generation_status_is_idle_before_any_run(make_client):
    client = make_client()
    interview_id = new_interview(client)["id"]
    assert client.get(f"/api/interviews/{interview_id}/questions/generate/status").json()["state"] == "idle"


def test_generation_needs_vacancy_or_profile(make_client):
    client = make_client()
    interview_id = new_interview(client)["id"]
    response = start_generation(client, interview_id)
    assert response.status_code == 409 and "вакансию" in response.json()["detail"]


@pytest.mark.parametrize("counts", [{}, {"technical": 0}, {"unknown": 2}, {"technical": 11}])
def test_generation_rejects_bad_counts(make_client, counts):
    client = make_client()
    interview_id = new_interview(client)["id"]
    client.put(f"/api/interviews/{interview_id}/profile", json={"profile": "Вёл A/B-тесты"})
    assert start_generation(client, interview_id, counts=counts or {"x": 0}).status_code == 422


def test_generation_runs_in_background_and_does_not_touch_approved_questions(make_client):
    llm = FakeLlm()
    client = make_client(llm=llm)
    interview_id = new_interview(client)["id"]
    upload(client, interview_id, "vacancy", "вакансия.md", "Нужен продуктовый аналитик".encode())
    client.put(f"/api/interviews/{interview_id}/profile", json={"profile": "Вёл A/B-тесты"})
    started = start_generation(client, interview_id, existing=["Что такое MDE?"])
    assert started.status_code == 200 and started.json()["state"] in ("running", "done")

    status = wait_for_generation(client, interview_id)
    assert status["state"] == "done" and status["percent"] == 100
    assert [(q["id"], q["category"], q["text"]) for q in status["questions"]] == [
        ("q01", "technical", "Как вы проверяете SRM?"), ("q02", "motivation", "Почему вы хотите к нам?"),
    ]
    assert status["questions"][0]["what_to_check"] == ["доли групп", "критерий"]
    user = llm.calls[0][1]["content"]
    assert "Нужен продуктовый аналитик" in user and "Вёл A/B-тесты" in user and "Что такое MDE?" in user
    assert client.get(f"/api/interviews/{interview_id}/questions").json() == {"questions": None}


def test_generation_works_with_profile_only(make_client):
    client = make_client()
    interview_id = new_interview(client)["id"]
    client.put(f"/api/interviews/{interview_id}/profile", json={"profile": "Вёл A/B-тесты"})
    assert start_generation(client, interview_id).status_code == 200
    assert wait_for_generation(client, interview_id)["state"] == "done"


def test_generation_error_is_reported_in_status(make_client):
    client = make_client(llm=FakeLlm(error=LlmError("LM Studio не отвечает.")))
    interview_id = new_interview(client)["id"]
    client.put(f"/api/interviews/{interview_id}/profile", json={"profile": "Вёл A/B-тесты"})
    start_generation(client, interview_id)
    status = wait_for_generation(client, interview_id)
    assert status["state"] == "error" and status["message"] == "LM Studio не отвечает."


def test_profile_can_be_edited_by_user(make_client):
    client = make_client()
    interview_id = new_interview(client)["id"]
    response = client.put(f"/api/interviews/{interview_id}/profile", json={"profile": "  Свой профиль  "})
    assert response.status_code == 200
    assert client.get(f"/api/interviews/{interview_id}").json()["profile"] == "Свой профиль"
    assert client.put(f"/api/interviews/{interview_id}/profile", json={"profile": "я" * 6001}).status_code == 422


def test_review_gets_profile_vacancy_reference_and_glossary_terms(make_client):
    llm = FakeLlm()
    glossary = Glossary(tts={"SQL": "эс-кью-эль"}, stt_fix={"клик хаус": "ClickHouse"})
    client = make_client(llm=llm, glossary=glossary)
    interview_id = new_interview(client)["id"]
    upload(client, interview_id, "vacancy", "вакансия.md", "Нужен продуктовый аналитик".encode())
    client.put(f"/api/interviews/{interview_id}/profile", json={"profile": "Вёл A/B-тесты"})
    approve(client, interview_id, [{"text": "Что такое SRM?", "reference_answer": "Проверка долей групп."}])
    sid = client.post(f"/api/interviews/{interview_id}/sessions").json()["sid"]
    client.post(f"/api/sessions/{sid}/transcribe?q=0", content=b"x")
    client.post(f"/api/sessions/{sid}/review", json={"q": 0})
    user = llm.calls[0][1]["content"]
    assert "Вёл A/B-тесты" in user
    assert "Нужен продуктовый аналитик" in user
    assert "Проверка долей групп." in user
    assert "ClickHouse" in user and "SQL" in user


def test_review_without_answer_is_409(make_client):
    client = make_client()
    sid = new_session(client)["sid"]
    response = client.post(f"/api/sessions/{sid}/review", json={"q": 0})
    assert response.status_code == 409
    assert "запишите" in response.json()["detail"].lower()


def test_review_llm_error_is_503_with_message(make_client):
    llm = FakeLlm()
    client = make_client(llm=llm)
    sid = new_session(client)["sid"]
    client.post(f"/api/sessions/{sid}/transcribe?q=0", content=b"x")
    llm.error = LlmError("LM Studio не отвечает.")
    response = client.post(f"/api/sessions/{sid}/review", json={"q": 0})
    assert response.status_code == 503
    assert response.json()["detail"] == "LM Studio не отвечает."


# ---------- пропуск, отчёт, история, слабые вопросы ----------

def answer(client, sid, q=0):
    client.post(f"/api/sessions/{sid}/transcribe?q={q}", content=b"x")
    return client.post(f"/api/sessions/{sid}/review", json={"q": q})


def wait_for_report(client, sid, timeout=5.0):
    deadline = time.time() + timeout
    while True:
        status = client.get(f"/api/sessions/{sid}/finish/status").json()
        if status["state"] != "running" or time.time() > deadline:
            return status
        time.sleep(0.02)


def test_skip_marks_question_once_and_validates_input(make_client):
    client = make_client()
    sid = new_session(client)["sid"]
    assert client.post(f"/api/sessions/{sid}/skip", json={"q": 1}).json() == {"skipped": [1]}
    assert client.post(f"/api/sessions/{sid}/skip", json={"q": 1}).json() == {"skipped": [1]}
    assert client.get(f"/api/sessions/{sid}").json()["skipped"] == [1]
    assert client.post(f"/api/sessions/{sid}/skip", json={"q": 9}).status_code == 404
    assert client.post("/api/sessions/nope/skip", json={"q": 0}).status_code == 404


def test_report_is_empty_before_finish(make_client):
    client = make_client()
    sid = new_session(client)["sid"]
    assert client.get(f"/api/sessions/{sid}/report").json() == {"report": None, "finished_at": None}
    assert client.get(f"/api/sessions/{sid}/finish/status").json()["state"] == "idle"


def test_finish_needs_at_least_one_answer(make_client):
    client = make_client()
    sid = new_session(client)["sid"]
    client.post(f"/api/sessions/{sid}/skip", json={"q": 0})
    response = client.post(f"/api/sessions/{sid}/finish")
    assert response.status_code == 409 and "ответ" in response.json()["detail"]


def test_finish_builds_report_in_background_and_saves_it(make_client, tmp_path):
    llm = FakeLlm()
    client = make_client(llm=llm)
    sid = new_session(client)["sid"]
    answer(client, sid, 0)
    client.post(f"/api/sessions/{sid}/skip", json={"q": 1})
    started = client.post(f"/api/sessions/{sid}/finish")
    assert started.status_code == 200 and started.json()["state"] in ("running", "done")

    status = wait_for_report(client, sid)
    report = status["report"]
    assert status["state"] == "done" and status["percent"] == 100
    assert report["title"] == "Т2 — аналитик"
    assert report["stats"]["answered"] == 1 and report["stats"]["skipped"] == 1 and report["stats"]["average"] == 3.0
    assert [r["status"] for r in report["rows"]] == ["improve", "skipped", "new"]
    assert report["summary"] == "Неплохо, но нужны цифры." and report["repeat"] == ["Первый вопрос?", "Второй вопрос?"]
    assert "Первый вопрос?" in llm.calls[-1][1]["content"]

    assert client.get(f"/api/sessions/{sid}/report").json()["report"] == report
    assert client.get(f"/api/sessions/{sid}").json()["finished_at"] == status["finished_at"]
    markdown = next(tmp_path.rglob("report.md")).read_text(encoding="utf-8")
    assert markdown.startswith("# Отчёт: Т2 — аналитик") and "Неплохо, но нужны цифры." in markdown


def test_finish_error_is_reported_in_status(make_client):
    llm = FakeLlm()
    client = make_client(llm=llm)
    sid = new_session(client)["sid"]
    answer(client, sid, 0)
    llm.error = LlmError("LM Studio не отвечает.")
    client.post(f"/api/sessions/{sid}/finish")
    status = wait_for_report(client, sid)
    assert status["state"] == "error" and status["message"] == "LM Studio не отвечает."


def test_history_lists_sessions_and_question_dynamics(make_client):
    client = make_client(stt=FakeStt(texts=["первый ответ", "второй ответ"]))
    interview = new_interview(client)
    approve(client, interview["id"])
    first = client.post(f"/api/interviews/{interview['id']}/sessions").json()["sid"]
    answer(client, first, 0)
    second = client.post(f"/api/interviews/{interview['id']}/sessions").json()["sid"]
    answer(client, second, 0)
    client.post(f"/api/interviews/{interview['id']}/sessions")  # без активности: в истории не показывается
    history = client.get(f"/api/interviews/{interview['id']}/history").json()
    assert [s["sid"] for s in history["sessions"]] == [first, second]
    assert history["sessions"][0]["average"] == 3.0 and history["sessions"][0]["answered"] == 1
    row = history["matrix"][0]
    assert row["text"] == "Первый вопрос?" and [c["score"] for c in row["cells"]] == [3, 3]


def test_history_of_unknown_interview_is_404(make_client):
    assert make_client().get("/api/interviews/nope/history").status_code == 404


def test_weak_mode_creates_session_from_questions_with_improve_or_redo(make_client):
    client = make_client()
    interview = new_interview(client)
    approve(client, interview["id"])
    first = client.post(f"/api/interviews/{interview['id']}/sessions").json()["sid"]
    answer(client, first, 0)  # FakeLlm даёт оценку 3: «доработать»
    weak = client.post(f"/api/interviews/{interview['id']}/sessions", json={"mode": "weak"})
    assert weak.status_code == 200
    assert [q["text"] for q in weak.json()["questions"]] == ["Первый вопрос?"]


def test_shuffle_changes_question_order_and_renumbers_ids(make_client, monkeypatch):
    monkeypatch.setattr("app.main.random.shuffle", lambda items: items.reverse())
    client = make_client()
    interview = new_interview(client)
    approve(client, interview["id"])
    plain = client.post(f"/api/interviews/{interview['id']}/sessions").json()["questions"]
    shuffled = client.post(f"/api/interviews/{interview['id']}/sessions", json={"shuffle": True}).json()["questions"]
    assert [q["text"] for q in shuffled] == [q["text"] for q in reversed(plain)]
    assert [q["id"] for q in shuffled] == [q["id"] for q in plain]


def test_weak_mode_without_weak_questions_is_409(make_client):
    client = make_client()
    interview = new_interview(client)
    approve(client, interview["id"])
    response = client.post(f"/api/interviews/{interview['id']}/sessions", json={"mode": "weak"})
    assert response.status_code == 409 and "Слабых" in response.json()["detail"]


def test_transcribe_rejects_oversized_body_with_413(make_client):
    client = make_client(config=Config(stt=SttConfig(max_answer_seconds=1)))
    sid = new_session(client)["sid"]
    response = client.post(f"/api/sessions/{sid}/transcribe?q=0", content=b"\0" * 6_500_000)
    assert response.status_code == 413
    assert "длинн" in response.json()["detail"]


def test_transcribe_stops_reading_streamed_body_at_cap(make_client):
    stt = FakeStt()
    client = make_client(stt=stt, config=Config(stt=SttConfig(max_answer_seconds=1)))
    sid = new_session(client)["sid"]
    received, sent = [], []

    async def receive():  # поток кусков по 1 МБ без заголовка Content-Length
        received.append(1)
        return {"type": "http.request", "body": b"\0" * 1_000_000, "more_body": len(received) < 50}

    async def send(message):
        sent.append(message)

    scope = {
        "type": "http", "method": "POST", "path": f"/api/sessions/{sid}/transcribe", "query_string": b"q=0",
        "headers": [(b"content-type", b"audio/wav")], "http_version": "1.1", "scheme": "http",
        "server": ("test", 80), "client": ("test", 1), "root_path": "",
    }
    asyncio.run(client.app(scope, receive, send))  # type: ignore[attr-defined]

    assert sent[0]["status"] == 413
    assert stt.texts == ["мой ответ"]  # распознавание не вызывалось
    assert len(received) < 50  # чтение прервано по достижении лимита, а не после буферизации всего тела


def test_transcribe_audio_error_is_422_with_message(make_client):
    class BadAudioStt:
        def transcribe(self, wav):
            raise AudioError("Неподдерживаемая частота записи.")

    client = make_client(stt=BadAudioStt())
    sid = new_session(client)["sid"]
    response = client.post(f"/api/sessions/{sid}/transcribe?q=0", content=b"x")
    assert response.status_code == 422
    assert response.json()["detail"] == "Неподдерживаемая частота записи."
