"""``subprocess_utils``（外部コマンドを安全に起動するための共通ヘルパー）
のテスト。

実際の外部コマンドは使用せず、``shutil.which`` をモック化して検証する。
"""

from __future__ import annotations

import time

import pytest

from mkhybrid_gui import subprocess_utils
from mkhybrid_gui.subprocess_utils import (
    resolve_command,
    resolve_executable,
    terminate_with_escalation,
)


def test_resolve_executable_returns_absolute_path_when_found(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        subprocess_utils.shutil,
        "which",
        lambda name, path=None: f"/opt/homebrew/bin/{name}",
    )

    assert resolve_executable("cdrdao") == "/opt/homebrew/bin/cdrdao"


def test_resolve_executable_passes_search_path_through(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, str | None]] = []

    def fake_which(name: str, path: str | None = None) -> str | None:
        calls.append((name, path))
        return f"/custom/{name}"

    monkeypatch.setattr(subprocess_utils.shutil, "which", fake_which)

    result = resolve_executable("cd-paranoia", path="/custom:/usr/bin")

    assert result == "/custom/cd-paranoia"
    assert calls == [("cd-paranoia", "/custom:/usr/bin")]


def test_resolve_executable_falls_back_to_name_when_not_found(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        subprocess_utils.shutil, "which", lambda name, path=None: None
    )

    assert resolve_executable("nonexistent-tool") == "nonexistent-tool"


def test_resolve_command_resolves_only_the_first_element(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        subprocess_utils.shutil,
        "which",
        lambda name, path=None: f"/opt/homebrew/bin/{name}",
    )

    cmd = resolve_command(["cdrdao", "read-cd", "--device", "cdrdao"])

    assert cmd == [
        "/opt/homebrew/bin/cdrdao",
        "read-cd",
        "--device",
        "cdrdao",
    ]


def test_resolve_command_handles_empty_list() -> None:
    assert resolve_command([]) == []


# --- terminate_with_escalation ------------------------------------------


class _FakeProcess:
    """``subprocess.Popen``の``terminate``/``poll``/``kill``だけを模した
    フェイク。``exits_after``秒後に自発的に終了する（``None``なら
    ``kill``されるまで終了しない）。
    """

    def __init__(self, exits_after: float | None = None) -> None:
        self.terminate_called = False
        self.kill_called = False
        self._start = time.monotonic()
        self._exits_after = exits_after

    def terminate(self) -> None:
        self.terminate_called = True

    def poll(self) -> int | None:
        if (
            self._exits_after is not None
            and time.monotonic() - self._start >= self._exits_after
        ):
            return 0
        return None

    def kill(self) -> None:
        self.kill_called = True


def test_terminate_with_escalation_sends_terminate_immediately() -> None:
    process = _FakeProcess()

    terminate_with_escalation(process, timeout=10)

    assert process.terminate_called is True
    assert process.kill_called is False


def test_terminate_with_escalation_escalates_to_kill_after_timeout() -> None:
    """``terminate``を無視し続けるプロセスは、猶予秒数経過後に
    ``kill``へエスカレーションされる。
    """
    process = _FakeProcess(exits_after=None)

    terminate_with_escalation(process, timeout=0.05)
    time.sleep(0.3)

    assert process.kill_called is True


def test_terminate_with_escalation_does_not_kill_already_exited_process() -> None:
    """猶予秒数の前に自発的に終了したプロセスへは``kill``を送らない。"""
    process = _FakeProcess(exits_after=0.02)

    terminate_with_escalation(process, timeout=0.1)
    time.sleep(0.3)

    assert process.kill_called is False
