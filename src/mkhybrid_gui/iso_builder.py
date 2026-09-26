"""``hdiutil makehybrid`` / ``hdiutil verify`` のラッパー。

コマンド組み立てと実行のロジックはUIフレームワークに依存しない関数として実装し、
GUIからの非同期実行のみ ``IsoWorker``（QThread）が担う。
"""

from __future__ import annotations

import re
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

ProgressCallback = Callable[[str], None]
ProgressPercentCallback = Callable[[int], None]


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


def build_verify_command(image_path: str | Path) -> list[str]:
    """``hdiutil verify`` のコマンド引数リストを組み立てる。"""
    return [
        "hdiutil",
        "verify",
        str(image_path),
    ]


def run_makehybrid(
    source: str | Path,
    output_path: str | Path,
    options: IsoOptions | None = None,
    on_progress: ProgressCallback | None = None,
    on_percent: ProgressPercentCallback | None = None,
) -> CommandResult:
    """ハイブリッドISOを作成する。

    標準出力・標準エラーを1行ずつ ``on_progress`` に渡し、
    ``-puppetstrings`` が出力する進捗率を ``on_percent`` に渡す。
    """
    cmd = build_makehybrid_command(source, output_path, options)

    return _run_streaming(
        cmd,
        on_progress=on_progress,
        on_percent=on_percent,
    )


def verify_iso(
    image_path: str | Path,
    on_progress: ProgressCallback | None = None,
    on_percent: ProgressPercentCallback | None = None,
) -> CommandResult:
    """作成済みイメージを ``hdiutil verify`` で検証する。

    ``subprocess.run`` を使用することで、既存のテストと互換性を保つ。
    """
    cmd = build_verify_command(image_path)

    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        check=False,
    )

    if on_progress is not None:
        for line in result.stdout.splitlines():
            on_progress(line)

    if on_percent is not None and result.returncode == 0:
        on_percent(100)

    return CommandResult(
        returncode=result.returncode,
        stderr=result.stderr,
    )


def _run_streaming(
    cmd: list[str],
    on_progress: ProgressCallback | None,
    on_percent: ProgressPercentCallback | None = None,
) -> CommandResult:
    """外部コマンドを実行し、出力をリアルタイムに通知する。"""
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )

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
            )

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
            )

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
