"""音楽CD（CDDAFSでマウントされたAudio CD）のトラック書き出しを行うモジュール。

macOSは音楽CDを挿入すると、各トラックを再生可能なAIFFファイルとして
仮想的にマウントする（``CDDAFS``）。追加の外部ツール（cdrdao等）を使わず、
このマウントポイントから標準の :mod:`shutil` でファイルコピーするだけで
トラックを書き出せる。ビット単位で完全なRed Book CDイメージ（BIN/CUE等）
の作成は対象外とする。

コマンド組み立てと同様、ロジックはUIフレームワークに依存しない関数として実装し、
GUIからの非同期実行のみ ``AudioRipWorker``（QThread）が担う。
"""

from __future__ import annotations

import shutil
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

ProgressCallback = Callable[[str], None]


class AudioCdError(RuntimeError):
    """音楽CDのトラック取得に失敗した場合に送出する。"""


def list_audio_tracks(mount_point: str | Path) -> list[Path]:
    """CDDAFSマウントポイント配下のオーディオトラック（``.aiff``）一覧を返す。"""
    mount = Path(mount_point)
    if not mount.is_dir():
        raise AudioCdError(f"マウントポイントが見つかりません: {mount}")

    tracks = sorted(mount.glob("*.aiff"))
    if not tracks:
        raise AudioCdError(f"{mount} 内にオーディオトラック（.aiff）が見つかりません。")
    return tracks


@dataclass(frozen=True)
class RipResult:
    """トラック書き出しの結果。"""

    copied: list[Path] = field(default_factory=list)
    failed: list[tuple[Path, str]] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.failed


def extract_audio_tracks(
    mount_point: str | Path,
    destination_dir: str | Path,
    on_progress: ProgressCallback | None = None,
) -> RipResult:
    """音楽CDの各トラックをAIFFファイルとして ``destination_dir`` に書き出す。"""
    tracks = list_audio_tracks(mount_point)
    dest = Path(destination_dir)
    dest.mkdir(parents=True, exist_ok=True)

    copied: list[Path] = []
    failed: list[tuple[Path, str]] = []
    total = len(tracks)

    for index, track in enumerate(tracks, start=1):
        target = dest / track.name
        if on_progress is not None:
            on_progress(f"[{index}/{total}] {track.name} を書き出しています…")
        try:
            shutil.copyfile(track, target)
        except OSError as exc:
            failed.append((track, str(exc)))
            if on_progress is not None:
                on_progress(f"エラー: {track.name} の書き出しに失敗しました: {exc}")
            continue
        copied.append(target)

    return RipResult(copied=copied, failed=failed)


try:
    from PySide6.QtCore import QThread, Signal
except ImportError:  # pragma: no cover - PySide6未インストール時はAudioRipWorkerを提供しない
    QThread = None  # type: ignore[assignment,misc]


if QThread is not None:

    class AudioRipWorker(QThread):  # type: ignore[misc]
        """音楽CDのトラック書き出しをバックグラウンドスレッドで実行するワーカー。"""

        progress = Signal(str)
        finished_ok = Signal(bool, str)

        def __init__(
            self,
            mount_point: str | Path,
            destination_dir: str | Path,
            parent=None,
        ) -> None:
            super().__init__(parent)
            self._mount_point = mount_point
            self._destination_dir = destination_dir

        def run(self) -> None:  # noqa: D102 - QThreadのオーバーライド
            try:
                result = extract_audio_tracks(
                    self._mount_point,
                    self._destination_dir,
                    on_progress=self.progress.emit,
                )
            except AudioCdError as exc:
                self.finished_ok.emit(False, str(exc))
                return

            if result.ok:
                self.finished_ok.emit(True, f"{len(result.copied)}曲を書き出しました。")
                return

            details = "; ".join(f"{path.name}: {message}" for path, message in result.failed)
            self.finished_ok.emit(False, f"一部のトラックの書き出しに失敗しました: {details}")
