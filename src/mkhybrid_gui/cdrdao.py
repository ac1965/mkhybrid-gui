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

``cd-paranoia``（マウントされたままの状態でも動作する）と異なり、
``cdrdao``はファイルシステム層を経由せずSCSI/MMCコマンドで直接
ドライブへアクセスするため、macOSがボリュームを1つでもマウントした
ままだと排他アクセスに失敗する（実機で確認済み）。呼び出し側は
``rip_disc_image()``を呼ぶ前に必ず``disk_utils.unmount_disk()``で
ディスク全体をアンマウントし（ユーザーへの確認を挟むこと）、完了後に
``disk_utils.mount_disk()``で再マウントすること。

``--device``にはmacOSの``/dev/rdiskN``ではなく、``cdrdao scanbus``が
返すIOKitレジストリパスを渡す必要がある（実機で確認済み。
``find_scsi_device()``を参照）。
"""

from __future__ import annotations

import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from mkhybrid_gui import audio_cd
from mkhybrid_gui.audio_cd import command_env, tool_path
from mkhybrid_gui.subprocess_utils import (
    SAFE_SUBPROCESS_KWARGS,
    resolve_command,
    terminate_with_escalation,
)

ProgressCallback = Callable[[str], None]
ProcessStartedCallback = Callable[["subprocess.Popen[str]"], None]
CancelCheck = Callable[[], bool]

REQUIRED_TOOLS: tuple[str, ...] = ("cdrdao",)
#: CUEシート生成（オプトイン）にのみ必要な追加コマンド。
#: ``cdrdao``と同じHomebrewフォーミュラに同梱されている。
CUE_REQUIRED_TOOLS: tuple[str, ...] = ("toc2cue",)


def missing_tools(*, generate_cue: bool = False) -> list[str]:
    """未インストールの外部コマンドを返す。

    ``generate_cue``がTrueの場合のみ、CUEシート生成に使う``toc2cue``の
    有無も確認する（既定のTOC+BIN作成のみなら不要なため）。
    """
    tools = REQUIRED_TOOLS + (CUE_REQUIRED_TOOLS if generate_cue else ())
    return [tool for tool in tools if tool_path(tool) is None]


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
        resolve_command(cmd, path=audio_cd.effective_path()),
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

    return CommandResult(returncode=returncode, output="".join(lines))


#: IOKitのパス自体に``:``（``IOService:/...``の直後、前後に空白なし）や
#: 空白（例: ``Mass Storage Device@...``）が含まれうるため、正規表現の
#: 単純な区切りでは誤爆する。区切りは常に空白付きの`` : ``
#: （パス側の``:``には前後に空白が無い）であることを利用し、文字列の
#: ``rsplit``で区切る方が確実（実機の``cdrdao scanbus``出力で確認済み）。
_SCANBUS_SEPARATOR = " : "


def scan_bus() -> str:
    """``cdrdao scanbus``の生出力（stdout+stderr結合）を返す。

    ``cdrdao``はmacOSでは``/dev/rdiskN``ではなく、この出力に含まれる
    IOKitレジストリパス（例:
    ``IOService:/AppleARMPE/.../IODVDServices``）を``--device``に
    渡す必要がある（実機で確認済み。``/dev/rdiskN``を渡すと
    ``Cannot setup device``で失敗する）。
    """
    result = subprocess.run(
        resolve_command(["cdrdao", "scanbus"], path=audio_cd.effective_path()),
        capture_output=True,
        text=True,
        check=False,
        env=command_env(),
        **SAFE_SUBPROCESS_KWARGS,
    )
    return result.stdout + result.stderr


def find_scsi_device(scanbus_output: str, media_name: str) -> str | None:
    """``scan_bus()``の出力から、``media_name``
    （``disk_utils.get_media_name()``、例: ``"ASUS SDRW-08U9M-U"``）に
    一致するドライブのIOKitパスを探す。

    同一モデルのドライブが複数接続されている場合は区別できず、最初に
    一致したものを返す（既知の制限）。
    """
    normalized_target = " ".join(media_name.split())

    for line in scanbus_output.splitlines():
        if _SCANBUS_SEPARATOR not in line:
            continue

        path, _, rest = line.partition(_SCANBUS_SEPARATOR)
        fields = rest.split(",")

        if len(fields) < 2:
            continue

        vendor, model = fields[0], fields[1]
        candidate = f"{vendor.strip()} {model.strip()}"

        if " ".join(candidate.split()) == normalized_target:
            return path.strip()

    return None


def build_read_cd_command(
    device: str, toc_path: Path, bin_path: Path
) -> list[str]:
    """ディスク全体をTOC+BINイメージとして読み取る``cdrdao``コマンドを組み立てる。

    ``device``は``/dev/rdiskN``ではなく、``find_scsi_device()``が返す
    IOKitレジストリパスであること（実機で確認済み。詳細は
    ``scan_bus()``のdocstringを参照）。

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


def build_toc2cue_command(
    toc_path: Path, cue_path: Path, swapped_bin_path: Path
) -> list[str]:
    """TOC+BINから互換性用のCUEシートを作る``toc2cue``コマンドを組み立てる。

    実機で確認済み: 音楽トラックを含むイメージでは、TOC用の``.bin``を
    そのままCUEから参照すると、``toc2cue``自身が「音声のバイト順が
    正しくない可能性がある」と警告する。``-s``（バイトスワップ）と
    ``-C``（新規bin出力先）を指定し、CUE専用のバイトスワップ済み
    ``.bin``を別途生成する（ディスク使用量が倍になる。GUI側で
    オプトインのチェックボックスとして開示すること）。
    """
    return [
        "toc2cue",
        "-s",
        "-C",
        str(swapped_bin_path),
        str(toc_path),
        str(cue_path),
    ]


def generate_cue_sheet(
    toc_path: Path,
    cue_path: Path,
    swapped_bin_path: Path,
    on_progress: ProgressCallback | None = None,
) -> CommandResult:
    """``toc2cue``でCUEシート（+バイトスワップ済みBIN）を生成する。"""
    return _run_streaming(
        build_toc2cue_command(toc_path, cue_path, swapped_bin_path),
        on_progress,
    )


@dataclass(frozen=True)
class DiscImageResult:
    """ディスクイメージ作成の結果。"""

    ok: bool
    toc_path: Path
    bin_path: Path
    cancelled: bool = False
    error: str = ""
    #: CUEシートを生成できた場合のパス（生成しなかった・失敗した場合はNone）。
    cue_path: Path | None = None
    #: CUEシート生成に失敗した場合のエラーメッセージ。TOC+BIN本体の
    #: 作成自体は成功しているため、この失敗だけで``ok``をFalseにはしない
    #: （ベストエフォート、musicbrainzのオンライン検索と同じ方針）。
    cue_error: str | None = None


def rip_disc_image(
    device: str,
    destination_dir: str | Path,
    base_name: str,
    on_progress: ProgressCallback | None = None,
    on_process_started: ProcessStartedCallback | None = None,
    cancel_check: CancelCheck | None = None,
    generate_cue: bool = False,
) -> DiscImageResult:
    """``device``のディスク全体を``destination_dir``直下に
    ``{base_name}.toc``/``{base_name}.bin``としてバックアップする。

    トラックごとのサブディレクトリは作らない（1回の実行につき
    1組のTOC+BINファイルのみのため、ISO作成と同様に出力先フォルダ
    直下に直接書き出す）。失敗時・中断時は作成途中のファイルを
    削除する。

    ``generate_cue``がTrueの場合、TOC+BIN作成成功後に``toc2cue``で
    互換性用のCUEシート（+バイトスワップ済みBIN、
    ``{base_name}.cue.bin``）を追加生成する。CUE生成の失敗は
    ベストエフォートで扱い、TOC+BIN本体が正常に作成できていれば
    ``DiscImageResult.ok``はTrueのままにする（``cue_error``に理由を残す）。
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

    if not generate_cue:
        return DiscImageResult(ok=True, toc_path=toc_path, bin_path=bin_path)

    cue_path = dest / f"{base_name}.cue"
    swapped_bin_path = dest / f"{base_name}.cue.bin"

    if on_progress is not None:
        on_progress("CUEシートを作成しています…")

    cue_result = generate_cue_sheet(
        toc_path, cue_path, swapped_bin_path, on_progress
    )

    if not cue_result.ok:
        cue_path.unlink(missing_ok=True)
        swapped_bin_path.unlink(missing_ok=True)
        return DiscImageResult(
            ok=True,
            toc_path=toc_path,
            bin_path=bin_path,
            cue_error=cue_result.output,
        )

    return DiscImageResult(
        ok=True, toc_path=toc_path, bin_path=bin_path, cue_path=cue_path
    )


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
            generate_cue: bool = False,
            parent=None,
        ) -> None:
            super().__init__(parent)
            self._device = device
            self._destination_dir = destination_dir
            self._base_name = base_name
            self._generate_cue = generate_cue
            self._process: subprocess.Popen[str] | None = None
            self._cancel_requested = False

        def request_cancel(self) -> None:
            """実行中の``cdrdao``プロセスを安全に中断する。

            ``terminate``を無視するコマンドが相手でもハングし続けない
            よう、一定時間後に``kill``へ自動的にエスカレーションする
            （``terminate_with_escalation``、詳細は``subprocess_utils``
            モジュールのdocstringを参照）。
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
            result = rip_disc_image(
                self._device,
                self._destination_dir,
                self._base_name,
                on_progress=self.progress.emit,
                on_process_started=self._capture_process,
                cancel_check=self._is_cancelled,
                generate_cue=self._generate_cue,
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

            message = f"ディスクイメージを作成しました（{result.toc_path}）。"

            if self._generate_cue:
                if result.cue_path is not None:
                    message += f"\nCUEシートも作成しました（{result.cue_path}）。"
                else:
                    message += (
                        "\nCUEシートの作成には失敗しました"
                        f"（TOC+BIN本体は正常です）: {result.cue_error}"
                    )

            self.finished_ok.emit(True, message)
