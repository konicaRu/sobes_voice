"""Фоновые задачи со статусом и процентами: сборка профиля, генерация вопросов."""
import threading
import time
from collections.abc import Callable

from app.llm import LlmError

START_PERCENT = 10
MAX_RUNNING_PERCENT = 95  # 100% только когда задача действительно завершена
GENERIC_ERROR = "Не удалось выполнить задачу."
INTERNAL_FIELDS = ("_started", "_finished")


class JobTracker:
    """Одна задача на ключ. `work(progress)` возвращает словарь с результатом, он попадает в статус."""

    def __init__(self) -> None:
        self._jobs: dict[str, dict] = {}
        self._lock = threading.Lock()

    def status(self, key: str) -> dict:
        job = self._jobs.get(key)
        if job is None:
            return {"state": "idle", "percent": 0, "message": "", "elapsed": 0}
        elapsed = round(job.get("_finished", time.time()) - job["_started"])
        return {**{k: v for k, v in job.items() if k not in INTERNAL_FIELDS}, "elapsed": elapsed}

    def start(
        self,
        key: str,
        work: Callable[[Callable[[int], None]], dict],
        *,
        percent_of: Callable[[int], int],
        start_message: str,
        running_message: str,
        done_message: str,
    ) -> dict:
        with self._lock:
            job = self._jobs.get(key)
            if job is not None and job["state"] == "running":
                return self.status(key)
            job = {"state": "running", "percent": START_PERCENT, "message": start_message, "_started": time.time()}
            self._jobs[key] = job
        threading.Thread(
            target=self._run, args=(job, work, percent_of, running_message, done_message), daemon=True
        ).start()
        return self.status(key)

    @staticmethod
    def _run(job: dict, work, percent_of, running_message: str, done_message: str) -> None:
        def progress(written: int) -> None:
            job.update(percent=min(MAX_RUNNING_PERCENT, percent_of(written)), message=running_message)

        try:
            result = work(progress) or {}
            job.update(result)
            job.update(state="done", percent=100, message=done_message)
        except LlmError as exc:
            job.update(state="error", message=str(exc))
        except Exception:
            job.update(state="error", message=GENERIC_ERROR)
        job["_finished"] = time.time()
