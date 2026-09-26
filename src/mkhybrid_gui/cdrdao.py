"""``cdrdao``を使った音楽CDのディスクイメージ（TOC+BIN）バックアップ。

``audio_cd.py``（``cd-paranoia``経由で1トラックずつ正確に読み取り、
選択した形式へ変換・タグ付けする経路）とは別の、ディスク全体を
ビット単位でTOC+BINイメージとしてバックアップする経路。

    brew install cdrdao

データ+音声混在（mixed-mode）ディスクや、コピーガード付き等の特殊な
ディスクで既存の経路が使えない場合のフォールバックとしても使う。
トラック単位の変換・タグ付けは行わない（TOC+BINイメージをそのまま
保存するのみ）。

``cdrdao``もHomebrewインストールの外部コマンドのため、PATH解決には
``audio_cd.py``の``effective_path``/``tool_path``/``command_env``を
再利用する（GUI起動時にログインシェルのPATHを常にマージする、という
過去に回帰した実績のあるロジックを、このモジュールで再実装して
重複させない）。
"""

from __future__ import annotations

import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from mkhybrid_gui.audio_cd import command_env, tool_path

ProgressCallback = Callable[[str], None]
ProcessStartedCallback = Callable[["subprocess.Popen[str]"], None]
CancelCheck = Callable[[], bool]

REQUIRED_TOOLS: tuple[str, ...] = ("cdrdao",)


def missing_tools() -> list[str]:
    """未インストールの外部コマンドを返す（現状は``cdrdao``のみ）。"""
    return [tool for tool in REQUIRED_TOOLS if tool_path(tool) is None]


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
    """外部コマンドをPATH引き継ぎ環境で実行する。"""
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        env=command_env(),
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

    return CommandResult(returncode=returncode, output="".join(lines))


def build_read_cd_command(
    device: str, toc_path: Path, bin_path: Path
) -> list[str]:
    """ディスク全体をTOC+BINイメージとして読み取る``cdrdao``コマンドを組み立てる。

    ``--paranoia-mode 3``（フルパラノイア: ジッター補正・誤り訂正・
    セクタ単位の再読込を最大限有効にするモード）は必ず指定する。これは
    ``cd-paranoia``に``-Z``（パラノイア無効化）を渡さない既定動作と
    同じ位置づけの要件であり、省略したり弱いモードに変更したりしない
    こと。
    """
    return [
        "cdrdao",
        "read-cd",
        "--device",
        device,
        "--driver",
        "generic-mmc-raw",
        "--paranoia-mode",
        "3",
        "--datafile",
        str(bin_path),
        str(toc_path),
    ]


@dataclass(frozen=True)
class DiscImageResult:
    """ディスクイメージ作成の結果。"""

    ok: bool
    toc_path: Path
    bin_path: Path
    cancelled: bool = False
    error: str = ""


def rip_disc_image(
    device: str,
    destination_dir: str | Path,
    base_name: str,
    on_progress: ProgressCallback | None = None,
    on_process_started: ProcessStartedCallback | None = None,
    cancel_check: CancelCheck | None = None,
) -> DiscImageResult:
    """``device``のディスク全体を``destination_dir``直下に
    ``{base_name}.toc``/``{base_name}.bin``としてバックアップする。

    トラックごとのサブディレクトリは作らない（1回の実行につき
    1組のTOC+BINファイルのみのため、ISO作成と同様に出力先フォルダ
    直下に直接書き出す）。失敗時・中断時は作成途中のファイルを
    削除する。
    """
    dest = Path(destination_dir)
    dest.mkdir(parents=True, exist_ok=True)

    toc_path = dest / f"{base_name}.toc"
    bin_path = dest / f"{base_name}.bin"

    result = _run_streaming(
        build_read_cd_command(device, toc_path, bin_path),
        on_progress,
        on_process_started=on_process_started,
    )

    cancelled = cancel_check is not None and cancel_check()

    if cancelled:
        toc_path.unlink(missing_ok=True)
        bin_path.unlink(missing_ok=True)
        return DiscImageResult(
            ok=False, toc_path=toc_path, bin_path=bin_path, cancelled=True
        )

    if not result.ok:
        toc_path.unlink(missing_ok=True)
        bin_path.unlink(missing_ok=True)
        return DiscImageResult(
            ok=False,
            toc_path=toc_path,
            bin_path=bin_path,
            error=result.output,
        )

    return DiscImageResult(ok=True, toc_path=toc_path, bin_path=bin_path)


try:
    from PySide6.QtCore import QThread, Signal
except ImportError:  # pragma: no cover - PySide6未インストール時
    QThread = None  # type: ignore[assignment,misc]


if QThread is not None:

    class CdrdaoWorker(QThread):  # type: ignore[misc]
        """``cdrdao``によるディスクイメージ作成をバックグラウンドスレッドで実行するワーカー。

        ``cdrdao``が機械可読な進捗率を出力するかどうかは実機未確認の
        ため（次回実機接続時に確認予定）、``AudioRipWorker``と異なり
        ``progress_percent``シグナルは持たない。GUI側の進捗バーは
        ``hdiutil makehybrid``と同様、不確定（ビジー）表示のままになる。
        """

        progress = Signal(str)
        finished_ok = Signal(bool, str)

        def __init__(
            self,
            device: str,
            destination_dir: str | Path,
            base_name: str,
            parent=None,
        ) -> None:
            super().__init__(parent)
            self._device = device
            self._destination_dir = destination_dir
            self._base_name = base_name
            self._process: subprocess.Popen[str] | None = None
            self._cancel_requested = False

        def request_cancel(self) -> None:
            """実行中の``cdrdao``プロセスを安全に中断する。"""
            self._cancel_requested = True

            process = self._process
            if process is not None and process.poll() is None:
                process.terminate()

        def _capture_process(self, process: subprocess.Popen[str]) -> None:
            self._process = process

        def _is_cancelled(self) -> bool:
            return self._cancel_requested

        def run(self) -> None:  # noqa: D102 - QThreadのオーバーライド
            result = rip_disc_image(
                self._device,
                self._destination_dir,
                self._base_name,
                on_progress=self.progress.emit,
                on_process_started=self._capture_process,
                cancel_check=self._is_cancelled,
            )

            if result.cancelled:
                self.finished_ok.emit(
                    False, "ユーザーの操作により中断しました。"
                )
                return

            if not result.ok:
                self.finished_ok.emit(
                    False,
                    "cdrdaoによるディスクイメージ作成に失敗しました: "
                    f"{result.error}",
                )
                return

            self.finished_ok.emit(
                True,
                f"ディスクイメージを作成しました（{result.toc_path}）。",
            )
