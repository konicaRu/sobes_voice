import os
import random
import threading
import time
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Annotated, Literal

from fastapi import FastAPI, HTTPException, Query, Request, Response
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, StringConstraints

from app.config import ROOT, lmstudio_hint, load_config, load_glossary
from app.documents import MAX_UPLOAD_BYTES, DocumentError, extract_text, safe_filename
from app.generation import MAX_VACANCY_CHARS as MAX_GENERATION_VACANCY_CHARS
from app.generation import generate_questions, generation_percent, validate_counts
from app.jobs import JobTracker
from app.llm import LlmCancelled, LlmError, build_llm
from app.models import MAX_QUESTION_CHARS, MAX_QUESTIONS, SLOTS, Attempt, Question
from app.profile import MAX_PROFILE_CHARS, build_profile, profile_percent
from app.questions import merge_questions, parse_questions
from app.report import (
    build_history,
    generate_report,
    render_markdown,
    report_percent,
    session_rows,
    session_stats,
    weak_questions,
)
from app.review import MAX_VACANCY_CHARS, review_answer
from app.spoken import humanize_question
from app.settings import (
    RATES,
    SPEAKERS,
    SettingsUpdate,
    apply_overrides,
    apply_to_config,
    apply_to_engines,
    current_values,
    save_overrides,
)
from app.storage import NotFoundError, Storage, StorageError
from app.stt import AudioError, build_stt
from app.tts import build_tts
from app.watchdog import Heartbeat, start_watchdog

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


class SessionRequest(BaseModel):
    mode: Literal["all", "weak"] = "all"
    shuffle: bool = False


class SkipRequest(BaseModel):
    q: int = Field(ge=0)


class ProfileRequest(BaseModel):
    profile: str = Field(max_length=MAX_PROFILE_CHARS)


class QuestionsPayload(BaseModel):
    questions: list[Question] = Field(min_length=1, max_length=MAX_QUESTIONS)


class GenerateRequest(BaseModel):
    counts: dict[str, int]
    existing: list[Annotated[str, StringConstraints(max_length=MAX_QUESTION_CHARS)]] = Field(
        default_factory=list, max_length=MAX_QUESTIONS
    )


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
    settings_path = storage.root.parent / "settings.json"
    apply_overrides(config, settings_path)  # настройки со страницы «Настройки» важнее config.yaml

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # тяжёлые модели грузятся один раз при старте сервера
        if app.state.stt is None:
            app.state.stt = build_stt(config, glossary)
        if app.state.tts is None:
            app.state.tts = build_tts(config, glossary, ROOT / config.paths.data_dir / "cache" / "tts")
        if app.state.llm is None:
            app.state.llm = build_llm(config)
        if config.server.stop_when_closed:
            start_watchdog(app.state.heartbeat, config.server.idle_timeout_s, on_idle=lambda: os._exit(0))
        yield

    app = FastAPI(title="Interview Trainer", lifespan=lifespan)
    app.state.stt, app.state.tts, app.state.llm = stt, tts, llm
    app.state.heartbeat = Heartbeat()
    review_cancel = threading.Event()  # один пользователь: флаг «прервать разбор» общий
    jobs = JobTracker()  # фоновые задачи: сборка профиля, генерация вопросов

    @app.exception_handler(LlmError)
    async def llm_error_handler(request: Request, exc: LlmError):
        return JSONResponse(status_code=503, content={"detail": str(exc)})

    @app.exception_handler(LlmCancelled)
    async def llm_cancelled_handler(request: Request, exc: LlmCancelled):
        return JSONResponse(status_code=409, content={"detail": "Разбор прерван."})

    @app.exception_handler(NotFoundError)
    async def not_found_handler(request: Request, exc: NotFoundError):
        return JSONResponse(status_code=404, content={"detail": str(exc)})

    @app.exception_handler(StorageError)
    async def storage_error_handler(request: Request, exc: StorageError):
        return JSONResponse(status_code=422, content={"detail": str(exc)})

    @app.exception_handler(DocumentError)
    async def document_error_handler(request: Request, exc: DocumentError):
        return JSONResponse(status_code=422, content={"detail": str(exc)})

    def vacancy_text(interview_id: str, limit: int) -> str:
        text = ""
        for name in storage.list_documents(interview_id)["vacancy"]:
            try:
                text += extract_text(name, storage.read_document(interview_id, "vacancy", name)) + "\n"
            except DocumentError:
                continue
            if len(text) >= limit:
                break
        return text[:limit]

    def review_context(interview_id: str) -> dict:
        """Профиль, вакансия и термины, которые добавляются к каждому разбору ответа."""
        return {
            "profile": storage.load_profile(interview_id) or "",
            "vacancy": vacancy_text(interview_id, MAX_VACANCY_CHARS),
            "terms": [*glossary.tts, *glossary.stt_fix.values()],
        }

    # ---------- здоровье и озвучка ----------

    @app.get("/api/health")
    def health():
        try:
            lmstudio = {"ok": True, "model": app.state.llm.get_model(), "error": None, "hint": None}
        except LlmError as exc:
            lmstudio = {"ok": False, "model": None, "error": str(exc), "hint": lmstudio_hint(config)}
        return {
            "lmstudio": lmstudio,
            "stt": {"ok": app.state.stt is not None},
            "tts": {"ok": app.state.tts is not None},
        }

    @app.post("/api/ping", status_code=204)
    def ping():
        app.state.heartbeat.ping()

    @app.post("/api/tts")
    def tts_endpoint(body: TtsRequest):
        try:
            audio = app.state.tts.synthesize(body.text)
        except Exception as exc:
            raise HTTPException(500, "Не удалось озвучить текст.") from exc
        return Response(audio, media_type="audio/wav")

    # ---------- настройки ----------

    def settings_payload() -> dict:
        try:
            models = app.state.llm.list_models()
        except Exception:
            models = []
        return {
            "values": current_values(config).model_dump(),
            "choices": {"speakers": list(SPEAKERS), "rates": list(RATES), "models": models},
            "stt": {"engine": config.stt.engine, "model": config.stt.model},
        }

    @app.get("/api/settings")
    def get_settings():
        return settings_payload()

    @app.put("/api/settings")
    def put_settings(body: SettingsUpdate):
        apply_to_config(config, body)
        apply_to_engines(config, body, app.state.tts, app.state.llm)
        save_overrides(settings_path, body)
        return settings_payload()

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

    @app.delete("/api/interviews/{interview_id}", status_code=204)
    def delete_interview(interview_id: str):
        storage.delete_interview(interview_id)

    @app.get("/api/interviews/{interview_id}")
    def get_interview(interview_id: str):
        questions = storage.load_questions(interview_id)
        return {
            "interview": storage.get_interview(interview_id),
            "documents": storage.list_documents(interview_id),
            "questions": None if questions is None else [q.model_dump() for q in questions],
            "profile": storage.load_profile(interview_id),
        }

    @app.post("/api/interviews/{interview_id}/profile")
    def create_profile(interview_id: str):
        """Запускает сборку профиля в фоне; ход виден через GET .../profile/status."""
        key = f"profile:{interview_id}"
        if jobs.status(key)["state"] == "running":
            return jobs.status(key)
        documents = storage.list_documents(interview_id)
        texts = {
            slot: [extract_text(name, storage.read_document(interview_id, slot, name)) for name in documents[slot]]
            for slot in ("resume", "cases")
        }
        if not texts["resume"] and not texts["cases"]:
            raise HTTPException(409, "Сначала загрузите резюме или кейсы.")

        def work(progress) -> dict:
            profile = build_profile(app.state.llm, texts["resume"], texts["cases"], on_progress=progress)
            storage.save_profile(interview_id, profile)
            return {"profile": profile}

        return jobs.start(
            key, work, percent_of=profile_percent, start_message="Модель читает документы…",
            running_message="Модель пишет профиль…", done_message="Профиль собран и сохранён.",
        )

    @app.get("/api/interviews/{interview_id}/profile/status")
    def get_profile_status(interview_id: str):
        storage.get_interview(interview_id)
        return jobs.status(f"profile:{interview_id}")

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

    @app.post("/api/interviews/{interview_id}/questions/generate")
    def generate_questions_endpoint(interview_id: str, body: GenerateRequest):
        """Генерирует вопросы в фоне (из вакансии и профиля); результат приходит в статусе, список не сохраняется."""
        storage.get_interview(interview_id)
        key = f"questions:{interview_id}"
        if jobs.status(key)["state"] == "running":
            return jobs.status(key)
        try:
            counts = validate_counts(body.counts)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        vacancy = vacancy_text(interview_id, MAX_GENERATION_VACANCY_CHARS)
        profile = storage.load_profile(interview_id) or ""
        if not vacancy.strip() and not profile.strip():
            raise HTTPException(409, "Загрузите вакансию или соберите профиль кандидата.")
        total = sum(counts.values())

        def work(progress) -> dict:
            questions = generate_questions(app.state.llm, vacancy, profile, counts, body.existing, on_progress=progress)
            return {"questions": [q.model_dump() for q in questions]}

        return jobs.start(
            key, work, percent_of=lambda written: generation_percent(written, total),
            start_message="Модель читает вакансию и профиль…", running_message="Модель придумывает вопросы…",
            done_message="Вопросы готовы. Проверьте и утвердите список.",
        )

    @app.get("/api/interviews/{interview_id}/questions/generate/status")
    def get_generation_status(interview_id: str):
        storage.get_interview(interview_id)
        return jobs.status(f"questions:{interview_id}")

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

    @app.get("/api/interviews/{interview_id}/history")
    def get_history(interview_id: str):
        storage.get_interview(interview_id)
        return build_history(storage.list_sessions(interview_id))

    @app.post("/api/interviews/{interview_id}/sessions")
    def create_session(interview_id: str, body: SessionRequest | None = None):
        approved = storage.load_questions(interview_id)
        if approved is None:
            raise HTTPException(409, "Утвердите список вопросов перед началом собеседования.")
        questions = None
        if body is not None and body.mode == "weak":
            questions = weak_questions(storage.list_sessions(interview_id), approved)
            if not questions:
                raise HTTPException(409, "Слабых вопросов нет: на всё отвечено хорошо или ответов ещё не было.")
        if body is not None and body.shuffle:
            questions = list(questions if questions is not None else approved)
            random.shuffle(questions)
        session = storage.create_session(interview_id, questions=questions)
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

    @app.post("/api/sessions/{sid}/skip")
    def skip_question(sid: str, body: SkipRequest):
        session = storage.load_session(sid)
        if body.q >= len(session.questions):
            raise HTTPException(404, "Вопрос не найден.")
        if body.q not in session.skipped:
            session.skipped.append(body.q)
            storage.save_session(session)
        return {"skipped": session.skipped}

    @app.post("/api/sessions/{sid}/finish")
    def finish_session(sid: str):
        """Собирает отчёт в фоне; ход виден через GET .../finish/status, готовый отчёт сохраняется на диск."""
        key = f"report:{sid}"
        if jobs.status(key)["state"] == "running":
            return jobs.status(key)
        session = storage.load_session(sid)
        if session_stats(session_rows(session))["answered"] == 0:
            raise HTTPException(409, "Нет ни одного ответа: ответьте хотя бы на один вопрос.")
        title = storage.get_interview(session.interview_id).title

        def work(progress) -> dict:
            fresh = storage.load_session(sid)
            data = generate_report(
                app.state.llm, fresh, title, on_progress=progress,
                known_terms=[*glossary.tts, *glossary.stt_fix.values()],
            )
            finished_at = datetime.now().isoformat(timespec="seconds")
            fresh.finished_at = finished_at
            storage.save_session(fresh)
            storage.save_report(sid, data, render_markdown(title, data, finished_at))
            return {"report": data, "finished_at": finished_at}

        return jobs.start(
            key, work, percent_of=report_percent, start_message="Считаю итоги…",
            running_message="Модель пишет итог…", done_message="Отчёт готов.",
        )

    @app.get("/api/sessions/{sid}/finish/status")
    def get_finish_status(sid: str):
        storage.load_session(sid)
        return jobs.status(f"report:{sid}")

    @app.get("/api/sessions/{sid}/report")
    def get_report(sid: str):
        session = storage.load_session(sid)
        return {"report": storage.load_report(sid), "finished_at": session.finished_at}

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
        review_cancel.clear()
        result = review_answer(
            app.state.llm, session.questions[body.q], attempt.text, attempt.seconds,
            should_cancel=review_cancel.is_set, **review_context(session.interview_id),
        )
        attempt.review = result.model_dump()
        storage.save_session(session)
        return {**result.model_dump(), "elapsed": time.perf_counter() - started}

    @app.post("/api/sessions/{sid}/questions/{q}/spoken")
    def spoken_question(sid: str, q: int):
        session = storage.load_session(sid)
        if not 0 <= q < len(session.questions):
            raise HTTPException(404, "Вопрос не найден.")
        if q in session.spoken:
            return {"text": session.spoken[q], "humanized": True}
        original = session.questions[q].text
        text = humanize_question(app.state.llm, original)
        if text == original:
            return {"text": original, "humanized": False}  # не кэшируем: в другой раз модель может ответить
        session.spoken[q] = text
        storage.save_session(session)
        return {"text": text, "humanized": True}

    @app.post("/api/sessions/{sid}/review/cancel", status_code=204)
    def cancel_review(sid: str):
        storage.load_session(sid)
        review_cancel.set()

    app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
    return app


app = create_app()
