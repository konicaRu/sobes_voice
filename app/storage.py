"""Хранение в файлах: data/interviews/<id>/{interview.json, docs/<слот>/, questions.json, sessions/<время>/}."""
import json
import os
import re
import shutil
from datetime import datetime
from pathlib import Path

from app.documents import DocumentError, safe_filename
from app.models import SLOTS, Interview, Question, Session

ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
SID_PATTERN = re.compile(r"^([a-z0-9][a-z0-9-]{0,63})__(\d{8}-\d{6}(?:-\d+)?)$")
MAX_TITLE_CHARS = 100
MAX_FILES_PER_SLOT = 20

TRANSLIT = dict(zip(
    "абвгдежзийклмнопрстуфхцчшщъыьэюя",
    ["a", "b", "v", "g", "d", "e", "zh", "z", "i", "i", "k", "l", "m", "n", "o", "p", "r", "s", "t", "u", "f",
     "h", "c", "ch", "sh", "sch", "", "y", "", "e", "yu", "ya"],
))


class StorageError(Exception):
    """Ошибка хранилища; сообщение на русском для показа в интерфейсе."""


class NotFoundError(StorageError):
    pass


def slugify(title: str) -> str:
    latin = "".join(TRANSLIT.get(c, c) for c in title.lower().replace("ё", "е"))
    slug = re.sub(r"[^a-z0-9]+", "-", latin).strip("-")[:40].strip("-")
    return slug or "interview"


def _write_json(path: Path, data: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(data, encoding="utf-8")
    os.replace(temporary, path)


class Storage:
    def __init__(self, data_dir: Path):
        self.root = data_dir / "interviews"

    # ---------- собеседования ----------

    def _interview_dir(self, interview_id: str) -> Path:
        if not ID_PATTERN.match(interview_id) or not (self.root / interview_id).is_dir():
            raise NotFoundError("Собеседование не найдено.")
        return self.root / interview_id

    def create_interview(self, title: str) -> Interview:
        title = title.strip()
        if not title or len(title) > MAX_TITLE_CHARS:
            raise StorageError(f"Название должно быть от 1 до {MAX_TITLE_CHARS} символов.")
        base, interview_id, counter = slugify(title), "", 1
        while True:
            interview_id = base if counter == 1 else f"{base}-{counter}"
            if not (self.root / interview_id).exists():
                break
            counter += 1
        interview = Interview(id=interview_id, title=title, created_at=datetime.now().isoformat(timespec="seconds"))
        _write_json(self.root / interview_id / "interview.json", interview.model_dump_json(indent=2))
        return interview

    def get_interview(self, interview_id: str) -> Interview:
        path = self._interview_dir(interview_id) / "interview.json"
        return Interview.model_validate_json(path.read_text(encoding="utf-8"))

    def delete_interview(self, interview_id: str) -> None:
        """Удаляет собеседование целиком: документы, вопросы, профиль, сессии и записи."""
        shutil.rmtree(self._interview_dir(interview_id))

    def list_interviews(self) -> list[Interview]:
        if not self.root.is_dir():
            return []
        found = [self.get_interview(p.name) for p in self.root.iterdir() if (p / "interview.json").is_file()]
        return sorted(found, key=lambda i: i.created_at, reverse=True)

    # ---------- документы ----------

    def _slot_dir(self, interview_id: str, slot: str) -> Path:
        if slot not in SLOTS:
            raise StorageError("Неизвестный слот документов.")
        return self._interview_dir(interview_id) / "docs" / slot

    def save_document(self, interview_id: str, slot: str, filename: str, data: bytes) -> str:
        folder = self._slot_dir(interview_id, slot)
        try:
            name = safe_filename(filename)
        except DocumentError as exc:
            raise StorageError(str(exc)) from exc
        existing = {p.name for p in folder.iterdir()} if folder.is_dir() else set()
        if len(existing) >= MAX_FILES_PER_SLOT:
            raise StorageError("В слоте слишком много файлов. Удалите ненужные.")
        stem, _, extension = name.rpartition(".")
        candidate, counter = name, 1
        while candidate in existing:
            counter += 1
            candidate = f"{stem} ({counter}).{extension}"
        folder.mkdir(parents=True, exist_ok=True)
        (folder / candidate).write_bytes(data)
        return candidate

    def list_documents(self, interview_id: str) -> dict[str, list[str]]:
        result = {}
        for slot in SLOTS:
            folder = self._slot_dir(interview_id, slot)
            result[slot] = sorted(p.name for p in folder.iterdir()) if folder.is_dir() else []
        return result

    def _document_path(self, interview_id: str, slot: str, filename: str) -> Path:
        folder = self._slot_dir(interview_id, slot)
        path = folder / filename
        if re.search(r"[\\/]", filename) or not path.is_file() or path.resolve().parent != folder.resolve():
            raise NotFoundError("Файл не найден.")
        return path

    def read_document(self, interview_id: str, slot: str, filename: str) -> bytes:
        return self._document_path(interview_id, slot, filename).read_bytes()

    def delete_document(self, interview_id: str, slot: str, filename: str) -> None:
        self._document_path(interview_id, slot, filename).unlink()

    # ---------- вопросы ----------

    def save_questions(self, interview_id: str, questions: list[Question]) -> None:
        payload = json.dumps({"questions": [q.model_dump() for q in questions]}, ensure_ascii=False, indent=2)
        _write_json(self._interview_dir(interview_id) / "questions.json", payload)

    def load_questions(self, interview_id: str) -> list[Question] | None:
        path = self._interview_dir(interview_id) / "questions.json"
        if not path.is_file():
            return None
        return [Question.model_validate(q) for q in json.loads(path.read_text(encoding="utf-8"))["questions"]]

    # ---------- профиль кандидата ----------

    def save_profile(self, interview_id: str, text: str) -> None:
        _write_json(self._interview_dir(interview_id) / "profile.md", text)

    def load_profile(self, interview_id: str) -> str | None:
        path = self._interview_dir(interview_id) / "profile.md"
        return path.read_text(encoding="utf-8") if path.is_file() else None

    # ---------- сессии ----------

    def _session_dir(self, sid: str) -> Path:
        match = SID_PATTERN.match(sid)
        if not match:
            raise NotFoundError("Сессия не найдена.")
        path = self.root / match[1] / "sessions" / match[2]
        if not (path / "session.json").is_file():
            raise NotFoundError("Сессия не найдена.")
        return path

    def create_session(self, interview_id: str, questions: list[Question] | None = None) -> Session:
        """Сессия по утверждённому списку или по переданному подмножеству (режим «слабые»); вопросы копируются."""
        interview_dir = self._interview_dir(interview_id)
        questions = questions if questions is not None else self.load_questions(interview_id)
        if not questions:
            raise StorageError("Утвердите список вопросов перед началом собеседования.")
        questions = [q.model_copy(update={"id": f"q{n:02d}"}) for n, q in enumerate(questions, 1)]
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        candidate, counter = stamp, 1
        while (interview_dir / "sessions" / candidate).exists():
            counter += 1
            candidate = f"{stamp}-{counter}"
        session = Session(
            sid=f"{interview_id}__{candidate}",
            interview_id=interview_id,
            started_at=datetime.now().isoformat(timespec="seconds"),
            questions=questions,
        )
        self.save_session(session)
        return session

    def save_session(self, session: Session) -> None:
        match = SID_PATTERN.match(session.sid)
        if not match:
            raise NotFoundError("Сессия не найдена.")
        path = self.root / match[1] / "sessions" / match[2] / "session.json"
        _write_json(path, session.model_dump_json(indent=2))

    def load_session(self, sid: str) -> Session:
        return Session.model_validate_json((self._session_dir(sid) / "session.json").read_text(encoding="utf-8"))

    def list_sessions(self, interview_id: str) -> list[Session]:
        folder = self._interview_dir(interview_id) / "sessions"
        if not folder.is_dir():
            return []
        found = [
            self.load_session(f"{interview_id}__{path.name}")
            for path in folder.iterdir()
            if (path / "session.json").is_file()
        ]
        return sorted(found, key=lambda s: s.started_at)

    def save_report(self, sid: str, data: dict, markdown: str) -> None:
        folder = self._session_dir(sid)
        _write_json(folder / "report.json", json.dumps(data, ensure_ascii=False, indent=2))
        _write_json(folder / "report.md", markdown)

    def load_report(self, sid: str) -> dict | None:
        path = self._session_dir(sid) / "report.json"
        return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None

    def save_audio(self, session: Session, q: int, n: int, wav: bytes) -> str:
        relative = f"audio/q{q + 1:02d}_a{n}.wav"
        path = self._session_dir(session.sid) / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(wav)
        return relative

    def audio_path(self, session: Session, relative: str) -> Path:
        return self._session_dir(session.sid) / relative
