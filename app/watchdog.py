"""Остановка сервера, когда страницу закрыли: страница шлёт ping, а при долгой тишине сервер завершается."""
import threading
import time
from collections.abc import Callable


class Heartbeat:
    def __init__(self, clock: Callable[[], float] = time.monotonic):
        self._clock = clock
        self._last: float | None = None

    def ping(self) -> None:
        self._last = self._clock()

    def idle_longer_than(self, seconds: float) -> bool:
        """До первого ping молчание не считается: браузер мог ещё не открыться."""
        return self._last is not None and self._clock() - self._last > seconds


def start_watchdog(heartbeat: Heartbeat, timeout_s: float, on_idle: Callable[[], None], interval_s: float = 5) -> None:
    def run() -> None:
        while not heartbeat.idle_longer_than(timeout_s):
            time.sleep(interval_s)
        on_idle()

    threading.Thread(target=run, daemon=True, name="idle-watchdog").start()
