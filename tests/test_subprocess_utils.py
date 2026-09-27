"""``subprocess_utils``（外部コマンドを安全に起動するための共通ヘルパー）
のテスト。

実際の外部コマンドは使用せず、``shutil.which`` をモック化して検証する。
"""

from __future__ import annotations

import pytest

from mkhybrid_gui import subprocess_utils
from mkhybrid_gui.subprocess_utils import resolve_command, resolve_executable


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
