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
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from mkhybrid_gui import cdrdao, config
from mkhybrid_gui.audio_cd import (
    AudioCdError,
    AudioFormat,
    AudioRipWorker,
    DiscToc,
    disc_id_from_disc_toc,
    missing_tools,
    query_disc_toc,
)
from mkhybrid_gui.cdrdao import CdrdaoWorker
from mkhybrid_gui.disk_utils import (
    MediaType,
    Volume,
    list_volumes,
    whole_disk_raw_device,
)
from mkhybrid_gui.iso_builder import IsoOptions, IsoWorker
from mkhybrid_gui.metadata import (
    AlbumMetadata,
    TrackMetadata,
    sanitize_filename_component,
)
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
    "cdrdao": "cdrdao",
}


class MainWindow(QMainWindow):
    """アプリケーションのメインウィンドウ。"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("mkhybrid-gui — ハイブリッドISO作成ツール")
        self.resize(640, 560)

        self._volumes: list[Volume] = []
        self._worker: IsoWorker | AudioRipWorker | CdrdaoWorker | None = None
        self._is_audio_job = False
        self._cancel_requested = False
        self._audio_work_tmpdir: tempfile.TemporaryDirectory[str] | None = None
        self._disc_toc: DiscToc | None = None
        self._metadata_lookup_worker: MetadataLookupWorker | None = None
        self._last_output_directory = ""
        self._drive_option_note_shown = False

        self._build_ui()
        self._apply_config(config.get_config())
        self._refresh_volumes()

        # マウント済みのドライブが1つも無い場合、_refresh_volumes()が
        # device_comboに何も追加せず currentIndexChanged が一度も発火
        # しないため、_on_device_changed() が呼ばれないまま各ウィジェットが
        # 構築時の既定の表示状態（音楽CD用の項目も含めて可視）になって
        # しまう。明示的に一度呼び、実際の選択状態と表示を確実に一致させる。
        self._on_device_changed(self.device_combo.currentIndex())

    # -- UI構築 -----------------------------------------------------

    def _build_ui(self) -> None:
        self.tab_widget = QTabWidget(self)
        self.setCentralWidget(self.tab_widget)

        self.tab_widget.addTab(self._build_main_tab(), "ISO作成 / 音楽CD")
        self.tab_widget.addTab(self._build_settings_tab(), "設定")

    def _build_main_tab(self) -> QWidget:
        central = QWidget()
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

        audio_mode_row = QHBoxLayout()
        self.audio_mode_group = QButtonGroup(self)
        self.audio_mode_accurate_radio = QRadioButton(
            "正確なリッピング（cd-paranoia、トラックごとに変換・タグ付け）"
        )
        self.audio_mode_accurate_radio.setChecked(True)
        self.audio_mode_cdrdao_radio = QRadioButton(
            "ディスクイメージ（cdrdao、TOC+BIN。コピーガード付き等のフォールバック用）"
        )
        self.audio_mode_group.addButton(self.audio_mode_accurate_radio)
        self.audio_mode_group.addButton(self.audio_mode_cdrdao_radio)
        self.audio_mode_accurate_radio.toggled.connect(
            self._on_audio_mode_changed
        )
        audio_mode_row.addWidget(self.audio_mode_accurate_radio)
        audio_mode_row.addWidget(self.audio_mode_cdrdao_radio)
        audio_mode_row.addStretch(1)
        self.audio_mode_label = QLabel("音楽CDの書き出し方法:")
        form.addRow(self.audio_mode_label, audio_mode_row)

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

        return central

    def _build_settings_tab(self) -> QWidget:
        """「設定」タブ: ``config.toml`` のチューニング値をGUIから編集する。

        ここで編集した値は「設定を保存」ボタン、またはウィンドウを閉じた
        タイミングで ``config.save_config()`` によりTOMLへ反映・保存される
        （``_collect_current_config`` / ``_save_current_settings``）。
        """
        tab = QWidget()
        layout = QVBoxLayout(tab)

        form = QFormLayout()
        layout.addLayout(form)

        self.cd_max_size_spin = QSpinBox()
        self.cd_max_size_spin.setRange(1, 999_999)
        self.cd_max_size_spin.setSuffix(" MB")
        form.addRow(
            "データCDとして扱う最大サイズ:", self.cd_max_size_spin
        )

        self.dvd_max_size_spin = QSpinBox()
        self.dvd_max_size_spin.setRange(1, 999_999)
        self.dvd_max_size_spin.setSuffix(" MB")
        form.addRow(
            "DVDとして扱う最大サイズ（超過時はBlu-ray扱い）:",
            self.dvd_max_size_spin,
        )

        self.audio_verify_attempts_spin = QSpinBox()
        self.audio_verify_attempts_spin.setRange(1, 10)
        self.audio_verify_attempts_spin.setSuffix(" 回")
        form.addRow(
            "音楽CD検証の最大試行回数:", self.audio_verify_attempts_spin
        )

        output_dir_row = QHBoxLayout()
        self.settings_output_dir_edit = QLineEdit()
        self.settings_output_dir_edit.setReadOnly(True)
        self.settings_output_dir_browse_button = QPushButton("参照…")
        self.settings_output_dir_browse_button.clicked.connect(
            self._on_settings_output_dir_browse_clicked
        )
        output_dir_row.addWidget(self.settings_output_dir_edit, stretch=1)
        output_dir_row.addWidget(self.settings_output_dir_browse_button)
        form.addRow(
            "音楽CDの既定の出力先フォルダ:", output_dir_row
        )

        # QFormLayoutの2カラム構造（キャプション列/値列）だと、値列の
        # 実効幅がウィンドウ幅に対して狭くなりがちで、長いパス文字列が
        # 折り返されずに途中で見切れてしまう（実機で報告された不具合）。
        # そのため、このラベルだけはformの外に出し、タブの全幅を使う
        # 独立した行として配置する。
        settings_path_caption = QLabel("設定ファイルの場所:")
        layout.addWidget(settings_path_caption)

        self.settings_path_label = QLabel(str(config.get_config_path()))
        self.settings_path_label.setWordWrap(True)
        self.settings_path_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        layout.addWidget(self.settings_path_label)

        save_row = QHBoxLayout()
        self.settings_save_button = QPushButton("設定を保存")
        self.settings_save_button.clicked.connect(
            self._on_settings_save_clicked
        )
        save_row.addWidget(self.settings_save_button)
        save_row.addStretch(1)
        layout.addLayout(save_row)

        self.settings_status_label = QLabel("")
        layout.addWidget(self.settings_status_label)

        layout.addStretch(1)

        return tab

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

        self._save_current_settings()
        super().closeEvent(event)

    # -- 設定（GUIオプションのTOML反映/保存） -----------------------------

    #: サイズ閾値スピンボックス（MB単位表示）とバイト単位の内部値との
    #: 換算係数。``MediaSizeThresholds`` の既定値（10億/100億バイト）が
    #: ちょうど1000/10000 MBになる10進基準を採用する（``QSpinBox`` は
    #: 32bit intのため、バイト単位のまま扱うとDVD既定値付近で桁があふれる）。
    _BYTES_PER_MB = 1_000_000

    def _apply_config(self, app_config: config.AppConfig) -> None:
        """読み込んだ ``AppConfig`` を「作成」タブ・「設定」タブ両方へ反映する。

        設定ファイルが存在しない場合は既定値の ``AppConfig`` が渡され、
        ``_build_ui()`` 構築時の既定状態と同じ値になるため、実質的に
        何も変化しない。
        """
        self._last_output_directory = app_config.ui.last_output_directory
        self.settings_output_dir_edit.setText(self._last_output_directory)
        self.joliet_checkbox.setChecked(app_config.ui.joliet)
        self.rock_checkbox.setChecked(app_config.ui.rock)
        self.udf_checkbox.setChecked(app_config.ui.udf)
        self.verify_checkbox.setChecked(app_config.ui.verify)

        try:
            audio_format = AudioFormat[app_config.ui.audio_format]
        except KeyError:
            audio_format = AudioFormat.ALAC

        self._audio_format_buttons[audio_format].setChecked(True)

        if app_config.ui.audio_rip_mode == "CDRDAO_IMAGE":
            self.audio_mode_cdrdao_radio.setChecked(True)
        else:
            self.audio_mode_accurate_radio.setChecked(True)

        self.cd_max_size_spin.setValue(
            max(1, app_config.media_size.cd_max_bytes // self._BYTES_PER_MB)
        )
        self.dvd_max_size_spin.setValue(
            max(1, app_config.media_size.dvd_max_bytes // self._BYTES_PER_MB)
        )
        self.audio_verify_attempts_spin.setValue(
            app_config.audio_rip.max_attempts
        )

    def _collect_current_config(self) -> config.AppConfig:
        """「作成」タブ・「設定」タブ両方の現在の状態から ``AppConfig`` を組み立てる。"""
        media_size = config.MediaSizeThresholds(
            cd_max_bytes=self.cd_max_size_spin.value() * self._BYTES_PER_MB,
            dvd_max_bytes=self.dvd_max_size_spin.value() * self._BYTES_PER_MB,
        )
        audio_rip = config.AudioRipSettings(
            max_attempts=self.audio_verify_attempts_spin.value()
        )
        ui = config.UiPreferences(
            last_output_directory=self._last_output_directory,
            joliet=self.joliet_checkbox.isChecked(),
            rock=self.rock_checkbox.isChecked(),
            udf=self.udf_checkbox.isChecked(),
            verify=self.verify_checkbox.isChecked(),
            audio_format=self._selected_audio_format().name,
            audio_rip_mode=(
                "CDRDAO_IMAGE"
                if self._is_cdrdao_mode_selected()
                else "ACCURATE"
            ),
        )

        return config.AppConfig(
            media_size=media_size, audio_rip=audio_rip, ui=ui
        )

    def _save_current_settings(self) -> bool:
        """現在の「作成」タブ・「設定」タブの内容を ``config.toml`` へ保存する。

        保存に失敗しても（設定ディレクトリへの書き込み権限が無い場合等）
        呼び出し元の処理（ウィンドウを閉じる、等）は妨げない。
        """
        try:
            config.save_config(self._collect_current_config())
        except OSError:
            return False

        return True

    def _on_settings_output_dir_browse_clicked(self) -> None:
        """「設定」タブから、音楽CDの既定の出力先フォルダを変更する。

        Finderの標準的なフォルダ選択ダイアログ（``QFileDialog``）で選び、
        「作成」タブの出力先フォルダ欄が現在音楽CDモードであれば、
        そちらにも即座に反映する（ISO作成モードの場合はファイル名まで
        必要なため上書きしない）。
        """
        path = QFileDialog.getExistingDirectory(
            self,
            "音楽CDの既定の出力先フォルダを選択",
            self._last_output_directory or "",
        )

        if not path:
            return

        self._last_output_directory = path
        self.settings_output_dir_edit.setText(path)

        volume: Volume | None = self.device_combo.currentData()
        is_audio = (
            volume is not None and volume.media_type == MediaType.CD_AUDIO
        )

        if is_audio:
            self.output_edit.setText(path)

    def _on_settings_save_clicked(self) -> None:
        """「設定」タブの「設定を保存」ボタン。

        「作成」タブの現在のオプション（Joliet/UDF等）も合わせて保存する
        （``closeEvent`` での自動保存と同じ ``_save_current_settings`` を
        使うため）。
        """
        if self._save_current_settings():
            self.settings_status_label.setText(
                f"設定を保存しました（{config.get_config_path()}）。"
            )
        else:
            self.settings_status_label.setText(
                "設定を保存できませんでした"
                "（保存先への書き込み権限を確認してください）。"
            )

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

        # 音楽CD選択時、出力先フォルダ欄が空であれば前回の出力先
        # （設定ファイルに保存された last_output_directory）を初期値
        # として表示する。以前は「参照…」ダイアログの初期フォルダとして
        # しか使われておらず、この欄自体には反映されていなかった
        # （実機での報告により発見）。ISO作成では出力先はファイル名
        # まで含む必要があり、前回のフォルダだけを補完しても不完全な
        # パスになってしまうため、音楽CD選択時のみ行う。
        if (
            is_audio
            and not self.output_edit.text().strip()
            and self._last_output_directory
        ):
            self.output_edit.setText(self._last_output_directory)

        self.options_label.setVisible(not is_audio)
        self.joliet_checkbox.setVisible(not is_audio)
        self.rock_checkbox.setVisible(not is_audio)
        self.udf_checkbox.setVisible(not is_audio)

        self.audio_mode_label.setVisible(is_audio)
        self.audio_mode_accurate_radio.setVisible(is_audio)
        self.audio_mode_cdrdao_radio.setVisible(is_audio)

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

        if is_audio and volume is not None:
            self._prepare_audio_metadata_ui(volume)
        else:
            self._disc_toc = None
            self.track_title_table.setRowCount(0)
            self.album_edit.clear()
            self.artist_edit.clear()
            self.year_edit.clear()
            self.metadata_status_label.setText("")

        self._on_audio_mode_changed()

    def _is_cdrdao_mode_selected(self) -> bool:
        return self.audio_mode_cdrdao_radio.isChecked()

    def _on_audio_mode_changed(self, *_args: object) -> None:
        """音楽CDの「正確なリッピング」/「ディスクイメージ（cdrdao）」の
        切り替え、およびISO作成/音楽CD自体の切り替えの両方に応じて、
        「作成」タブのウィジェット表示を更新する。

        cdrdaoモードは、トラックごとの変換・タグ付けを行わない
        ディスク全体のバックアップのため、正確なリッピング専用の
        ウィジェット（書き出し形式・厳密な検証・アーティスト名/年・
        MusicBrainz検索・トラック名テーブル）を隠す。``album_edit``は
        両モードで表示したままにする（cdrdaoモードでのTOC+BINファイル名
        にも使うため）。
        """
        volume: Volume | None = self.device_combo.currentData()
        is_audio = (
            volume is not None and volume.media_type == MediaType.CD_AUDIO
        )
        is_cdrdao = is_audio and self._is_cdrdao_mode_selected()
        accurate_visible = is_audio and not is_cdrdao

        self.audio_format_label.setVisible(accurate_visible)

        for radio in self._audio_format_buttons.values():
            radio.setVisible(accurate_visible)

        self.verify_checkbox.setVisible(accurate_visible)
        self.artist_edit.setVisible(accurate_visible)
        self.year_edit.setVisible(accurate_visible)
        self.metadata_lookup_row_label.setVisible(accurate_visible)
        self.metadata_lookup_button.setVisible(accurate_visible)
        self.metadata_privacy_label.setVisible(accurate_visible)
        self.metadata_status_label.setVisible(accurate_visible)
        self.track_title_table.setVisible(accurate_visible)

        if is_cdrdao:
            self.start_button.setText("ディスクイメージを作成")
        elif is_audio:
            self.start_button.setText("オーディオトラックを書き出す")
        else:
            self.start_button.setText("ISOイメージを作成")

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
                self._last_output_directory,
            )
        else:
            path, _ = QFileDialog.getSaveFileName(
                self,
                "出力先ISOファイルを選択",
                self._last_output_directory,
                "ISOイメージ (*.iso)",
            )

            if path and not path.endswith(".iso"):
                path += ".iso"

        if path:
            self.output_edit.setText(path)
            # 次回のファイルダイアログの初期表示位置、および設定保存用に
            # 「フォルダ」を記録する（ISO作成時はファイルの親ディレクトリ、
            # 音楽CDリッピング時は選択したフォルダそのもの）。
            self._last_output_directory = (
                path if is_audio else str(Path(path).parent)
            )

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
            if self._is_cdrdao_mode_selected():
                self._start_cdrdao_rip(volume)
            else:
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

        # Rock Ridgeはこの判定に含めない。`options.rock` は
        # 実際のhdiutilコマンドには一切影響しない（`-iso`指定時に
        # 自動的に有効になり、`-rock`という形で明示的に渡すことは
        # できない）ため、Rock Ridgeだけを有効にした状態と全項目を
        # 無効にした状態とで、生成されるISOイメージの中身が完全に
        # 同一になってしまう。それにもかかわらずRock Ridgeを含めて
        # 判定すると、後者だけがエラーになるという一貫性のない挙動に
        # なる（実機での報告により発見）。
        if not (options.joliet or options.udf):
            QMessageBox.warning(
                self,
                "オプションエラー",
                "Joliet / UDF のいずれかは有効にしてください。",
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
        self._drive_option_note_shown = False
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
        album_metadata = self._collect_album_metadata()
        fallback_folder_name = volume.volume_name or volume.device_identifier

        # 実際の書き出し先は、dest_path直下ではなく、アルバム名
        # （未入力ならディスクのボリューム名）のサブディレクトリになる
        # （audio_cd.rip_and_convert_discと同じロジック）。上書き確認は
        # 実際に書き込まれる場所に対して行う。
        if album_metadata is not None and album_metadata.album:
            folder_name = album_metadata.album
        else:
            folder_name = fallback_folder_name

        actual_output_dir = dest_path / sanitize_filename_component(
            folder_name
        )

        if actual_output_dir.exists() and any(
            actual_output_dir.iterdir()
        ):
            reply = QMessageBox.question(
                self,
                "確認",
                f"{actual_output_dir} は空ではありません。"
                "同名ファイルは上書きされます。続行しますか？",
                QMessageBox.StandardButton.Yes
                | QMessageBox.StandardButton.No,
            )

            if reply != QMessageBox.StandardButton.Yes:
                return

        self._is_audio_job = True
        self._cancel_requested = False
        self._drive_option_note_shown = False
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
            album_metadata=album_metadata,
            fallback_folder_name=fallback_folder_name,
            parent=self,
        )

        worker.progress.connect(self._on_progress)
        worker.progress_percent.connect(self._on_progress_percent)
        worker.finished_ok.connect(self._on_finished)

        self._worker = worker
        worker.start()

    def _start_cdrdao_rip(self, volume: Volume) -> None:
        """cdrdaoによるディスクイメージ（TOC+BIN）作成を開始する。

        トラックごとの変換・タグ付けは行わないため、
        ``audio_cd.rip_and_convert_disc``のようなアルバム名サブ
        フォルダは作らず、出力先フォルダ直下に直接``{アルバム名}.toc``/
        ``.bin``を書き出す（ISO作成と同様、1回の実行につき1組の
        ファイルのため）。
        """
        dest_text = self.output_edit.text().strip()

        if not dest_text:
            QMessageBox.warning(
                self,
                "入力エラー",
                "出力先フォルダを指定してください。",
            )
            return

        missing = cdrdao.missing_tools()

        if missing:
            tools = " ".join(missing)
            brew_packages = [
                _BREW_PACKAGES[tool]
                for tool in missing
                if tool in _BREW_PACKAGES
            ]

            message = f"ディスクイメージの作成には次のコマンドが必要です: {tools}"

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
        fallback_base_name = volume.volume_name or volume.device_identifier
        base_name = sanitize_filename_component(
            self.album_edit.text().strip() or fallback_base_name
        )

        toc_path = dest_path / f"{base_name}.toc"
        bin_path = dest_path / f"{base_name}.bin"

        if toc_path.exists() or bin_path.exists():
            reply = QMessageBox.question(
                self,
                "確認",
                f"{toc_path.name} / {bin_path.name} は既に存在します。"
                "上書きしますか？",
                QMessageBox.StandardButton.Yes
                | QMessageBox.StandardButton.No,
            )

            if reply != QMessageBox.StandardButton.Yes:
                return

        self._is_audio_job = True
        self._cancel_requested = False
        self._drive_option_note_shown = False
        # cdrdaoは一時作業ディレクトリを使わないため、前回の正確な
        # リッピングで使った TemporaryDirectory を _on_finished() が
        # 誤って二重クリーンアップしないよう、明示的に None へ戻す。
        self._audio_work_tmpdir = None
        self.log_view.clear()
        self.status_label.setText("ディスクイメージを作成しています…")
        self._start_progress_indicator()
        self._set_controls_enabled(False)

        device = whole_disk_raw_device(volume.device_identifier)

        worker = CdrdaoWorker(device, dest_path, base_name, parent=self)

        worker.progress.connect(self._on_progress)
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
        self.audio_mode_accurate_radio.setEnabled(enabled)
        self.audio_mode_cdrdao_radio.setEnabled(enabled)

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

        # cd-paranoiaがドライブ側の未対応な拡張コマンドを検出した際に
        # 出す通知（例: "405: Option not supported by drive"）は、
        # エラーのように見えるが実際にはリッピングの継続に影響しない
        # （ドライブが特定の任意機能に対応していないという診断メッセージ
        # であり、cd-paranoia自体がそのまま処理を続行する）。実機での
        # 報告により、説明が無いためエラーだと誤解されやすいことが
        # 判明したため、ログ本文はそのまま残しつつ、1回だけ補足を添える。
        if (
            not self._drive_option_note_shown
            and "Option not supported by drive" in line
        ):
            self._drive_option_note_shown = True
            self.log_view.appendPlainText(
                "  → 上記はドライブが一部の拡張コマンドに対応していない"
                "という通知です。リッピング自体には影響なく、"
                "そのまま処理が続行されます。"
            )

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
