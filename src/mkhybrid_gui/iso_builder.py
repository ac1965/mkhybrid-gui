"""``hdiutil makehybrid`` / 検証(attach + ``diskutil verifyVolume``) のラッパー。

コマンド組み立てと実行のロジックはUIフレームワークに依存しない関数として実装し、
GUIからの非同期実行のみ ``IsoWorker``（QThread）が担う。
"""

from __future__ import annotations

import plistlib
import re
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

ProgressCallback = Callable[[str], None]
ProgressPercentCallback = Callable[[int], None]
ProcessStartedCallback = Callable[["subprocess.Popen[str]"], None]


@dataclass(frozen=True)
class IsoOptions:
    """イメージ作成オプション。

    ``udf`` はDVD/Blu-ray（BDXL・M-DISCを含む）の大容量メディアで、
    ISO9660の4GBファイルサイズ上限を回避するために使用する。
    """

    iso: bool = True
    joliet: bool = True
    rock: bool = True
    udf: bool = False


@dataclass(frozen=True)
class CommandResult:
    """外部コマンドの実行結果。"""

    returncode: int
    stderr: str

    @property
    def ok(self) -> bool:
        return self.returncode == 0


def build_makehybrid_command(
    source: str | Path,
    output_path: str | Path,
    options: IsoOptions | None = None,
) -> list[str]:
    """``hdiutil makehybrid`` のコマンド引数リストを組み立てる。

    パスにスペースや日本語が含まれても安全なよう、常にリストで返す
    （``shell=True`` は使用しない）。

    ``hdiutil makehybrid`` は ``-rock`` および ``-puppetstrings``
    オプションを受け付けないため、これらはコマンドには渡さない。
    """
    options = options or IsoOptions()

    flags: list[str] = []

    if options.iso:
        flags.append("-iso")

    if options.joliet:
        flags.append("-joliet")

    if options.udf:
        flags.append("-udf")

    if not flags:
        raise ValueError(
            "ISO9660 / Joliet / UDF のいずれかは有効にしてください。"
        )

    if options.joliet and not options.iso:
        raise ValueError(
            "Joliet を使用する場合は ISO9660 を有効にしてください。"
        )

    if options.rock and not options.iso:
        raise ValueError(
            "Rock Ridge を指定する場合は ISO9660 を有効にしてください。"
        )

    return [
        "hdiutil",
        "makehybrid",
        *flags,
        "-o",
        str(output_path),
        str(source),
    ]


def build_attach_command(image_path: str | Path) -> list[str]:
    """検証のためにイメージを読み取り専用でattach（マウント）するコマンド。"""
    return [
        "hdiutil",
        "attach",
        "-readonly",
        str(image_path),
    ]


def build_verify_volume_command(device: str) -> list[str]:
    """attach済みデバイスのファイルシステム整合性を検証するコマンド。"""
    return ["diskutil", "verifyVolume", device]


def build_detach_command(device: str) -> list[str]:
    """検証のためにattachしたデバイスを取り外すコマンド。"""
    return ["hdiutil", "detach", device]


def _extract_attached_device(output: str) -> str | None:
    """``hdiutil attach`` の出力からアタッチされたデバイスパスを取り出す。

    典型的な出力は ``/dev/disk5          <tab...>  /Volumes/Foo`` の
    ような1行（``-nomount`` 時はマウントポイント欄が空）。
    """
    for line in output.splitlines():
        stripped = line.strip()
        if stripped.startswith("/dev/disk"):
            return stripped.split()[0]

    return None


def _device_filesystem_type(device: str) -> str | None:
    """``diskutil info -plist`` からアタッチ済みデバイスのFilesystemTypeを取得する。"""
    result = subprocess.run(
        ["diskutil", "info", "-plist", device],
        capture_output=True,
        check=False,
    )

    if result.returncode != 0:
        return None

    try:
        info = plistlib.loads(result.stdout)
    except Exception:  # noqa: BLE001
        return None

    if not isinstance(info, dict):
        return None

    filesystem_type = info.get("FilesystemType")
    return filesystem_type if isinstance(filesystem_type, str) else None


def run_makehybrid(
    source: str | Path,
    output_path: str | Path,
    options: IsoOptions | None = None,
    on_progress: ProgressCallback | None = None,
    on_percent: ProgressPercentCallback | None = None,
    on_process_started: ProcessStartedCallback | None = None,
) -> CommandResult:
    """ハイブリッドISOを作成する。

    標準出力・標準エラーを1行ずつ ``on_progress`` に渡す。
    ``hdiutil makehybrid`` は ``-puppetstrings`` を受け付けず、通常の
    出力にもパーセント表示を含まないため、``on_percent`` が実際の
    進捗率で呼ばれることは基本的にない（呼び出し側はビジー表示に
    フォールバックすること）。``on_process_started`` は起動直後の
    ``Popen`` を受け取り、外部から中断（``terminate``/``kill``）
    できるようにするためのフックである。
    """
    cmd = build_makehybrid_command(source, output_path, options)

    return _run_streaming(
        cmd,
        on_progress=on_progress,
        on_percent=on_percent,
        on_process_started=on_process_started,
    )


def verify_iso(
    image_path: str | Path,
    on_progress: ProgressCallback | None = None,
    on_percent: ProgressPercentCallback | None = None,
    on_process_started: ProcessStartedCallback | None = None,
) -> CommandResult:
    """作成済みイメージのファイルシステムが健全かどうかを検証する。

    以前は ``hdiutil verify`` を使用していたが、実機検証の結果、
    ``hdiutil makehybrid`` が生成するイメージにはチェックサムが一切
    含まれないため（``hdiutil imageinfo`` で ``Checksummed: false``
    ``Checksum Type: なし`` を確認済み）、``hdiutil verify`` は
    あらゆる正常なISOイメージに対しても必ず
    ``"has no checksum"`` で失敗する（＝この機能は実質的に常に
    「失敗」を報告していた）ことが判明した。

    そのため、次の手順に置き換える。
    1. ``hdiutil attach -readonly`` でイメージを実際にattach（マウント）
       できるか確認する。
    2. マウントされたファイルシステムが ``udf`` の場合のみ、
       ``diskutil verifyVolume``（内部的に ``fsck_udf``）で
       ファイルシステムの整合性を検証する。実機で、意図的に
       truncateした壊れたイメージに対して ``Bad extent in file`` /
       ``Filesystem is dirty`` を正しく検出できることを確認済み。
    3. UDFを含まない（ISO9660/Jolietのみの）イメージについては、
       macOS側に対応するファイルシステム検証ツールが存在せず
       ``diskutil verifyVolume`` は常に ``"Invalid request"`` で
       失敗する（実機確認済み）。この場合はattachできたことのみを
       もって検証成功とみなす。
    4. 検証後は必ずdetachする。
    """
    attach_result = _run_streaming(
        build_attach_command(image_path),
        on_progress=on_progress,
        on_process_started=on_process_started,
    )

    if not attach_result.ok:
        return CommandResult(
            returncode=attach_result.returncode,
            stderr=(
                "イメージをアタッチできませんでした: "
                f"{attach_result.stderr}"
            ),
        )

    device = _extract_attached_device(attach_result.stderr)

    if device is None:
        return CommandResult(
            returncode=1,
            stderr=(
                "アタッチされたデバイスを特定できませんでした: "
                f"{attach_result.stderr}"
            ),
        )

    try:
        filesystem_type = _device_filesystem_type(device)

        if filesystem_type == "udf":
            verify_result = _run_streaming(
                build_verify_volume_command(device),
                on_progress=on_progress,
                on_process_started=on_process_started,
            )
        else:
            if on_progress is not None:
                on_progress(
                    "ISO9660/Jolietのみのイメージのため、詳細な"
                    "ファイルシステム検証には対応していません"
                    "（アタッチ確認のみ実施しました）。"
                )

            verify_result = CommandResult(returncode=0, stderr="")
    finally:
        _run_streaming(
            build_detach_command(device),
            on_progress=on_progress,
        )

    if on_percent is not None and verify_result.ok:
        on_percent(100)

    return verify_result


def _run_streaming(
    cmd: list[str],
    on_progress: ProgressCallback | None,
    on_percent: ProgressPercentCallback | None = None,
    on_process_started: ProcessStartedCallback | None = None,
) -> CommandResult:
    """外部コマンドを実行し、出力をリアルタイムに通知する。"""
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )

    if on_process_started is not None:
        on_process_started(proc)

    output_lines: list[str] = []

    assert proc.stdout is not None

    for line in proc.stdout:
        output_lines.append(line)

        text = line.rstrip("\r\n")

        if on_progress is not None:
            on_progress(text)

        percent = _extract_percent(text)

        if percent is not None and on_percent is not None:
            on_percent(percent)

    returncode = proc.wait()

    return CommandResult(
        returncode=returncode,
        stderr="".join(output_lines),
    )


_PERCENT_RE = re.compile(
    r"(?<![\d-])(\d{1,3}(?:\.\d+)?)\s*%"
)


def _extract_percent(line: str) -> int | None:
    """出力行から進捗率を抽出する。

    ``hdiutil -puppetstrings`` の出力に含まれる
    パーセント表現を取得する。

    例:
        ``PERCENTAGE: 37%`` -> 37
        ``37%`` -> 37
        ``Progress: 82.5%`` -> 82

    ``-1`` は ``hdiutil`` が進捗不定を表す特殊値なので無視する。
    100を超える値や負数も無視する。
    """
    matches = _PERCENT_RE.findall(line)

    if not matches:
        return None

    try:
        value = float(matches[-1])
    except ValueError:
        return None

    if not 0.0 <= value <= 100.0:
        return None

    return round(value)


try:
    from PySide6.QtCore import QThread, Signal
except ImportError:  # pragma: no cover - PySide6未インストール時
    QThread = None  # type: ignore[assignment,misc]


if QThread is not None:

    class IsoWorker(QThread):  # type: ignore[misc]
        """ISO作成〜検証をバックグラウンドスレッドで実行するワーカー。"""

        progress = Signal(str)
        progress_percent = Signal(int)
        finished_ok = Signal(bool, str)

        def __init__(
            self,
            source: str | Path,
            output_path: str | Path,
            options: IsoOptions | None = None,
            parent=None,
        ) -> None:
            super().__init__(parent)
            self._source = source
            self._output_path = output_path
            self._options = options or IsoOptions()
            self._process: subprocess.Popen[str] | None = None
            self._cancel_requested = False

        def request_cancel(self) -> None:
            """実行中の ``hdiutil`` プロセスを安全に中断する。

            GUIスレッドから呼び出される想定。実行中のプロセスへ
            ``terminate`` を送ることで、ブロッキングしている出力読み取り
            ループを速やかに終了させる。
            """
            self._cancel_requested = True

            process = self._process
            if process is not None and process.poll() is None:
                process.terminate()

        def _capture_process(self, process: subprocess.Popen[str]) -> None:
            self._process = process

        def run(self) -> None:  # noqa: D102 - QThreadのオーバーライド
            def on_build_percent(percent: int) -> None:
                # ISO作成: 0〜90%
                mapped = round(percent * 0.9)
                self.progress_percent.emit(min(mapped, 90))

            build_result = run_makehybrid(
                self._source,
                self._output_path,
                self._options,
                on_progress=self.progress.emit,
                on_percent=on_build_percent,
                on_process_started=self._capture_process,
            )

            if self._cancel_requested:
                self.finished_ok.emit(
                    False,
                    "ユーザーの操作により中断しました。",
                )
                return

            if not build_result.ok:
                self.finished_ok.emit(
                    False,
                    build_result.stderr,
                )
                return

            self.progress.emit(
                "イメージ作成完了。検証を実行しています…"
            )

            # 検証開始時点を90%とする。
            self.progress_percent.emit(90)

            verify_result = verify_iso(
                self._output_path,
                on_progress=self.progress.emit,
                on_process_started=self._capture_process,
            )

            if self._cancel_requested:
                self.finished_ok.emit(
                    False,
                    "ユーザーの操作により中断しました。",
                )
                return

            if not verify_result.ok:
                self.finished_ok.emit(
                    False,
                    verify_result.stderr,
                )
                return

            self.progress_percent.emit(100)

            self.finished_ok.emit(
                True,
                "ISOイメージの作成・検証が完了しました。",
            )
