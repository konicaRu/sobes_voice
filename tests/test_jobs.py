import threading
import time

from app.jobs import JobTracker
from app.llm import LlmError


def wait(tracker, key, timeout=3.0):
    deadline = time.time() + timeout
    while True:
        status = tracker.status(key)
        if status["state"] != "running" or time.time() > deadline:
            return status
        time.sleep(0.01)


def start(tracker, work, key="k"):
    return tracker.start(
        key, work, percent_of=lambda written: 10 + written, start_message="Читаю…",
        running_message="Пишу…", done_message="Готово.",
    )


def test_unknown_key_is_idle():
    assert JobTracker().status("nothing") == {"state": "idle", "percent": 0, "message": "", "elapsed": 0}


def test_job_runs_in_background_and_returns_result_fields():
    tracker = JobTracker()
    first = start(tracker, lambda progress: {"items": [1, 2]})
    assert first["state"] in ("running", "done")
    status = wait(tracker, "k")
    assert status["state"] == "done" and status["percent"] == 100
    assert status["message"] == "Готово." and status["items"] == [1, 2]


def test_progress_callback_updates_percent_and_message_while_running():
    tracker = JobTracker()
    go, reached, release = threading.Event(), threading.Event(), threading.Event()

    def work(progress):
        go.wait(3)
        progress(30)
        reached.set()
        release.wait(3)
        return {}

    first = start(tracker, work)
    assert (first["percent"], first["message"]) == (10, "Читаю…")
    go.set()
    assert reached.wait(3)
    running = tracker.status("k")
    assert (running["state"], running["percent"], running["message"]) == ("running", 40, "Пишу…")
    release.set()
    assert wait(tracker, "k")["state"] == "done"


def test_percent_never_reaches_100_before_the_job_is_done():
    tracker = JobTracker()
    seen = []

    def work(progress):
        progress(10_000)
        seen.append(tracker.status("k")["percent"])
        return {}

    start(tracker, work)
    wait(tracker, "k")
    assert seen == [95]


def test_second_start_while_running_does_not_start_another_job():
    tracker = JobTracker()
    calls, release = [], threading.Event()

    def work(progress):
        calls.append(1)
        release.wait(3)
        return {}

    start(tracker, work)
    assert start(tracker, work)["state"] == "running"
    release.set()
    wait(tracker, "k")
    assert len(calls) == 1


def test_llm_error_text_is_reported_and_job_can_be_restarted():
    tracker = JobTracker()

    def failing(progress):
        raise LlmError("LM Studio не отвечает.")

    start(tracker, failing)
    status = wait(tracker, "k")
    assert status["state"] == "error" and status["message"] == "LM Studio не отвечает."
    start(tracker, lambda progress: {"ok": True})
    assert wait(tracker, "k")["state"] == "done"


def test_unexpected_error_gets_generic_message_without_details():
    tracker = JobTracker()

    def broken(progress):
        raise ValueError("внутренняя ошибка с путём C:\\secret")

    start(tracker, broken)
    status = wait(tracker, "k")
    assert status["state"] == "error"
    assert "secret" not in status["message"]


def test_jobs_with_different_keys_are_independent():
    tracker = JobTracker()
    start(tracker, lambda progress: {"a": 1}, key="one")
    start(tracker, lambda progress: {"b": 2}, key="two")
    assert wait(tracker, "one")["a"] == 1 and wait(tracker, "two")["b"] == 2


def test_elapsed_seconds_are_reported():
    tracker = JobTracker()
    start(tracker, lambda progress: {})
    assert wait(tracker, "k")["elapsed"] >= 0
