"""``cdrdao`` によるディスクイメージ（TOC+BIN）作成のテスト。

実際の音楽CDや``cdrdao``バイナリは使用せず、``subprocess`` をモック化して
コマンド組み立て・実行結果処理のみを検証する。
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from mkhybrid_gui import audio_cd, cdrdao
from mkhybrid_gui.cdrdao import (
    build_read_cd_command,
    missing_tools,
    rip_disc_image,
)

# --- コマンド組み立て ---------------------------------------------------


def test_build_read_cd_command() -> None:
    toc_path = Path("/tmp/out/Test Album.toc")
    bin_path = Path("/tmp/out/Test Album.bin")

    cmd = build_read_cd_command("/dev/rdisk5", toc_path, bin_path)

    assert cmd == [
        "cdrdao",
        "read-cd",
        "--device",
        "/dev/rdisk5",
        "--driver",
        "generic-mmc-raw",
        "--paranoia-mode",
        "3",
        "--datafile",
        str(bin_path),
        str(toc_path),
    ]


def test_build_read_cd_command_always_uses_full_paranoia_mode() -> None:
    """``--paranoia-mode 3``（フルパラノイア）は必ず含まれること。

    cd-paranoiaに``-Z``を渡さない既定動作と同じ位置づけの要件であり、
    弱いモードへ変更したり省略したりしてはならない。
    """
    cmd = build_read_cd_command(
        "/dev/rdisk5", Path("a.toc"), Path("a.bin")
    )

    assert "--paranoia-mode" in cmd
    assert cmd[cmd.index("--paranoia-mode") + 1] == "3"


# --- 外部ツールの有無チェック --------------------------------------------


def test_missing_tools_uses_effective_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """cdrdao.missing_tools() が audio_cd の共有PATH解決を再利用することを確認する。"""
    calls: list[tuple[str, str | None]] = []

    def fake_which(name: str, path: str | None = None) -> str | None:
        calls.append((name, path))
        return None

    monkeypatch.setattr(audio_cd.shutil, "which", fake_which)
    monkeypatch.setattr(
        audio_cd,
        "effective_path",
        lambda: "/opt/homebrew/bin:/usr/bin:/bin",
    )

    assert missing_tools() == ["cdrdao"]
    assert calls == [("cdrdao", "/opt/homebrew/bin:/usr/bin:/bin")]


def test_missing_tools_empty_when_present(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        audio_cd.shutil, "which", lambda name, path=None: f"/opt/homebrew/bin/{name}"
    )
    monkeypatch.setattr(
        audio_cd, "effective_path", lambda: "/opt/homebrew/bin:/usr/bin:/bin"
    )

    assert missing_tools() == []


# --- ディスクイメージ作成 ------------------------------------------------


def test_rip_disc_image_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class _FakePopen:
        def __init__(self, cmd, **kwargs):
            bin_path = Path(cmd[cmd.index("--datafile") + 1])
            toc_path = Path(cmd[-1])
            bin_path.write_bytes(b"fake-bin-data")
            toc_path.write_text("CD_DA\n")
            self.stdout = iter(["Analyzing...\n", "Writing...\n"])

        def wait(self) -> int:
            return 0

    monkeypatch.setattr(subprocess, "Popen", _FakePopen)
    monkeypatch.setattr(
        audio_cd, "effective_path", lambda: "/opt/homebrew/bin:/usr/bin:/bin"
    )

    dest = tmp_path / "out"
    progress_lines: list[str] = []

    result = rip_disc_image(
        "/dev/rdisk5", dest, "Test Album", on_progress=progress_lines.append
    )

    assert result.ok is True
    assert result.cancelled is False
    assert result.toc_path == dest / "Test Album.toc"
    assert result.bin_path == dest / "Test Album.bin"
    assert result.toc_path.exists()
    assert result.bin_path.exists()
    assert progress_lines == ["Analyzing...", "Writing..."]


def test_rip_disc_image_failure_removes_partial_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class _FakeFailingPopen:
        def __init__(self, cmd, **kwargs):
            bin_path = Path(cmd[cmd.index("--datafile") + 1])
            bin_path.write_bytes(b"partial")
            self.stdout = iter(["Error: drive not ready\n"])

        def wait(self) -> int:
            return 1

    monkeypatch.setattr(subprocess, "Popen", _FakeFailingPopen)
    monkeypatch.setattr(
        audio_cd, "effective_path", lambda: "/opt/homebrew/bin:/usr/bin:/bin"
    )

    dest = tmp_path / "out"

    result = rip_disc_image("/dev/rdisk5", dest, "Test Album")

    assert result.ok is False
    assert result.cancelled is False
    assert "drive not ready" in result.error
    assert not result.bin_path.exists()
    assert not result.toc_path.exists()


def test_rip_disc_image_cancelled_removes_partial_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class _FakeCancelledPopen:
        def __init__(self, cmd, **kwargs):
            bin_path = Path(cmd[cmd.index("--datafile") + 1])
            toc_path = Path(cmd[-1])
            bin_path.write_bytes(b"partial")
            toc_path.write_text("partial")
            self.stdout = iter(["Writing...\n"])

        def wait(self) -> int:
            return 0

    monkeypatch.setattr(subprocess, "Popen", _FakeCancelledPopen)
    monkeypatch.setattr(
        audio_cd, "effective_path", lambda: "/opt/homebrew/bin:/usr/bin:/bin"
    )

    dest = tmp_path / "out"

    result = rip_disc_image(
        "/dev/rdisk5", dest, "Test Album", cancel_check=lambda: True
    )

    assert result.ok is False
    assert result.cancelled is True
    assert not result.bin_path.exists()
    assert not result.toc_path.exists()


# --- CdrdaoWorker（QThread） ---------------------------------------------


def test_cdrdao_worker_emits_success_message(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, qtbot
) -> None:
    class _FakePopen:
        def __init__(self, cmd, **kwargs):
            bin_path = Path(cmd[cmd.index("--datafile") + 1])
            toc_path = Path(cmd[-1])
            bin_path.write_bytes(b"fake")
            toc_path.write_text("CD_DA\n")
            self.stdout = iter([])

        def wait(self) -> int:
            return 0

    monkeypatch.setattr(subprocess, "Popen", _FakePopen)
    monkeypatch.setattr(
        audio_cd, "effective_path", lambda: "/opt/homebrew/bin:/usr/bin:/bin"
    )

    worker = cdrdao.CdrdaoWorker("/dev/rdisk5", tmp_path / "out", "My Album")

    results: list[tuple[bool, str]] = []
    worker.finished_ok.connect(lambda ok, message: results.append((ok, message)))

    with qtbot.waitSignal(worker.finished_ok, timeout=2000):
        worker.start()

    assert results[0][0] is True
    assert "My Album.toc" in results[0][1]
