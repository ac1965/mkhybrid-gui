"""音楽CDの正確なリッピングとフォーマット変換を行うモジュール。

macOSのCDDAFSマウント（各トラックが仮想的なAIFFファイルとして見える仕組み）は、
ドライブの誤り訂正・C2エラーポインタにアクセスできず、読み取り結果の検証手段もない
ため「正確なリッピング」の要件を満たせない。本モジュールはEAC/XLD相当の精度を得る
ため、以下の外部ツール（Homebrew経由でインストール）に依存する。

    brew install libcdio-paranoia flac

- **読み取り・誤り訂正・再読込**: ``cd-paranoia`` をパラノイアモード（既定、``-Z`` を
  渡さない）で実行する。ドライブのジッター補正・C2エラー利用・セクタ単位の
  再読込はcd-paranoia自体が内部で行う。
- **検証**: 1トラックを独立して複数回（既定2回、一致しなければ最大 ``max_attempts``
  回まで）リッピングし、得られたWAVのSHA-256チェックサムが一致するかどうかで
  「検証済み」を判定する。一致しない場合は最後の読み取り結果を「未検証」として
  採用し、GUI側に警告として報告する。
- **フォーマット変換**: cd-paranoiaの出力（WAV/PCM）を、macOS標準の ``afconvert``
  （ALAC/AIFF/AAC）または ``flac`` コマンド（FLAC）でユーザー選択の形式に変換する。
  WAVはそのまま採用する（変換不要）。

外部コマンドは固定されたHomebrewパスを直接参照せず、アプリケーションの実行環境の
PATHを使用する。Terminalから起動した場合はそのPATHをそのまま利用し、Finder等から
起動した場合はログインシェルからPATHを取得する。

コマンド組み立てと同様、ロジックはUIフレームワークに依存しない関数として実装し、
GUIからの非同期実行のみ ``AudioRipWorker``（QThread）が担う。
"""

from __future__ import annotations

import array
import hashlib
import os
import re
import shutil
import subprocess
import wave
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from enum import Enum
from functools import lru_cache
from pathlib import Path

import mutagen.aiff
import mutagen.flac
import mutagen.id3
import mutagen.mp4
import mutagen.wave

from mkhybrid_gui import accuraterip
from mkhybrid_gui.config import get_config
from mkhybrid_gui.metadata import (
    LEAD_IN_FRAMES,
    AlbumMetadata,
    compute_disc_id,
    sanitize_filename_component,
)
from mkhybrid_gui.subprocess_utils import (
    SAFE_SUBPROCESS_KWARGS,
    resolve_command,
    terminate_with_escalation,
)

#: CD-DA（音楽CD）固定のPCMフォーマット。AccurateRip照合用のサンプル
#: 読み込み（``read_pcm_samples``）が前提とする値。
_ACCURATERIP_SAMPLE_RATE = 44100

ProgressCallback = Callable[[str], None]
ProgressPercentCallback = Callable[[int], None]
ProcessStartedCallback = Callable[["subprocess.Popen[str]"], None]
CancelCheck = Callable[[], bool]

_TRACK_LINE_RE = re.compile(r"^\s*(\d+)\.\s")

#: ``cd-paranoia -Q`` の1トラック分の行から、トラック番号と開始セクタ
#: （"begin"列の生の整数）を取り出す。例:
#:   "  2.    28653 [06:22.03]    17462 [03:52.62]    no   no  2"
#: -> track_number=2, begin_sector=17462
_TRACK_OFFSET_RE = re.compile(
    r"^\s*(\d+)\.\s+\d+\s+\[[^\]]*\]\s+(\d+)\s+\[[^\]]*\]"
)

#: ``TOTAL`` 行から、リードアウト位置（生セクタ）を取り出す。例:
#:   "TOTAL    65960 [14:39.10]    (audio only)"
_TOTAL_LINE_RE = re.compile(r"^TOTAL\s+(\d+)\s+\[[^\]]*\]")


class AudioCdError(RuntimeError):
    """音楽CDのリッピング・変換に失敗した場合に送出する。"""


class RipCancelled(AudioCdError):
    """ユーザーの操作によりリッピング・変換を中断した場合に送出する。"""


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

# フォーマットごとに必要な外部コマンド。
# cd-paranoia は全フォーマット共通で必須。
REQUIRED_TOOLS: dict[AudioFormat, tuple[str, ...]] = {
    AudioFormat.ALAC: ("cd-paranoia", "afconvert"),
    AudioFormat.AIFF: ("cd-paranoia", "afconvert"),
    AudioFormat.FLAC: ("cd-paranoia", "flac"),
    AudioFormat.WAV: ("cd-paranoia",),
    AudioFormat.AAC: ("cd-paranoia", "afconvert"),
}


@lru_cache(maxsize=1)
def effective_path() -> str:
    """外部コマンド実行に使用するPATHを取得する。

    Finder/LaunchServices経由でGUIアプリとして起動された場合でも、
    launchdが ``/usr/bin:/bin:/usr/sbin:/sbin`` 程度の最小限のPATHを
    設定するため、プロセスの ``PATH`` が空になることはほぼない。
    そのため「PATHが空なら」という条件でログインシェルのPATH取得を
    スキップすると、Homebrewのインストール先（``/opt/homebrew/bin`` や
    ``/usr/local/bin``）が常に欠落し、GUI起動時に外部コマンドが
    見つからなくなる。これを避けるため、プロセスのPATHとログイン
    シェルのPATHを常にマージして返す。

    Homebrewインストールの外部コマンドに依存するのはこのモジュール
    だけではないため（``cdrdao.py``も``cdrdao``コマンドの検出に
    この関数を再利用する）、先頭アンダースコアを付けない公開関数と
    している。
    """
    current_path = os.environ.get("PATH", "")

    shell = os.environ.get("SHELL", "/bin/zsh")

    try:
        result = subprocess.run(
            resolve_command([shell, "-lc", 'printf "%s" "$PATH"']),
            capture_output=True,
            text=True,
            check=True,
            env=os.environ.copy(),
            **SAFE_SUBPROCESS_KWARGS,
        )
        shell_path = result.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        shell_path = ""

    merged: list[str] = []

    for path_entry in (*current_path.split(":"), *shell_path.split(":")):
        if path_entry and path_entry not in merged:
            merged.append(path_entry)

    return ":".join(merged) if merged else current_path


def tool_path(tool: str) -> str | None:
    """現在の実行環境で利用可能な外部コマンドのパスを返す。"""
    return shutil.which(tool, path=effective_path())


def command_env() -> dict[str, str]:
    """外部コマンド実行用の環境変数を返す。"""
    env = os.environ.copy()
    env["PATH"] = effective_path()
    return env


def output_extension(audio_format: AudioFormat) -> str:
    """指定フォーマットの出力ファイル拡張子を返す。"""
    return _FILE_EXTENSIONS[audio_format]


def missing_tools(audio_format: AudioFormat) -> list[str]:
    """指定フォーマットの処理に必要な外部コマンドのうち、未インストールのものを返す。"""
    return [
        tool
        for tool in REQUIRED_TOOLS[audio_format]
        if tool_path(tool) is None
    ]


@dataclass(frozen=True)
class CommandResult:
    """外部コマンドの実行結果。"""

    returncode: int
    output: str

    @property
    def ok(self) -> bool:
        return self.returncode == 0


def _run_streaming(
    cmd: list[str],
    on_progress: ProgressCallback | None,
    on_process_started: ProcessStartedCallback | None = None,
) -> CommandResult:
    """外部コマンドをPATH引き継ぎ環境で実行する。

    ``resolve_command``/``SAFE_SUBPROCESS_KWARGS``で実行ファイルを
    絶対パスに解決し``close_fds=False``を指定する。GUIアプリ
    （マルチスレッドのQtプロセス）から`fork()`する際のクラッシュ回避に
    必須（詳細は``subprocess_utils``モジュールのdocstringを参照）。
    """
    proc = subprocess.Popen(
        resolve_command(cmd, path=effective_path()),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        env=command_env(),
        **SAFE_SUBPROCESS_KWARGS,
    )

    if on_process_started is not None:
        on_process_started(proc)

    lines: list[str] = []
    assert proc.stdout is not None

    for line in proc.stdout:
        lines.append(line)
        if on_progress is not None:
            on_progress(line.rstrip("\n"))

    returncode = proc.wait()

    return CommandResult(
        returncode=returncode,
        output="".join(lines),
    )


# --- トラック数の取得 -------------------------------------------------


def parse_track_count(query_output: str) -> int:
    """``cd-paranoia -Q`` の出力（標準エラー）からオーディオトラック数を求める。"""
    track_numbers = {
        int(match.group(1))
        for line in query_output.splitlines()
        if (match := _TRACK_LINE_RE.match(line))
    }

    if not track_numbers:
        raise AudioCdError(
            "音楽CDのトラック情報を取得できませんでした（cd-paranoia -Q）。"
        )

    return max(track_numbers)


def query_track_count(device: str | None = None) -> int:
    """``cd-paranoia -Q`` を実行し、オーディオトラック数を取得する。"""
    cmd = ["cd-paranoia", "-Q"]

    if device:
        cmd += ["-d", device]

    if tool_path("cd-paranoia") is None:
        raise AudioCdError(
            "cd-paranoia が見つかりません。PATHを確認してください。"
        )

    result = subprocess.run(
        resolve_command(cmd, path=effective_path()),
        capture_output=True,
        text=True,
        check=False,
        env=command_env(),
        **SAFE_SUBPROCESS_KWARGS,
    )

    # cd-paranoia -Q は正常時でも終了コードが0以外になることがあるため、
    # 終了コードではなく出力内容の解析可否で成否を判定する。
    return parse_track_count(result.stderr + result.stdout)


# --- TOC（目次情報）の取得・Disc ID計算 ---------------------------------


@dataclass(frozen=True)
class DiscToc:
    """音楽CDのTOC（目次情報）。MusicBrainz Disc ID計算に使う。

    ここでの値は ``cd-paranoia -Q`` が報告する生のセクタ値であり、
    ``metadata.LEAD_IN_FRAMES`` の加算はまだ行っていない
    （加算は ``disc_id_from_toc`` が行う）。
    """

    track_offsets: list[int]
    leadout_offset: int


def parse_disc_toc(query_output: str) -> DiscToc:
    """``cd-paranoia -Q`` の出力から、各トラックの開始位置とリードアウト位置を求める。"""
    offsets_by_track: dict[int, int] = {}

    for line in query_output.splitlines():
        match = _TRACK_OFFSET_RE.match(line)

        if match is not None:
            offsets_by_track[int(match.group(1))] = int(match.group(2))

    if not offsets_by_track:
        raise AudioCdError(
            "音楽CDのTOC情報を取得できませんでした（cd-paranoia -Q）。"
        )

    leadout_offset: int | None = None

    for line in query_output.splitlines():
        match = _TOTAL_LINE_RE.match(line)

        if match is not None:
            leadout_offset = int(match.group(1))
            break

    if leadout_offset is None:
        raise AudioCdError(
            "音楽CDのリードアウト位置を取得できませんでした（cd-paranoia -Q）。"
        )

    track_offsets = [
        offsets_by_track[number] for number in sorted(offsets_by_track)
    ]

    return DiscToc(track_offsets=track_offsets, leadout_offset=leadout_offset)


def disc_id_from_disc_toc(toc: DiscToc) -> str:
    """既に取得済みの ``DiscToc`` からMusicBrainz Disc IDを計算する。

    GUI側でトラック数（テーブルの行数）とDisc IDの両方が必要な場合、
    ``query_disc_toc()`` を1回だけ呼んでこの関数に渡せば、
    ``cd-paranoia -Q`` を二重に実行せずに済む。
    """
    track_offsets = [
        offset + LEAD_IN_FRAMES for offset in toc.track_offsets
    ]
    leadout_offset = toc.leadout_offset + LEAD_IN_FRAMES

    return compute_disc_id(
        first_track=1,
        last_track=len(toc.track_offsets),
        leadout_offset=leadout_offset,
        track_offsets=track_offsets,
    )


def disc_id_from_toc(query_output: str) -> str:
    """``cd-paranoia -Q`` の出力からMusicBrainz Disc IDを直接計算する。"""
    return disc_id_from_disc_toc(parse_disc_toc(query_output))


def query_disc_toc(device: str | None = None) -> DiscToc:
    """``cd-paranoia -Q`` を実行し、TOC（目次情報）を取得する。"""
    cmd = ["cd-paranoia", "-Q"]

    if device:
        cmd += ["-d", device]

    if tool_path("cd-paranoia") is None:
        raise AudioCdError(
            "cd-paranoia が見つかりません。PATHを確認してください。"
        )

    result = subprocess.run(
        resolve_command(cmd, path=effective_path()),
        capture_output=True,
        text=True,
        check=False,
        env=command_env(),
        **SAFE_SUBPROCESS_KWARGS,
    )

    return parse_disc_toc(result.stderr + result.stdout)


def query_disc_id(device: str | None = None) -> str:
    """``cd-paranoia -Q`` を実行し、MusicBrainz Disc IDを計算する。"""
    return disc_id_from_disc_toc(query_disc_toc(device))


# --- リッピング（読み取り・誤り訂正・再読込・検証） -----------------------


def build_rip_command(
    track_number: int,
    output_wav: Path,
    device: str | None = None,
) -> list[str]:
    """``cd-paranoia`` の1トラック分リッピングコマンドを組み立てる。

    ``-Z``（パラノイア無効化）は意図的に指定しない。これによりcd-paranoia既定の
    ジッター補正・誤り訂正・セクタ単位の再読込が常に有効になる。
    """
    cmd = ["cd-paranoia"]

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
    max_attempts: int = get_config().audio_rip.max_attempts,
    on_process_started: ProcessStartedCallback | None = None,
    cancel_check: CancelCheck | None = None,
) -> RipTrackResult:
    """トラックをcd-paranoiaでリッピングし、独立した複数回の読み取り結果が
    一致するかどうかで検証する。

    ``verify`` がFalseの場合は1回だけ読み取り、cd-paranoia自身の誤り訂正・
    再読込のみに頼る（検証は行わない）。

    ``cancel_check`` は各試行の前後で呼び出され、``True`` を返すと
    ``RipCancelled`` を送出して安全に中断する（これまでの試行で作成した
    一時WAVファイルは削除する）。
    """
    attempts_needed = max_attempts if verify else 1
    seen: list[tuple[Path, str]] = []

    def cancelled() -> bool:
        return cancel_check is not None and cancel_check()

    for attempt in range(1, attempts_needed + 1):
        if cancelled():
            for stale_path, _ in seen:
                stale_path.unlink(missing_ok=True)
            raise RipCancelled(
                f"トラック{track_number}のリッピングを中断しました。"
            )

        wav_path = work_dir / f"track{track_number:02d}.attempt{attempt}.wav"

        if on_progress is not None:
            label = f"（{attempt}回目）" if verify else ""
            on_progress(
                f"トラック{track_number}: 読み取り中{label}…"
            )

        result = _run_streaming(
            build_rip_command(track_number, wav_path, device),
            on_progress,
            on_process_started=on_process_started,
        )

        if cancelled():
            wav_path.unlink(missing_ok=True)
            for stale_path, _ in seen:
                stale_path.unlink(missing_ok=True)
            raise RipCancelled(
                f"トラック{track_number}のリッピングを中断しました。"
            )

        if not result.ok or not wav_path.exists():
            continue

        if not verify:
            return RipTrackResult(
                track_number=track_number,
                wav_path=wav_path,
                verified=False,
                attempts=1,
            )

        checksum = _sha256_of_file(wav_path)

        for other_path, other_checksum in seen:
            if other_checksum == checksum:
                if on_progress is not None:
                    on_progress(
                        f"トラック{track_number}: "
                        "読み取り結果が一致し検証されました。"
                    )

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
            track_number=track_number,
            wav_path=kept_path,
            verified=False,
            attempts=len(seen),
        )

    raise AudioCdError(
        f"トラック{track_number}のリッピングに失敗しました。"
    )


# --- フォーマット変換 ---------------------------------------------------


def build_convert_command(
    source_wav: Path,
    target_path: Path,
    audio_format: AudioFormat,
) -> list[str]:
    """WAVを指定フォーマットへ変換するコマンドを組み立てる。WAVは変換不要のため対象外。"""
    if audio_format == AudioFormat.ALAC:
        return [
            "afconvert",
            "-f",
            "m4af",
            "-d",
            "alac",
            str(source_wav),
            str(target_path),
        ]

    if audio_format == AudioFormat.AIFF:
        return [
            "afconvert",
            "-f",
            "AIFF",
            "-d",
            "BEI16",
            str(source_wav),
            str(target_path),
        ]

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
        return [
            "flac",
            "--silent",
            "--force",
            "-o",
            str(target_path),
            str(source_wav),
        ]

    raise ValueError(f"変換不要なフォーマットです: {audio_format}")


def convert_audio(
    source_wav: Path,
    target_path: Path,
    audio_format: AudioFormat,
    on_progress: ProgressCallback | None = None,
    on_process_started: ProcessStartedCallback | None = None,
    cancel_check: CancelCheck | None = None,
) -> None:
    """``source_wav`` を ``audio_format`` に変換し ``target_path`` に書き出す。"""
    if audio_format == AudioFormat.WAV:
        shutil.copyfile(source_wav, target_path)
        return

    if cancel_check is not None and cancel_check():
        raise RipCancelled("フォーマット変換を中断しました。")

    cmd = build_convert_command(
        source_wav,
        target_path,
        audio_format,
    )

    result = _run_streaming(
        cmd,
        on_progress,
        on_process_started=on_process_started,
    )

    if cancel_check is not None and cancel_check():
        target_path.unlink(missing_ok=True)
        raise RipCancelled("フォーマット変換を中断しました。")

    if not result.ok:
        raise AudioCdError(
            f"フォーマット変換に失敗しました（{audio_format.value}）: "
            f"{result.output}"
        )


# --- メタデータのタグ書き込み --------------------------------------------


def write_metadata_tags(
    path: Path,
    audio_format: AudioFormat,
    album: AlbumMetadata,
    track_number: int,
) -> None:
    """変換済みファイルに ``mutagen`` でメタデータタグを書き込む。

    アルバム名・アーティスト名・トラックタイトルがいずれも未入力
    （空文字）の場合は何もしない。トラックタイトルだけが未入力の場合、
    そのトラックのタイトルは書き込まない（アルバム名・アーティスト名のみ
    書き込む）。
    """
    title = album.track_title(track_number)

    if not (album.album or album.artist or title):
        return

    track_total = len(album.tracks) or None

    if audio_format in (AudioFormat.ALAC, AudioFormat.AAC):
        audio = mutagen.mp4.MP4(str(path))

        if album.album:
            audio["\xa9alb"] = [album.album]
        if album.artist:
            audio["\xa9ART"] = [album.artist]
        if title:
            audio["\xa9nam"] = [title]
        if album.year:
            audio["\xa9day"] = [album.year]

        audio["trkn"] = [(track_number, track_total or 0)]
        audio.save()
        return

    if audio_format == AudioFormat.FLAC:
        audio = mutagen.flac.FLAC(str(path))

        if album.album:
            audio["ALBUM"] = album.album
        if album.artist:
            audio["ARTIST"] = album.artist
        if title:
            audio["TITLE"] = title
        if album.year:
            audio["DATE"] = album.year

        audio["TRACKNUMBER"] = str(track_number)
        audio.save()
        return

    # WAV / AIFF: ID3v2タグをチャンクとして埋め込む。
    audio_cls = (
        mutagen.wave.WAVE
        if audio_format == AudioFormat.WAV
        else mutagen.aiff.AIFF
    )
    audio = audio_cls(str(path))

    if audio.tags is None:
        audio.add_tags()

    if album.album:
        audio.tags.add(mutagen.id3.TALB(encoding=3, text=[album.album]))
    if album.artist:
        audio.tags.add(mutagen.id3.TPE1(encoding=3, text=[album.artist]))
    if title:
        audio.tags.add(mutagen.id3.TIT2(encoding=3, text=[title]))
    if album.year:
        audio.tags.add(mutagen.id3.TDRC(encoding=3, text=[album.year]))

    audio.tags.add(
        mutagen.id3.TRCK(encoding=3, text=[str(track_number)])
    )
    audio.save()


# --- AccurateRip照合用のPCMサンプル読み込み -------------------------------


def read_pcm_samples(
    wav_path: Path,
    start_frame: int = 0,
    num_frames: int | None = None,
) -> list[int]:
    """WAVファイルからAccurateRip形式のサンプル列を読み込む。

    音楽CD（44.1kHz/16bit/ステレオ、CD-DA固定フォーマット）を前提とする。
    各サンプルは左右チャンネル（16bit符号あり→符号なし変換）を
    ``(right << 16) | left``として結合した32bit値
    （``accuraterip.search_offset_v1``が要求する形式、実機データで
    動作確認済み）。
    """
    with wave.open(str(wav_path), "rb") as wav_file:
        if wav_file.getnchannels() != 2:
            raise AudioCdError(
                f"想定外のチャンネル数です: {wav_file.getnchannels()}"
            )
        if wav_file.getsampwidth() != 2:
            raise AudioCdError(
                f"想定外のサンプル幅です: {wav_file.getsampwidth()}"
            )
        if wav_file.getframerate() != _ACCURATERIP_SAMPLE_RATE:
            raise AudioCdError(
                f"想定外のサンプルレートです: {wav_file.getframerate()}"
            )

        if start_frame:
            wav_file.setpos(start_frame)

        frames_to_read = (
            wav_file.getnframes() - start_frame
            if num_frames is None
            else num_frames
        )
        raw = wav_file.readframes(frames_to_read)

    interleaved = array.array("h")
    interleaved.frombytes(raw)

    samples = [0] * (len(interleaved) // 2)

    for i in range(len(samples)):
        left = interleaved[2 * i] & 0xFFFF
        right = interleaved[2 * i + 1] & 0xFFFF
        samples[i] = (right << 16) | left

    return samples


# --- ディスク全体のリッピング -------------------------------------------


@dataclass(frozen=True)
class TrackOutcome:
    track_number: int
    output_path: Path
    verified: bool
    #: AccurateRipで一致が確認できた場合の信頼度（投稿件数）。
    #: ``None``は「未実施」（先頭/最終トラック、ディスク未登録、
    #: 探索範囲内で一致無し等）を意味し、「不一致」とは区別する。
    accuraterip_confidence: int | None = None


@dataclass(frozen=True)
class RipResult:
    """ディスク全体のリッピング結果。"""

    tracks: list[TrackOutcome] = field(default_factory=list)
    failed_tracks: list[tuple[int, str]] = field(default_factory=list)
    cancelled: bool = False
    #: トラックファイルを実際に書き出したディレクトリ。アルバム名が
    #: 入力されていれば、指定した出力先フォルダ直下ではなく、その中の
    #: アルバム名サブディレクトリになる。
    output_directory: Path = field(default_factory=Path)

    @property
    def ok(self) -> bool:
        return not self.failed_tracks

    @property
    def unverified_tracks(self) -> list[int]:
        return [
            track.track_number
            for track in self.tracks
            if not track.verified
        ]

    @property
    def accuraterip_confirmed_tracks(self) -> list[int]:
        """AccurateRipで一致が確認できたトラック番号一覧。"""
        return [
            track.track_number
            for track in self.tracks
            if track.accuraterip_confidence is not None
        ]


def rip_and_convert_disc(
    device: str,
    destination_dir: str | Path,
    audio_format: AudioFormat,
    work_dir: str | Path,
    on_progress: ProgressCallback | None = None,
    *,
    verify: bool = True,
    max_attempts: int = get_config().audio_rip.max_attempts,
    on_percent: ProgressPercentCallback | None = None,
    on_process_started: ProcessStartedCallback | None = None,
    cancel_check: CancelCheck | None = None,
    album_metadata: AlbumMetadata | None = None,
    fallback_folder_name: str | None = None,
    disc_toc: DiscToc | None = None,
    accuraterip_search_range: int = get_config().accuraterip.search_range_samples,
) -> RipResult:
    """音楽CDの全トラックをリッピングし、指定フォーマットで
    ``destination_dir`` に書き出す。

    ``verify`` が ``True`` の場合、各トラックの自己一致検証に加えて、
    リッピング完了後に一括でAccurateRip照合を試みる（詳細は
    ``docs/design/accuraterip.md``を参照）。``disc_toc``を渡すと
    （呼び出し側が既にTOCを取得済みの場合）、AccurateRip用の
    ``cd-paranoia -Q``の再実行を省略できる。ディスクの最初・最後の
    トラックは照合の対象外（端点のトリミング規則が実データで未確認の
    ため）。ディスクがAccurateRipに登録されていない・ネットワーク
    エラーの場合もベストエフォートで無視し、リッピング自体の成否には
    影響させない。

    ``on_percent`` にはトラック単位の粗い進捗率（0〜100）を通知する。
    ``cancel_check`` が ``True`` を返した時点で、以降のトラック処理を
    行わずに安全に打ち切る（``RipResult.cancelled`` が ``True`` になる）。

    ``album_metadata`` が指定され、該当トラックにタイトルが入力されている
    場合、出力ファイル名は ``NN - タイトル.ext`` になり、``mutagen`` で
    タグ（アルバム名・アーティスト名・トラック名・年）を書き込む。
    ``album_metadata`` が ``None``、またはタイトル未入力の場合は従来通り
    ``TrackNN.ext`` のままタグ付けは行わない。

    ``destination_dir`` 直下に複数回のリッピング結果が無秩序に混在しない
    よう、必ず何らかのサブディレクトリの中にトラックファイルを書き出す。
    サブディレクトリ名は、アルバム名が入力されていればそれを使い
    （ファイル名として安全な文字列に変換）、未入力の場合は
    ``fallback_folder_name``（通常はディスクのボリューム名、例:
    「Audio CD」）を使う。``fallback_folder_name`` も指定されない場合に
    限り、従来通り ``destination_dir`` 直下に書き出す。
    """
    dest = Path(destination_dir)

    if album_metadata is not None and album_metadata.album:
        folder_name: str | None = album_metadata.album
    else:
        folder_name = fallback_folder_name

    if folder_name:
        dest = dest / sanitize_filename_component(folder_name)

    dest.mkdir(parents=True, exist_ok=True)

    work = Path(work_dir)
    work.mkdir(parents=True, exist_ok=True)

    track_count = query_track_count(device)

    tracks: list[TrackOutcome] = []
    failed: list[tuple[int, str]] = []
    cancelled = False
    #: verify=True の場合のみ、変換後も即座に削除せず保持しておく
    #: トラック番号→WAVパス（AccurateRip照合が前後トラックの境界
    #: サンプルを必要とするため）。verify=False時は空のまま
    #: （従来どおり即座に削除、挙動変更なし）。
    retained_wav_paths: dict[int, Path] = {}

    for track_number in range(1, track_count + 1):
        if cancel_check is not None and cancel_check():
            cancelled = True
            break

        if on_percent is not None:
            on_percent(round((track_number - 1) / track_count * 100))

        if on_progress is not None:
            on_progress(
                f"[{track_number}/{track_count}] "
                f"トラック{track_number}を処理しています…"
            )

        try:
            rip_result = rip_track_verified(
                track_number,
                work,
                device,
                on_progress,
                verify=verify,
                max_attempts=max_attempts,
                on_process_started=on_process_started,
                cancel_check=cancel_check,
            )
        except RipCancelled:
            cancelled = True
            break
        except AudioCdError as exc:
            failed.append((track_number, str(exc)))
            continue

        track_title = (
            album_metadata.track_title(track_number)
            if album_metadata is not None
            else ""
        )
        ext = output_extension(audio_format)

        if track_title:
            sanitized_title = sanitize_filename_component(track_title)
            filename = f"{track_number:02d} - {sanitized_title}{ext}"
        else:
            filename = f"Track{track_number:02d}{ext}"

        target = dest / filename

        try:
            convert_audio(
                rip_result.wav_path,
                target,
                audio_format,
                on_progress,
                on_process_started=on_process_started,
                cancel_check=cancel_check,
            )
        except RipCancelled:
            cancelled = True
            break
        except AudioCdError as exc:
            failed.append((track_number, str(exc)))
            continue
        finally:
            if verify:
                retained_wav_paths[track_number] = rip_result.wav_path
            else:
                rip_result.wav_path.unlink(missing_ok=True)

        if album_metadata is not None:
            try:
                write_metadata_tags(
                    target, audio_format, album_metadata, track_number
                )
            except Exception as exc:  # noqa: BLE001
                if on_progress is not None:
                    on_progress(
                        f"警告: トラック{track_number}のタグ書き込みに"
                        f"失敗しました: {exc}"
                    )

        tracks.append(
            TrackOutcome(
                track_number=track_number,
                output_path=target,
                verified=rip_result.verified,
            )
        )

    if on_percent is not None and not cancelled:
        on_percent(100)

    accuraterip_confidences: dict[int, int] = {}

    if verify:
        try:
            if not cancelled and tracks:
                try:
                    toc = (
                        disc_toc
                        if disc_toc is not None
                        else query_disc_toc(device)
                    )
                    ids = accuraterip.compute_ids(
                        toc.track_offsets, toc.leadout_offset
                    )
                    lookup_result = accuraterip.lookup(
                        len(toc.track_offsets), ids
                    )

                    # ディスクの最初・最後のトラックは、端点の
                    # トリミング規則が実データで未確認のため対象外
                    # （docs/design/accuraterip.md 4.2節を参照）。
                    if lookup_result.ok and lookup_result.tracks:
                        if on_progress is not None:
                            on_progress("AccurateRipに照合しています…")

                        pad_frames = accuraterip_search_range + 100

                        for target_track in range(
                            2, len(toc.track_offsets)
                        ):
                            candidates = lookup_result.tracks.get(
                                target_track
                            )
                            prev_wav = retained_wav_paths.get(
                                target_track - 1
                            )
                            this_wav = retained_wav_paths.get(
                                target_track
                            )
                            next_wav = retained_wav_paths.get(
                                target_track + 1
                            )

                            if not candidates or not (
                                prev_wav and this_wav and next_wav
                            ):
                                if on_progress is not None and not candidates:
                                    on_progress(
                                        f"トラック{target_track}: "
                                        "AccurateRipに投稿がありません。"
                                    )
                                continue

                            leading = read_pcm_samples(prev_wav)[
                                -pad_frames:
                            ]
                            this_samples = read_pcm_samples(this_wav)
                            trailing = read_pcm_samples(
                                next_wav, 0, pad_frames
                            )
                            padded = leading + this_samples + trailing

                            match = accuraterip.search_offset_v1(
                                padded,
                                len(leading),
                                len(this_samples),
                                candidates,
                                accuraterip_search_range,
                            )

                            if match is not None:
                                accuraterip_confidences[target_track] = (
                                    match.confidence
                                )

                                if on_progress is not None:
                                    on_progress(
                                        f"トラック{target_track}: "
                                        "AccurateRipで確認されました"
                                        f"（confidence={match.confidence}、"
                                        f"オフセット{match.offset:+d}）。"
                                    )
                            elif on_progress is not None:
                                on_progress(
                                    f"トラック{target_track}: "
                                    "AccurateRipでの照合はできませんでした。"
                                )
                except Exception:  # noqa: BLE001
                    # ディスク未登録・ネットワークエラー・WAV読み込みの
                    # 想定外の失敗等はベストエフォートで無視し、
                    # リッピング自体は成功として扱う
                    # （musicbrainzのオンライン検索と同じ方針）。
                    pass
        finally:
            for wav_path in retained_wav_paths.values():
                wav_path.unlink(missing_ok=True)

    if accuraterip_confidences:
        tracks = [
            replace(
                track,
                accuraterip_confidence=accuraterip_confidences.get(
                    track.track_number
                ),
            )
            for track in tracks
        ]

    return RipResult(
        tracks=tracks,
        failed_tracks=failed,
        cancelled=cancelled,
        output_directory=dest,
    )


try:
    from PySide6.QtCore import QThread, Signal
except ImportError:  # pragma: no cover - PySide6未インストール時
    QThread = None  # type: ignore[assignment,misc]


if QThread is not None:

    class AudioRipWorker(QThread):  # type: ignore[misc]
        """音楽CDのリッピング・変換をバックグラウンドスレッドで実行するワーカー。"""

        progress = Signal(str)
        progress_percent = Signal(int)
        finished_ok = Signal(bool, str)

        def __init__(
            self,
            device: str,
            destination_dir: str | Path,
            audio_format: AudioFormat,
            work_dir: str | Path,
            verify: bool = True,
            album_metadata: AlbumMetadata | None = None,
            fallback_folder_name: str | None = None,
            disc_toc: DiscToc | None = None,
            accuraterip_search_range: int = get_config().accuraterip.search_range_samples,
            parent=None,
        ) -> None:
            super().__init__(parent)
            self._device = device
            self._destination_dir = destination_dir
            self._audio_format = audio_format
            self._work_dir = work_dir
            self._verify = verify
            self._album_metadata = album_metadata
            self._accuraterip_search_range = accuraterip_search_range
            self._fallback_folder_name = fallback_folder_name
            self._disc_toc = disc_toc
            self._process: subprocess.Popen[str] | None = None
            self._cancel_requested = False

        def request_cancel(self) -> None:
            """実行中の外部コマンドを安全に中断する。

            GUIスレッドから呼び出される想定。次にトラック/試行の境界へ
            達した時点で処理を打ち切るほか、実行中のプロセスへも
            ``terminate`` を送り、ブロッキングしている出力読み取りを
            速やかに終了させる。``terminate`` を無視するコマンドが
            相手でもハングし続けないよう、一定時間後に ``kill`` へ
            自動的にエスカレーションする（``terminate_with_escalation``、
            詳細は ``subprocess_utils`` モジュールのdocstringを参照）。
            """
            self._cancel_requested = True

            process = self._process
            if process is not None and process.poll() is None:
                terminate_with_escalation(process)

        def _capture_process(self, process: subprocess.Popen[str]) -> None:
            self._process = process

        def _is_cancelled(self) -> bool:
            return self._cancel_requested

        def run(self) -> None:  # noqa: D102 - QThreadのオーバーライド
            try:
                result = rip_and_convert_disc(
                    self._device,
                    self._destination_dir,
                    self._audio_format,
                    self._work_dir,
                    on_progress=self.progress.emit,
                    verify=self._verify,
                    on_percent=self.progress_percent.emit,
                    on_process_started=self._capture_process,
                    cancel_check=self._is_cancelled,
                    album_metadata=self._album_metadata,
                    fallback_folder_name=self._fallback_folder_name,
                    disc_toc=self._disc_toc,
                    accuraterip_search_range=self._accuraterip_search_range,
                )
            except AudioCdError as exc:
                self.finished_ok.emit(False, str(exc))
                return

            if result.cancelled:
                self.finished_ok.emit(
                    False,
                    "ユーザーの操作により中断しました。",
                )
                return

            if not result.ok:
                details = "; ".join(
                    f"トラック{number}: {message}"
                    for number, message in result.failed_tracks
                )
                self.finished_ok.emit(
                    False,
                    f"一部のトラックの処理に失敗しました: {details}",
                )
                return

            message = (
                f"{len(result.tracks)}曲を書き出しました。"
                f"（保存先: {result.output_directory}）"
            )

            if self._verify and result.unverified_tracks:
                unverified = "、".join(
                    str(number)
                    for number in result.unverified_tracks
                )
                message += (
                    f"（トラック{unverified}は複数回読み取っても"
                    "一致せず未検証です）"
                )

            if self._verify and result.accuraterip_confirmed_tracks:
                confirmed = "、".join(
                    str(number)
                    for number in result.accuraterip_confirmed_tracks
                )
                message += (
                    f"（AccurateRipで"
                    f"{len(result.accuraterip_confirmed_tracks)}/"
                    f"{len(result.tracks)}曲が確認されました: "
                    f"トラック{confirmed}）"
                )

            self.finished_ok.emit(True, message)
