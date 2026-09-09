"""音楽CDの正確なリッピングとフォーマット変換を行うモジュール。

macOSのCDDAFSマウント（各トラックが仮想的なAIFFファイルとして見える仕組み）は、
ドライブの誤り訂正・C2エラーポインタにアクセスできず、読み取り結果の検証手段もない
ため「正確なリッピング」の要件を満たせない。本モジュールはEAC/XLD相当の精度を得る
ため、以下の外部ツール（Homebrew経由でインストール）に依存する。

    brew install cdparanoia flac

- **読み取り・誤り訂正・再読込**: ``cdparanoia`` をパラノイアモード（既定、``-Z`` を
  渡さない）で実行する。ドライブのジッター補正・C2エラー利用・セクタ単位の
  再読込はcdparanoia自体が内部で行う。
- **検証**: 1トラックを独立して複数回（既定2回、一致しなければ最大 ``max_attempts``
  回まで）リッピングし、得られたWAVのSHA-256チェックサムが一致するかどうかで
  「検証済み」を判定する。一致しない場合は最後の読み取り結果を「未検証」として
  採用し、GUI側に警告として報告する。
- **フォーマット変換**: cdparanoiaの出力（WAV/PCM）を、macOS標準の ``afconvert``
  （ALAC/AIFF/AAC）または ``flac`` コマンド（FLAC）でユーザー選択の形式に変換する。
  WAVはそのまま採用する（変換不要）。

コマンド組み立てと同様、ロジックはUIフレームワークに依存しない関数として実装し、
GUIからの非同期実行のみ ``AudioRipWorker``（QThread）が担う。
"""

from __future__ import annotations

import hashlib
import re
import shutil
import subprocess
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

ProgressCallback = Callable[[str], None]

_TRACK_LINE_RE = re.compile(r"^\s*(\d+)\.\s")


class AudioCdError(RuntimeError):
    """音楽CDのリッピング・変換に失敗した場合に送出する。"""


class AudioFormat(str, Enum):
    """書き出し先のオーディオ形式。"""

    ALAC = "Apple Lossless (ALAC)"
    AIFF = "AIFF（非圧縮）"
    FLAC = "FLAC（可逆圧縮）"
    WAV = "WAV（非圧縮PCM）"
    AAC = "AAC（容量優先）"


_FILE_EXTENSIONS: dict[AudioFormat, str] = {
    AudioFormat.ALAC: ".m4a",
    AudioFormat.AIFF: ".aiff",
    AudioFormat.FLAC: ".flac",
    AudioFormat.WAV: ".wav",
    AudioFormat.AAC: ".m4a",
}

# フォーマットごとに必要な外部コマンド。cdparanoiaは全フォーマット共通で必須。
REQUIRED_TOOLS: dict[AudioFormat, tuple[str, ...]] = {
    AudioFormat.ALAC: ("cdparanoia", "afconvert"),
    AudioFormat.AIFF: ("cdparanoia", "afconvert"),
    AudioFormat.FLAC: ("cdparanoia", "flac"),
    AudioFormat.WAV: ("cdparanoia",),
    AudioFormat.AAC: ("cdparanoia", "afconvert"),
}


def output_extension(audio_format: AudioFormat) -> str:
    """指定フォーマットの出力ファイル拡張子を返す。"""
    return _FILE_EXTENSIONS[audio_format]


def missing_tools(audio_format: AudioFormat) -> list[str]:
    """指定フォーマットの処理に必要な外部コマンドのうち、未インストールのものを返す。"""
    return [tool for tool in REQUIRED_TOOLS[audio_format] if shutil.which(tool) is None]


@dataclass(frozen=True)
class CommandResult:
    """外部コマンドの実行結果。"""

    returncode: int
    output: str

    @property
    def ok(self) -> bool:
        return self.returncode == 0


def _run_streaming(cmd: list[str], on_progress: ProgressCallback | None) -> CommandResult:
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    lines: list[str] = []
    assert proc.stdout is not None
    for line in proc.stdout:
        lines.append(line)
        if on_progress is not None:
            on_progress(line.rstrip("\n"))
    returncode = proc.wait()
    return CommandResult(returncode=returncode, output="".join(lines))


# --- トラック数の取得 -------------------------------------------------


def parse_track_count(query_output: str) -> int:
    """``cdparanoia -Q`` の出力（標準エラー）からオーディオトラック数を求める。"""
    track_numbers = {
        int(match.group(1))
        for line in query_output.splitlines()
        if (match := _TRACK_LINE_RE.match(line))
    }
    if not track_numbers:
        raise AudioCdError("音楽CDのトラック情報を取得できませんでした（cdparanoia -Q）。")
    return max(track_numbers)


def query_track_count(device: str | None = None) -> int:
    """``cdparanoia -Q`` を実行し、オーディオトラック数を取得する。"""
    cmd = ["cdparanoia", "-Q"]
    if device:
        cmd += ["-d", device]
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    # cdparanoia -Q は正常時でも終了コードが0以外になることがあるため、
    # 終了コードではなく出力内容の解析可否で成否を判定する。
    return parse_track_count(result.stderr + result.stdout)


# --- リッピング（読み取り・誤り訂正・再読込・検証） -----------------------


def build_rip_command(
    track_number: int, output_wav: Path, device: str | None = None
) -> list[str]:
    """``cdparanoia`` の1トラック分リッピングコマンドを組み立てる。

    ``-Z``（パラノイア無効化）は意図的に指定しない。これによりcdparanoia既定の
    ジッター補正・誤り訂正・セクタ単位の再読込が常に有効になる。
    """
    cmd = ["cdparanoia"]
    if device:
        cmd += ["-d", device]
    cmd += [str(track_number), str(output_wav)]
    return cmd


def _sha256_of_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


@dataclass(frozen=True)
class RipTrackResult:
    """1トラック分のリッピング結果。"""

    track_number: int
    wav_path: Path
    verified: bool
    attempts: int


def rip_track_verified(
    track_number: int,
    work_dir: Path,
    device: str | None = None,
    on_progress: ProgressCallback | None = None,
    *,
    verify: bool = True,
    max_attempts: int = 3,
) -> RipTrackResult:
    """トラックをcdparanoiaでリッピングし、独立した複数回の読み取り結果が
    一致するかどうかで検証する。

    ``verify`` がFalseの場合は1回だけ読み取り、cdparanoia自身の誤り訂正・
    再読込のみに頼る（検証は行わない）。
    """
    attempts_needed = max_attempts if verify else 1
    seen: list[tuple[Path, str]] = []

    for attempt in range(1, attempts_needed + 1):
        wav_path = work_dir / f"track{track_number:02d}.attempt{attempt}.wav"
        if on_progress is not None:
            label = f"（{attempt}回目）" if verify else ""
            on_progress(f"トラック{track_number}: 読み取り中{label}…")

        result = _run_streaming(
            build_rip_command(track_number, wav_path, device), on_progress
        )
        if not result.ok or not wav_path.exists():
            continue

        if not verify:
            return RipTrackResult(
                track_number=track_number, wav_path=wav_path, verified=False, attempts=1
            )

        checksum = _sha256_of_file(wav_path)
        for other_path, other_checksum in seen:
            if other_checksum == checksum:
                if on_progress is not None:
                    on_progress(f"トラック{track_number}: 読み取り結果が一致し検証されました。")
                if wav_path != other_path:
                    wav_path.unlink(missing_ok=True)
                for stale_path, _ in seen:
                    if stale_path != other_path:
                        stale_path.unlink(missing_ok=True)
                return RipTrackResult(
                    track_number=track_number,
                    wav_path=other_path,
                    verified=True,
                    attempts=attempt,
                )
        seen.append((wav_path, checksum))

    if seen:
        if on_progress is not None:
            on_progress(
                f"警告: トラック{track_number}は{len(seen)}回読み取っても一致せず、"
                "未検証のまま採用します。"
            )
        kept_path, _ = seen[-1]
        for stale_path, _ in seen[:-1]:
            stale_path.unlink(missing_ok=True)
        return RipTrackResult(
            track_number=track_number, wav_path=kept_path, verified=False, attempts=len(seen)
        )

    raise AudioCdError(f"トラック{track_number}のリッピングに失敗しました。")


# --- フォーマット変換 ---------------------------------------------------


def build_convert_command(
    source_wav: Path, target_path: Path, audio_format: AudioFormat
) -> list[str]:
    """WAVを指定フォーマットへ変換するコマンドを組み立てる。WAVは変換不要のため対象外。"""
    if audio_format == AudioFormat.ALAC:
        return ["afconvert", "-f", "m4af", "-d", "alac", str(source_wav), str(target_path)]
    if audio_format == AudioFormat.AIFF:
        return ["afconvert", "-f", "AIFF", "-d", "BEI16", str(source_wav), str(target_path)]
    if audio_format == AudioFormat.AAC:
        return [
            "afconvert",
            "-f",
            "m4af",
            "-d",
            "aac",
            "-b",
            "256000",
            str(source_wav),
            str(target_path),
        ]
    if audio_format == AudioFormat.FLAC:
        return ["flac", "--silent", "--force", "-o", str(target_path), str(source_wav)]
    raise ValueError(f"変換不要なフォーマットです: {audio_format}")


def convert_audio(
    source_wav: Path,
    target_path: Path,
    audio_format: AudioFormat,
    on_progress: ProgressCallback | None = None,
) -> None:
    """``source_wav`` を ``audio_format`` に変換し ``target_path`` に書き出す。"""
    if audio_format == AudioFormat.WAV:
        shutil.copyfile(source_wav, target_path)
        return

    cmd = build_convert_command(source_wav, target_path, audio_format)
    result = _run_streaming(cmd, on_progress)
    if not result.ok:
        raise AudioCdError(f"フォーマット変換に失敗しました（{audio_format.value}）: {result.output}")


# --- ディスク全体のリッピング -------------------------------------------


@dataclass(frozen=True)
class TrackOutcome:
    track_number: int
    output_path: Path
    verified: bool


@dataclass(frozen=True)
class RipResult:
    """ディスク全体のリッピング結果。"""

    tracks: list[TrackOutcome] = field(default_factory=list)
    failed_tracks: list[tuple[int, str]] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.failed_tracks

    @property
    def unverified_tracks(self) -> list[int]:
        return [t.track_number for t in self.tracks if not t.verified]


def rip_and_convert_disc(
    device: str,
    destination_dir: str | Path,
    audio_format: AudioFormat,
    work_dir: str | Path,
    on_progress: ProgressCallback | None = None,
    *,
    verify: bool = True,
    max_attempts: int = 3,
) -> RipResult:
    """音楽CDの全トラックをリッピングし、指定フォーマットで ``destination_dir`` に書き出す。"""
    dest = Path(destination_dir)
    dest.mkdir(parents=True, exist_ok=True)
    work = Path(work_dir)
    work.mkdir(parents=True, exist_ok=True)

    track_count = query_track_count(device)

    tracks: list[TrackOutcome] = []
    failed: list[tuple[int, str]] = []

    for track_number in range(1, track_count + 1):
        if on_progress is not None:
            on_progress(f"[{track_number}/{track_count}] トラック{track_number}を処理しています…")
        try:
            rip_result = rip_track_verified(
                track_number,
                work,
                device,
                on_progress,
                verify=verify,
                max_attempts=max_attempts,
            )
        except AudioCdError as exc:
            failed.append((track_number, str(exc)))
            continue

        target = dest / f"Track{track_number:02d}{output_extension(audio_format)}"
        try:
            convert_audio(rip_result.wav_path, target, audio_format, on_progress)
        except AudioCdError as exc:
            failed.append((track_number, str(exc)))
            continue
        finally:
            rip_result.wav_path.unlink(missing_ok=True)

        tracks.append(
            TrackOutcome(track_number=track_number, output_path=target, verified=rip_result.verified)
        )

    return RipResult(tracks=tracks, failed_tracks=failed)


try:
    from PySide6.QtCore import QThread, Signal
except ImportError:  # pragma: no cover - PySide6未インストール時はAudioRipWorkerを提供しない
    QThread = None  # type: ignore[assignment,misc]


if QThread is not None:

    class AudioRipWorker(QThread):  # type: ignore[misc]
        """音楽CDのリッピング・変換をバックグラウンドスレッドで実行するワーカー。"""

        progress = Signal(str)
        finished_ok = Signal(bool, str)

        def __init__(
            self,
            device: str,
            destination_dir: str | Path,
            audio_format: AudioFormat,
            work_dir: str | Path,
            verify: bool = True,
            parent=None,
        ) -> None:
            super().__init__(parent)
            self._device = device
            self._destination_dir = destination_dir
            self._audio_format = audio_format
            self._work_dir = work_dir
            self._verify = verify

        def run(self) -> None:  # noqa: D102 - QThreadのオーバーライド
            try:
                result = rip_and_convert_disc(
                    self._device,
                    self._destination_dir,
                    self._audio_format,
                    self._work_dir,
                    on_progress=self.progress.emit,
                    verify=self._verify,
                )
            except AudioCdError as exc:
                self.finished_ok.emit(False, str(exc))
                return

            if not result.ok:
                details = "; ".join(f"トラック{n}: {msg}" for n, msg in result.failed_tracks)
                self.finished_ok.emit(False, f"一部のトラックの処理に失敗しました: {details}")
                return

            message = f"{len(result.tracks)}曲を書き出しました。"
            if self._verify and result.unverified_tracks:
                unverified = "、".join(str(n) for n in result.unverified_tracks)
                message += f"（トラック{unverified}は複数回読み取っても一致せず未検証です）"
            self.finished_ok.emit(True, message)
