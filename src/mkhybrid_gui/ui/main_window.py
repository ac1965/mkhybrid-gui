"""メインウィンドウのUI定義。"""

from __future__ import annotations

import tempfile
from pathlib import Path

from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QRadioButton,
    QVBoxLayout,
    QWidget,
)

from mkhybrid_gui.audio_cd import AudioFormat, AudioRipWorker, missing_tools
from mkhybrid_gui.disk_utils import (
    DiskUtilError,
    MediaType,
    Volume,
    list_volumes,
    whole_disk_raw_device,
)
from mkhybrid_gui.iso_builder import IsoOptions, IsoWorker

# ALACを先頭（既定・推奨）にした表示順
_AUDIO_FORMAT_ORDER = [
    AudioFormat.ALAC,
    AudioFormat.AIFF,
    AudioFormat.FLAC,
    AudioFormat.WAV,
    AudioFormat.AAC,
]


class MainWindow(QMainWindow):
    """アプリケーションのメインウィンドウ。"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("mkhybrid-gui — ハイブリッドISO作成ツール")
        self.resize(640, 560)

        self._volumes: list[Volume] = []
        self._worker: IsoWorker | AudioRipWorker | None = None
        self._is_audio_job = False
        self._audio_work_tmpdir: tempfile.TemporaryDirectory[str] | None = None

        self._build_ui()
        self._refresh_volumes()

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

        self.media_info_label = QLabel("")
        root_layout.addWidget(self.media_info_label)

        self.start_button = QPushButton("ISOイメージを作成")
        self.start_button.clicked.connect(self._on_start_clicked)
        root_layout.addWidget(self.start_button)

        self.status_label = QLabel("待機中")
        root_layout.addWidget(self.status_label)

        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        root_layout.addWidget(self.log_view, stretch=1)

    # -- ドライブ一覧 -------------------------------------------------

    def _refresh_volumes(self) -> None:
        try:
            self._volumes = list_volumes()
        except DiskUtilError as exc:
            QMessageBox.critical(self, "エラー", str(exc))
            self._volumes = []

        self.device_combo.clear()
        for volume in self._volumes:
            self.device_combo.addItem(volume.display_name, userData=volume)

        if not self._volumes:
            self.device_combo.addItem("(利用可能なボリュームがありません)", userData=None)

        self._on_device_changed(self.device_combo.currentIndex())

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
            "オーディオトラックを書き出す" if is_audio else "ISOイメージを作成"
        )

        if volume is not None and not is_audio:
            # DVD/Blu-ray（BDXL・M-DISCを含む）は大容量ファイルを含みうるためUDFを既定でON
            self.udf_checkbox.setChecked(volume.media_type in (MediaType.DVD, MediaType.BD))

        self.media_info_label.setText(
            f"検出されたメディア種別: {volume.media_type.value}"
            if volume is not None and volume.media_type is not None
            else ""
        )

    def _choose_output_path(self) -> None:
        volume: Volume | None = self.device_combo.currentData()
        is_audio = volume is not None and volume.media_type == MediaType.CD_AUDIO

        if is_audio:
            path = QFileDialog.getExistingDirectory(self, "出力先フォルダを選択")
        else:
            path, _ = QFileDialog.getSaveFileName(
                self, "出力先ISOファイルを選択", "", "ISOイメージ (*.iso)"
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

    # -- 実行 ---------------------------------------------------------

    def _on_start_clicked(self) -> None:
        volume: Volume | None = self.device_combo.currentData()
        if volume is None:
            QMessageBox.warning(self, "選択エラー", "作成元のボリュームを選択してください。")
            return

        if volume.media_type == MediaType.CD_AUDIO:
            self._start_audio_rip(volume)
        else:
            self._start_iso_build(volume)

    def _start_iso_build(self, volume: Volume) -> None:
        output_text = self.output_edit.text().strip()
        if not output_text:
            QMessageBox.warning(self, "入力エラー", "出力先ISOファイルを指定してください。")
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
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                return

        source = volume.mount_point or f"/dev/{volume.device_identifier}"

        self._is_audio_job = False
        self.log_view.clear()
        self.status_label.setText("ISOイメージを作成しています…")
        self._set_controls_enabled(False)

        worker = IsoWorker(source, output_path, options, parent=self)
        worker.progress.connect(self._on_progress)
        worker.finished_ok.connect(self._on_finished)
        self._worker = worker
        worker.start()

    def _start_audio_rip(self, volume: Volume) -> None:
        dest_text = self.output_edit.text().strip()
        if not dest_text:
            QMessageBox.warning(self, "入力エラー", "出力先フォルダを指定してください。")
            return

        audio_format = self._selected_audio_format()
        missing = missing_tools(audio_format)
        if missing:
            tools = " ".join(missing)
            QMessageBox.critical(
                self,
                "外部ツールが不足しています",
                f"{audio_format.value} の書き出しには次のコマンドが必要です: {tools}\n\n"
                f"Homebrewでインストールしてください:\n  brew install {tools}",
            )
            return

        dest_path = Path(dest_text)
        if dest_path.exists() and any(dest_path.iterdir()):
            reply = QMessageBox.question(
                self,
                "確認",
                f"{dest_path} は空ではありません。同名ファイルは上書きされます。続行しますか？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                return

        self._is_audio_job = True
        self.log_view.clear()
        self.status_label.setText("オーディオトラックを書き出しています…")
        self._set_controls_enabled(False)

        self._audio_work_tmpdir = tempfile.TemporaryDirectory(prefix="mkhybrid-gui-rip-")
        device = whole_disk_raw_device(volume.device_identifier)

        worker = AudioRipWorker(
            device,
            dest_path,
            audio_format,
            self._audio_work_tmpdir.name,
            verify=self.verify_checkbox.isChecked(),
            parent=self,
        )
        worker.progress.connect(self._on_progress)
        worker.finished_ok.connect(self._on_finished)
        self._worker = worker
        worker.start()

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

    def _on_progress(self, line: str) -> None:
        self.log_view.appendPlainText(line)

    def _on_finished(self, ok: bool, message: str) -> None:
        self._set_controls_enabled(True)
        self.status_label.setText(message if ok else f"エラー: {message}")
        if ok:
            QMessageBox.information(self, "完了", message)
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
