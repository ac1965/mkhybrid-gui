"""``MainWindow`` のGUIロジックのテスト（``pytest-qt`` 使用）。

実際の ``hdiutil``/``diskutil``/``cd-paranoia`` やMusicBrainzへのネットワーク
通信は一切行わない。``IsoWorker``/``AudioRipWorker``/``MetadataLookupWorker``
（いずれも実際の外部コマンド実行・ネットワーク通信を伴う ``QThread``）は
実際に起動せず、``list_volumes``/``query_disc_toc``/``missing_tools`` 等の
呼び出し境界をモック化して、ウィジェットの表示切り替え・入力検証・状態
管理のロジックのみを検証する。

``QMessageBox`` はモーダルダイアログのため、staticメソッドを差し替えて
テストがブロックされないようにする（``_no_modal_dialogs`` フィクスチャ）。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtWidgets import QMessageBox

from mkhybrid_gui import config
from mkhybrid_gui.audio_cd import AudioCdError, AudioFormat, DiscToc
from mkhybrid_gui.disk_utils import MediaType, Volume
from mkhybrid_gui.metadata import AlbumMetadata
from mkhybrid_gui.musicbrainz import LookupResult, ReleaseCandidate
from mkhybrid_gui.ui import main_window as main_window_module
from mkhybrid_gui.ui.main_window import MainWindow

# --- 共通フィクスチャ -----------------------------------------------------


@pytest.fixture(autouse=True)
def _no_modal_dialogs(monkeypatch: pytest.MonkeyPatch):
    """``QMessageBox`` のモーダル表示を抑止し、呼び出し内容を記録する。

    ``question`` は既定で「はい」を返す（確認ダイアログを進める側に
    倒す）。「いいえ」の挙動を検証したいテストは、このフィクスチャが
    返す ``calls`` を使わず、個別に ``QMessageBox.question`` を
    上書きすること。
    """
    calls: list[tuple[str, tuple, dict]] = []

    def _make(name: str, return_value):
        def _call(*args, **kwargs):
            calls.append((name, args, kwargs))
            return return_value

        return _call

    monkeypatch.setattr(
        QMessageBox, "warning", _make("warning", QMessageBox.StandardButton.Ok)
    )
    monkeypatch.setattr(
        QMessageBox,
        "critical",
        _make("critical", QMessageBox.StandardButton.Ok),
    )
    monkeypatch.setattr(
        QMessageBox,
        "information",
        _make("information", QMessageBox.StandardButton.Ok),
    )
    monkeypatch.setattr(
        QMessageBox,
        "question",
        _make("question", QMessageBox.StandardButton.Yes),
    )

    return calls


@pytest.fixture
def window(qtbot, monkeypatch: pytest.MonkeyPatch) -> MainWindow:
    """マウント済みボリュームが無い状態のクリーンな ``MainWindow``。"""
    monkeypatch.setattr(main_window_module, "list_volumes", lambda: [])

    w = MainWindow()
    qtbot.addWidget(w)
    return w


def _dvd_volume() -> Volume:
    return Volume(
        device_identifier="disk4",
        volume_name="TEST_DVD",
        mount_point="/Volumes/TEST_DVD",
        size=4_000_000_000,
        content=None,
        filesystem_type="udf",
        media_type=MediaType.DVD,
    )


def _audio_volume() -> Volume:
    return Volume(
        device_identifier="disk5",
        volume_name="TEST_CD",
        mount_point="/Volumes/TEST_CD",
        size=200_000_000,
        content=None,
        filesystem_type="cddafs",
        media_type=MediaType.CD_AUDIO,
    )


def _select_volume(window: MainWindow, volume: Volume) -> None:
    window.device_combo.addItem(volume.volume_name, volume)
    window.device_combo.setCurrentIndex(window.device_combo.count() - 1)


class _FakeWorker:
    """``IsoWorker``/``AudioRipWorker`` の代わりに使う最小限のフェイク。"""

    def __init__(self, running: bool = True) -> None:
        self._running = running
        self.cancel_requested = False

    def isRunning(self) -> bool:  # noqa: N802 - Qtの命名規則に合わせる
        return self._running

    def request_cancel(self) -> None:
        self.cancel_requested = True


# --- 構築・初期状態 --------------------------------------------------------


def test_window_constructs_with_no_volumes(window: MainWindow) -> None:
    assert window.device_combo.count() == 0
    assert window.cancel_button.isEnabled() is False
    assert window.start_button.text() == "ISOイメージを作成"


def test_metadata_widgets_hidden_by_default(
    qtbot, window: MainWindow
) -> None:
    window.show()
    assert window.album_edit.isVisible() is False
    assert window.track_title_table.isVisible() is False


# --- デバイス選択でのUI切り替え --------------------------------------------


def test_selecting_dvd_shows_iso_options_and_hides_metadata(
    qtbot, window: MainWindow
) -> None:
    window.show()
    _select_volume(window, _dvd_volume())

    assert window.joliet_checkbox.isVisible() is True
    assert window.album_edit.isVisible() is False
    assert window.track_title_table.isVisible() is False
    assert window.start_button.text() == "ISOイメージを作成"
    # DVDはUDFが既定でON
    assert window.udf_checkbox.isChecked() is True


def test_selecting_audio_cd_shows_metadata_and_hides_iso_options(
    qtbot,
    window: MainWindow,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    toc = DiscToc(track_offsets=[0, 1000, 2000], leadout_offset=3000)
    monkeypatch.setattr(
        main_window_module, "query_disc_toc", lambda device: toc
    )

    window.show()
    _select_volume(window, _audio_volume())

    assert window.joliet_checkbox.isVisible() is False
    assert window.album_edit.isVisible() is True
    assert window.track_title_table.isVisible() is True
    assert window.start_button.text() == "オーディオトラックを書き出す"
    assert window.track_title_table.rowCount() == 3
    assert window.track_title_table.item(0, 0).text() == "1"
    assert "3トラック" in window.metadata_status_label.text()


def test_audio_cd_toc_failure_shows_error_status(
    qtbot,
    window: MainWindow,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _raise(device):
        raise AudioCdError("cd-paranoia が見つかりません。")

    monkeypatch.setattr(main_window_module, "query_disc_toc", _raise)

    window.show()
    _select_volume(window, _audio_volume())

    assert window.track_title_table.rowCount() == 0
    assert "cd-paranoia" in window.metadata_status_label.text()


def test_switching_back_to_dvd_clears_metadata_state(
    qtbot,
    window: MainWindow,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    toc = DiscToc(track_offsets=[0], leadout_offset=1000)
    monkeypatch.setattr(
        main_window_module, "query_disc_toc", lambda device: toc
    )

    window.show()
    _select_volume(window, _audio_volume())
    window.album_edit.setText("何か入力")

    _select_volume(window, _dvd_volume())

    assert window.track_title_table.rowCount() == 0
    assert window.album_edit.text() == ""


# --- 入力検証（開始ボタン） -------------------------------------------------


def test_start_without_volume_warns(
    window: MainWindow, _no_modal_dialogs: list
) -> None:
    window._on_start_clicked()

    assert any(name == "warning" for name, _, _ in _no_modal_dialogs)


def test_start_iso_build_warns_on_empty_output_path(
    qtbot, window: MainWindow, _no_modal_dialogs: list
) -> None:
    window.show()
    _select_volume(window, _dvd_volume())
    window.output_edit.setText("")

    window._on_start_clicked()

    assert any(name == "warning" for name, _, _ in _no_modal_dialogs)
    assert window._worker is None


def test_start_iso_build_warns_when_all_format_options_disabled(
    qtbot, window: MainWindow, _no_modal_dialogs: list, tmp_path: Path
) -> None:
    window.show()
    _select_volume(window, _dvd_volume())
    window.output_edit.setText(str(tmp_path / "out.iso"))
    window.joliet_checkbox.setChecked(False)
    window.rock_checkbox.setChecked(False)
    window.udf_checkbox.setChecked(False)

    window._on_start_clicked()

    assert any(name == "warning" for name, _, _ in _no_modal_dialogs)
    assert window._worker is None


def test_start_iso_build_rock_ridge_alone_is_treated_like_no_options(
    qtbot, window: MainWindow, _no_modal_dialogs: list, tmp_path: Path
) -> None:
    """Rock Ridgeだけを有効にした状態は、実際には全項目無効の状態と
    同一のコマンド（``-iso`` のみ）を生成する（``-rock`` は
    ``hdiutil makehybrid`` に渡せず、``-iso`` 指定時に自動的に有効に
    なるため）。したがって「Rock Ridgeだけ有効」も「全項目無効」も、
    どちらも同じ警告になるべきで、前者だけ素通りするのは一貫性のない
    挙動（実機で報告された不具合）。
    """
    window.show()
    _select_volume(window, _dvd_volume())
    window.output_edit.setText(str(tmp_path / "out.iso"))
    window.joliet_checkbox.setChecked(False)
    window.rock_checkbox.setChecked(True)
    window.udf_checkbox.setChecked(False)

    window._on_start_clicked()

    assert any(name == "warning" for name, _, _ in _no_modal_dialogs)
    assert window._worker is None


def test_start_audio_rip_warns_on_empty_output_path(
    qtbot,
    window: MainWindow,
    monkeypatch: pytest.MonkeyPatch,
    _no_modal_dialogs: list,
) -> None:
    monkeypatch.setattr(
        main_window_module,
        "query_disc_toc",
        lambda device: DiscToc(track_offsets=[0], leadout_offset=1000),
    )

    window.show()
    _select_volume(window, _audio_volume())
    window.output_edit.setText("")

    window._on_start_clicked()

    assert any(name == "warning" for name, _, _ in _no_modal_dialogs)
    assert window._worker is None


def test_start_audio_rip_warns_on_missing_tools(
    qtbot,
    window: MainWindow,
    monkeypatch: pytest.MonkeyPatch,
    _no_modal_dialogs: list,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        main_window_module,
        "query_disc_toc",
        lambda device: DiscToc(track_offsets=[0], leadout_offset=1000),
    )
    monkeypatch.setattr(
        main_window_module, "missing_tools", lambda fmt: ["cd-paranoia"]
    )

    window.show()
    _select_volume(window, _audio_volume())
    window.output_edit.setText(str(tmp_path / "out"))

    window._on_start_clicked()

    assert any(name == "critical" for name, _, _ in _no_modal_dialogs)
    assert window._worker is None


# --- メタデータの収集 ------------------------------------------------------


def test_collect_album_metadata_returns_none_when_all_empty(
    qtbot,
    window: MainWindow,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        main_window_module,
        "query_disc_toc",
        lambda device: DiscToc(track_offsets=[0, 1000], leadout_offset=2000),
    )
    window.show()
    _select_volume(window, _audio_volume())

    assert window._collect_album_metadata() is None


def test_collect_album_metadata_returns_album_when_track_title_entered(
    qtbot,
    window: MainWindow,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        main_window_module,
        "query_disc_toc",
        lambda device: DiscToc(track_offsets=[0, 1000], leadout_offset=2000),
    )
    window.show()
    _select_volume(window, _audio_volume())

    window.album_edit.setText("Test Album")
    window.track_title_table.item(0, 1).setText("Opening")

    album = window._collect_album_metadata()

    assert album is not None
    assert album.album == "Test Album"
    assert album.track_title(1) == "Opening"
    assert album.track_title(2) == ""


# --- メタデータのオンライン検索 --------------------------------------------


def test_metadata_lookup_without_toc_warns(
    window: MainWindow, _no_modal_dialogs: list
) -> None:
    window._on_metadata_lookup_clicked()

    assert any(name == "warning" for name, _, _ in _no_modal_dialogs)


def test_metadata_lookup_finished_applies_single_candidate(
    qtbot,
    window: MainWindow,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        main_window_module,
        "query_disc_toc",
        lambda device: DiscToc(track_offsets=[0], leadout_offset=1000),
    )
    window.show()
    _select_volume(window, _audio_volume())

    from mkhybrid_gui.metadata import TrackMetadata

    candidate = ReleaseCandidate(
        album=AlbumMetadata(
            album="Found Album",
            artist="Found Artist",
            year="2001",
            tracks=[TrackMetadata(title="Found Title")],
        ),
        country="JP",
    )

    window._on_metadata_lookup_finished(
        LookupResult(candidates=[candidate])
    )

    assert window.album_edit.text() == "Found Album"
    assert window.artist_edit.text() == "Found Artist"
    assert window.track_title_table.item(0, 1).text() == "Found Title"


def test_metadata_lookup_finished_with_no_candidates_shows_status(
    qtbot,
    window: MainWindow,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        main_window_module,
        "query_disc_toc",
        lambda device: DiscToc(track_offsets=[0], leadout_offset=1000),
    )
    window.show()
    _select_volume(window, _audio_volume())

    window._on_metadata_lookup_finished(LookupResult(candidates=[]))

    assert "見つかりませんでした" in window.metadata_status_label.text()


def test_metadata_lookup_finished_with_error_shows_status(
    qtbot,
    window: MainWindow,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        main_window_module,
        "query_disc_toc",
        lambda device: DiscToc(track_offsets=[0], leadout_offset=1000),
    )
    window.show()
    _select_volume(window, _audio_volume())

    window._on_metadata_lookup_finished(
        LookupResult(error="ネットワークエラー")
    )

    assert "ネットワークエラー" in window.metadata_status_label.text()


def test_metadata_lookup_asks_before_overwriting_existing_input(
    qtbot,
    window: MainWindow,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        main_window_module,
        "query_disc_toc",
        lambda device: DiscToc(track_offsets=[0], leadout_offset=1000),
    )
    window.show()
    _select_volume(window, _audio_volume())
    window.album_edit.setText("既存の入力")

    # question()が「いいえ」を返すよう上書きする。
    monkeypatch.setattr(
        QMessageBox,
        "question",
        lambda *a, **k: QMessageBox.StandardButton.No,
    )

    candidate = ReleaseCandidate(
        album=AlbumMetadata(album="Found Album", artist="", tracks=[])
    )
    window._on_metadata_lookup_finished(
        LookupResult(candidates=[candidate])
    )

    # 「いいえ」を選んだので上書きされない。
    assert window.album_edit.text() == "既存の入力"


# --- 中断・終了 -------------------------------------------------------------


def test_set_controls_enabled_toggles_cancel_button(
    window: MainWindow,
) -> None:
    window._set_controls_enabled(False)
    assert window.cancel_button.isEnabled() is True
    assert window.start_button.isEnabled() is False

    window._set_controls_enabled(True)
    assert window.cancel_button.isEnabled() is False
    assert window.start_button.isEnabled() is True


def test_cancel_button_requests_cancel_after_confirmation(
    window: MainWindow,
) -> None:
    fake_worker = _FakeWorker(running=True)
    window._worker = fake_worker

    window._on_cancel_clicked()

    assert fake_worker.cancel_requested is True
    assert window._cancel_requested is True


def test_cancel_button_does_nothing_without_worker(
    window: MainWindow,
) -> None:
    window._worker = None
    window._on_cancel_clicked()  # クラッシュしないことのみ確認


def test_close_event_blocks_while_worker_running(
    window: MainWindow, _no_modal_dialogs: list
) -> None:
    from PySide6.QtGui import QCloseEvent

    window._worker = _FakeWorker(running=True)
    event = QCloseEvent()

    window.closeEvent(event)

    assert event.isAccepted() is False
    assert any(name == "warning" for name, _, _ in _no_modal_dialogs)


def test_close_event_allows_close_when_idle(window: MainWindow) -> None:
    from PySide6.QtGui import QCloseEvent

    window._worker = None
    event = QCloseEvent()

    window.closeEvent(event)

    assert event.isAccepted() is True


# --- 進捗表示 ---------------------------------------------------------------


def test_progress_indicator_starts_busy_then_becomes_determinate(
    window: MainWindow,
) -> None:
    window._start_progress_indicator()
    assert (window.progress_bar.minimum(), window.progress_bar.maximum()) == (
        0,
        0,
    )

    window._on_progress_percent(42)
    assert (window.progress_bar.minimum(), window.progress_bar.maximum()) == (
        0,
        100,
    )
    assert window.progress_bar.value() == 42


# --- 設定（GUIオプションのTOML保存/復元） -------------------------------


def test_construction_applies_saved_ui_preferences(
    qtbot, monkeypatch: pytest.MonkeyPatch
) -> None:
    """設定ファイルに保存済みの値が、起動時に各ウィジェットへ反映される。

    ``conftest.py`` の ``_isolate_app_config``（autouse）が
    ``MKHYBRID_GUI_CONFIG`` を一時ファイルへ向けているため、ここで
    ``config.save_config`` した内容がそのまま次に構築する
    ``MainWindow`` から読み込まれる。
    """
    monkeypatch.setattr(main_window_module, "list_volumes", lambda: [])

    config.save_config(
        config.AppConfig(
            ui=config.UiPreferences(
                last_output_directory="/Volumes/Backup",
                joliet=False,
                rock=False,
                udf=True,
                verify=False,
                audio_format="FLAC",
            )
        )
    )

    w = MainWindow()
    qtbot.addWidget(w)

    assert w.joliet_checkbox.isChecked() is False
    assert w.rock_checkbox.isChecked() is False
    assert w.udf_checkbox.isChecked() is True
    assert w.verify_checkbox.isChecked() is False
    assert w._audio_format_buttons[AudioFormat.FLAC].isChecked() is True
    assert w._last_output_directory == "/Volumes/Backup"


def test_construction_falls_back_to_alac_for_unknown_saved_format(
    qtbot, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(main_window_module, "list_volumes", lambda: [])

    config.save_config(
        config.AppConfig(ui=config.UiPreferences(audio_format="MP3"))
    )

    w = MainWindow()
    qtbot.addWidget(w)

    assert w._audio_format_buttons[AudioFormat.ALAC].isChecked() is True


def test_close_event_saves_current_ui_preferences(
    window: MainWindow,
) -> None:
    from PySide6.QtGui import QCloseEvent

    window.joliet_checkbox.setChecked(False)
    window.rock_checkbox.setChecked(False)
    window.udf_checkbox.setChecked(True)
    window.verify_checkbox.setChecked(False)
    window._audio_format_buttons[AudioFormat.FLAC].setChecked(True)
    window._last_output_directory = "/tmp/my-output"

    window.closeEvent(QCloseEvent())

    saved = config.get_config().ui
    assert saved.joliet is False
    assert saved.rock is False
    assert saved.udf is True
    assert saved.verify is False
    assert saved.audio_format == "FLAC"
    assert saved.last_output_directory == "/tmp/my-output"


def test_close_event_while_worker_running_does_not_save_preferences(
    window: MainWindow, _no_modal_dialogs: list
) -> None:
    from PySide6.QtGui import QCloseEvent

    window.joliet_checkbox.setChecked(False)
    window._worker = _FakeWorker(running=True)

    window.closeEvent(QCloseEvent())

    # デフォルト値のまま（保存が実行されていない）ことを確認する。
    assert config.get_config().ui.joliet is True


def test_choose_output_path_updates_last_output_directory_for_iso(
    qtbot,
    window: MainWindow,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    window.show()
    _select_volume(window, _dvd_volume())

    chosen = tmp_path / "out.iso"
    monkeypatch.setattr(
        main_window_module.QFileDialog,
        "getSaveFileName",
        lambda *a, **k: (str(chosen), "ISOイメージ (*.iso)"),
    )

    window._choose_output_path()

    assert window.output_edit.text() == str(chosen)
    assert window._last_output_directory == str(tmp_path)


def test_choose_output_path_updates_last_output_directory_for_audio(
    qtbot,
    window: MainWindow,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        main_window_module,
        "query_disc_toc",
        lambda device: DiscToc(track_offsets=[0], leadout_offset=1000),
    )
    window.show()
    _select_volume(window, _audio_volume())

    monkeypatch.setattr(
        main_window_module.QFileDialog,
        "getExistingDirectory",
        lambda *a, **k: str(tmp_path),
    )

    window._choose_output_path()

    assert window.output_edit.text() == str(tmp_path)
    assert window._last_output_directory == str(tmp_path)


# --- 設定タブ（サイズ閾値・検証リトライ回数） ------------------------------


def test_settings_tab_shows_default_tuning_values(window: MainWindow) -> None:
    assert window.cd_max_size_spin.value() == 1000
    assert window.dvd_max_size_spin.value() == 10000
    assert window.audio_verify_attempts_spin.value() == 3


def test_construction_applies_saved_tuning_values(
    qtbot, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(main_window_module, "list_volumes", lambda: [])

    config.save_config(
        config.AppConfig(
            media_size=config.MediaSizeThresholds(
                cd_max_bytes=500_000_000, dvd_max_bytes=8_000_000_000
            ),
            audio_rip=config.AudioRipSettings(max_attempts=5),
        )
    )

    w = MainWindow()
    qtbot.addWidget(w)

    assert w.cd_max_size_spin.value() == 500
    assert w.dvd_max_size_spin.value() == 8000
    assert w.audio_verify_attempts_spin.value() == 5


def test_settings_save_button_persists_tuning_and_ui_values(
    window: MainWindow,
) -> None:
    window.cd_max_size_spin.setValue(123)
    window.dvd_max_size_spin.setValue(4567)
    window.audio_verify_attempts_spin.setValue(7)
    window.udf_checkbox.setChecked(True)

    window._on_settings_save_clicked()

    saved = config.get_config()
    assert saved.media_size.cd_max_bytes == 123_000_000
    assert saved.media_size.dvd_max_bytes == 4_567_000_000
    assert saved.audio_rip.max_attempts == 7
    assert saved.ui.udf is True
    assert "保存しました" in window.settings_status_label.text()


def test_close_event_also_saves_settings_tab_values(
    window: MainWindow,
) -> None:
    from PySide6.QtGui import QCloseEvent

    window.cd_max_size_spin.setValue(42)

    window.closeEvent(QCloseEvent())

    assert config.get_config().media_size.cd_max_bytes == 42_000_000
