import threading
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Query, Request, Response
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app.config import ROOT, load_config, load_glossary
from app.documents import MAX_UPLOAD_BYTES, DocumentError, extract_text, safe_filename
from app.llm import LlmError, build_llm
from app.models import MAX_QUESTIONS, SLOTS, Attempt, Question
from app.profile import MAX_PROFILE_CHARS, build_profile, profile_percent
from app.questions import merge_questions, parse_questions
from app.review import MAX_VACANCY_CHARS, review_answer
from app.storage import NotFoundError, Storage, StorageError
from app.stt import AudioError, build_stt
from app.tts import build_tts

STATIC_DIR = ROOT / "static"
MAX_TTS_CHARS = 2000
MAX_WAV_BYTES_PER_SECOND = 48000 * 2 * 2  # 48 кГц, 16 бит, стерео: потолок для проверки размера тела
MAX_RECORDING_MARGIN_S = 30


class TtsRequest(BaseModel):
    text: str = Field(min_length=1, max_length=MAX_TTS_CHARS)


class ReviewRequest(BaseModel):
    q: int = Field(ge=0)


class InterviewRequest(BaseModel):
    title: str


class ProfileRequest(BaseModel):
    profile: str = Field(max_length=MAX_PROFILE_CHARS)


class QuestionsPayload(BaseModel):
    questions: list[Question] = Field(min_length=1, max_length=MAX_QUESTIONS)


async def read_limited(request: Request, max_bytes: int, message: str) -> bytes:
    """Читает тело запроса потоком и обрывает чтение при превышении лимита (Content-Length необязателен)."""
    declared = request.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > max_bytes:
        raise HTTPException(413, message)
    chunks, size = [], 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > max_bytes:
            raise HTTPException(413, message)
        chunks.append(chunk)
    return b"".join(chunks)


def clean_questions(questions: list[Question]) -> list[Question]:
    cleaned = []
    for question in questions:
        text = question.text.strip()
        if not text:
            raise HTTPException(422, "Текст вопроса не может быть пустым.")
        criteria = [c.strip() for c in question.what_to_check if c.strip()]
        points = [p.strip() for p in question.expected_points if p.strip()]
        cleaned.append(
            question.model_copy(
                update={
                    "id": f"q{len(cleaned) + 1:02d}", "text": text, "what_to_check": criteria,
                    "expected_points": points, "reference_answer": question.reference_answer.strip(),
                }
            )
        )
    return cleaned


def create_app(config=None, glossary=None, stt=None, tts=None, llm=None, storage=None) -> FastAPI:
    config = config or load_config()
    glossary = glossary or load_glossary()
    storage = storage or Storage(ROOT / config.paths.data_dir)

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
    profile_jobs: dict[str, dict] = {}  # фоновая сборка профиля по собеседованиям

    @app.exception_handler(LlmError)
    async def llm_error_handler(request: Request, exc: LlmError):
        return JSONResponse(status_code=503, content={"detail": str(exc)})

    @app.exception_handler(NotFoundError)
    async def not_found_handler(request: Request, exc: NotFoundError):
        return JSONResponse(status_code=404, content={"detail": str(exc)})

    @app.exception_handler(StorageError)
    async def storage_error_handler(request: Request, exc: StorageError):
        return JSONResponse(status_code=422, content={"detail": str(exc)})

    @app.exception_handler(DocumentError)
    async def document_error_handler(request: Request, exc: DocumentError):
        return JSONResponse(status_code=422, content={"detail": str(exc)})

    def review_context(interview_id: str) -> dict:
        """Профиль, вакансия и термины, которые добавляются к каждому разбору ответа."""
        vacancy = ""
        for name in storage.list_documents(interview_id)["vacancy"]:
            try:
                vacancy += extract_text(name, storage.read_document(interview_id, "vacancy", name)) + "\n"
            except DocumentError:
                continue
            if len(vacancy) >= MAX_VACANCY_CHARS:
                break
        return {
            "profile": storage.load_profile(interview_id) or "",
            "vacancy": vacancy[:MAX_VACANCY_CHARS],
            "terms": [*glossary.tts, *glossary.stt_fix.values()],
        }

    # ---------- здоровье и озвучка ----------

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

    # ---------- собеседования и документы ----------

    @app.get("/api/interviews")
    def list_interviews():
        result = []
        for interview in storage.list_interviews():
            documents = storage.list_documents(interview.id)
            questions = storage.load_questions(interview.id)
            result.append({
                **interview.model_dump(),
                "documents": {slot: len(files) for slot, files in documents.items()},
                "questions": None if questions is None else len(questions),
            })
        return result

    @app.post("/api/interviews")
    def create_interview(body: InterviewRequest):
        return storage.create_interview(body.title)

    @app.get("/api/interviews/{interview_id}")
    def get_interview(interview_id: str):
        questions = storage.load_questions(interview_id)
        return {
            "interview": storage.get_interview(interview_id),
            "documents": storage.list_documents(interview_id),
            "questions": None if questions is None else [q.model_dump() for q in questions],
            "profile": storage.load_profile(interview_id),
        }

    def profile_status(interview_id: str) -> dict:
        job = profile_jobs.get(interview_id)
        if job is None:
            return {"state": "idle", "percent": 0, "message": "", "elapsed": 0}
        finished = job.get("finished") or time.time()
        return {**{k: v for k, v in job.items() if k not in ("started", "finished")}, "elapsed": round(finished - job["started"])}

    def run_profile_job(interview_id: str, resumes: list[str], cases: list[str]) -> None:
        job = profile_jobs[interview_id]

        def progress(written: int) -> None:
            job.update(percent=profile_percent(written), message="Модель пишет профиль…")

        try:
            profile = build_profile(app.state.llm, resumes, cases, on_progress=progress)
            storage.save_profile(interview_id, profile)
            job.update(state="done", percent=100, message="Профиль собран и сохранён.", profile=profile)
        except Exception as exc:
            message = str(exc) if isinstance(exc, LlmError) else "Не удалось собрать профиль."
            job.update(state="error", message=message)
        job["finished"] = time.time()

    @app.post("/api/interviews/{interview_id}/profile")
    def create_profile(interview_id: str):
        """Запускает сборку профиля в фоне; ход виден через GET .../profile/status."""
        if profile_jobs.get(interview_id, {}).get("state") == "running":
            return profile_status(interview_id)
        documents = storage.list_documents(interview_id)
        texts = {
            slot: [extract_text(name, storage.read_document(interview_id, slot, name)) for name in documents[slot]]
            for slot in ("resume", "cases")
        }
        if not texts["resume"] and not texts["cases"]:
            raise HTTPException(409, "Сначала загрузите резюме или кейсы.")
        profile_jobs[interview_id] = {
            "state": "running", "percent": 10, "message": "Модель читает документы…", "started": time.time(),
        }
        threading.Thread(
            target=run_profile_job, args=(interview_id, texts["resume"], texts["cases"]), daemon=True
        ).start()
        return profile_status(interview_id)

    @app.get("/api/interviews/{interview_id}/profile/status")
    def get_profile_status(interview_id: str):
        storage.get_interview(interview_id)
        return profile_status(interview_id)

    @app.put("/api/interviews/{interview_id}/profile")
    def save_profile(interview_id: str, body: ProfileRequest):
        profile = body.profile.strip()
        storage.save_profile(interview_id, profile)
        return {"profile": profile}

    @app.post("/api/interviews/{interview_id}/documents")
    async def upload_document(
        interview_id: str, request: Request, slot: str = Query(), filename: str = Query(max_length=255)
    ):
        storage.get_interview(interview_id)
        if slot not in SLOTS:
            raise StorageError("Неизвестный слот документов.")
        data = await read_limited(request, MAX_UPLOAD_BYTES, "Файл слишком большой (максимум 5 МБ).")
        await run_in_threadpool(extract_text, safe_filename(filename), data)  # нечитаемый файл не сохраняем
        return {"filename": storage.save_document(interview_id, slot, filename, data)}

    @app.delete("/api/interviews/{interview_id}/documents/{slot}/{filename}")
    def delete_document(interview_id: str, slot: str, filename: str):
        storage.delete_document(interview_id, slot, filename)
        return {"ok": True}

    # ---------- вопросы ----------

    @app.post("/api/interviews/{interview_id}/questions/parse")
    def parse_questions_endpoint(interview_id: str):
        files = storage.list_documents(interview_id)["questions"]
        if not files:
            raise HTTPException(409, "Сначала загрузите файл с вопросами.")
        groups = [
            parse_questions(extract_text(name, storage.read_document(interview_id, "questions", name))) for name in files
        ]
        questions = merge_questions(groups)
        if not questions:
            raise HTTPException(422, "Не нашёл вопросов в файле. Добавьте их в редакторе вручную.")
        return {"questions": [q.model_dump() for q in questions], "files": files}

    @app.get("/api/interviews/{interview_id}/questions")
    def get_questions(interview_id: str):
        questions = storage.load_questions(interview_id)
        return {"questions": None if questions is None else [q.model_dump() for q in questions]}

    @app.put("/api/interviews/{interview_id}/questions")
    def approve_questions(interview_id: str, body: QuestionsPayload):
        questions = clean_questions(body.questions)
        storage.save_questions(interview_id, questions)
        return {"questions": [q.model_dump() for q in questions]}

    # ---------- сессии ----------

    @app.post("/api/interviews/{interview_id}/sessions")
    def create_session(interview_id: str):
        if storage.load_questions(interview_id) is None:
            raise HTTPException(409, "Утвердите список вопросов перед началом собеседования.")
        session = storage.create_session(interview_id)
        return {
            "sid": session.sid,
            "questions": [{"id": q.id, "text": q.text, "category": q.category} for q in session.questions],
            "settings": {
                "max_answer_seconds": config.stt.max_answer_seconds,
                "speak_feedback": config.tts.speak_feedback,
            },
        }

    @app.get("/api/sessions/{sid}")
    def get_session(sid: str):
        return storage.load_session(sid)

    @app.post("/api/sessions/{sid}/transcribe")
    async def transcribe(sid: str, request: Request, q: int = Query(ge=0)):
        session = storage.load_session(sid)
        if q >= len(session.questions):
            raise HTTPException(404, "Вопрос не найден.")
        max_bytes = (config.stt.max_answer_seconds + MAX_RECORDING_MARGIN_S) * MAX_WAV_BYTES_PER_SECOND
        body = await read_limited(request, max_bytes, "Запись слишком длинная.")
        try:
            result = await run_in_threadpool(app.state.stt.transcribe, body)
        except AudioError as exc:
            raise HTTPException(422, str(exc)) from exc
        if not result.text:
            raise HTTPException(422, "Не удалось распознать речь. Проверьте микрофон и попробуйте ещё раз.")
        number = 1 + sum(1 for attempt in session.attempts if attempt.q == q)
        audio = storage.save_audio(session, q, number, body)
        session.attempts.append(Attempt(q=q, n=number, text=result.text, seconds=result.seconds, audio=audio))
        storage.save_session(session)
        return {"text": result.text, "seconds": result.seconds, "elapsed": result.elapsed}

    @app.post("/api/sessions/{sid}/review")
    def review(sid: str, body: ReviewRequest):
        session = storage.load_session(sid)
        if body.q >= len(session.questions):
            raise HTTPException(404, "Вопрос не найден.")
        attempts = [a for a in session.attempts if a.q == body.q]
        if not attempts:
            raise HTTPException(409, "Сначала запишите ответ на этот вопрос.")
        attempt = attempts[-1]
        started = time.perf_counter()
        result = review_answer(
            app.state.llm, session.questions[body.q], attempt.text, attempt.seconds, **review_context(session.interview_id)
        )
        attempt.review = result.model_dump()
        storage.save_session(session)
        return {**result.model_dump(), "elapsed": time.perf_counter() - started}

    app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
    return app


app = create_app()
