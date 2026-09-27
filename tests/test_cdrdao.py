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
    build_toc2cue_command,
    find_scsi_device,
    missing_tools,
    rip_disc_image,
)

# 実機（ASUS SDRW-08U9M-U、USB接続）の ``cdrdao scanbus`` 実行結果。
SAMPLE_SCANBUS_OUTPUT = (
    "IOService:/AppleARMPE/arm-io@10F00000/AppleH16GFamilyIO/"
    "usb-drd3@1A280000/AppleT8132USBXHCI@03000000/"
    "usb-drd3-port-hs@03100000/Mass Storage Device@03100000/"
    "6238--Storage@0/IOUSBMassStorageInterfaceNub/"
    "IOUSBMassStorageDriverNub/IOUSBMassStorageDriver/"
    "IOSCSILogicalUnitNub@0/IOSCSIPeripheralDeviceType05/"
    "IODVDServices : ASUS, SDRW-08U9M-U, A114\n"
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


def test_scan_bus_combines_stdout_and_stderr(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_run(cmd, **kwargs):
        assert cmd == ["cdrdao", "scanbus"]

        class FakeCompletedProcess:
            stdout = "line-from-stdout\n"
            stderr = "line-from-stderr\n"

        return FakeCompletedProcess()

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(
        audio_cd, "effective_path", lambda: "/opt/homebrew/bin:/usr/bin:/bin"
    )

    output = cdrdao.scan_bus()

    assert "line-from-stdout" in output
    assert "line-from-stderr" in output


# --- scanbusによるデバイス解決 --------------------------------------------


def test_find_scsi_device_matches_media_name() -> None:
    """実機の``cdrdao scanbus``出力から、diskutilの``MediaName``に
    一致するドライブのIOKitパスを見つけられることを確認する。
    """
    device = find_scsi_device(SAMPLE_SCANBUS_OUTPUT, "ASUS SDRW-08U9M-U")

    assert device == (
        "IOService:/AppleARMPE/arm-io@10F00000/AppleH16GFamilyIO/"
        "usb-drd3@1A280000/AppleT8132USBXHCI@03000000/"
        "usb-drd3-port-hs@03100000/Mass Storage Device@03100000/"
        "6238--Storage@0/IOUSBMassStorageInterfaceNub/"
        "IOUSBMassStorageDriverNub/IOUSBMassStorageDriver/"
        "IOSCSILogicalUnitNub@0/IOSCSIPeripheralDeviceType05/"
        "IODVDServices"
    )


def test_find_scsi_device_returns_none_when_no_match() -> None:
    assert find_scsi_device(SAMPLE_SCANBUS_OUTPUT, "Some Other Drive") is None


def test_find_scsi_device_returns_none_for_empty_output() -> None:
    assert find_scsi_device("", "ASUS SDRW-08U9M-U") is None


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


def test_missing_tools_does_not_check_toc2cue_by_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """CUE生成をリクエストしていない場合、toc2cueの有無は確認しない。"""
    checked: list[str] = []

    def fake_which(name: str, path: str | None = None) -> str | None:
        checked.append(name)
        return f"/opt/homebrew/bin/{name}" if name == "cdrdao" else None

    monkeypatch.setattr(audio_cd.shutil, "which", fake_which)
    monkeypatch.setattr(
        audio_cd, "effective_path", lambda: "/opt/homebrew/bin:/usr/bin:/bin"
    )

    assert missing_tools() == []
    assert checked == ["cdrdao"]


def test_missing_tools_checks_toc2cue_when_generate_cue_requested(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(audio_cd.shutil, "which", lambda name, path=None: None)
    monkeypatch.setattr(
        audio_cd, "effective_path", lambda: "/opt/homebrew/bin:/usr/bin:/bin"
    )

    assert missing_tools(generate_cue=True) == ["cdrdao", "toc2cue"]


# --- CUEシート生成（toc2cue） ----------------------------------------------


def test_build_toc2cue_command() -> None:
    cmd = build_toc2cue_command(
        Path("/out/Album.toc"),
        Path("/out/Album.cue"),
        Path("/out/Album.cue.bin"),
    )

    assert cmd == [
        "toc2cue",
        "-s",
        "-C",
        "/out/Album.cue.bin",
        "/out/Album.toc",
        "/out/Album.cue",
    ]


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


def test_rip_disc_image_with_cue_generates_cue_and_swapped_bin(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class _FakePopen:
        def __init__(self, cmd, **kwargs):
            if cmd[0] == "cdrdao":
                bin_path = Path(cmd[cmd.index("--datafile") + 1])
                toc_path = Path(cmd[-1])
                bin_path.write_bytes(b"fake-bin-data")
                toc_path.write_text("CD_DA\n")
                self.stdout = iter(["Analyzing...\n"])
            elif cmd[0] == "toc2cue":
                swapped_bin_path = Path(cmd[cmd.index("-C") + 1])
                cue_path = Path(cmd[-1])
                swapped_bin_path.write_bytes(b"fake-swapped-bin-data")
                cue_path.write_text('FILE "fake-swapped-bin-data" BINARY\n')
                self.stdout = iter(["Converting bin file...\n"])
            else:
                raise AssertionError(f"想定外のコマンド: {cmd}")

        def wait(self) -> int:
            return 0

    monkeypatch.setattr(subprocess, "Popen", _FakePopen)
    monkeypatch.setattr(
        audio_cd, "effective_path", lambda: "/opt/homebrew/bin:/usr/bin:/bin"
    )

    dest = tmp_path / "out"
    progress_lines: list[str] = []

    result = rip_disc_image(
        "/dev/rdisk5",
        dest,
        "Test Album",
        on_progress=progress_lines.append,
        generate_cue=True,
    )

    assert result.ok is True
    assert result.cue_error is None
    assert result.cue_path == dest / "Test Album.cue"
    assert result.cue_path.exists()
    assert (dest / "Test Album.cue.bin").exists()
    assert "CUEシートを作成しています…" in progress_lines


def test_rip_disc_image_cue_failure_does_not_fail_whole_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """CUE生成が失敗しても、TOC+BIN本体は正常に作成されていれば
    ``DiscImageResult.ok``はTrueのままにする（ベストエフォート）。
    """

    class _FakePopen:
        def __init__(self, cmd, **kwargs):
            if cmd[0] == "cdrdao":
                bin_path = Path(cmd[cmd.index("--datafile") + 1])
                toc_path = Path(cmd[-1])
                bin_path.write_bytes(b"fake-bin-data")
                toc_path.write_text("CD_DA\n")
                self.stdout = iter([])
                self._returncode = 0
            elif cmd[0] == "toc2cue":
                self.stdout = iter(["ERROR: something went wrong\n"])
                self._returncode = 1
            else:
                raise AssertionError(f"想定外のコマンド: {cmd}")

        def wait(self) -> int:
            return self._returncode

    monkeypatch.setattr(subprocess, "Popen", _FakePopen)
    monkeypatch.setattr(
        audio_cd, "effective_path", lambda: "/opt/homebrew/bin:/usr/bin:/bin"
    )

    dest = tmp_path / "out"

    result = rip_disc_image(
        "/dev/rdisk5", dest, "Test Album", generate_cue=True
    )

    assert result.ok is True
    assert result.toc_path.exists()
    assert result.bin_path.exists()
    assert result.cue_path is None
    assert "something went wrong" in result.cue_error
    assert not (dest / "Test Album.cue").exists()
    assert not (dest / "Test Album.cue.bin").exists()


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


def test_cdrdao_worker_reports_cue_path_when_generate_cue_enabled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, qtbot
) -> None:
    class _FakePopen:
        def __init__(self, cmd, **kwargs):
            if cmd[0] == "cdrdao":
                bin_path = Path(cmd[cmd.index("--datafile") + 1])
                toc_path = Path(cmd[-1])
                bin_path.write_bytes(b"fake")
                toc_path.write_text("CD_DA\n")
            elif cmd[0] == "toc2cue":
                swapped_bin_path = Path(cmd[cmd.index("-C") + 1])
                cue_path = Path(cmd[-1])
                swapped_bin_path.write_bytes(b"fake-swapped")
                cue_path.write_text("FILE ...\n")
            self.stdout = iter([])

        def wait(self) -> int:
            return 0

    monkeypatch.setattr(subprocess, "Popen", _FakePopen)
    monkeypatch.setattr(
        audio_cd, "effective_path", lambda: "/opt/homebrew/bin:/usr/bin:/bin"
    )

    worker = cdrdao.CdrdaoWorker(
        "/dev/rdisk5", tmp_path / "out", "My Album", generate_cue=True
    )

    results: list[tuple[bool, str]] = []
    worker.finished_ok.connect(lambda ok, message: results.append((ok, message)))

    with qtbot.waitSignal(worker.finished_ok, timeout=2000):
        worker.start()

    assert results[0][0] is True
    assert "My Album.toc" in results[0][1]
    assert "My Album.cue" in results[0][1]
