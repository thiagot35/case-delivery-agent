import subprocess
from typing import Any

import pytest

from delivery_agent import main
from delivery_agent.config import Settings


class FakeProcess:
    """Stand-in for the Menu service child process."""

    def __init__(self, exit_code: int | None = None):
        """Start alive, or already exited with `exit_code`."""
        self.returncode = exit_code
        self.terminated = False

    def poll(self) -> int | None:
        """Return the exit code, or None while running."""
        return self.returncode

    def terminate(self) -> None:
        """Record the termination."""
        self.terminated = True
        self.returncode = 0

    def wait(self, timeout: float | None = None) -> int:
        """Return immediately."""
        return 0


@pytest.fixture
def spawned(monkeypatch: pytest.MonkeyPatch) -> list[Any]:
    """Capture the spawned commands instead of starting processes."""
    commands: list[Any] = []

    def popen(command: list[str]) -> FakeProcess:
        """Record the command and return a running fake process."""
        process = FakeProcess()
        commands.append((command, process))
        return process

    monkeypatch.setattr(subprocess, "Popen", popen)
    monkeypatch.setattr(main, "HEALTH_POLL_SECONDS", 0)
    return commands


def test_autostart_spawns_and_stops_the_menu_service(monkeypatch: pytest.MonkeyPatch, spawned: list[Any]) -> None:
    """The Menu service is started, awaited until healthy and terminated on exit."""
    answers = iter([False, False, True])  # not running yet, starting, healthy
    monkeypatch.setattr(main, "menu_service_healthy", lambda url: next(answers))
    with main.menu_service_process(Settings(menu_service_autostart=True)) as process:
        assert process is not None
    ((command, fake),) = spawned
    assert command[1:] == ["-m", "menu_service.main"]
    assert fake.terminated


def test_already_running_or_disabled_does_not_spawn(monkeypatch: pytest.MonkeyPatch, spawned: list[Any]) -> None:
    """An already healthy service, or autostart off, means no child process."""
    monkeypatch.setattr(main, "menu_service_healthy", lambda url: True)
    with main.menu_service_process(Settings(menu_service_autostart=True)) as process:
        assert process is None
    with main.menu_service_process(Settings(menu_service_autostart=False)) as process:
        assert process is None
    assert spawned == []


def test_startup_failure_is_reported(monkeypatch: pytest.MonkeyPatch, spawned: list[Any]) -> None:
    """A child that exits during startup raises instead of serving an agent without menu."""
    monkeypatch.setattr(main, "menu_service_healthy", lambda url: False)
    monkeypatch.setattr(subprocess, "Popen", lambda command: FakeProcess(exit_code=1))
    with pytest.raises(RuntimeError, match="exited"), main.menu_service_process(Settings()):
        pass
