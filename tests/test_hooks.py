import subprocess
from types import SimpleNamespace as NS

from scripts.hooks.pytest_on_py_edit import run_hook


class Runner:
    def __init__(self, returncode=0, stdout=""):
        self.returncode, self.stdout, self.calls = returncode, stdout, []

    def __call__(self, cmd, **_):
        self.calls.append(cmd)
        return NS(returncode=self.returncode, stdout=self.stdout, stderr="")


runner = Runner


def payload(path):
    return {"tool_input": {"file_path": path}}


def test_python_edit_runs_pytest_and_stays_silent_when_green():
    run = runner(0)
    assert run_hook(payload("app/main.py"), run=run) == (0, "")
    assert len(run.calls) == 1 and "pytest" in run.calls[0]


def test_non_python_edit_does_not_run_pytest():
    run = runner()
    assert run_hook(payload("README.md"), run=run) == (0, "")
    assert not run.calls


def test_files_inside_venv_are_ignored():
    run = runner()
    assert run_hook(payload(r"C:\proj\.venv\Lib\site-packages\x.py"), run=run) == (0, "")
    assert not run.calls


def test_payload_without_file_path_is_ignored():
    run = runner()
    assert run_hook({}, run=run) == (0, "")
    assert not run.calls


def test_failed_tests_give_exit_2_with_tail_of_output():
    output = "\n".join(f"строка{i}" for i in range(100)) + "\nFAILED tests/test_x.py::test_y"
    code, message = run_hook(payload("app/main.py"), run=runner(1, output))
    assert code == 2
    assert "FAILED tests/test_x.py::test_y" in message
    assert "строка0\n" not in message


def test_no_tests_collected_is_not_a_failure():
    assert run_hook(payload("app/main.py"), run=runner(5)) == (0, "")


def test_timeout_is_reported_as_exit_2():
    def run(cmd, **_):
        raise subprocess.TimeoutExpired(cmd, 1)

    code, message = run_hook(payload("app/main.py"), run=run)
    assert code == 2
    assert "не уложился" in message
