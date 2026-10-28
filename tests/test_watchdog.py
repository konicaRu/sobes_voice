import threading

from fastapi.testclient import TestClient

from app.config import Config, Glossary
from app.main import create_app
from app.storage import Storage
from app.watchdog import Heartbeat, start_watchdog


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def test_not_idle_until_first_ping():
    clock = Clock()
    heartbeat = Heartbeat(clock)
    clock.now += 10_000
    assert not heartbeat.idle_longer_than(150)


def test_idle_after_timeout_since_last_ping():
    clock = Clock()
    heartbeat = Heartbeat(clock)
    heartbeat.ping()
    clock.now += 149
    assert not heartbeat.idle_longer_than(150)
    clock.now += 2
    assert heartbeat.idle_longer_than(150)
    heartbeat.ping()
    assert not heartbeat.idle_longer_than(150)


def test_watchdog_calls_on_idle_once_when_pings_stop():
    clock = Clock()
    heartbeat = Heartbeat(clock)
    heartbeat.ping()
    clock.now += 500
    fired = threading.Event()
    start_watchdog(heartbeat, timeout_s=150, on_idle=fired.set, interval_s=0.01)
    assert fired.wait(2)


def test_ping_endpoint_records_heartbeat(tmp_path):
    app = create_app(config=Config(), glossary=Glossary(), storage=Storage(tmp_path), stt=object(), tts=object(), llm=object())
    client = TestClient(app)
    assert not app.state.heartbeat.idle_longer_than(0)
    assert client.post("/api/ping").status_code == 204
    assert app.state.heartbeat.idle_longer_than(-1)
