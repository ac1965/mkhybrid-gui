"""``audio_cd`` のトラック数取得・リッピング検証・フォーマット変換のテスト。

実際の音楽CDやcd-paranoia/afconvert/flacバイナリは使用せず、
``subprocess`` をモック化して検証する。
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from mkhybrid_gui import accuraterip, audio_cd
from mkhybrid_gui.accuraterip import AccurateRipLookupResult, AccurateRipTrackEntry
from mkhybrid_gui.audio_cd import (
    AudioCdError,
    AudioFormat,
    AudioRipWorker,
    DiscToc,
    RipCancelled,
    RipTrackResult,
    build_convert_command,
    build_rip_command,
    convert_audio,
    disc_id_from_toc,
    missing_tools,
    output_extension,
    parse_disc_toc,
    parse_track_count,
    read_pcm_samples,
    rip_and_convert_disc,
    rip_track_verified,
    write_metadata_tags,
)
from mkhybrid_gui.metadata import AlbumMetadata, TrackMetadata

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


# --- TOC解析・Disc ID計算 ------------------------------------------------


def test_parse_disc_toc_extracts_track_offsets_and_leadout() -> None:
    toc = parse_disc_toc(SAMPLE_QUERY_OUTPUT)

    assert toc.track_offsets == [0, 17462, 46115]
    assert toc.leadout_offset == 65960


def test_parse_disc_toc_no_tracks_raises() -> None:
    with pytest.raises(AudioCdError):
        parse_disc_toc("no audio tracks found on this disc")


def test_parse_disc_toc_missing_total_line_raises() -> None:
    output_without_total = "\n".join(
        line
        for line in SAMPLE_QUERY_OUTPUT.splitlines()
        if not line.startswith("TOTAL")
    )

    with pytest.raises(AudioCdError):
        parse_disc_toc(output_without_total)


def test_disc_id_from_toc_matches_manual_computation() -> None:
    from mkhybrid_gui.metadata import LEAD_IN_FRAMES, compute_disc_id

    expected = compute_disc_id(
        first_track=1,
        last_track=3,
        leadout_offset=65960 + LEAD_IN_FRAMES,
        track_offsets=[
            0 + LEAD_IN_FRAMES,
            17462 + LEAD_IN_FRAMES,
            46115 + LEAD_IN_FRAMES,
        ],
    )

    assert disc_id_from_toc(SAMPLE_QUERY_OUTPUT) == expected


# --- コマンド組み立て ----------------------------------------------------


def test_build_rip_command_without_device() -> None:
    cmd = build_rip_command(1, Path("/tmp/track01.wav"))
    assert cmd == ["cd-paranoia", "1", "/tmp/track01.wav"]


def test_build_rip_command_with_device() -> None:
    cmd = build_rip_command(
        2,
        Path("/tmp/track02.wav"),
        device="/dev/rdisk4",
    )
    assert cmd == [
        "cd-paranoia",
        "-d",
        "/dev/rdisk4",
        "2",
        "/tmp/track02.wav",
    ]


def test_build_rip_command_never_disables_paranoia() -> None:
    # -Z（パラノイア無効化）は誤り訂正・再読込を無効にしてしまうため、
    # 常に含まれてはならない。
    cmd = build_rip_command(
        1,
        Path("/tmp/track01.wav"),
    )
    assert "-Z" not in cmd


def test_build_convert_command_alac() -> None:
    cmd = build_convert_command(
        Path("in.wav"),
        Path("out.m4a"),
        AudioFormat.ALAC,
    )
    assert cmd == [
        "afconvert",
        "-f",
        "m4af",
        "-d",
        "alac",
        "in.wav",
        "out.m4a",
    ]


def test_build_convert_command_aiff() -> None:
    cmd = build_convert_command(
        Path("in.wav"),
        Path("out.aiff"),
        AudioFormat.AIFF,
    )
    assert cmd == [
        "afconvert",
        "-f",
        "AIFF",
        "-d",
        "BEI16",
        "in.wav",
        "out.aiff",
    ]


def test_build_convert_command_aac() -> None:
    cmd = build_convert_command(
        Path("in.wav"),
        Path("out.m4a"),
        AudioFormat.AAC,
    )
    assert cmd[:4] == [
        "afconvert",
        "-f",
        "m4af",
        "-d",
    ]
    assert "aac" in cmd


def test_build_convert_command_flac() -> None:
    cmd = build_convert_command(
        Path("in.wav"),
        Path("out.flac"),
        AudioFormat.FLAC,
    )
    assert cmd[0] == "flac"
    assert "in.wav" in cmd
    assert "out.flac" in cmd


def test_build_convert_command_wav_raises() -> None:
    with pytest.raises(ValueError):
        build_convert_command(
            Path("in.wav"),
            Path("out.wav"),
            AudioFormat.WAV,
        )


# --- PATH / 外部コマンドのチェック --------------------------------------


def test_missing_tools_uses_effective_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """missing_tools() がPATHを指定してwhich()を呼び出すことを確認する。"""
    calls: list[tuple[str, str | None]] = []

    def fake_which(
        name: str,
        path: str | None = None,
    ) -> str | None:
        calls.append((name, path))
        return f"/opt/homebrew/bin/{name}"

    monkeypatch.setattr(
        audio_cd.shutil,
        "which",
        fake_which,
    )
    monkeypatch.setattr(
        audio_cd,
        "effective_path",
        lambda: "/opt/homebrew/bin:/usr/bin:/bin",
    )

    assert missing_tools(AudioFormat.FLAC) == []

    assert calls == [
        ("cd-paranoia", "/opt/homebrew/bin:/usr/bin:/bin"),
        ("flac", "/opt/homebrew/bin:/usr/bin:/bin"),
    ]


def test_missing_tools_reports_absent_binaries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        audio_cd.shutil,
        "which",
        lambda name, path=None: None,
    )
    monkeypatch.setattr(
        audio_cd,
        "effective_path",
        lambda: "/usr/bin:/bin",
    )

    assert missing_tools(AudioFormat.FLAC) == [
        "cd-paranoia",
        "flac",
    ]


def test_missing_tools_empty_when_all_present(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        audio_cd.shutil,
        "which",
        lambda name, path=None: f"/usr/bin/{name}",
    )
    monkeypatch.setattr(
        audio_cd,
        "effective_path",
        lambda: "/usr/bin:/bin",
    )

    assert missing_tools(AudioFormat.ALAC) == []


def test_effective_path_merges_shell_path_even_when_process_path_is_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """GUI起動時のようにPATHが最小限でも、ログインシェルのPATH
    （Homebrewのインストール先を含む）を必ずマージすることを確認する。

    以前は ``PATH`` が非空（launchdが設定する最小値でも常に非空）だと
    ログインシェルへの問い合わせ自体をスキップしており、Homebrewの
    パスが決して追加されないバグがあった。
    """
    audio_cd.effective_path.cache_clear()

    monkeypatch.setenv("PATH", "/usr/bin:/bin:/usr/sbin:/sbin")
    monkeypatch.setenv("SHELL", "/bin/zsh")

    def fake_run(cmd, **kwargs):
        class FakeCompletedProcess:
            stdout = "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin\n"

        assert cmd[0] == "/bin/zsh"
        return FakeCompletedProcess()

    monkeypatch.setattr(subprocess, "run", fake_run)

    try:
        effective = audio_cd.effective_path()
    finally:
        audio_cd.effective_path.cache_clear()

    entries = effective.split(":")

    assert "/opt/homebrew/bin" in entries
    assert "/usr/local/bin" in entries
    # 重複したエントリ（両方に含まれる /usr/bin, /bin）は1つだけにする。
    assert entries.count("/usr/bin") == 1
    assert entries.count("/bin") == 1


def test_effective_path_falls_back_to_process_path_when_shell_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    audio_cd.effective_path.cache_clear()

    monkeypatch.setenv("PATH", "/usr/bin:/bin")

    def fake_run(cmd, **kwargs):
        raise OSError("shell not found")

    monkeypatch.setattr(subprocess, "run", fake_run)

    try:
        assert audio_cd.effective_path() == "/usr/bin:/bin"
    finally:
        audio_cd.effective_path.cache_clear()


def test_command_environment_uses_effective_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        audio_cd,
        "effective_path",
        lambda: "/opt/homebrew/bin:/usr/bin:/bin",
    )
    monkeypatch.setenv(
        "PATH",
        "/usr/bin:/bin",
    )

    env = audio_cd.command_env()

    assert env["PATH"] == "/opt/homebrew/bin:/usr/bin:/bin"


# --- 出力拡張子 ---------------------------------------------------------


def test_output_extension_matches_container() -> None:
    assert output_extension(AudioFormat.ALAC) == ".m4a"
    assert output_extension(AudioFormat.AAC) == ".m4a"
    assert output_extension(AudioFormat.AIFF) == ".aiff"
    assert output_extension(AudioFormat.FLAC) == ".flac"
    assert output_extension(AudioFormat.WAV) == ".wav"


# --- リッピングの検証ロジック ---------------------------------------------


class _FakeRipPopen:
    """``cd-paranoia`` の代わりに、
    あらかじめ用意したバイト列を出力先へ書き込む。
    """

    _content_queue: list[bytes] = []
    commands: list[list[str]] = []
    environments: list[dict[str, str] | None] = []

    def __init__(self, cmd, **kwargs):
        self.cmd = cmd
        self.commands.append(cmd)
        self.environments.append(kwargs.get("env"))

        output_path = Path(cmd[-1])
        content = (
            self._content_queue.pop(0)
            if self._content_queue
            else b"default"
        )
        output_path.write_bytes(content)
        self.stdout = iter([])

    def wait(self) -> int:
        return 0


def test_rip_track_verified_accepts_matching_second_read(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _FakeRipPopen._content_queue = [b"AAA", b"AAA"]
    _FakeRipPopen.commands = []
    _FakeRipPopen.environments = []

    monkeypatch.setattr(
        subprocess,
        "Popen",
        _FakeRipPopen,
    )
    monkeypatch.setattr(
        audio_cd,
        "effective_path",
        lambda: "/opt/homebrew/bin:/usr/bin:/bin",
    )

    result = rip_track_verified(
        1,
        tmp_path,
        verify=True,
        max_attempts=3,
    )

    assert isinstance(result, RipTrackResult)
    assert result.verified is True
    assert result.attempts == 2
    assert result.wav_path.read_bytes() == b"AAA"

    assert _FakeRipPopen.commands[0] == [
        "cd-paranoia",
        "1",
        str(tmp_path / "track01.attempt1.wav"),
    ]
    assert _FakeRipPopen.environments[0]["PATH"] == (
        "/opt/homebrew/bin:/usr/bin:/bin"
    )


def test_rip_track_verified_falls_back_to_unverified_after_max_attempts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _FakeRipPopen._content_queue = [
        b"AAA",
        b"BBB",
        b"CCC",
    ]
    _FakeRipPopen.commands = []
    _FakeRipPopen.environments = []

    monkeypatch.setattr(
        subprocess,
        "Popen",
        _FakeRipPopen,
    )
    monkeypatch.setattr(
        audio_cd,
        "effective_path",
        lambda: "/usr/bin:/bin",
    )

    result = rip_track_verified(
        1,
        tmp_path,
        verify=True,
        max_attempts=3,
    )

    assert result.verified is False
    assert result.attempts == 3
    assert result.wav_path.read_bytes() == b"CCC"


def test_rip_track_verified_single_attempt_when_verify_disabled(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _FakeRipPopen._content_queue = [b"AAA"]
    _FakeRipPopen.commands = []
    _FakeRipPopen.environments = []

    monkeypatch.setattr(
        subprocess,
        "Popen",
        _FakeRipPopen,
    )
    monkeypatch.setattr(
        audio_cd,
        "effective_path",
        lambda: "/usr/bin:/bin",
    )

    result = rip_track_verified(
        1,
        tmp_path,
        verify=False,
    )

    assert result.verified is False
    assert result.attempts == 1


class _FailingPopen:
    def __init__(self, cmd, **kwargs):
        self.stdout = iter([])

    def wait(self) -> int:
        return 1


def test_rip_track_verified_raises_when_every_attempt_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        subprocess,
        "Popen",
        _FailingPopen,
    )
    monkeypatch.setattr(
        audio_cd,
        "effective_path",
        lambda: "/usr/bin:/bin",
    )

    with pytest.raises(AudioCdError):
        rip_track_verified(
            1,
            tmp_path,
            verify=True,
            max_attempts=2,
        )


# --- 安全な中断 -----------------------------------------------------------


def test_rip_track_verified_raises_cancelled_before_starting_attempt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """既にキャンセル要求が来ている場合、次の試行を開始せず即座に中断する。"""

    def fail_if_called(*args, **kwargs):
        raise AssertionError("キャンセル済みなのに外部コマンドを呼び出した")

    monkeypatch.setattr(subprocess, "Popen", fail_if_called)

    with pytest.raises(RipCancelled):
        rip_track_verified(
            1,
            tmp_path,
            verify=True,
            cancel_check=lambda: True,
        )


def test_rip_track_verified_raises_cancelled_after_process_and_cleans_up(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """試行の実行中にキャンセルされた場合、途中経過のWAVを削除して中断する。"""
    _FakeRipPopen._content_queue = [b"AAA"]
    _FakeRipPopen.commands = []
    _FakeRipPopen.environments = []

    monkeypatch.setattr(subprocess, "Popen", _FakeRipPopen)
    monkeypatch.setattr(
        audio_cd,
        "effective_path",
        lambda: "/usr/bin:/bin",
    )

    with pytest.raises(RipCancelled):
        rip_track_verified(
            1,
            tmp_path,
            verify=True,
            cancel_check=lambda: True,
        )

    assert list(tmp_path.glob("*.wav")) == []


def test_rip_and_convert_disc_stops_early_when_cancelled(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """キャンセル要求後は以降のトラックを処理せず、cancelled=True を返す。"""

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
    monkeypatch.setattr(
        audio_cd,
        "effective_path",
        lambda: "/opt/homebrew/bin:/usr/bin:/bin",
    )

    # SAMPLE_QUERY_OUTPUT には3トラックあるが、2トラック目の着手が
    # 通知された時点でキャンセルする（1トラック目は完了させる）。
    should_cancel = {"flag": False}

    def on_progress(line: str) -> None:
        if line.startswith("[2/3]"):
            should_cancel["flag"] = True

    def cancel_check() -> bool:
        return should_cancel["flag"]

    result = rip_and_convert_disc(
        "/dev/rdisk4",
        tmp_path / "out",
        AudioFormat.WAV,
        tmp_path / "work",
        on_progress=on_progress,
        verify=True,
        cancel_check=cancel_check,
    )

    assert result.cancelled is True
    assert len(result.tracks) == 1
    assert result.failed_tracks == []


def test_rip_and_convert_disc_reports_per_track_percent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """トラックごとの粗い進捗率がon_percentへ通知されることを確認する。"""

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
    monkeypatch.setattr(
        audio_cd,
        "effective_path",
        lambda: "/opt/homebrew/bin:/usr/bin:/bin",
    )

    percents: list[int] = []

    result = rip_and_convert_disc(
        "/dev/rdisk4",
        tmp_path / "out",
        AudioFormat.WAV,
        tmp_path / "work",
        verify=True,
        on_percent=percents.append,
    )

    assert result.cancelled is False
    # SAMPLE_QUERY_OUTPUT は3トラック: 0%, 33%, 66% の後、完了で100%。
    assert percents == [0, 33, 67, 100]


# --- フォーマット変換の実行 -----------------------------------------------


def test_convert_audio_wav_copies_without_external_command(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_if_called(*args, **kwargs):
        raise AssertionError(
            "WAV変換で外部コマンドを呼び出すべきではない"
        )

    monkeypatch.setattr(
        subprocess,
        "Popen",
        fail_if_called,
    )

    source = tmp_path / "in.wav"
    source.write_bytes(b"pcm-data")

    target = tmp_path / "out.wav"

    convert_audio(
        source,
        target,
        AudioFormat.WAV,
    )

    assert target.read_bytes() == b"pcm-data"


def test_convert_audio_raises_on_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        subprocess,
        "Popen",
        _FailingPopen,
    )
    monkeypatch.setattr(
        audio_cd,
        "effective_path",
        lambda: "/usr/bin:/bin",
    )

    with pytest.raises(AudioCdError):
        convert_audio(
            tmp_path / "in.wav",
            tmp_path / "out.m4a",
            AudioFormat.ALAC,
        )


# --- メタデータタグの書き込み --------------------------------------------
#
# mutagenは外部バイナリに依存しない純Pythonライブラリのため、実際に
# 妥当なフォーマットの最小限のファイルを用意した上で、モックせずに
# 実際のタグ読み書きをテストする。FLAC/AIFFはHomebrew依存の実バイナリ
# （flac/afconvert相当）や非推奨のaifcモジュールを使わず、バイト列を
# 直接組み立てて生成する（M4Aのみ、macOS標準コマンドのafconvertで生成）。


def _build_minimal_wav(path: Path) -> None:
    import wave

    with wave.open(str(path), "wb") as f:
        f.setnchannels(2)
        f.setsampwidth(2)
        f.setframerate(44100)
        f.writeframes(b"\x00" * 4 * 10)


def _build_wav_with_stereo_samples(
    path: Path, stereo_samples: list[tuple[int, int]]
) -> None:
    """左右チャンネル（16bit符号あり整数）を指定してWAVを組み立てる
    （``read_pcm_samples``のテスト用）。
    """
    import struct
    import wave

    frames = b"".join(
        struct.pack("<hh", left, right) for left, right in stereo_samples
    )

    with wave.open(str(path), "wb") as f:
        f.setnchannels(2)
        f.setsampwidth(2)
        f.setframerate(44100)
        f.writeframes(frames)


# --- read_pcm_samples（AccurateRip照合用のPCM読み込み） -------------------


def test_read_pcm_samples_combines_left_right_channels(tmp_path: Path) -> None:
    path = tmp_path / "samples.wav"
    _build_wav_with_stereo_samples(
        path,
        [
            (0, 0),
            (1, 0),
            (0, 1),
            (-1, -1),
            (32767, -32768),
        ],
    )

    samples = read_pcm_samples(path)

    assert samples[0] == 0
    assert samples[1] == 1
    assert samples[2] == (1 << 16)
    assert samples[3] == 0xFFFFFFFF
    assert samples[4] == (0x8000 << 16) | 0x7FFF


def test_read_pcm_samples_supports_partial_range(tmp_path: Path) -> None:
    path = tmp_path / "samples.wav"
    _build_wav_with_stereo_samples(path, [(i, i) for i in range(10)])

    samples = read_pcm_samples(path, start_frame=3, num_frames=2)

    assert samples == [(3 << 16) | 3, (4 << 16) | 4]


def test_read_pcm_samples_rejects_mono_wav(tmp_path: Path) -> None:
    import wave

    path = tmp_path / "mono.wav"
    with wave.open(str(path), "wb") as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(44100)
        f.writeframes(b"\x00\x00" * 10)

    with pytest.raises(AudioCdError):
        read_pcm_samples(path)


def test_read_pcm_samples_rejects_unexpected_sample_rate(
    tmp_path: Path,
) -> None:
    import wave

    path = tmp_path / "wrong_rate.wav"
    with wave.open(str(path), "wb") as f:
        f.setnchannels(2)
        f.setsampwidth(2)
        f.setframerate(48000)
        f.writeframes(b"\x00" * 4 * 10)

    with pytest.raises(AudioCdError):
        read_pcm_samples(path)


def _build_minimal_flac(path: Path) -> None:
    import struct

    streaminfo = struct.pack(">HH", 1, 1)  # min/max block size
    streaminfo += (0).to_bytes(3, "big")  # min frame size
    streaminfo += (0).to_bytes(3, "big")  # max frame size
    packed = (44100 << 44) | (1 << 41) | (15 << 36) | 0
    streaminfo += packed.to_bytes(8, "big")
    streaminfo += b"\x00" * 16  # MD5 signature (unknown)

    header = bytes([0x80]) + (34).to_bytes(3, "big")
    path.write_bytes(b"fLaC" + header + streaminfo)


def _build_minimal_aiff(path: Path) -> None:
    import math
    import struct

    def encode_ieee_extended(value: float) -> bytes:
        mantissa, exponent = math.frexp(value)
        mantissa *= 2
        exponent = exponent - 1 + 16383
        mantissa_bits = int(mantissa * (2**63))
        return struct.pack(">H", exponent) + struct.pack(">Q", mantissa_bits)

    num_channels, sample_size, sample_rate, num_frames = 2, 16, 44100, 10
    ssnd_data = b"\x00" * (num_frames * num_channels * (sample_size // 8))
    comm_data = struct.pack(
        ">hIh", num_channels, num_frames, sample_size
    ) + encode_ieee_extended(float(sample_rate))
    comm_chunk = b"COMM" + struct.pack(">I", len(comm_data)) + comm_data
    ssnd_chunk = (
        b"SSND"
        + struct.pack(">I", len(ssnd_data) + 8)
        + struct.pack(">II", 0, 0)
        + ssnd_data
    )
    form_data = b"AIFF" + comm_chunk + ssnd_chunk
    path.write_bytes(b"FORM" + struct.pack(">I", len(form_data)) + form_data)


def _build_minimal_m4a(path: Path) -> None:
    """``afconvert``（macOS標準コマンド）で最小限のALAC/.m4aを生成する。"""
    wav_path = path.with_suffix(".src.wav")
    _build_minimal_wav(wav_path)
    subprocess.run(
        ["afconvert", "-f", "m4af", "-d", "alac", str(wav_path), str(path)],
        check=True,
        capture_output=True,
    )


def test_write_metadata_tags_flac(tmp_path: Path) -> None:
    path = tmp_path / "track.flac"
    _build_minimal_flac(path)

    album = AlbumMetadata(
        album="Test Album",
        artist="Test Artist",
        year="2001",
        tracks=[TrackMetadata(title="Opening")],
    )

    write_metadata_tags(path, AudioFormat.FLAC, album, 1)

    import mutagen.flac

    check = mutagen.flac.FLAC(str(path))
    assert check["ALBUM"] == ["Test Album"]
    assert check["ARTIST"] == ["Test Artist"]
    assert check["TITLE"] == ["Opening"]
    assert check["DATE"] == ["2001"]
    assert check["TRACKNUMBER"] == ["1"]


def test_write_metadata_tags_wav(tmp_path: Path) -> None:
    path = tmp_path / "track.wav"
    _build_minimal_wav(path)

    album = AlbumMetadata(
        album="Test Album",
        artist="Test Artist",
        tracks=[TrackMetadata(title="Opening")],
    )

    write_metadata_tags(path, AudioFormat.WAV, album, 1)

    import mutagen.wave

    check = mutagen.wave.WAVE(str(path))
    assert str(check.tags.get("TALB")) == "Test Album"
    assert str(check.tags.get("TPE1")) == "Test Artist"
    assert str(check.tags.get("TIT2")) == "Opening"


def test_write_metadata_tags_aiff(tmp_path: Path) -> None:
    path = tmp_path / "track.aiff"
    _build_minimal_aiff(path)

    album = AlbumMetadata(
        album="Test Album",
        artist="Test Artist",
        tracks=[TrackMetadata(title="Opening")],
    )

    write_metadata_tags(path, AudioFormat.AIFF, album, 1)

    import mutagen.aiff

    check = mutagen.aiff.AIFF(str(path))
    assert str(check.tags.get("TALB")) == "Test Album"
    assert str(check.tags.get("TIT2")) == "Opening"


def test_write_metadata_tags_mp4() -> None:
    if shutil.which("afconvert") is None:
        pytest.skip("afconvertが利用できない環境のためスキップ")

    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "track.m4a"
        _build_minimal_m4a(path)

        album = AlbumMetadata(
            album="Test Album",
            artist="Test Artist",
            year="2001",
            tracks=[TrackMetadata(title="Opening")],
        )

        write_metadata_tags(path, AudioFormat.ALAC, album, 1)

        import mutagen.mp4

        check = mutagen.mp4.MP4(str(path))
        assert check["\xa9alb"] == ["Test Album"]
        assert check["\xa9ART"] == ["Test Artist"]
        assert check["\xa9nam"] == ["Opening"]
        assert check["\xa9day"] == ["2001"]


def test_write_metadata_tags_skips_when_all_fields_empty(
    tmp_path: Path,
) -> None:
    """アルバム名・アーティスト名・トラック名がすべて未入力なら何もしない。"""
    path = tmp_path / "track.flac"
    _build_minimal_flac(path)
    original_bytes = path.read_bytes()

    write_metadata_tags(path, AudioFormat.FLAC, AlbumMetadata(), 1)

    assert path.read_bytes() == original_bytes


# --- ディスク全体のリッピング -------------------------------------------


def test_rip_and_convert_disc_end_to_end(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
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

    monkeypatch.setattr(
        subprocess,
        "run",
        fake_run,
    )
    monkeypatch.setattr(
        subprocess,
        "Popen",
        _FakeEndToEndPopen,
    )

    monkeypatch.setattr(
        audio_cd,
        "effective_path",
        lambda: "/opt/homebrew/bin:/usr/bin:/bin",
    )

    dest = tmp_path / "out"
    work = tmp_path / "work"

    result = rip_and_convert_disc(
        "/dev/rdisk4",
        dest,
        AudioFormat.WAV,
        work,
        verify=True,
    )

    assert result.ok is True
    assert len(result.tracks) == 3
    assert all(track.verified for track in result.tracks)

    assert (dest / "Track01.wav").exists()
    assert (dest / "Track02.wav").exists()
    assert (dest / "Track03.wav").exists()


def test_rip_and_convert_disc_with_metadata_names_files_and_writes_tags(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """album_metadataでトラック名が入力されている場合、
    ファイル名が「NN - タイトル.ext」になり、タグが書き込まれる。
    """

    def fake_run(cmd, **kwargs):
        class FakeCompletedProcess:
            returncode = 0
            stdout = SAMPLE_QUERY_OUTPUT
            stderr = ""

        return FakeCompletedProcess()

    class _FakeWavPopen:
        def __init__(self, cmd, **kwargs):
            output_path = Path(cmd[-1])
            _build_minimal_wav(output_path)
            self.stdout = iter([])

        def wait(self) -> int:
            return 0

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(subprocess, "Popen", _FakeWavPopen)
    monkeypatch.setattr(
        audio_cd,
        "effective_path",
        lambda: "/opt/homebrew/bin:/usr/bin:/bin",
    )

    dest = tmp_path / "out"
    work = tmp_path / "work"

    album = AlbumMetadata(
        album="Test Album",
        artist="Test Artist",
        year="1999",
        tracks=[
            TrackMetadata(title="Opening"),
            TrackMetadata(title=""),  # 2曲目は未入力
            TrackMetadata(title="Finale"),
        ],
    )

    result = rip_and_convert_disc(
        "/dev/rdisk4",
        dest,
        AudioFormat.WAV,
        work,
        verify=True,
        album_metadata=album,
    )

    assert result.ok is True
    assert len(result.tracks) == 3

    # アルバム名が入力されている場合、destination_dir直下ではなく
    # アルバム名サブディレクトリの中に書き出される。
    album_dir = dest / "Test Album"
    assert result.output_directory == album_dir

    assert (album_dir / "01 - Opening.wav").exists()
    assert (album_dir / "Track02.wav").exists()  # タイトル未入力は従来通り
    assert (album_dir / "03 - Finale.wav").exists()

    import mutagen.wave

    tagged = mutagen.wave.WAVE(str(album_dir / "01 - Opening.wav"))
    assert str(tagged.tags.get("TALB")) == "Test Album"
    assert str(tagged.tags.get("TIT2")) == "Opening"

    untagged = mutagen.wave.WAVE(str(album_dir / "Track02.wav"))
    # アルバム名・アーティスト名はトラックタイトル未入力でも書き込まれる。
    assert str(untagged.tags.get("TALB")) == "Test Album"
    assert untagged.tags.get("TIT2") is None


def test_rip_and_convert_disc_without_album_name_uses_destination_dir_directly(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """アルバム名が未入力の場合は、従来通りサブディレクトリを作らない。"""

    def fake_run(cmd, **kwargs):
        class FakeCompletedProcess:
            returncode = 0
            stdout = SAMPLE_QUERY_OUTPUT
            stderr = ""

        return FakeCompletedProcess()

    class _FakeWavPopen:
        def __init__(self, cmd, **kwargs):
            output_path = Path(cmd[-1])
            _build_minimal_wav(output_path)
            self.stdout = iter([])

        def wait(self) -> int:
            return 0

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(subprocess, "Popen", _FakeWavPopen)
    monkeypatch.setattr(
        audio_cd,
        "effective_path",
        lambda: "/opt/homebrew/bin:/usr/bin:/bin",
    )

    dest = tmp_path / "out"
    work = tmp_path / "work"

    # アルバム名は空文字だが、トラックタイトルだけは入力されているケース。
    album = AlbumMetadata(
        album="",
        artist="",
        tracks=[TrackMetadata(title="Opening")] + [TrackMetadata()] * 2,
    )

    result = rip_and_convert_disc(
        "/dev/rdisk4",
        dest,
        AudioFormat.WAV,
        work,
        verify=True,
        album_metadata=album,
    )

    assert result.ok is True
    assert result.output_directory == dest
    assert (dest / "01 - Opening.wav").exists()


def test_rip_and_convert_disc_uses_fallback_folder_name_without_album(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """アルバム名未入力でも、fallback_folder_name（ディスクのボリューム名等）
    が指定されていれば、そのサブディレクトリに書き出す。
    """

    def fake_run(cmd, **kwargs):
        class FakeCompletedProcess:
            returncode = 0
            stdout = SAMPLE_QUERY_OUTPUT
            stderr = ""

        return FakeCompletedProcess()

    class _FakeWavPopen:
        def __init__(self, cmd, **kwargs):
            output_path = Path(cmd[-1])
            _build_minimal_wav(output_path)
            self.stdout = iter([])

        def wait(self) -> int:
            return 0

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(subprocess, "Popen", _FakeWavPopen)
    monkeypatch.setattr(
        audio_cd,
        "effective_path",
        lambda: "/opt/homebrew/bin:/usr/bin:/bin",
    )

    dest = tmp_path / "out"
    work = tmp_path / "work"

    result = rip_and_convert_disc(
        "/dev/rdisk4",
        dest,
        AudioFormat.WAV,
        work,
        verify=True,
        fallback_folder_name="Audio CD",
    )

    assert result.ok is True
    fallback_dir = dest / "Audio CD"
    assert result.output_directory == fallback_dir
    assert (fallback_dir / "Track01.wav").exists()


def test_rip_and_convert_disc_album_name_takes_priority_over_fallback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """アルバム名とfallback_folder_nameの両方が指定されている場合は
    アルバム名を優先する。
    """

    def fake_run(cmd, **kwargs):
        class FakeCompletedProcess:
            returncode = 0
            stdout = SAMPLE_QUERY_OUTPUT
            stderr = ""

        return FakeCompletedProcess()

    class _FakeWavPopen:
        def __init__(self, cmd, **kwargs):
            output_path = Path(cmd[-1])
            _build_minimal_wav(output_path)
            self.stdout = iter([])

        def wait(self) -> int:
            return 0

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(subprocess, "Popen", _FakeWavPopen)
    monkeypatch.setattr(
        audio_cd,
        "effective_path",
        lambda: "/opt/homebrew/bin:/usr/bin:/bin",
    )

    dest = tmp_path / "out"
    work = tmp_path / "work"

    album = AlbumMetadata(album="Test Album", artist="", tracks=[TrackMetadata()] * 3)

    result = rip_and_convert_disc(
        "/dev/rdisk4",
        dest,
        AudioFormat.WAV,
        work,
        verify=True,
        album_metadata=album,
        fallback_folder_name="Audio CD",
    )

    assert result.ok is True
    album_dir = dest / "Test Album"
    assert result.output_directory == album_dir
    assert (album_dir / "Track01.wav").exists()


# --- AccurateRip照合の統合 ------------------------------------------------


def _brute_v1(samples: list[int], start: int, length: int) -> int:
    """テスト内でのみ使う素朴な参照実装（乗数はウィンドウ内ローカル位置）。"""
    total = 0
    for local_index, value in enumerate(samples[start : start + length]):
        total += value * (local_index + 1)
    return total & 0xFFFFFFFF


def _track_samples(track_number: int, count: int) -> list[tuple[int, int]]:
    """トラック番号ごとに決定的な（左, 右）サンプル列を作る（テスト用）。"""
    base = track_number * 1000
    return [(base + i, base + i) for i in range(count)]


def _combined_track_values(
    track_number: int, count: int
) -> list[int]:
    return [
        (right << 16) | (left & 0xFFFF)
        for left, right in _track_samples(track_number, count)
    ]


def test_rip_and_convert_disc_confirms_middle_track_via_accuraterip(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """3トラック中、中間のトラック2のみAccurateRip照合の対象になり、
    既知のオフセットで一致した投稿のconfidenceが反映されることを検証する。
    先頭・最終トラック（1・3）は対象外のまま``None``であることも確認する。
    """
    track_frame_counts = {1: 30, 2: 200, 3: 30}

    def fake_run(cmd, **kwargs):
        class FakeCompletedProcess:
            returncode = 0
            stdout = SAMPLE_QUERY_OUTPUT
            stderr = ""

        return FakeCompletedProcess()

    class _FakeAccurateRipPopen:
        def __init__(self, cmd, **kwargs):
            track_number = int(cmd[-2])
            output_path = Path(cmd[-1])
            _build_wav_with_stereo_samples(
                output_path,
                _track_samples(
                    track_number, track_frame_counts[track_number]
                ),
            )
            self.stdout = iter([])

        def wait(self) -> int:
            return 0

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(subprocess, "Popen", _FakeAccurateRipPopen)
    monkeypatch.setattr(
        audio_cd,
        "effective_path",
        lambda: "/opt/homebrew/bin:/usr/bin:/bin",
    )

    # トラック2の前後（トラック1の末尾・トラック3の先頭）を含めた
    # パディング済みサンプル列から、既知のオフセット(+2)でのCRCを
    # あらかじめ計算し、AccurateRipの投稿として仕込む。
    search_range = 10
    pad_frames = search_range + 5
    leading = _combined_track_values(1, track_frame_counts[1])[
        -pad_frames:
    ]
    this_samples = _combined_track_values(2, track_frame_counts[2])
    trailing = _combined_track_values(3, track_frame_counts[3])[
        :pad_frames
    ]
    padded = leading + this_samples + trailing
    known_offset = 2
    target_crc_v1 = _brute_v1(
        padded, len(leading) + known_offset, len(this_samples)
    )

    fake_lookup_result = AccurateRipLookupResult(
        tracks={
            2: [
                AccurateRipTrackEntry(
                    confidence=17, crc_v1=target_crc_v1, crc_v2=0
                )
            ]
        }
    )
    monkeypatch.setattr(
        audio_cd.accuraterip,
        "lookup",
        lambda track_count, ids, **kwargs: fake_lookup_result,
    )

    dest = tmp_path / "out"
    work = tmp_path / "work"
    progress_lines: list[str] = []

    result = rip_and_convert_disc(
        "/dev/rdisk4",
        dest,
        AudioFormat.WAV,
        work,
        on_progress=progress_lines.append,
        verify=True,
        accuraterip_search_range=search_range,
    )

    assert result.ok is True
    outcomes = {t.track_number: t for t in result.tracks}
    assert outcomes[1].accuraterip_confidence is None
    assert outcomes[3].accuraterip_confidence is None
    assert outcomes[2].accuraterip_confidence == 17
    assert result.accuraterip_confirmed_tracks == [2]

    # トラックごとの照合結果がリアルタイムでprogressに流れること
    # （完了メッセージだけでなく、処理中にも詳細が分かるようにするため）。
    assert any(
        "トラック2" in line and "confidence=17" in line
        for line in progress_lines
    )

    # AccurateRip照合のために保持していた一時WAVは、最終的に
    # すべて削除されていること（ディスク容量を無駄に消費しない）。
    assert list(work.glob("*.wav")) == []


def test_audio_rip_worker_lists_confirmed_tracks_in_completion_message(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    qtbot,
) -> None:
    """完了メッセージに、確認できた曲数だけでなくトラック番号の一覧も
    含まれることを検証する（``AudioRipWorker.run()``が組み立てる文言）。
    """
    track_frame_counts = {1: 30, 2: 200, 3: 30}

    def fake_run(cmd, **kwargs):
        class FakeCompletedProcess:
            returncode = 0
            stdout = SAMPLE_QUERY_OUTPUT
            stderr = ""

        return FakeCompletedProcess()

    class _FakeAccurateRipPopen:
        def __init__(self, cmd, **kwargs):
            track_number = int(cmd[-2])
            output_path = Path(cmd[-1])
            _build_wav_with_stereo_samples(
                output_path,
                _track_samples(
                    track_number, track_frame_counts[track_number]
                ),
            )
            self.stdout = iter([])

        def wait(self) -> int:
            return 0

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(subprocess, "Popen", _FakeAccurateRipPopen)
    monkeypatch.setattr(
        audio_cd,
        "effective_path",
        lambda: "/opt/homebrew/bin:/usr/bin:/bin",
    )

    search_range = 10
    pad_frames = search_range + 5
    leading = _combined_track_values(1, track_frame_counts[1])[
        -pad_frames:
    ]
    this_samples = _combined_track_values(2, track_frame_counts[2])
    trailing = _combined_track_values(3, track_frame_counts[3])[
        :pad_frames
    ]
    padded = leading + this_samples + trailing
    target_crc_v1 = _brute_v1(padded, len(leading) + 2, len(this_samples))

    fake_lookup_result = AccurateRipLookupResult(
        tracks={
            2: [
                AccurateRipTrackEntry(
                    confidence=17, crc_v1=target_crc_v1, crc_v2=0
                )
            ]
        }
    )
    monkeypatch.setattr(
        audio_cd.accuraterip,
        "lookup",
        lambda track_count, ids, **kwargs: fake_lookup_result,
    )

    worker = AudioRipWorker(
        "/dev/rdisk4",
        tmp_path / "out",
        AudioFormat.WAV,
        tmp_path / "work",
        verify=True,
        accuraterip_search_range=search_range,
        parent=None,
    )

    results: list[tuple[bool, str]] = []
    worker.finished_ok.connect(
        lambda ok, message: results.append((ok, message))
    )

    with qtbot.waitSignal(worker.finished_ok, timeout=5000):
        worker.start()

    assert results[0][0] is True
    assert "AccurateRipで1/3曲が確認されました" in results[0][1]
    assert "トラック2" in results[0][1]


def test_rip_and_convert_disc_reports_no_match_when_offset_search_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """投稿は見つかったが探索範囲内で一致するオフセットが無い場合、
    「照合はできませんでした」というトラック別の進捗メッセージを出し、
    accuraterip_confidenceはNoneのままにする（「不一致」ではなく
    「未実施/未確認」として扱う）。
    """
    track_frame_counts = {1: 30, 2: 200, 3: 30}

    def fake_run(cmd, **kwargs):
        class FakeCompletedProcess:
            returncode = 0
            stdout = SAMPLE_QUERY_OUTPUT
            stderr = ""

        return FakeCompletedProcess()

    class _FakeAccurateRipPopen:
        def __init__(self, cmd, **kwargs):
            track_number = int(cmd[-2])
            output_path = Path(cmd[-1])
            _build_wav_with_stereo_samples(
                output_path,
                _track_samples(
                    track_number, track_frame_counts[track_number]
                ),
            )
            self.stdout = iter([])

        def wait(self) -> int:
            return 0

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(subprocess, "Popen", _FakeAccurateRipPopen)
    monkeypatch.setattr(
        audio_cd,
        "effective_path",
        lambda: "/opt/homebrew/bin:/usr/bin:/bin",
    )

    # 実在しないCRCを投稿として仕込み、探索範囲内で絶対に一致しないようにする。
    fake_lookup_result = AccurateRipLookupResult(
        tracks={
            2: [
                AccurateRipTrackEntry(
                    confidence=5, crc_v1=0xDEADBEEF, crc_v2=0
                )
            ]
        }
    )
    monkeypatch.setattr(
        audio_cd.accuraterip,
        "lookup",
        lambda track_count, ids, **kwargs: fake_lookup_result,
    )

    dest = tmp_path / "out"
    work = tmp_path / "work"
    progress_lines: list[str] = []

    result = rip_and_convert_disc(
        "/dev/rdisk4",
        dest,
        AudioFormat.WAV,
        work,
        on_progress=progress_lines.append,
        verify=True,
        accuraterip_search_range=10,
    )

    assert result.ok is True
    outcomes = {t.track_number: t for t in result.tracks}
    assert outcomes[2].accuraterip_confidence is None
    assert result.accuraterip_confirmed_tracks == []
    assert any(
        "トラック2" in line and "照合はできませんでした" in line
        for line in progress_lines
    )


def test_rip_and_convert_disc_skips_accuraterip_when_lookup_finds_nothing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_run(cmd, **kwargs):
        class FakeCompletedProcess:
            returncode = 0
            stdout = SAMPLE_QUERY_OUTPUT
            stderr = ""

        return FakeCompletedProcess()

    class _FakePopen:
        def __init__(self, cmd, **kwargs):
            output_path = Path(cmd[-1])
            _build_minimal_wav(output_path)
            self.stdout = iter([])

        def wait(self) -> int:
            return 0

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(subprocess, "Popen", _FakePopen)
    monkeypatch.setattr(
        audio_cd,
        "effective_path",
        lambda: "/opt/homebrew/bin:/usr/bin:/bin",
    )
    monkeypatch.setattr(
        audio_cd.accuraterip,
        "lookup",
        lambda track_count, ids, **kwargs: AccurateRipLookupResult(
            tracks={}
        ),
    )

    dest = tmp_path / "out"
    work = tmp_path / "work"

    result = rip_and_convert_disc(
        "/dev/rdisk4", dest, AudioFormat.WAV, work, verify=True
    )

    assert result.ok is True
    assert all(
        t.accuraterip_confidence is None for t in result.tracks
    )
    assert result.accuraterip_confirmed_tracks == []
    assert list(work.glob("*.wav")) == []


def test_rip_and_convert_disc_does_not_retain_wavs_when_verify_false(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``verify=False``時は、AccurateRip関連の変更前と挙動が変わらない
    （一時WAVは変換直後に即座に削除される）ことを確認する。
    """

    def fake_run(cmd, **kwargs):
        class FakeCompletedProcess:
            returncode = 0
            stdout = SAMPLE_QUERY_OUTPUT
            stderr = ""

        return FakeCompletedProcess()

    class _FakePopen:
        def __init__(self, cmd, **kwargs):
            output_path = Path(cmd[-1])
            _build_minimal_wav(output_path)
            self.stdout = iter([])

        def wait(self) -> int:
            return 0

    lookup_calls: list = []
    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(subprocess, "Popen", _FakePopen)
    monkeypatch.setattr(
        audio_cd,
        "effective_path",
        lambda: "/opt/homebrew/bin:/usr/bin:/bin",
    )
    monkeypatch.setattr(
        audio_cd.accuraterip,
        "lookup",
        lambda *a, **k: lookup_calls.append(True),
    )

    dest = tmp_path / "out"
    work = tmp_path / "work"

    result = rip_and_convert_disc(
        "/dev/rdisk4", dest, AudioFormat.WAV, work, verify=False
    )

    assert result.ok is True
    assert lookup_calls == []  # verify=Falseの場合は照合自体を試みない
    assert all(
        t.accuraterip_confidence is None for t in result.tracks
    )
