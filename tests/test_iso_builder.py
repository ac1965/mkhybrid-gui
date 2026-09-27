"""``iso_builder`` のコマンド組み立て・実行ロジックのテスト。

実際のCD-ROMやhdiutilは使用せず、``subprocess`` をモック化して検証する。
"""

from __future__ import annotations

import plistlib
import subprocess
from pathlib import Path

import pytest

from mkhybrid_gui.iso_builder import (
    IsoOptions,
    build_attach_command,
    build_detach_command,
    build_makehybrid_command,
    build_verify_volume_command,
    compare_contents,
    run_makehybrid,
    verify_iso,
)


def _norm(cmd: list[str]) -> list[str]:
    """先頭要素（実行ファイル）が絶対パスに解決されていても比較できるよう、
    コマンド名部分をbasenameに正規化する（``subprocess_utils.resolve_command``
    により、実行時のコマンドは絶対パスへ解決されるため）。
    """
    if not cmd:
        return cmd
    from pathlib import Path as _Path
    return [_Path(cmd[0]).name, *cmd[1:]]


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


def test_build_attach_command() -> None:
    assert build_attach_command("/tmp/out.iso") == [
        "hdiutil",
        "attach",
        "-readonly",
        "/tmp/out.iso",
    ]


def test_build_verify_volume_command() -> None:
    assert build_verify_volume_command("/dev/disk5") == [
        "diskutil",
        "verifyVolume",
        "/dev/disk5",
    ]


def test_build_detach_command() -> None:
    assert build_detach_command("/dev/disk5") == [
        "hdiutil",
        "detach",
        "/dev/disk5",
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
    assert _norm(captured[0].cmd)[:2] == ["hdiutil", "makehybrid"]


class _FakeCompletedProcess:
    def __init__(self, stdout: bytes, returncode: int = 0):
        self.stdout = stdout
        self.returncode = returncode


def _fake_diskutil_info_run(
    filesystem_type: str | None, mount_point: str | None = None
):
    """``diskutil info -plist <device>`` をフェイクする ``subprocess.run``。"""

    def fake_run(cmd, **kwargs):
        assert _norm(cmd)[:2] == ["diskutil", "info"]
        info = {"FilesystemType": filesystem_type}
        if mount_point is not None:
            info["MountPoint"] = mount_point
        return _FakeCompletedProcess(plistlib.dumps(info))

    return fake_run


def _make_multi_step_popen(behaviors: dict[str, dict]):
    """``hdiutil attach``/``diskutil verifyVolume``/``hdiutil detach`` の
    それぞれについて、コマンド種別ごとに異なる振る舞いを返すフェイクPopen。

    ``behaviors`` は ``{"attach": {...}, "verifyVolume": {...}, "detach": {...}}``
    の形式で、各値は ``{"returncode": int, "lines": list[str]}``。
    """
    commands: list[list[str]] = []

    def _step_key(cmd: list[str]) -> str:
        if _norm(cmd)[:2] == ["hdiutil", "attach"]:
            return "attach"
        if _norm(cmd)[:2] == ["diskutil", "verifyVolume"]:
            return "verifyVolume"
        if _norm(cmd)[:2] == ["hdiutil", "detach"]:
            return "detach"
        raise AssertionError(f"想定外のコマンド: {cmd}")

    class _FakePopen:
        def __init__(self, cmd, **kwargs):
            self.cmd = cmd
            commands.append(cmd)
            behavior = behaviors.get(_step_key(cmd), {})
            self.stdout = iter(behavior.get("lines", []))
            self._returncode = behavior.get("returncode", 0)

        def wait(self) -> int:
            return self._returncode

    return _FakePopen, commands


def test_verify_iso_success_udf(monkeypatch: pytest.MonkeyPatch) -> None:
    """UDFを含むイメージは、attach後にdiskutil verifyVolumeで検証される。"""
    fake_popen, commands = _make_multi_step_popen(
        {
            "attach": {
                "returncode": 0,
                "lines": ["/dev/disk5          \t\t/Volumes/SAMPLE\n"],
            },
            "verifyVolume": {
                "returncode": 0,
                "lines": ["Filesystem is clean\n"],
            },
            "detach": {"returncode": 0, "lines": []},
        }
    )

    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    monkeypatch.setattr(subprocess, "run", _fake_diskutil_info_run("udf"))

    result = verify_iso("/tmp/out.iso")

    assert result.ok is True
    assert [_norm(cmd)[:2] for cmd in commands] == [
        ["hdiutil", "attach"],
        ["diskutil", "verifyVolume"],
        ["hdiutil", "detach"],
    ]
    assert _norm(commands[1]) == ["diskutil", "verifyVolume", "/dev/disk5"]
    assert _norm(commands[2]) == ["hdiutil", "detach", "/dev/disk5"]


def test_verify_iso_detects_corrupted_udf_filesystem(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """diskutil verifyVolumeが異常終了した場合は検証失敗として扱う。"""
    fake_popen, commands = _make_multi_step_popen(
        {
            "attach": {
                "returncode": 0,
                "lines": ["/dev/disk5          \t\t/Volumes/SAMPLE\n"],
            },
            "verifyVolume": {
                "returncode": 1,
                "lines": ["** Filesystem is dirty and non-repairable\n"],
            },
            "detach": {"returncode": 0, "lines": []},
        }
    )

    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    monkeypatch.setattr(subprocess, "run", _fake_diskutil_info_run("udf"))

    result = verify_iso("/tmp/out.iso")

    assert result.ok is False
    assert "dirty" in result.stderr
    # 検証に失敗してもdetachは必ず実行される。
    assert ["hdiutil", "detach", "/dev/disk5"] in [_norm(c) for c in commands]


def test_verify_iso_iso9660_only_skips_verify_volume(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """UDFを含まない（ISO9660/Jolietのみの）イメージは、macOS側に対応する
    ファイルシステム検証ツールがなく diskutil verifyVolume が常に失敗する
    （実機確認済み）ため、attachできたことのみで検証成功とみなす。
    """
    fake_popen, commands = _make_multi_step_popen(
        {
            "attach": {
                "returncode": 0,
                "lines": ["/dev/disk5          \t\t/Volumes/SAMPLE\n"],
            },
            "detach": {"returncode": 0, "lines": []},
        }
    )

    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    monkeypatch.setattr(subprocess, "run", _fake_diskutil_info_run("cd9660"))

    lines: list[str] = []
    result = verify_iso("/tmp/out.iso", on_progress=lines.append)

    assert result.ok is True
    # diskutil verifyVolumeは呼ばれない。
    assert ["diskutil", "verifyVolume"] not in [
        _norm(cmd)[:2] for cmd in commands
    ]
    assert any("検証には対応していません" in line for line in lines)


# --- ファイル一覧・サイズ比較（UDFなしイメージの実質的な検証） -------------


def test_compare_contents_matching_trees_returns_no_problems(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    target = tmp_path / "target"
    (source / "sub").mkdir(parents=True)
    (target / "sub").mkdir(parents=True)

    (source / "a.txt").write_bytes(b"hello")
    (target / "a.txt").write_bytes(b"hello")
    (source / "sub" / "b.txt").write_bytes(b"world!")
    (target / "sub" / "b.txt").write_bytes(b"world!")

    assert compare_contents(source, target) == []


def test_compare_contents_detects_missing_file(tmp_path: Path) -> None:
    source = tmp_path / "source"
    target = tmp_path / "target"
    source.mkdir()
    target.mkdir()

    (source / "a.txt").write_bytes(b"hello")
    (source / "b.txt").write_bytes(b"missing-from-target")

    problems = compare_contents(source, target)

    assert any("b.txt" in p and "元にあってISOに無い" in p for p in problems)


def test_compare_contents_detects_extra_file(tmp_path: Path) -> None:
    source = tmp_path / "source"
    target = tmp_path / "target"
    source.mkdir()
    target.mkdir()

    (target / "unexpected.txt").write_bytes(b"surprise")

    problems = compare_contents(source, target)

    assert any(
        "unexpected.txt" in p and "ISOにあって元に無い" in p
        for p in problems
    )


def test_compare_contents_detects_size_mismatch_truncation(
    tmp_path: Path,
) -> None:
    """切り詰められた（truncateされた）ファイルをサイズ不一致として検出する。"""
    source = tmp_path / "source"
    target = tmp_path / "target"
    source.mkdir()
    target.mkdir()

    (source / "big.bin").write_bytes(b"x" * 1000)
    (target / "big.bin").write_bytes(b"x" * 10)  # 切り詰められた状態を模す

    problems = compare_contents(source, target)

    assert any(
        "big.bin" in p and "サイズ不一致" in p for p in problems
    )


def test_compare_contents_ignores_macos_metadata_files(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    target = tmp_path / "target"
    source.mkdir()
    target.mkdir()

    (source / ".DS_Store").write_bytes(b"finder-metadata")
    (target / ".DS_Store").write_bytes(b"different-finder-metadata")

    assert compare_contents(source, target) == []


def test_verify_iso_iso9660_only_compares_contents_when_source_provided(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """UDFなしイメージでも、sourceが実在するディレクトリなら
    attach後のマウントポイントと内容を比較して検証する。
    """
    source = tmp_path / "source"
    mounted = tmp_path / "mounted"
    source.mkdir()
    mounted.mkdir()
    (source / "file.txt").write_bytes(b"content")
    (mounted / "file.txt").write_bytes(b"content")

    fake_popen, commands = _make_multi_step_popen(
        {
            "attach": {
                "returncode": 0,
                "lines": [f"/dev/disk5          \t\t{mounted}\n"],
            },
            "detach": {"returncode": 0, "lines": []},
        }
    )

    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    monkeypatch.setattr(
        subprocess,
        "run",
        _fake_diskutil_info_run("cd9660", mount_point=str(mounted)),
    )

    lines: list[str] = []
    result = verify_iso(
        "/tmp/out.iso", source=str(source), on_progress=lines.append
    )

    assert result.ok is True
    assert ["diskutil", "verifyVolume"] not in [
        _norm(cmd)[:2] for cmd in commands
    ]
    assert any("一致しました" in line for line in lines)


def test_verify_iso_iso9660_only_detects_truncated_copy(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """元のファイルより短く切り詰められたコピーを検証失敗として検出する。"""
    source = tmp_path / "source"
    mounted = tmp_path / "mounted"
    source.mkdir()
    mounted.mkdir()
    (source / "file.bin").write_bytes(b"x" * 1000)
    (mounted / "file.bin").write_bytes(b"x" * 10)

    fake_popen, _commands = _make_multi_step_popen(
        {
            "attach": {
                "returncode": 0,
                "lines": [f"/dev/disk5          \t\t{mounted}\n"],
            },
            "detach": {"returncode": 0, "lines": []},
        }
    )

    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    monkeypatch.setattr(
        subprocess,
        "run",
        _fake_diskutil_info_run("cd9660", mount_point=str(mounted)),
    )

    result = verify_iso("/tmp/out.iso", source=str(source))

    assert result.ok is False
    assert "file.bin" in result.stderr
    assert "一致しません" in result.stderr


def test_verify_iso_iso9660_only_falls_back_without_source(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """sourceを渡さない場合は、従来通りattachできたことのみで成功とみなす
    （呼び出し元が更新されていない場合の後方互換性）。
    """
    fake_popen, commands = _make_multi_step_popen(
        {
            "attach": {
                "returncode": 0,
                "lines": ["/dev/disk5          \t\t/Volumes/SAMPLE\n"],
            },
            "detach": {"returncode": 0, "lines": []},
        }
    )

    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    monkeypatch.setattr(
        subprocess,
        "run",
        _fake_diskutil_info_run("cd9660", mount_point="/Volumes/SAMPLE"),
    )

    lines: list[str] = []
    result = verify_iso("/tmp/out.iso", on_progress=lines.append)

    assert result.ok is True
    assert ["diskutil", "verifyVolume"] not in [
        _norm(cmd)[:2] for cmd in commands
    ]
    assert any("検証には対応していません" in line for line in lines)


def test_verify_iso_attach_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """イメージ自体をattachできない場合は、検証失敗として扱う。"""
    fake_popen, commands = _make_multi_step_popen(
        {
            "attach": {
                "returncode": 1,
                "lines": ["hdiutil: attach failed - Not a CD/DVD image\n"],
            },
        }
    )

    monkeypatch.setattr(subprocess, "Popen", fake_popen)

    result = verify_iso("/tmp/out.iso")

    assert result.ok is False
    assert "アタッチできませんでした" in result.stderr
    # attachに失敗した場合、detachは試みない。
    assert ["hdiutil", "detach"] not in [_norm(cmd)[:2] for cmd in commands]


def test_verify_iso_reports_100_percent_on_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_popen, _commands = _make_multi_step_popen(
        {
            "attach": {
                "returncode": 0,
                "lines": ["/dev/disk5          \t\t/Volumes/SAMPLE\n"],
            },
            "verifyVolume": {"returncode": 0, "lines": []},
            "detach": {"returncode": 0, "lines": []},
        }
    )

    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    monkeypatch.setattr(subprocess, "run", _fake_diskutil_info_run("udf"))

    percents: list[int] = []
    result = verify_iso("/tmp/out.iso", on_percent=percents.append)

    assert result.ok is True
    assert percents == [100]
