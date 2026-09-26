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
        "-o",
        "/tmp/out.iso",
        "/Volumes/SAMPLE_CD",
    ]


def test_build_makehybrid_command_joliet_only() -> None:
    cmd = build_makehybrid_command(
        "/Volumes/SAMPLE_CD",
        "/tmp/out.iso",
        IsoOptions(joliet=True, rock=False),
    )

    assert "-rock" not in cmd
    assert "-joliet" in cmd


def test_build_makehybrid_command_joliet_and_rock_off_keeps_plain_iso() -> None:
    # -iso はデフォルトで有効なため、Joliet/Rockを両方OFFにしても
    # 素のISO9660イメージとして有効なコマンドになる。
    cmd = build_makehybrid_command(
        "/Volumes/SAMPLE_CD",
        "/tmp/out.iso",
        IsoOptions(joliet=False, rock=False),
    )

    assert cmd == [
        "hdiutil",
        "makehybrid",
        "-iso",
        "-o",
        "/tmp/out.iso",
        "/Volumes/SAMPLE_CD",
    ]


def test_build_makehybrid_command_rock_option_is_not_passed_to_hdiutil() -> None:
    cmd = build_makehybrid_command(
        "/Volumes/SAMPLE_CD",
        "/tmp/out.iso",
        IsoOptions(rock=True),
    )

    assert "-rock" not in cmd
    assert "-iso" in cmd


def test_build_makehybrid_command_udf_for_dvd_bd() -> None:
    cmd = build_makehybrid_command(
        "/Volumes/SAMPLE_DVD",
        "/tmp/out.iso",
        IsoOptions(udf=True),
    )

    assert cmd == [
        "hdiutil",
        "makehybrid",
        "-iso",
        "-joliet",
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
            IsoOptions(
                iso=False,
                joliet=False,
                rock=False,
                udf=False,
            ),
        )


def test_build_makehybrid_command_preserves_paths_with_spaces() -> None:
    cmd = build_makehybrid_command(
        "/Volumes/My CD",
        "/tmp/日本語 出力.iso",
    )

    assert "/Volumes/My CD" in cmd
    assert "/tmp/日本語 出力.iso" in cmd


def test_build_verify_command() -> None:
    assert build_verify_command("/tmp/out.iso") == [
        "hdiutil",
        "verify",
        "/tmp/out.iso",
    ]


class _FakePopen:
    def __init__(self, cmd, **kwargs):
        self.cmd = cmd
        self.stdout = iter(
            [
                "Creating hybrid image...\n",
                "done\n",
            ]
        )
        self._returncode = 0

    def wait(self) -> int:
        return self._returncode


def test_run_makehybrid_streams_progress_and_returns_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(subprocess, "Popen", _FakePopen)

    lines: list[str] = []
    result = run_makehybrid(
        "/Volumes/SAMPLE_CD",
        "/tmp/out.iso",
        on_progress=lines.append,
    )

    assert result.ok is True
    assert lines == [
        "Creating hybrid image...",
        "done",
    ]


class _FakeVerifyPopen:
    def __init__(self, cmd, returncode: int = 0, lines: list[str] | None = None):
        self.cmd = cmd
        self.stdout = iter(lines or [])
        self._returncode = returncode

    def wait(self) -> int:
        return self._returncode


def test_run_makehybrid_exposes_process_for_cancellation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``on_process_started`` で ``Popen`` を受け取り、後から中断できることを
    確認する（安全な強制終了機能の前提となるフック）。
    """
    monkeypatch.setattr(subprocess, "Popen", _FakePopen)

    captured: list[_FakePopen] = []

    result = run_makehybrid(
        "/Volumes/SAMPLE_CD",
        "/tmp/out.iso",
        on_process_started=captured.append,
    )

    assert result.ok is True
    assert len(captured) == 1
    assert captured[0].cmd[:2] == ["hdiutil", "makehybrid"]


def test_verify_iso_success(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_popen(cmd, **kwargs):
        assert cmd == [
            "hdiutil",
            "verify",
            "/tmp/out.iso",
        ]
        return _FakeVerifyPopen(cmd, returncode=0)

    monkeypatch.setattr(subprocess, "Popen", fake_popen)

    result = verify_iso("/tmp/out.iso")
    assert result.ok is True


def test_verify_iso_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_popen(cmd, **kwargs):
        assert cmd == [
            "hdiutil",
            "verify",
            "/tmp/out.iso",
        ]
        return _FakeVerifyPopen(
            cmd,
            returncode=1,
            lines=["checksum mismatch\n"],
        )

    monkeypatch.setattr(subprocess, "Popen", fake_popen)

    result = verify_iso("/tmp/out.iso")
    assert result.ok is False
    assert "checksum mismatch" in result.stderr


def test_verify_iso_streams_progress_live(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """検証完了を待たずに、出力がその場で on_progress に届くことを確認する。

    以前は ``subprocess.run`` で完了を待ってから出力をまとめて渡していたため、
    大容量イメージの検証中はGUIに一切の進捗が反映されなかった。
    """

    def fake_popen(cmd, **kwargs):
        return _FakeVerifyPopen(
            cmd,
            returncode=0,
            lines=["Verifying...\n", "checksum: OK\n"],
        )

    monkeypatch.setattr(subprocess, "Popen", fake_popen)

    lines: list[str] = []
    result = verify_iso("/tmp/out.iso", on_progress=lines.append)

    assert result.ok is True
    assert lines == ["Verifying...", "checksum: OK"]


def test_verify_iso_reports_100_percent_on_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_popen(cmd, **kwargs):
        return _FakeVerifyPopen(cmd, returncode=0)

    monkeypatch.setattr(subprocess, "Popen", fake_popen)

    percents: list[int] = []
    result = verify_iso("/tmp/out.iso", on_percent=percents.append)

    assert result.ok is True
    assert percents == [100]
