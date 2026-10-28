import pytest
from fastapi.testclient import TestClient

from app.config import Config, Glossary
from app.llm import LlmError
from app.main import create_app
from app.review import ReviewResult
from app.stt import Transcription

RESULT = ReviewResult(
    verdict="improve", score=3, strengths=["есть пример"], issues=["нет цифр"],
    how_to_fix=["назвать цифры"], improved_outline="1. контекст 2. цифры", spoken_summary="Добавьте цифры.",
)


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

    def chat_json(self, messages, model_cls):
        if self.error:
            raise self.error
        self.calls.append(messages)
        return RESULT


def make_client(stt=None, tts=None, llm=None):
    app = create_app(
        config=Config(), glossary=Glossary(),
        stt=stt or FakeStt(), tts=tts or FakeTts(), llm=llm or FakeLlm(),
    )
    return TestClient(app)


def new_session(client):
    return client.post("/api/sessions").json()


def test_health_reports_model():
    body = make_client().get("/api/health").json()
    assert body["lmstudio"] == {"ok": True, "model": "qwen-test", "error": None}
    assert body["stt"]["ok"] and body["tts"]["ok"]


def test_health_reports_llm_error_without_failing():
    client = make_client(llm=FakeLlm(error=LlmError("LM Studio не отвечает.")))
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json()["lmstudio"] == {"ok": False, "model": None, "error": "LM Studio не отвечает."}


def test_create_session_returns_three_questions():
    session = new_session(make_client())
    assert session["sid"]
    assert len(session["questions"]) == 3
    assert all(q["id"] and q["text"] for q in session["questions"])


def test_transcribe_returns_text():
    client = make_client()
    sid = new_session(client)["sid"]
    response = client.post(f"/api/sessions/{sid}/transcribe?q=0", content=b"wav-bytes")
    assert response.status_code == 200
    assert response.json()["text"] == "мой ответ"


def test_transcribe_unknown_session_is_404():
    assert make_client().post("/api/sessions/nope/transcribe?q=0", content=b"x").status_code == 404


def test_transcribe_empty_speech_is_422_with_russian_hint():
    client = make_client(stt=FakeStt(texts=[""]))
    sid = new_session(client)["sid"]
    response = client.post(f"/api/sessions/{sid}/transcribe?q=0", content=b"x")
    assert response.status_code == 422
    assert "микрофон" in response.json()["detail"]


def test_review_uses_question_and_transcript():
    llm = FakeLlm()
    client = make_client(llm=llm)
    session = new_session(client)
    sid = session["sid"]
    client.post(f"/api/sessions/{sid}/transcribe?q=1", content=b"x")
    response = client.post(f"/api/sessions/{sid}/review", json={"q": 1})
    assert response.status_code == 200
    assert response.json()["verdict"] == "improve"
    user_message = llm.calls[0][1]["content"]
    assert session["questions"][1]["text"] in user_message
    assert "мой ответ" in user_message


def test_review_uses_latest_attempt():
    llm = FakeLlm()
    client = make_client(stt=FakeStt(texts=["первая попытка", "вторая попытка"]), llm=llm)
    sid = new_session(client)["sid"]
    client.post(f"/api/sessions/{sid}/transcribe?q=0", content=b"x")
    client.post(f"/api/sessions/{sid}/transcribe?q=0", content=b"x")
    client.post(f"/api/sessions/{sid}/review", json={"q": 0})
    assert "вторая попытка" in llm.calls[0][1]["content"]
    assert "первая попытка" not in llm.calls[0][1]["content"]


def test_review_without_answer_is_409():
    client = make_client()
    sid = new_session(client)["sid"]
    response = client.post(f"/api/sessions/{sid}/review", json={"q": 0})
    assert response.status_code == 409
    assert "запишите" in response.json()["detail"].lower()


def test_review_llm_error_is_503_with_message():
    llm = FakeLlm()
    client = make_client(llm=llm)
    sid = new_session(client)["sid"]
    client.post(f"/api/sessions/{sid}/transcribe?q=0", content=b"x")
    llm.error = LlmError("LM Studio не отвечает.")
    response = client.post(f"/api/sessions/{sid}/review", json={"q": 0})
    assert response.status_code == 503
    assert response.json()["detail"] == "LM Studio не отвечает."


def test_tts_returns_wav():
    tts = FakeTts()
    response = make_client(tts=tts).post("/api/tts", json={"text": "Привет"})
    assert response.status_code == 200
    assert response.headers["content-type"] == "audio/wav"
    assert response.content == b"RIFFfake-wav"
    assert tts.texts == ["Привет"]


def test_tts_failure_is_500_with_russian_message():
    class BrokenTts:
        def synthesize(self, text):
            raise ValueError("Failed to parse SSML")

    response = make_client(tts=BrokenTts()).post("/api/tts", json={"text": "Привет"})
    assert response.status_code == 500
    assert response.json()["detail"] == "Не удалось озвучить текст."


def test_tts_rejects_empty_text():
    assert make_client().post("/api/tts", json={"text": ""}).status_code == 422


def test_index_page_is_served():
    response = make_client().get("/")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
