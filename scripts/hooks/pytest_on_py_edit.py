"""PostToolUse-хук Claude Code: после правки .py-файла прогоняет pytest.

Читает JSON события со stdin. Если тесты упали, печатает хвост вывода в stderr и выходит с кодом 2:
так Claude видит падение сразу после правки. Каждый запуск пишется в data/logs/hook.log.
"""
import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
TIMEOUT_S = 120
TAIL_LINES = 12
PYTEST_NO_TESTS_COLLECTED = 5


def run_hook(payload: dict, run=subprocess.run) -> tuple[int, str]:
    """Возвращает (код выхода, сообщение для Claude)."""
    path = (payload.get("tool_input") or {}).get("file_path", "")
    if not path.endswith(".py") or ".venv" in Path(path).parts:
        return 0, ""
    env = {**os.environ, "PYTHONUTF8": "1"}
    try:
        result = run(
            [sys.executable, "-m", "pytest", "-q", "-x"],
            cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=TIMEOUT_S, env=env,
        )
    except subprocess.TimeoutExpired:
        return 2, f"pytest не уложился в {TIMEOUT_S} с после правки {path}."
    if result.returncode in (0, PYTEST_NO_TESTS_COLLECTED):
        return 0, ""
    tail = "\n".join((result.stdout + result.stderr).splitlines()[-TAIL_LINES:])
    return 2, f"pytest упал после правки {path}:\n{tail}"


def log(payload: dict, code: int) -> None:
    path = (payload.get("tool_input") or {}).get("file_path", "")
    log_file = ROOT / "data" / "logs" / "hook.log"
    log_file.parent.mkdir(parents=True, exist_ok=True)
    with log_file.open("a", encoding="utf-8") as f:
        f.write(f"{datetime.now().isoformat(timespec='seconds')} code={code} file={path}\n")


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except json.JSONDecodeError:
        return 0
    code, message = run_hook(payload)
    log(payload, code)
    if message:
        sys.stderr.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
        print(message, file=sys.stderr)
    return code


if __name__ == "__main__":
    sys.exit(main())
