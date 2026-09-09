"""``iso_builder`` のコマンド組み立て・実行ロジックのテスト。

実際のCD-ROMやhdiutilは使用せず、``subprocess`` をモック化して検証する。
"""

from __future__ import annotations

import subprocess

import pytest

from mkhybrid_gui.iso_builder import (
    IsoOptions,
    build_makehybrid_command,
    build_verify_command,
    run_makehybrid,
    verify_iso,
)


def test_build_makehybrid_command_default_options() -> None:
    cmd = build_makehybrid_command("/Volumes/SAMPLE_CD", "/tmp/out.iso")

    assert cmd == [
        "hdiutil",
        "makehybrid",
        "-iso",
        "-joliet",
        "-rock",
        "-o",
        "/tmp/out.iso",
        "/Volumes/SAMPLE_CD",
    ]


def test_build_makehybrid_command_joliet_only() -> None:
    cmd = build_makehybrid_command(
        "/Volumes/SAMPLE_CD", "/tmp/out.iso", IsoOptions(joliet=True, rock=False)
    )

    assert "-rock" not in cmd
    assert "-joliet" in cmd


def test_build_makehybrid_command_joliet_and_rock_off_keeps_plain_iso() -> None:
    # -iso はデフォルトで有効なため、Joliet/Rockを両方OFFにしても
    # 素のISO9660イメージとして有効なコマンドになる。
    cmd = build_makehybrid_command(
        "/Volumes/SAMPLE_CD", "/tmp/out.iso", IsoOptions(joliet=False, rock=False)
    )

    assert cmd == ["hdiutil", "makehybrid", "-iso", "-o", "/tmp/out.iso", "/Volumes/SAMPLE_CD"]


def test_build_makehybrid_command_udf_for_dvd_bd() -> None:
    cmd = build_makehybrid_command(
        "/Volumes/SAMPLE_DVD", "/tmp/out.iso", IsoOptions(udf=True)
    )

    assert cmd == [
        "hdiutil",
        "makehybrid",
        "-iso",
        "-joliet",
        "-rock",
        "-udf",
        "-o",
        "/tmp/out.iso",
        "/Volumes/SAMPLE_DVD",
    ]


def test_build_makehybrid_command_rejects_no_format() -> None:
    with pytest.raises(ValueError):
        build_makehybrid_command(
            "/Volumes/SAMPLE_CD",
            "/tmp/out.iso",
            IsoOptions(iso=False, joliet=False, rock=False, udf=False),
        )


def test_build_makehybrid_command_preserves_paths_with_spaces() -> None:
    cmd = build_makehybrid_command("/Volumes/My CD", "/tmp/日本語 出力.iso")

    assert "/Volumes/My CD" in cmd
    assert "/tmp/日本語 出力.iso" in cmd


def test_build_verify_command() -> None:
    assert build_verify_command("/tmp/out.iso") == ["hdiutil", "verify", "/tmp/out.iso"]


class _FakePopen:
    def __init__(self, cmd, **kwargs):
        self.cmd = cmd
        self.stdout = iter(["Creating hybrid image...\n", "done\n"])
        self._returncode = 0

    def wait(self) -> int:
        return self._returncode


def test_run_makehybrid_streams_progress_and_returns_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(subprocess, "Popen", _FakePopen)

    lines: list[str] = []
    result = run_makehybrid(
        "/Volumes/SAMPLE_CD", "/tmp/out.iso", on_progress=lines.append
    )

    assert result.ok is True
    assert lines == ["Creating hybrid image...", "done"]


def test_verify_iso_success(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeCompletedProcess:
        returncode = 0
        stderr = ""

    def fake_run(cmd, **kwargs):
        assert cmd == ["hdiutil", "verify", "/tmp/out.iso"]
        return FakeCompletedProcess()

    monkeypatch.setattr(subprocess, "run", fake_run)

    result = verify_iso("/tmp/out.iso")
    assert result.ok is True


def test_verify_iso_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeCompletedProcess:
        returncode = 1
        stderr = "checksum mismatch"

    def fake_run(cmd, **kwargs):
        return FakeCompletedProcess()

    monkeypatch.setattr(subprocess, "run", fake_run)

    result = verify_iso("/tmp/out.iso")
    assert result.ok is False
    assert "checksum mismatch" in result.stderr
