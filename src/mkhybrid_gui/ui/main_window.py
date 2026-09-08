"""メインウィンドウのUI定義。"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
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
    QVBoxLayout,
    QWidget,
)

from mkhybrid_gui.disk_utils import DiskUtilError, Volume, list_volumes
from mkhybrid_gui.iso_builder import IsoOptions, IsoWorker


class MainWindow(QMainWindow):
    """アプリケーションのメインウィンドウ。"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("mkhybrid-gui — ハイブリッドISO作成ツール")
        self.resize(640, 480)

        self._volumes: list[Volume] = []
        self._worker: IsoWorker | None = None

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
        form.addRow("出力先ISO:", output_row)

        options_row = QHBoxLayout()
        self.joliet_checkbox = QCheckBox("Joliet")
        self.joliet_checkbox.setChecked(True)
        self.rock_checkbox = QCheckBox("Rock Ridge")
        self.rock_checkbox.setChecked(True)
        options_row.addWidget(self.joliet_checkbox)
        options_row.addWidget(self.rock_checkbox)
        options_row.addStretch(1)
        form.addRow("オプション:", options_row)

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

    def _choose_output_path(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self, "出力先ISOファイルを選択", "", "ISOイメージ (*.iso)"
        )
        if path:
            if not path.endswith(".iso"):
                path += ".iso"
            self.output_edit.setText(path)

    # -- 実行 ---------------------------------------------------------

    def _on_start_clicked(self) -> None:
        volume: Volume | None = self.device_combo.currentData()
        if volume is None:
            QMessageBox.warning(self, "選択エラー", "作成元のボリュームを選択してください。")
            return

        output_text = self.output_edit.text().strip()
        if not output_text:
            QMessageBox.warning(self, "入力エラー", "出力先ISOファイルを指定してください。")
            return

        if not self.joliet_checkbox.isChecked() and not self.rock_checkbox.isChecked():
            QMessageBox.warning(
                self, "オプションエラー", "Joliet / Rock Ridge のいずれかは有効にしてください。"
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

        options = IsoOptions(
            joliet=self.joliet_checkbox.isChecked(),
            rock=self.rock_checkbox.isChecked(),
        )

        source = volume.mount_point or f"/dev/{volume.device_identifier}"
        self._start_worker(source, output_path, options)

    def _start_worker(self, source: str, output_path: Path, options: IsoOptions) -> None:
        self.log_view.clear()
        self.status_label.setText("ISOイメージを作成しています…")
        self._set_controls_enabled(False)

        self._worker = IsoWorker(source, output_path, options, parent=self)
        self._worker.progress.connect(self._on_progress)
        self._worker.finished_ok.connect(self._on_finished)
        self._worker.start()

    def _set_controls_enabled(self, enabled: bool) -> None:
        self.start_button.setEnabled(enabled)
        self.refresh_button.setEnabled(enabled)
        self.device_combo.setEnabled(enabled)
        self.output_edit.setEnabled(enabled)
        self.output_browse_button.setEnabled(enabled)
        self.joliet_checkbox.setEnabled(enabled)
        self.rock_checkbox.setEnabled(enabled)

    def _on_progress(self, line: str) -> None:
        self.log_view.appendPlainText(line)

    def _on_finished(self, ok: bool, message: str) -> None:
        self._set_controls_enabled(True)
        self.status_label.setText(message if ok else f"エラー: {message}")
        if ok:
            QMessageBox.information(self, "完了", message)
        else:
            QMessageBox.critical(
                self,
                "失敗",
                f"{message}\n\n"
                "コピーガード付きメディア等、セクタ単位の読み取りが必要な場合は"
                "対応できません。専用ツールの利用をご検討ください。",
            )
        self._worker = None
