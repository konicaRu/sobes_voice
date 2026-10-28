import time
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass, field

from fastapi import FastAPI, HTTPException, Query, Request, Response
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app.config import ROOT, load_config, load_glossary
from app.llm import LlmError, build_llm
from app.models import Question
from app.review import review_answer
from app.sample_questions import SAMPLE_QUESTIONS
from app.stt import AudioError, build_stt
from app.tts import build_tts

STATIC_DIR = ROOT / "static"


@dataclass
class Session:
    questions: list[Question]
    attempts: dict[int, list[tuple[str, float]]] = field(default_factory=dict)


MAX_TTS_CHARS = 2000
MAX_WAV_BYTES_PER_SECOND = 48000 * 2 * 2  # 48 кГц, 16 бит, стерео: потолок для проверки размера тела
MAX_RECORDING_MARGIN_S = 30


class TtsRequest(BaseModel):
    text: str = Field(min_length=1, max_length=MAX_TTS_CHARS)


class ReviewRequest(BaseModel):
    q: int = Field(ge=0)


def create_app(config=None, glossary=None, stt=None, tts=None, llm=None) -> FastAPI:
    config = config or load_config()
    glossary = glossary or load_glossary()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # тяжёлые модели грузятся один раз при старте сервера
        if app.state.stt is None:
            app.state.stt = build_stt(config, glossary)
        if app.state.tts is None:
            app.state.tts = build_tts(config, glossary, ROOT / config.paths.data_dir / "cache" / "tts")
        if app.state.llm is None:
            app.state.llm = build_llm(config)
        yield

    app = FastAPI(title="Interview Trainer", lifespan=lifespan)
    app.state.stt, app.state.tts, app.state.llm = stt, tts, llm
    sessions: dict[str, Session] = {}

    @app.exception_handler(LlmError)
    async def llm_error_handler(request: Request, exc: LlmError):
        return JSONResponse(status_code=503, content={"detail": str(exc)})

    def get_session(sid: str) -> Session:
        if sid not in sessions:
            raise HTTPException(404, "Сессия не найдена. Обновите страницу.")
        return sessions[sid]

    def check_question(session: Session, q: int) -> Question:
        if q >= len(session.questions):
            raise HTTPException(404, "Вопрос не найден.")
        return session.questions[q]

    @app.get("/api/health")
    def health():
        try:
            lmstudio = {"ok": True, "model": app.state.llm.get_model(), "error": None}
        except LlmError as exc:
            lmstudio = {"ok": False, "model": None, "error": str(exc)}
        return {
            "lmstudio": lmstudio,
            "stt": {"ok": app.state.stt is not None},
            "tts": {"ok": app.state.tts is not None},
        }

    @app.post("/api/tts")
    def tts_endpoint(body: TtsRequest):
        try:
            audio = app.state.tts.synthesize(body.text)
        except Exception as exc:
            raise HTTPException(500, "Не удалось озвучить текст.") from exc
        return Response(audio, media_type="audio/wav")

    @app.post("/api/sessions")
    def create_session():
        sid = uuid.uuid4().hex[:12]
        sessions[sid] = Session(questions=list(SAMPLE_QUESTIONS))
        return {
            "sid": sid,
            "questions": [{"id": q.id, "text": q.text, "category": q.category} for q in SAMPLE_QUESTIONS],
            "settings": {
                "max_answer_seconds": config.stt.max_answer_seconds,
                "speak_feedback": config.tts.speak_feedback,
            },
        }

    @app.post("/api/sessions/{sid}/transcribe")
    async def transcribe(sid: str, request: Request, q: int = Query(ge=0)):
        session = get_session(sid)
        check_question(session, q)
        max_bytes = (config.stt.max_answer_seconds + MAX_RECORDING_MARGIN_S) * MAX_WAV_BYTES_PER_SECOND
        too_long = HTTPException(413, "Запись слишком длинная.")
        if int(request.headers.get("content-length") or 0) > max_bytes:
            raise too_long
        # Content-Length необязателен, поэтому лимит проверяется и по ходу чтения тела
        chunks, size = [], 0
        async for chunk in request.stream():
            size += len(chunk)
            if size > max_bytes:
                raise too_long
            chunks.append(chunk)
        body = b"".join(chunks)
        try:
            result = await run_in_threadpool(app.state.stt.transcribe, body)
        except AudioError as exc:
            raise HTTPException(422, str(exc)) from exc
        if not result.text:
            raise HTTPException(422, "Не удалось распознать речь. Проверьте микрофон и попробуйте ещё раз.")
        session.attempts.setdefault(q, []).append((result.text, result.seconds))
        return {"text": result.text, "seconds": result.seconds, "elapsed": result.elapsed}

    @app.post("/api/sessions/{sid}/review")
    def review(sid: str, body: ReviewRequest):
        session = get_session(sid)
        question = check_question(session, body.q)
        if not session.attempts.get(body.q):
            raise HTTPException(409, "Сначала запишите ответ на этот вопрос.")
        transcript, seconds = session.attempts[body.q][-1]
        started = time.perf_counter()
        result = review_answer(app.state.llm, question, transcript, seconds)
        return {**result.model_dump(), "elapsed": time.perf_counter() - started}

    app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
    return app


def _default_app() -> FastAPI:
    return create_app()


app = _default_app()
