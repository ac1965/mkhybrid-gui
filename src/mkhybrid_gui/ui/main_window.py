"""メインウィンドウのUI定義。"""

from __future__ import annotations

import tempfile
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QRadioButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from mkhybrid_gui.audio_cd import (
    AudioCdError,
    AudioFormat,
    AudioRipWorker,
    DiscToc,
    disc_id_from_disc_toc,
    missing_tools,
    query_disc_toc,
)
from mkhybrid_gui.disk_utils import (
    MediaType,
    Volume,
    list_volumes,
    whole_disk_raw_device,
)
from mkhybrid_gui.iso_builder import IsoOptions, IsoWorker
from mkhybrid_gui.metadata import AlbumMetadata, TrackMetadata
from mkhybrid_gui.musicbrainz import LookupResult, MetadataLookupWorker

# ALACを先頭（既定・推奨）にした表示順
_AUDIO_FORMAT_ORDER = [
    AudioFormat.ALAC,
    AudioFormat.AIFF,
    AudioFormat.FLAC,
    AudioFormat.WAV,
    AudioFormat.AAC,
]

# 実行コマンド名とHomebrewパッケージ名の対応。
# cd-paranoia は libcdio-paranoia パッケージから提供される。
_BREW_PACKAGES = {
    "cd-paranoia": "libcdio-paranoia",
    "flac": "flac",
}


class MainWindow(QMainWindow):
    """アプリケーションのメインウィンドウ。"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("mkhybrid-gui — ハイブリッドISO作成ツール")
        self.resize(640, 560)

        self._volumes: list[Volume] = []
        self._worker: IsoWorker | AudioRipWorker | None = None
        self._is_audio_job = False
        self._cancel_requested = False
        self._audio_work_tmpdir: tempfile.TemporaryDirectory[str] | None = None
        self._disc_toc: DiscToc | None = None
        self._metadata_lookup_worker: MetadataLookupWorker | None = None

        self._build_ui()
        self._refresh_volumes()

        # マウント済みのドライブが1つも無い場合、_refresh_volumes()が
        # device_comboに何も追加せず currentIndexChanged が一度も発火
        # しないため、_on_device_changed() が呼ばれないまま各ウィジェットが
        # 構築時の既定の表示状態（音楽CD用の項目も含めて可視）になって
        # しまう。明示的に一度呼び、実際の選択状態と表示を確実に一致させる。
        self._on_device_changed(self.device_combo.currentIndex())

    # -- UI構築 -----------------------------------------------------

    def _build_ui(self) -> None:
        central = QWidget(self)
        self.setCentralWidget(central)
        root_layout = QVBoxLayout(central)

        form = QFormLayout()
        root_layout.addLayout(form)

        device_row = QHBoxLayout()
        self.device_combo = QComboBox()
        self.device_combo.currentIndexChanged.connect(self._on_device_changed)
        self.refresh_button = QPushButton("更新")
        self.refresh_button.clicked.connect(self._refresh_volumes)
        device_row.addWidget(self.device_combo, stretch=1)
        device_row.addWidget(self.refresh_button)
        form.addRow("ドライブ/ボリューム:", device_row)

        output_row = QHBoxLayout()
        self.output_edit = QLineEdit()
        self.output_browse_button = QPushButton("参照…")
        self.output_browse_button.clicked.connect(self._choose_output_path)
        output_row.addWidget(self.output_edit, stretch=1)
        output_row.addWidget(self.output_browse_button)
        self.output_label = QLabel("出力先ISO:")
        form.addRow(self.output_label, output_row)

        options_row = QHBoxLayout()
        self.joliet_checkbox = QCheckBox("Joliet")
        self.joliet_checkbox.setChecked(True)
        self.rock_checkbox = QCheckBox("Rock Ridge")
        self.rock_checkbox.setChecked(True)
        self.udf_checkbox = QCheckBox("UDF")
        self.udf_checkbox.setChecked(False)
        options_row.addWidget(self.joliet_checkbox)
        options_row.addWidget(self.rock_checkbox)
        options_row.addWidget(self.udf_checkbox)
        options_row.addStretch(1)
        self.options_label = QLabel("オプション:")
        form.addRow(self.options_label, options_row)

        audio_format_row = QHBoxLayout()
        self.audio_format_group = QButtonGroup(self)
        self._audio_format_buttons: dict[AudioFormat, QRadioButton] = {}

        for audio_format in _AUDIO_FORMAT_ORDER:
            label = audio_format.value
            if audio_format is AudioFormat.ALAC:
                label += " ← 推奨"

            radio = QRadioButton(label)
            self.audio_format_group.addButton(radio)
            audio_format_row.addWidget(radio)
            self._audio_format_buttons[audio_format] = radio

        self._audio_format_buttons[AudioFormat.ALAC].setChecked(True)
        audio_format_row.addStretch(1)
        self.audio_format_label = QLabel("書き出し形式:")
        form.addRow(self.audio_format_label, audio_format_row)

        self.verify_checkbox = QCheckBox(
            "厳密な検証（各トラックを複数回読み取り比較。時間は約2倍）"
        )
        self.verify_checkbox.setChecked(True)
        form.addRow(self.verify_checkbox)

        metadata_fields_row = QHBoxLayout()
        self.album_edit = QLineEdit()
        self.album_edit.setPlaceholderText("アルバム名")
        self.artist_edit = QLineEdit()
        self.artist_edit.setPlaceholderText("アーティスト名")
        self.year_edit = QLineEdit()
        self.year_edit.setPlaceholderText("年")
        self.year_edit.setMaximumWidth(80)
        metadata_fields_row.addWidget(self.album_edit, stretch=2)
        metadata_fields_row.addWidget(self.artist_edit, stretch=2)
        metadata_fields_row.addWidget(self.year_edit, stretch=1)
        self.metadata_fields_label = QLabel("メタデータ（任意）:")
        form.addRow(self.metadata_fields_label, metadata_fields_row)

        lookup_row = QHBoxLayout()
        self.metadata_lookup_button = QPushButton(
            "オンラインで検索（MusicBrainz）"
        )
        self.metadata_lookup_button.clicked.connect(
            self._on_metadata_lookup_clicked
        )
        lookup_row.addWidget(self.metadata_lookup_button)
        lookup_row.addStretch(1)
        self.metadata_lookup_row_label = QLabel("")
        form.addRow(self.metadata_lookup_row_label, lookup_row)

        self.metadata_privacy_label = QLabel(
            "※ディスクの識別情報（トラック数・長さのみ）をMusicBrainz.org"
            "へ送信します。個人情報は含まれません。"
        )
        self.metadata_privacy_label.setWordWrap(True)
        root_layout.addWidget(self.metadata_privacy_label)

        self.metadata_status_label = QLabel("")
        root_layout.addWidget(self.metadata_status_label)

        self.track_title_table = QTableWidget(0, 2)
        self.track_title_table.setHorizontalHeaderLabels(["#", "トラック名"])
        self.track_title_table.horizontalHeader().setStretchLastSection(True)
        self.track_title_table.verticalHeader().setVisible(False)
        self.track_title_table.setMaximumHeight(160)
        root_layout.addWidget(self.track_title_table)

        self.media_info_label = QLabel("")
        root_layout.addWidget(self.media_info_label)

        start_row = QHBoxLayout()
        self.start_button = QPushButton("ISOイメージを作成")
        self.start_button.clicked.connect(self._on_start_clicked)
        self.cancel_button = QPushButton("中断")
        self.cancel_button.setEnabled(False)
        self.cancel_button.clicked.connect(self._on_cancel_clicked)
        start_row.addWidget(self.start_button, stretch=1)
        start_row.addWidget(self.cancel_button)
        root_layout.addLayout(start_row)

        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setTextVisible(True)
        self.progress_bar.setVisible(False)
        root_layout.addWidget(self.progress_bar)

        self.status_label = QLabel("待機中")
        root_layout.addWidget(self.status_label)

        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        root_layout.addWidget(self.log_view, stretch=1)

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802 - Qtのオーバーライド
        """処理中のワーカースレッドを残したままウィンドウが閉じられるのを防ぐ。

        実行中に閉じると、バックグラウンドの ``QThread`` が動いたままアプリが
        終了し、Qtの異常終了や書き出し途中のファイルの破損につながるため。
        """
        if self._worker is not None and self._worker.isRunning():
            QMessageBox.warning(
                self,
                "処理を実行中です",
                "ISOイメージの作成/オーディオの書き出しが完了するまで"
                "お待ちいただくか、「中断」ボタンで安全に停止してください。",
            )
            event.ignore()
            return

        super().closeEvent(event)

    # -- ドライブ一覧 -------------------------------------------------

    def _refresh_volumes(self) -> None:
        self.device_combo.clear()

        try:
            volumes = list_volumes()
        except Exception as exc:
            self.status_label.setText(str(exc))
            return

        # マウント済みデバイスを最上部に表示する。
        # mount_point が存在する = 現在マウントされているボリューム。
        volumes.sort(
            key=lambda volume: (
                volume.mount_point is None,
                volume.volume_name or volume.device_identifier,
            )
        )

        for volume in volumes:
            self.device_combo.addItem(
                volume.volume_name or volume.device_identifier,
                volume,
            )

    def _on_device_changed(self, _index: int) -> None:
        volume: Volume | None = self.device_combo.currentData()
        is_audio = volume is not None and volume.media_type == MediaType.CD_AUDIO

        self.output_label.setText("出力先フォルダ:" if is_audio else "出力先ISO:")
        self.options_label.setVisible(not is_audio)
        self.joliet_checkbox.setVisible(not is_audio)
        self.rock_checkbox.setVisible(not is_audio)
        self.udf_checkbox.setVisible(not is_audio)
        self.audio_format_label.setVisible(is_audio)

        for radio in self._audio_format_buttons.values():
            radio.setVisible(is_audio)

        self.verify_checkbox.setVisible(is_audio)
        self.start_button.setText(
            "オーディオトラックを書き出す"
            if is_audio
            else "ISOイメージを作成"
        )

        if volume is not None and not is_audio:
            # DVD/Blu-ray（BDXL・M-DISCを含む）は大容量ファイルを含みうるためUDFを既定でON
            self.udf_checkbox.setChecked(
                volume.media_type in (MediaType.DVD, MediaType.BD)
            )

        self.media_info_label.setText(
            f"検出されたメディア種別: {volume.media_type.value}"
            if volume is not None and volume.media_type is not None
            else ""
        )

        self.metadata_fields_label.setVisible(is_audio)
        self.album_edit.setVisible(is_audio)
        self.artist_edit.setVisible(is_audio)
        self.year_edit.setVisible(is_audio)
        self.metadata_lookup_row_label.setVisible(is_audio)
        self.metadata_lookup_button.setVisible(is_audio)
        self.metadata_privacy_label.setVisible(is_audio)
        self.metadata_status_label.setVisible(is_audio)
        self.track_title_table.setVisible(is_audio)

        if is_audio and volume is not None:
            self._prepare_audio_metadata_ui(volume)
        else:
            self._disc_toc = None
            self.track_title_table.setRowCount(0)
            self.album_edit.clear()
            self.artist_edit.clear()
            self.year_edit.clear()
            self.metadata_status_label.setText("")

    def _prepare_audio_metadata_ui(self, volume: Volume) -> None:
        """音楽CD選択時に、TOCを取得してトラック名入力欄の行数を確定させる。"""
        device = whole_disk_raw_device(volume.device_identifier)

        self.track_title_table.setRowCount(0)
        self.album_edit.clear()
        self.artist_edit.clear()
        self.year_edit.clear()
        self.metadata_status_label.setText("トラック情報を取得しています…")

        try:
            toc = query_disc_toc(device)
        except AudioCdError as exc:
            self._disc_toc = None
            self.metadata_status_label.setText(str(exc))
            return

        self._disc_toc = toc
        track_count = len(toc.track_offsets)
        self.track_title_table.setRowCount(track_count)

        for row in range(track_count):
            number_item = QTableWidgetItem(str(row + 1))
            number_item.setFlags(
                number_item.flags() & ~Qt.ItemFlag.ItemIsEditable
            )
            self.track_title_table.setItem(row, 0, number_item)
            self.track_title_table.setItem(row, 1, QTableWidgetItem(""))

        self.metadata_status_label.setText(
            f"{track_count}トラックを検出しました。"
            "アルバム名・アーティスト名・トラック名は任意入力です。"
        )

    def _choose_output_path(self) -> None:
        volume: Volume | None = self.device_combo.currentData()
        is_audio = volume is not None and volume.media_type == MediaType.CD_AUDIO

        if is_audio:
            path = QFileDialog.getExistingDirectory(
                self,
                "出力先フォルダを選択",
            )
        else:
            path, _ = QFileDialog.getSaveFileName(
                self,
                "出力先ISOファイルを選択",
                "",
                "ISOイメージ (*.iso)",
            )

            if path and not path.endswith(".iso"):
                path += ".iso"

        if path:
            self.output_edit.setText(path)

    def _selected_audio_format(self) -> AudioFormat:
        for audio_format, radio in self._audio_format_buttons.items():
            if radio.isChecked():
                return audio_format

        return AudioFormat.ALAC

    # -- メタデータ（音楽CD） -------------------------------------------

    def _on_metadata_lookup_clicked(self) -> None:
        """「オンラインで検索」ボタン。明示的なクリックでのみ通信する。"""
        if self._disc_toc is None:
            QMessageBox.warning(
                self,
                "検索エラー",
                "トラック情報を取得できていないため検索できません。",
            )
            return

        disc_id = disc_id_from_disc_toc(self._disc_toc)

        self.metadata_lookup_button.setEnabled(False)
        self.metadata_status_label.setText(
            "MusicBrainzに問い合わせています…"
        )

        worker = MetadataLookupWorker(disc_id, parent=self)
        worker.finished_lookup.connect(self._on_metadata_lookup_finished)

        self._metadata_lookup_worker = worker
        worker.start()

    def _on_metadata_lookup_finished(self, result: LookupResult) -> None:
        self.metadata_lookup_button.setEnabled(True)
        self._metadata_lookup_worker = None

        if not result.ok:
            self.metadata_status_label.setText(f"検索エラー: {result.error}")
            return

        if not result.candidates:
            self.metadata_status_label.setText(
                "見つかりませんでした。手動で入力してください。"
            )
            return

        if len(result.candidates) == 1:
            candidate = result.candidates[0]
        else:
            labels = [c.display_label for c in result.candidates]
            label, ok = QInputDialog.getItem(
                self,
                "候補の選択",
                f"{len(labels)}件の候補が見つかりました。選んでください:",
                labels,
                0,
                False,
            )

            if not ok:
                self.metadata_status_label.setText(
                    f"{len(labels)}件見つかりましたが、選択されませんでした。"
                )
                return

            candidate = result.candidates[labels.index(label)]

        if self._has_existing_metadata_input():
            reply = QMessageBox.question(
                self,
                "上書きの確認",
                "既に入力されている内容を、検索結果で上書きしますか？",
                QMessageBox.StandardButton.Yes
                | QMessageBox.StandardButton.No,
            )

            if reply != QMessageBox.StandardButton.Yes:
                self.metadata_status_label.setText(
                    "入力内容は変更していません。"
                )
                return

        self._apply_album_metadata(candidate.album)
        self.metadata_status_label.setText(
            "メタデータを反映しました。内容を確認してください。"
        )

    def _has_existing_metadata_input(self) -> bool:
        if self.album_edit.text().strip() or self.artist_edit.text().strip():
            return True

        for row in range(self.track_title_table.rowCount()):
            item = self.track_title_table.item(row, 1)

            if item is not None and item.text().strip():
                return True

        return False

    def _apply_album_metadata(self, album: AlbumMetadata) -> None:
        self.album_edit.setText(album.album)
        self.artist_edit.setText(album.artist)
        self.year_edit.setText(album.year or "")

        row_count = min(self.track_title_table.rowCount(), len(album.tracks))

        for row in range(row_count):
            item = self.track_title_table.item(row, 1)

            if item is not None:
                item.setText(album.tracks[row].title)

    def _collect_album_metadata(self) -> AlbumMetadata | None:
        """入力欄の内容から ``AlbumMetadata`` を組み立てる。

        すべて未入力の場合は ``None`` を返す（従来通りタグ付けなし・
        ``TrackNN`` のファイル名のままにするため）。
        """
        album = self.album_edit.text().strip()
        artist = self.artist_edit.text().strip()
        year = self.year_edit.text().strip() or None

        tracks: list[TrackMetadata] = []

        for row in range(self.track_title_table.rowCount()):
            item = self.track_title_table.item(row, 1)
            tracks.append(
                TrackMetadata(title=item.text().strip() if item else "")
            )

        if not album and not artist and not any(t.title for t in tracks):
            return None

        return AlbumMetadata(
            album=album, artist=artist, year=year, tracks=tracks
        )

    # -- 実行 ---------------------------------------------------------

    def _on_start_clicked(self) -> None:
        volume: Volume | None = self.device_combo.currentData()

        if volume is None:
            QMessageBox.warning(
                self,
                "選択エラー",
                "作成元のボリュームを選択してください。",
            )
            return

        if volume.media_type == MediaType.CD_AUDIO:
            self._start_audio_rip(volume)
        else:
            self._start_iso_build(volume)

    def _start_iso_build(self, volume: Volume) -> None:
        output_text = self.output_edit.text().strip()

        if not output_text:
            QMessageBox.warning(
                self,
                "入力エラー",
                "出力先ISOファイルを指定してください。",
            )
            return

        options = IsoOptions(
            joliet=self.joliet_checkbox.isChecked(),
            rock=self.rock_checkbox.isChecked(),
            udf=self.udf_checkbox.isChecked(),
        )

        if not (options.joliet or options.rock or options.udf):
            QMessageBox.warning(
                self,
                "オプションエラー",
                "Joliet / Rock Ridge / UDF のいずれかは有効にしてください。",
            )
            return

        output_path = Path(output_text)

        if output_path.exists():
            reply = QMessageBox.question(
                self,
                "確認",
                f"{output_path} は既に存在します。上書きしますか？",
                QMessageBox.StandardButton.Yes
                | QMessageBox.StandardButton.No,
            )

            if reply != QMessageBox.StandardButton.Yes:
                return

        source = volume.mount_point or f"/dev/{volume.device_identifier}"

        self._is_audio_job = False
        self._cancel_requested = False
        self.log_view.clear()
        self.status_label.setText("ISOイメージを作成しています…")
        self._start_progress_indicator()
        self._set_controls_enabled(False)

        worker = IsoWorker(
            source,
            output_path,
            options,
            parent=self,
        )

        worker.progress.connect(self._on_progress)
        worker.progress_percent.connect(self._on_progress_percent)
        worker.finished_ok.connect(self._on_finished)

        self._worker = worker
        worker.start()

    def _start_audio_rip(self, volume: Volume) -> None:
        dest_text = self.output_edit.text().strip()

        if not dest_text:
            QMessageBox.warning(
                self,
                "入力エラー",
                "出力先フォルダを指定してください。",
            )
            return

        audio_format = self._selected_audio_format()
        missing = missing_tools(audio_format)

        if missing:
            tools = " ".join(missing)
            brew_packages = [
                _BREW_PACKAGES[tool]
                for tool in missing
                if tool in _BREW_PACKAGES
            ]

            message = (
                f"{audio_format.value} の書き出しには次のコマンドが必要です: "
                f"{tools}"
            )

            if brew_packages:
                message += (
                    "\n\nHomebrewでインストールしてください:\n"
                    f"  brew install {' '.join(brew_packages)}"
                )

            QMessageBox.critical(
                self,
                "外部ツールが不足しています",
                message,
            )
            return

        dest_path = Path(dest_text)

        if dest_path.exists() and any(dest_path.iterdir()):
            reply = QMessageBox.question(
                self,
                "確認",
                f"{dest_path} は空ではありません。同名ファイルは上書きされます。続行しますか？",
                QMessageBox.StandardButton.Yes
                | QMessageBox.StandardButton.No,
            )

            if reply != QMessageBox.StandardButton.Yes:
                return

        self._is_audio_job = True
        self._cancel_requested = False
        self.log_view.clear()
        self.status_label.setText("オーディオトラックを書き出しています…")
        self._start_progress_indicator()
        self._set_controls_enabled(False)

        self._audio_work_tmpdir = tempfile.TemporaryDirectory(
            prefix="mkhybrid-gui-rip-"
        )

        device = whole_disk_raw_device(volume.device_identifier)

        worker = AudioRipWorker(
            device,
            dest_path,
            audio_format,
            self._audio_work_tmpdir.name,
            verify=self.verify_checkbox.isChecked(),
            album_metadata=self._collect_album_metadata(),
            parent=self,
        )

        worker.progress.connect(self._on_progress)
        worker.progress_percent.connect(self._on_progress_percent)
        worker.finished_ok.connect(self._on_finished)

        self._worker = worker
        worker.start()

    def _start_progress_indicator(self) -> None:
        """処理開始時に進捗バーを表示する。

        実際の進捗率が届くまでは、処理が固まっているように見えない
        よう不確定（ビジー）表示にする。``hdiutil makehybrid`` は
        実際には進捗率を一切出力しないため、ISO作成中はこのビジー表示の
        ままとなる（検証開始・完了時にのみ確定的な値へ切り替わる）。
        """
        self.progress_bar.setRange(0, 0)
        self.progress_bar.setVisible(True)

    def _on_progress_percent(self, percent: int) -> None:
        """ワーカーから受信した実進捗率を表示する。

        初めて実進捗率を受信した時点で、不確定（ビジー）表示から
        確定的な0〜100%表示へ切り替える。
        """
        if self.progress_bar.maximum() == 0:
            self.progress_bar.setRange(0, 100)

        self.progress_bar.setValue(max(0, min(percent, 100)))

    def _set_controls_enabled(self, enabled: bool) -> None:
        self.start_button.setEnabled(enabled)
        self.refresh_button.setEnabled(enabled)
        self.device_combo.setEnabled(enabled)
        self.output_edit.setEnabled(enabled)
        self.output_browse_button.setEnabled(enabled)
        self.joliet_checkbox.setEnabled(enabled)
        self.rock_checkbox.setEnabled(enabled)
        self.udf_checkbox.setEnabled(enabled)

        for radio in self._audio_format_buttons.values():
            radio.setEnabled(enabled)

        self.verify_checkbox.setEnabled(enabled)

        self.album_edit.setEnabled(enabled)
        self.artist_edit.setEnabled(enabled)
        self.year_edit.setEnabled(enabled)
        self.track_title_table.setEnabled(enabled)
        self.metadata_lookup_button.setEnabled(enabled)

        # 中断ボタンは処理中（enabled=False）のみ有効にする。
        self.cancel_button.setEnabled(not enabled)

    def _on_cancel_clicked(self) -> None:
        if self._worker is None:
            return

        reply = QMessageBox.question(
            self,
            "中断の確認",
            "実行中の処理を中断しますか？\n"
            "ここまでの一時ファイルは破棄されます。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )

        if reply != QMessageBox.StandardButton.Yes:
            return

        self._cancel_requested = True
        self.cancel_button.setEnabled(False)
        self.status_label.setText("中断しています…")
        self._worker.request_cancel()

    def _on_progress(self, line: str) -> None:
        self.log_view.appendPlainText(line)

    def _on_finished(self, ok: bool, message: str) -> None:
        if ok:
            self.progress_bar.setRange(0, 100)
            self.progress_bar.setValue(100)

        self.progress_bar.setVisible(False)

        self._set_controls_enabled(True)

        cancelled = self._cancel_requested and not ok
        self._cancel_requested = False

        self.status_label.setText(
            message if (ok or cancelled) else f"エラー: {message}"
        )

        if ok:
            QMessageBox.information(self, "完了", message)
        elif cancelled:
            QMessageBox.information(self, "中断しました", message)
        elif self._is_audio_job:
            QMessageBox.critical(self, "失敗", message)
        else:
            QMessageBox.critical(
                self,
                "失敗",
                f"{message}\n\n"
                "コピーガード付きメディア等、セクタ単位の読み取りが必要な場合は"
                "対応できません。専用ツールの利用をご検討ください。",
            )

        self._worker = None

        if self._is_audio_job and self._audio_work_tmpdir is not None:
            self._audio_work_tmpdir.cleanup()
            self._audio_work_tmpdir = None
