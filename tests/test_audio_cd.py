"""``audio_cd`` のトラック数取得・リッピング検証・フォーマット変換のテスト。

実際の音楽CDやcdparanoia/afconvert/flacバイナリは使用せず、``subprocess`` を
モック化して検証する。
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from mkhybrid_gui.audio_cd import (
    AudioCdError,
    AudioFormat,
    RipTrackResult,
    build_convert_command,
    build_rip_command,
    convert_audio,
    missing_tools,
    output_extension,
    parse_track_count,
    rip_and_convert_disc,
    rip_track_verified,
)

SAMPLE_QUERY_OUTPUT = """\
cdparanoia III release 10.2 (December 22, 2008)

Table of contents (audio tracks only):
track        length               begin        copy pre ch
===========================================================
  1.    17462 [03:52.62]        0 [00:00.00]    no   no  2
  2.    28653 [06:22.03]    17462 [03:52.62]    no   no  2
  3.    19845 [04:24.45]    46115 [10:14.65]    no   no  2
TOTAL    65960 [14:39.10]    (audio only)
"""


# --- トラック数の解析 ---------------------------------------------------


def test_parse_track_count_returns_max_track_number() -> None:
    assert parse_track_count(SAMPLE_QUERY_OUTPUT) == 3


def test_parse_track_count_no_tracks_raises() -> None:
    with pytest.raises(AudioCdError):
        parse_track_count("no audio tracks found on this disc")


# --- コマンド組み立て ----------------------------------------------------


def test_build_rip_command_without_device() -> None:
    cmd = build_rip_command(1, Path("/tmp/track01.wav"))
    assert cmd == ["cdparanoia", "1", "/tmp/track01.wav"]


def test_build_rip_command_with_device() -> None:
    cmd = build_rip_command(2, Path("/tmp/track02.wav"), device="/dev/rdisk4")
    assert cmd == ["cdparanoia", "-d", "/dev/rdisk4", "2", "/tmp/track02.wav"]


def test_build_rip_command_never_disables_paranoia() -> None:
    # -Z（パラノイア無効化）は誤り訂正・再読込を無効にしてしまうため、常に含まれてはならない
    cmd = build_rip_command(1, Path("/tmp/track01.wav"))
    assert "-Z" not in cmd


def test_build_convert_command_alac() -> None:
    cmd = build_convert_command(Path("in.wav"), Path("out.m4a"), AudioFormat.ALAC)
    assert cmd == ["afconvert", "-f", "m4af", "-d", "alac", "in.wav", "out.m4a"]


def test_build_convert_command_aiff() -> None:
    cmd = build_convert_command(Path("in.wav"), Path("out.aiff"), AudioFormat.AIFF)
    assert cmd == ["afconvert", "-f", "AIFF", "-d", "BEI16", "in.wav", "out.aiff"]


def test_build_convert_command_aac() -> None:
    cmd = build_convert_command(Path("in.wav"), Path("out.m4a"), AudioFormat.AAC)
    assert cmd[:4] == ["afconvert", "-f", "m4af", "-d"]
    assert "aac" in cmd


def test_build_convert_command_flac() -> None:
    cmd = build_convert_command(Path("in.wav"), Path("out.flac"), AudioFormat.FLAC)
    assert cmd[0] == "flac"
    assert "in.wav" in cmd
    assert "out.flac" in cmd


def test_build_convert_command_wav_raises() -> None:
    with pytest.raises(ValueError):
        build_convert_command(Path("in.wav"), Path("out.wav"), AudioFormat.WAV)


# --- 必要な外部コマンドのチェック -----------------------------------------


def test_missing_tools_reports_absent_binaries(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("mkhybrid_gui.audio_cd.shutil.which", lambda name: None)
    assert missing_tools(AudioFormat.FLAC) == ["cdparanoia", "flac"]


def test_missing_tools_empty_when_all_present(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("mkhybrid_gui.audio_cd.shutil.which", lambda name: f"/usr/bin/{name}")
    assert missing_tools(AudioFormat.ALAC) == []


def test_output_extension_matches_container() -> None:
    assert output_extension(AudioFormat.ALAC) == ".m4a"
    assert output_extension(AudioFormat.AAC) == ".m4a"
    assert output_extension(AudioFormat.AIFF) == ".aiff"
    assert output_extension(AudioFormat.FLAC) == ".flac"
    assert output_extension(AudioFormat.WAV) == ".wav"


# --- リッピングの検証ロジック ---------------------------------------------


class _FakeRipPopen:
    """``cdparanoia`` の代わりに、あらかじめ用意したバイト列を出力先へ書き込む。"""

    _content_queue: list[bytes] = []

    def __init__(self, cmd, **kwargs):
        self.cmd = cmd
        output_path = Path(cmd[-1])
        content = self._content_queue.pop(0) if self._content_queue else b"default"
        output_path.write_bytes(content)
        self.stdout = iter([])

    def wait(self) -> int:
        return 0


def test_rip_track_verified_accepts_matching_second_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _FakeRipPopen._content_queue = [b"AAA", b"AAA"]
    monkeypatch.setattr(subprocess, "Popen", _FakeRipPopen)

    result = rip_track_verified(1, tmp_path, verify=True, max_attempts=3)

    assert isinstance(result, RipTrackResult)
    assert result.verified is True
    assert result.attempts == 2
    assert result.wav_path.read_bytes() == b"AAA"


def test_rip_track_verified_falls_back_to_unverified_after_max_attempts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _FakeRipPopen._content_queue = [b"AAA", b"BBB", b"CCC"]
    monkeypatch.setattr(subprocess, "Popen", _FakeRipPopen)

    result = rip_track_verified(1, tmp_path, verify=True, max_attempts=3)

    assert result.verified is False
    assert result.attempts == 3
    assert result.wav_path.read_bytes() == b"CCC"


def test_rip_track_verified_single_attempt_when_verify_disabled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _FakeRipPopen._content_queue = [b"AAA"]
    monkeypatch.setattr(subprocess, "Popen", _FakeRipPopen)

    result = rip_track_verified(1, tmp_path, verify=False)

    assert result.verified is False
    assert result.attempts == 1


class _FailingPopen:
    def __init__(self, cmd, **kwargs):
        self.stdout = iter([])

    def wait(self) -> int:
        return 1


def test_rip_track_verified_raises_when_every_attempt_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(subprocess, "Popen", _FailingPopen)

    with pytest.raises(AudioCdError):
        rip_track_verified(1, tmp_path, verify=True, max_attempts=2)


# --- フォーマット変換の実行 -----------------------------------------------


def test_convert_audio_wav_copies_without_external_command(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail_if_called(*args, **kwargs):
        raise AssertionError("WAV変換で外部コマンドを呼び出すべきではない")

    monkeypatch.setattr(subprocess, "Popen", fail_if_called)

    source = tmp_path / "in.wav"
    source.write_bytes(b"pcm-data")
    target = tmp_path / "out.wav"

    convert_audio(source, target, AudioFormat.WAV)

    assert target.read_bytes() == b"pcm-data"


def test_convert_audio_raises_on_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(subprocess, "Popen", _FailingPopen)

    with pytest.raises(AudioCdError):
        convert_audio(tmp_path / "in.wav", tmp_path / "out.m4a", AudioFormat.ALAC)


# --- ディスク全体のリッピング -------------------------------------------


def test_rip_and_convert_disc_end_to_end(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fake_run(cmd, **kwargs):
        class FakeCompletedProcess:
            returncode = 0
            stdout = SAMPLE_QUERY_OUTPUT
            stderr = ""

        return FakeCompletedProcess()

    class _FakeEndToEndPopen:
        def __init__(self, cmd, **kwargs):
            output_path = Path(cmd[-1])
            output_path.write_bytes(b"same-bytes")
            self.stdout = iter([])

        def wait(self) -> int:
            return 0

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(subprocess, "Popen", _FakeEndToEndPopen)

    dest = tmp_path / "out"
    work = tmp_path / "work"

    result = rip_and_convert_disc(
        "/dev/rdisk4", dest, AudioFormat.WAV, work, verify=True
    )

    assert result.ok is True
    assert len(result.tracks) == 3
    assert all(t.verified for t in result.tracks)
    assert (dest / "Track01.wav").exists()
    assert (dest / "Track02.wav").exists()
    assert (dest / "Track03.wav").exists()
