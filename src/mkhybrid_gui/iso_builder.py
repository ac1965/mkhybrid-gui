"""``hdiutil makehybrid`` / ``hdiutil verify`` のラッパー。

コマンド組み立てと実行のロジックはUIフレームワークに依存しない関数として実装し、
GUIからの非同期実行のみ ``IsoWorker``（QThread）が担う。
"""

from __future__ import annotations

import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

ProgressCallback = Callable[[str], None]


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
    """
    options = options or IsoOptions()

    flags: list[str] = []
    if options.iso:
        flags.append("-iso")
    if options.joliet:
        flags.append("-joliet")
    if options.rock:
        flags.append("-rock")
    if options.udf:
        flags.append("-udf")
    if not flags:
        raise ValueError(
            "ISO9660 / Joliet / Rock Ridge / UDF のいずれかは有効にしてください。"
        )

    cmd: list[str] = ["hdiutil", "makehybrid", *flags, "-o", str(output_path), str(source)]
    return cmd


def build_verify_command(image_path: str | Path) -> list[str]:
    """``hdiutil verify`` のコマンド引数リストを組み立てる。"""
    return ["hdiutil", "verify", str(image_path)]


def run_makehybrid(
    source: str | Path,
    output_path: str | Path,
    options: IsoOptions | None = None,
    on_progress: ProgressCallback | None = None,
) -> CommandResult:
    """ハイブリッドISOを作成する。標準出力を1行ずつ ``on_progress`` に渡す。"""
    cmd = build_makehybrid_command(source, output_path, options)
    return _run_streaming(cmd, on_progress)


def verify_iso(image_path: str | Path) -> CommandResult:
    """作成済みイメージを ``hdiutil verify`` で検証する。"""
    cmd = build_verify_command(image_path)
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    return CommandResult(returncode=result.returncode, stderr=result.stderr)


def _run_streaming(cmd: list[str], on_progress: ProgressCallback | None) -> CommandResult:
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    output_lines: list[str] = []
    assert proc.stdout is not None
    for line in proc.stdout:
        output_lines.append(line)
        if on_progress is not None:
            on_progress(line.rstrip("\n"))
    returncode = proc.wait()
    return CommandResult(returncode=returncode, stderr="".join(output_lines))


try:
    from PySide6.QtCore import QThread, Signal
except ImportError:  # pragma: no cover - PySide6未インストール時はIsoWorkerを提供しない
    QThread = None  # type: ignore[assignment,misc]


if QThread is not None:

    class IsoWorker(QThread):  # type: ignore[misc]
        """ISO作成〜検証をバックグラウンドスレッドで実行するワーカー。"""

        progress = Signal(str)
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
            build_result = run_makehybrid(
                self._source,
                self._output_path,
                self._options,
                on_progress=self.progress.emit,
            )
            if not build_result.ok:
                self.finished_ok.emit(False, build_result.stderr)
                return

            self.progress.emit("イメージ作成完了。検証を実行しています…")
            verify_result = verify_iso(self._output_path)
            if not verify_result.ok:
                self.finished_ok.emit(False, verify_result.stderr)
                return

            self.finished_ok.emit(True, "ISOイメージの作成・検証が完了しました。")
