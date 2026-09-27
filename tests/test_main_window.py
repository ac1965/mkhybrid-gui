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


def test_selecting_audio_cd_defaults_to_accurate_mode(
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

    assert window.audio_mode_label.isVisible() is True
    assert window.audio_mode_accurate_radio.isChecked() is True
    assert window._is_cdrdao_mode_selected() is False


def test_selecting_cdrdao_mode_hides_accurate_rip_widgets(
    qtbot,
    window: MainWindow,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """cdrdao（ディスクイメージ）モードでは、トラックごとの変換・タグ付けに
    関するウィジェットを隠すが、``album_edit``（TOC+BINのファイル名に
    使う）と、それを埋めるためのMusicBrainz検索ボタンは表示したままにする。
    """
    monkeypatch.setattr(
        main_window_module,
        "query_disc_toc",
        lambda device: DiscToc(track_offsets=[0, 1000], leadout_offset=2000),
    )

    window.show()
    _select_volume(window, _audio_volume())

    window.audio_mode_cdrdao_radio.setChecked(True)

    assert window._is_cdrdao_mode_selected() is True
    assert window.album_edit.isVisible() is True
    assert window.artist_edit.isVisible() is False
    assert window.year_edit.isVisible() is False
    assert window.track_title_table.isVisible() is False
    assert window.verify_checkbox.isVisible() is False
    assert window.metadata_lookup_button.isVisible() is True
    for radio in window._audio_format_buttons.values():
        assert radio.isVisible() is False
    assert window.start_button.text() == "ディスクイメージを作成"
    assert window.cdrdao_generate_cue_checkbox.isVisible() is True


def test_selecting_accurate_mode_hides_cdrdao_cue_checkbox(
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

    assert window.audio_mode_accurate_radio.isChecked() is True
    assert window.cdrdao_generate_cue_checkbox.isVisible() is False


def test_selecting_audio_cd_prefills_output_folder_from_last_directory(
    qtbot,
    window: MainWindow,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """音楽CD選択時、出力先フォルダ欄が空なら前回の出力先を初期値にする。

    以前はlast_output_directoryが「参照…」ダイアログの初期フォルダとして
    しか使われておらず、出力先フォルダ欄自体には反映されていなかった
    （実機での報告により発見）。
    """
    monkeypatch.setattr(
        main_window_module,
        "query_disc_toc",
        lambda device: DiscToc(track_offsets=[0], leadout_offset=1000),
    )

    window.show()
    window._last_output_directory = "/Users/ac1965/Downloads/Music/"
    assert window.output_edit.text() == ""

    _select_volume(window, _audio_volume())

    assert window.output_edit.text() == "/Users/ac1965/Downloads/Music/"


def test_selecting_audio_cd_does_not_overwrite_existing_output_text(
    qtbot,
    window: MainWindow,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """出力先フォルダ欄に既に入力がある場合、前回の出力先で上書きしない。"""
    monkeypatch.setattr(
        main_window_module,
        "query_disc_toc",
        lambda device: DiscToc(track_offsets=[0], leadout_offset=1000),
    )

    window.show()
    window._last_output_directory = "/some/remembered/path"
    window.output_edit.setText("/user/typed/this/path")

    _select_volume(window, _audio_volume())

    assert window.output_edit.text() == "/user/typed/this/path"


def test_selecting_dvd_does_not_prefill_output_field(
    qtbot, window: MainWindow
) -> None:
    """ISO作成では出力先はファイル名まで必要なため、フォルダだけの
    last_output_directoryを補完しない（不完全なパスになるため）。
    """
    window.show()
    window._last_output_directory = "/Users/ac1965/Downloads/Music/"

    _select_volume(window, _dvd_volume())

    assert window.output_edit.text() == ""


def test_progress_adds_note_for_drive_option_not_supported(
    window: MainWindow,
) -> None:
    """cd-paranoiaの「405: Option not supported by drive」通知に、
    エラーではないことを説明する補足を1回だけ添える。
    """
    window._on_progress("405: Option not supported by drive")
    window._on_progress("405: Option not supported by drive")

    log_text = window.log_view.toPlainText()

    assert log_text.count("Option not supported by drive") == 2
    assert log_text.count("影響なく") == 1


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


def test_start_audio_rip_passes_search_range_spinbox_value_to_worker(
    qtbot,
    window: MainWindow,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """設定タブの「AccurateRipのオフセット探索範囲」スピンボックスの値が、
    実際に``AudioRipWorker``へ伝わることを確認する。

    以前はこのスピンボックスの値がconfig保存にしか使われず、実際の
    リッピング時には反映されない回帰があった（``AudioRipWorker``が
    ``accuraterip_search_range``を受け取らず、常にインポート時点の
    既定値が使われていた）。
    """
    monkeypatch.setattr(
        main_window_module,
        "query_disc_toc",
        lambda device: DiscToc(track_offsets=[0], leadout_offset=1000),
    )
    monkeypatch.setattr(
        main_window_module, "missing_tools", lambda fmt: []
    )

    captured: dict = {}

    class _FakeAudioRipWorker:
        def __init__(self, *args, **kwargs):
            captured.update(kwargs)
            self.progress = _FakeSignal()
            self.progress_percent = _FakeSignal()
            self.finished_ok = _FakeSignal()

        def isRunning(self) -> bool:  # noqa: N802 - Qtの命名規則に合わせる
            return False

        def start(self) -> None:
            pass

    monkeypatch.setattr(
        main_window_module, "AudioRipWorker", _FakeAudioRipWorker
    )

    window.show()
    _select_volume(window, _audio_volume())
    window.output_edit.setText(str(tmp_path / "out"))
    window.accuraterip_search_range_spin.setValue(4242)

    window._on_start_clicked()

    assert captured["accuraterip_search_range"] == 4242


def test_start_cdrdao_rip_warns_on_empty_output_path(
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
    window.audio_mode_cdrdao_radio.setChecked(True)
    window.output_edit.setText("")

    window._on_start_clicked()

    assert any(name == "warning" for name, _, _ in _no_modal_dialogs)
    assert window._worker is None


def test_start_cdrdao_rip_warns_on_missing_tools(
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
        main_window_module.cdrdao, "missing_tools", lambda **kwargs: ["cdrdao"]
    )

    window.show()
    _select_volume(window, _audio_volume())
    window.audio_mode_cdrdao_radio.setChecked(True)
    window.output_edit.setText(str(tmp_path / "out"))

    window._on_start_clicked()

    assert any(name == "critical" for name, _, _ in _no_modal_dialogs)
    assert window._worker is None


def test_start_cdrdao_rip_uses_album_name_for_base_filename(
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
        main_window_module.cdrdao, "missing_tools", lambda **kwargs: []
    )
    monkeypatch.setattr(
        main_window_module, "get_media_name", lambda device_id: "TEST DRIVE"
    )
    monkeypatch.setattr(
        main_window_module, "unmount_disk", lambda device_id: True
    )
    monkeypatch.setattr(
        main_window_module, "mount_disk", lambda device_id: True
    )
    monkeypatch.setattr(
        main_window_module.cdrdao, "scan_bus", lambda: "fake scanbus output"
    )
    monkeypatch.setattr(
        main_window_module.cdrdao,
        "find_scsi_device",
        lambda output, media_name: "IOService:/fake/path",
    )

    captured: dict = {}

    class _FakeCdrdaoWorker:
        def __init__(self, device, destination_dir, base_name, generate_cue=False, parent=None):
            captured["device"] = device
            captured["destination_dir"] = destination_dir
            captured["base_name"] = base_name
            self.progress = _FakeSignal()
            self.finished_ok = _FakeSignal()

        def isRunning(self) -> bool:  # noqa: N802 - Qtの命名規則に合わせる
            return False

        def start(self) -> None:
            captured["started"] = True

    monkeypatch.setattr(
        main_window_module, "CdrdaoWorker", _FakeCdrdaoWorker
    )

    window.show()
    _select_volume(window, _audio_volume())
    window.audio_mode_cdrdao_radio.setChecked(True)
    window.output_edit.setText(str(tmp_path / "out"))
    window.album_edit.setText("My Great Album")

    window._on_start_clicked()

    assert captured["base_name"] == "My Great Album"
    assert captured.get("started") is True


def test_start_cdrdao_rip_passes_generate_cue_to_missing_tools_and_worker(
    qtbot,
    window: MainWindow,
    monkeypatch: pytest.MonkeyPatch,
    _no_modal_dialogs: list,
    tmp_path: Path,
) -> None:
    """「CUEシートも生成する」チェックボックスがONの場合、
    ``cdrdao.missing_tools``と``CdrdaoWorker``の両方に
    ``generate_cue=True``が伝わることを確認する。
    """
    monkeypatch.setattr(
        main_window_module,
        "query_disc_toc",
        lambda device: DiscToc(track_offsets=[0], leadout_offset=1000),
    )

    missing_tools_calls: list[dict] = []
    monkeypatch.setattr(
        main_window_module.cdrdao,
        "missing_tools",
        lambda **kwargs: missing_tools_calls.append(kwargs) or [],
    )
    monkeypatch.setattr(
        main_window_module, "get_media_name", lambda device_id: "TEST DRIVE"
    )
    monkeypatch.setattr(
        main_window_module, "unmount_disk", lambda device_id: True
    )
    monkeypatch.setattr(
        main_window_module, "mount_disk", lambda device_id: True
    )
    monkeypatch.setattr(
        main_window_module.cdrdao, "scan_bus", lambda: "fake scanbus output"
    )
    monkeypatch.setattr(
        main_window_module.cdrdao,
        "find_scsi_device",
        lambda output, media_name: "IOService:/fake/path",
    )

    captured: dict = {}

    class _FakeCdrdaoWorker:
        def __init__(
            self,
            device,
            destination_dir,
            base_name,
            generate_cue=False,
            parent=None,
        ):
            captured["generate_cue"] = generate_cue
            self.progress = _FakeSignal()
            self.finished_ok = _FakeSignal()

        def isRunning(self) -> bool:  # noqa: N802 - Qtの命名規則に合わせる
            return False

        def start(self) -> None:
            pass

    monkeypatch.setattr(
        main_window_module, "CdrdaoWorker", _FakeCdrdaoWorker
    )

    window.show()
    _select_volume(window, _audio_volume())
    window.audio_mode_cdrdao_radio.setChecked(True)
    window.cdrdao_generate_cue_checkbox.setChecked(True)
    window.output_edit.setText(str(tmp_path / "out"))

    window._on_start_clicked()

    assert missing_tools_calls == [{"generate_cue": True}]
    assert captured["generate_cue"] is True


def test_start_cdrdao_rip_falls_back_to_volume_name_without_album(
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
        main_window_module.cdrdao, "missing_tools", lambda **kwargs: []
    )
    monkeypatch.setattr(
        main_window_module, "get_media_name", lambda device_id: "TEST DRIVE"
    )
    monkeypatch.setattr(
        main_window_module, "unmount_disk", lambda device_id: True
    )
    monkeypatch.setattr(
        main_window_module, "mount_disk", lambda device_id: True
    )
    monkeypatch.setattr(
        main_window_module.cdrdao, "scan_bus", lambda: "fake scanbus output"
    )
    monkeypatch.setattr(
        main_window_module.cdrdao,
        "find_scsi_device",
        lambda output, media_name: "IOService:/fake/path",
    )

    captured: dict = {}

    class _FakeCdrdaoWorker:
        def __init__(self, device, destination_dir, base_name, generate_cue=False, parent=None):
            captured["base_name"] = base_name
            self.progress = _FakeSignal()
            self.finished_ok = _FakeSignal()

        def isRunning(self) -> bool:  # noqa: N802 - Qtの命名規則に合わせる
            return False

        def start(self) -> None:
            pass

    monkeypatch.setattr(
        main_window_module, "CdrdaoWorker", _FakeCdrdaoWorker
    )

    window.show()
    _select_volume(window, _audio_volume())
    window.audio_mode_cdrdao_radio.setChecked(True)
    window.output_edit.setText(str(tmp_path / "out"))
    window.album_edit.setText("")

    window._on_start_clicked()

    assert captured["base_name"] == "TEST_CD"  # _audio_volume()のvolume_name


def test_start_cdrdao_rip_asks_before_overwriting_existing_files(
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
    monkeypatch.setattr(
        main_window_module.cdrdao, "missing_tools", lambda **kwargs: []
    )
    monkeypatch.setattr(
        main_window_module, "CdrdaoWorker", lambda *a, **k: None
    )

    dest = tmp_path / "out"
    dest.mkdir()
    (dest / "TEST_CD.toc").write_text("existing")

    calls: list = []
    monkeypatch.setattr(
        QMessageBox,
        "question",
        lambda *a, **k: calls.append(True)
        or QMessageBox.StandardButton.No,
    )

    window.show()
    _select_volume(window, _audio_volume())
    window.audio_mode_cdrdao_radio.setChecked(True)
    window.output_edit.setText(str(dest))
    window.album_edit.setText("")

    window._on_start_clicked()

    assert calls == [True]
    assert window._worker is None


def test_start_cdrdao_rip_declines_unmount_confirmation(
    qtbot,
    window: MainWindow,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """アンマウント確認ダイアログで「いいえ」を選んだ場合、
    アンマウント・ワーカー起動のいずれも行わない。
    """
    monkeypatch.setattr(
        main_window_module,
        "query_disc_toc",
        lambda device: DiscToc(track_offsets=[0], leadout_offset=1000),
    )
    monkeypatch.setattr(
        main_window_module.cdrdao, "missing_tools", lambda **kwargs: []
    )
    monkeypatch.setattr(
        main_window_module, "CdrdaoWorker", lambda *a, **k: None
    )

    unmount_calls: list = []
    monkeypatch.setattr(
        main_window_module,
        "unmount_disk",
        lambda device_id: unmount_calls.append(device_id) or True,
    )

    monkeypatch.setattr(
        QMessageBox,
        "question",
        lambda *a, **k: QMessageBox.StandardButton.No,
    )

    window.show()
    _select_volume(window, _audio_volume())
    window.audio_mode_cdrdao_radio.setChecked(True)
    window.output_edit.setText(str(tmp_path / "out"))

    window._on_start_clicked()

    assert unmount_calls == []
    assert window._worker is None


def test_start_cdrdao_rip_shows_error_when_media_name_unavailable(
    qtbot,
    window: MainWindow,
    monkeypatch: pytest.MonkeyPatch,
    _no_modal_dialogs: list,
    tmp_path: Path,
) -> None:
    """ドライブのモデル名（MediaName）が取得できない場合、
    アンマウントを試みずにエラーを表示する。
    """
    monkeypatch.setattr(
        main_window_module,
        "query_disc_toc",
        lambda device: DiscToc(track_offsets=[0], leadout_offset=1000),
    )
    monkeypatch.setattr(
        main_window_module.cdrdao, "missing_tools", lambda **kwargs: []
    )
    monkeypatch.setattr(
        main_window_module, "get_media_name", lambda device_id: None
    )

    unmount_calls: list = []
    monkeypatch.setattr(
        main_window_module,
        "unmount_disk",
        lambda device_id: unmount_calls.append(device_id) or True,
    )

    window.show()
    _select_volume(window, _audio_volume())
    window.audio_mode_cdrdao_radio.setChecked(True)
    window.output_edit.setText(str(tmp_path / "out"))

    window._on_start_clicked()

    assert unmount_calls == []
    assert window._worker is None
    assert any(name == "critical" for name, _, _ in _no_modal_dialogs)


def test_start_cdrdao_rip_shows_error_when_unmount_fails(
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
        main_window_module.cdrdao, "missing_tools", lambda **kwargs: []
    )
    monkeypatch.setattr(
        main_window_module, "get_media_name", lambda device_id: "TEST DRIVE"
    )
    monkeypatch.setattr(
        main_window_module, "unmount_disk", lambda device_id: False
    )
    monkeypatch.setattr(
        main_window_module, "CdrdaoWorker", lambda *a, **k: None
    )

    window.show()
    _select_volume(window, _audio_volume())
    window.audio_mode_cdrdao_radio.setChecked(True)
    window.output_edit.setText(str(tmp_path / "out"))

    window._on_start_clicked()

    assert window._worker is None
    assert any(name == "critical" for name, _, _ in _no_modal_dialogs)


def test_start_cdrdao_rip_remounts_when_device_not_found(
    qtbot,
    window: MainWindow,
    monkeypatch: pytest.MonkeyPatch,
    _no_modal_dialogs: list,
    tmp_path: Path,
) -> None:
    """cdrdao scanbusの出力からドライブを特定できない場合、
    アンマウント済みのディスクを再マウントしたうえでエラーを表示する。
    """
    monkeypatch.setattr(
        main_window_module,
        "query_disc_toc",
        lambda device: DiscToc(track_offsets=[0], leadout_offset=1000),
    )
    monkeypatch.setattr(
        main_window_module.cdrdao, "missing_tools", lambda **kwargs: []
    )
    monkeypatch.setattr(
        main_window_module, "get_media_name", lambda device_id: "TEST DRIVE"
    )
    monkeypatch.setattr(
        main_window_module, "unmount_disk", lambda device_id: True
    )
    monkeypatch.setattr(
        main_window_module.cdrdao, "scan_bus", lambda: "no matching device"
    )
    monkeypatch.setattr(
        main_window_module.cdrdao,
        "find_scsi_device",
        lambda output, media_name: None,
    )

    mount_calls: list = []
    monkeypatch.setattr(
        main_window_module,
        "mount_disk",
        lambda device_id: mount_calls.append(device_id) or True,
    )
    monkeypatch.setattr(
        main_window_module, "CdrdaoWorker", lambda *a, **k: None
    )

    window.show()
    _select_volume(window, _audio_volume())
    window.audio_mode_cdrdao_radio.setChecked(True)
    window.output_edit.setText(str(tmp_path / "out"))

    window._on_start_clicked()

    assert mount_calls == ["disk5"]  # _audio_volume()のdevice_identifier
    assert window._worker is None
    assert any(name == "critical" for name, _, _ in _no_modal_dialogs)


def test_on_finished_remounts_disk_after_cdrdao_job(
    qtbot,
    window: MainWindow,
    monkeypatch: pytest.MonkeyPatch,
    _no_modal_dialogs: list,
    tmp_path: Path,
) -> None:
    """cdrdaoジョブの完了時、開始時にアンマウントしたディスクを
    再マウントする（成功・失敗を問わず）。
    """
    monkeypatch.setattr(
        main_window_module,
        "query_disc_toc",
        lambda device: DiscToc(track_offsets=[0], leadout_offset=1000),
    )
    monkeypatch.setattr(
        main_window_module.cdrdao, "missing_tools", lambda **kwargs: []
    )
    monkeypatch.setattr(
        main_window_module, "get_media_name", lambda device_id: "TEST DRIVE"
    )
    monkeypatch.setattr(
        main_window_module, "unmount_disk", lambda device_id: True
    )
    monkeypatch.setattr(
        main_window_module.cdrdao, "scan_bus", lambda: "fake scanbus output"
    )
    monkeypatch.setattr(
        main_window_module.cdrdao,
        "find_scsi_device",
        lambda output, media_name: "IOService:/fake/path",
    )

    mount_calls: list = []
    monkeypatch.setattr(
        main_window_module,
        "mount_disk",
        lambda device_id: mount_calls.append(device_id) or True,
    )

    class _FakeCdrdaoWorker:
        def __init__(self, device, destination_dir, base_name, generate_cue=False, parent=None):
            self.progress = _FakeSignal()
            self.finished_ok = _FakeSignal()

        def isRunning(self) -> bool:  # noqa: N802 - Qtの命名規則に合わせる
            return False

        def start(self) -> None:
            pass

    monkeypatch.setattr(
        main_window_module, "CdrdaoWorker", _FakeCdrdaoWorker
    )

    window.show()
    _select_volume(window, _audio_volume())
    window.audio_mode_cdrdao_radio.setChecked(True)
    window.output_edit.setText(str(tmp_path / "out"))

    window._on_start_clicked()

    assert mount_calls == []  # まだ実行中なので再マウントしない

    window._on_finished(True, "ディスクイメージを作成しました。")

    assert mount_calls == ["disk5"]  # _audio_volume()のdevice_identifier
    assert window._cdrdao_unmounted_device_identifier is None


class _FakeSignal:
    def connect(self, *_args, **_kwargs) -> None:
        return None


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


class _FakeCandidateDialog:
    """``MusicBrainzCandidateDialog``のフェイク。実際のQDialogを表示・
    ブロックさせず、``accepted``/``chosen_index``で選択結果を注入する。
    """

    def __init__(self, candidates, parent=None) -> None:
        self.candidates = candidates

    def exec(self) -> int:
        from PySide6.QtWidgets import QDialog

        return (
            QDialog.DialogCode.Accepted
            if _FakeCandidateDialog.accepted
            else QDialog.DialogCode.Rejected
        )

    def selected_index(self):
        return _FakeCandidateDialog.chosen_index

    accepted = True
    chosen_index: int | None = 0


def test_metadata_lookup_finished_applies_selected_candidate_from_dialog(
    qtbot,
    window: MainWindow,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """複数候補が見つかった場合、選択ダイアログで選んだ候補
    （先頭とは限らない）が反映されることを確認する。
    """
    from mkhybrid_gui.metadata import TrackMetadata

    monkeypatch.setattr(
        main_window_module,
        "query_disc_toc",
        lambda device: DiscToc(track_offsets=[0], leadout_offset=1000),
    )
    window.show()
    _select_volume(window, _audio_volume())

    first_candidate = ReleaseCandidate(
        album=AlbumMetadata(album="First Album", artist="First Artist")
    )
    second_candidate = ReleaseCandidate(
        album=AlbumMetadata(
            album="Second Album",
            artist="Second Artist",
            year="2010",
            tracks=[TrackMetadata(title="Second Title")],
        ),
        country="JP",
    )

    _FakeCandidateDialog.accepted = True
    _FakeCandidateDialog.chosen_index = 1
    monkeypatch.setattr(
        main_window_module, "MusicBrainzCandidateDialog", _FakeCandidateDialog
    )

    window._on_metadata_lookup_finished(
        LookupResult(candidates=[first_candidate, second_candidate])
    )

    assert window.album_edit.text() == "Second Album"
    assert window.artist_edit.text() == "Second Artist"
    assert window.track_title_table.item(0, 1).text() == "Second Title"


def test_metadata_lookup_finished_dialog_cancelled_shows_status(
    qtbot,
    window: MainWindow,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """複数候補の選択ダイアログをキャンセルした場合、入力欄は変更せず
    ステータス表示のみ更新する。
    """
    monkeypatch.setattr(
        main_window_module,
        "query_disc_toc",
        lambda device: DiscToc(track_offsets=[0], leadout_offset=1000),
    )
    window.show()
    _select_volume(window, _audio_volume())

    first_candidate = ReleaseCandidate(
        album=AlbumMetadata(album="First Album", artist="First Artist")
    )
    second_candidate = ReleaseCandidate(
        album=AlbumMetadata(album="Second Album", artist="Second Artist")
    )

    _FakeCandidateDialog.accepted = False
    _FakeCandidateDialog.chosen_index = None
    monkeypatch.setattr(
        main_window_module, "MusicBrainzCandidateDialog", _FakeCandidateDialog
    )

    window._on_metadata_lookup_finished(
        LookupResult(candidates=[first_candidate, second_candidate])
    )

    assert window.album_edit.text() == ""
    assert "選択されませんでした" in window.metadata_status_label.text()


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
                audio_rip_mode="CDRDAO_IMAGE",
                cdrdao_generate_cue=True,
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
    assert w.audio_mode_cdrdao_radio.isChecked() is True
    assert w.cdrdao_generate_cue_checkbox.isChecked() is True


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
    window.audio_mode_cdrdao_radio.setChecked(True)
    window.cdrdao_generate_cue_checkbox.setChecked(True)

    window.closeEvent(QCloseEvent())

    saved = config.get_config().ui
    assert saved.joliet is False
    assert saved.rock is False
    assert saved.udf is True
    assert saved.verify is False
    assert saved.audio_format == "FLAC"
    assert saved.last_output_directory == "/tmp/my-output"
    assert saved.audio_rip_mode == "CDRDAO_IMAGE"
    assert saved.cdrdao_generate_cue is True


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


def test_apply_config_populates_settings_output_dir_field(
    qtbot, window: MainWindow
) -> None:
    app_config = config.AppConfig(
        ui=config.UiPreferences(
            last_output_directory="/Users/ac1965/Downloads/Music/"
        )
    )

    window._apply_config(app_config)

    assert (
        window.settings_output_dir_edit.text()
        == "/Users/ac1965/Downloads/Music/"
    )


def test_settings_output_dir_browse_updates_field_and_audio_output(
    qtbot,
    window: MainWindow,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """設定タブでの変更は、音楽CDモードなら「作成」タブの出力先欄にも
    即座に反映する。
    """
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

    window._on_settings_output_dir_browse_clicked()

    assert window._last_output_directory == str(tmp_path)
    assert window.settings_output_dir_edit.text() == str(tmp_path)
    assert window.output_edit.text() == str(tmp_path)


def test_settings_output_dir_browse_does_not_touch_iso_output_field(
    qtbot,
    window: MainWindow,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """ISO作成モードでは出力先はファイル名まで必要なため、
    設定タブでのフォルダ変更で「作成」タブの出力先欄を上書きしない。
    """
    window.show()
    _select_volume(window, _dvd_volume())
    window.output_edit.setText("/existing/output.iso")

    monkeypatch.setattr(
        main_window_module.QFileDialog,
        "getExistingDirectory",
        lambda *a, **k: str(tmp_path),
    )

    window._on_settings_output_dir_browse_clicked()

    assert window._last_output_directory == str(tmp_path)
    assert window.settings_output_dir_edit.text() == str(tmp_path)
    assert window.output_edit.text() == "/existing/output.iso"


def test_settings_output_dir_browse_cancelled_changes_nothing(
    window: MainWindow, monkeypatch: pytest.MonkeyPatch
) -> None:
    window._last_output_directory = "/original/path"
    window.settings_output_dir_edit.setText("/original/path")

    monkeypatch.setattr(
        main_window_module.QFileDialog,
        "getExistingDirectory",
        lambda *a, **k: "",
    )

    window._on_settings_output_dir_browse_clicked()

    assert window._last_output_directory == "/original/path"
    assert window.settings_output_dir_edit.text() == "/original/path"


# --- 設定タブ（サイズ閾値・検証リトライ回数） ------------------------------


def test_settings_tab_shows_default_tuning_values(window: MainWindow) -> None:
    assert window.cd_max_size_spin.value() == 1000
    assert window.dvd_max_size_spin.value() == 10000
    assert window.audio_verify_attempts_spin.value() == 3
    assert window.accuraterip_search_range_spin.value() == 1000


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
            accuraterip=config.AccurateRipSettings(
                search_range_samples=250
            ),
        )
    )

    w = MainWindow()
    qtbot.addWidget(w)

    assert w.cd_max_size_spin.value() == 500
    assert w.dvd_max_size_spin.value() == 8000
    assert w.audio_verify_attempts_spin.value() == 5
    assert w.accuraterip_search_range_spin.value() == 250


def test_settings_save_button_persists_tuning_and_ui_values(
    window: MainWindow,
) -> None:
    window.cd_max_size_spin.setValue(123)
    window.dvd_max_size_spin.setValue(4567)
    window.audio_verify_attempts_spin.setValue(7)
    window.accuraterip_search_range_spin.setValue(333)
    window.udf_checkbox.setChecked(True)

    window._on_settings_save_clicked()

    saved = config.get_config()
    assert saved.media_size.cd_max_bytes == 123_000_000
    assert saved.media_size.dvd_max_bytes == 4_567_000_000
    assert saved.audio_rip.max_attempts == 7
    assert saved.accuraterip.search_range_samples == 333
    assert saved.ui.udf is True
    assert "保存しました" in window.settings_status_label.text()


def test_verify_checkbox_discloses_accuraterip_network_communication(
    window: MainWindow,
) -> None:
    assert "AccurateRip" in window.verify_checkbox.text()


def test_close_event_also_saves_settings_tab_values(
    window: MainWindow,
) -> None:
    from PySide6.QtGui import QCloseEvent

    window.cd_max_size_spin.setValue(42)

    window.closeEvent(QCloseEvent())

    assert config.get_config().media_size.cd_max_bytes == 42_000_000


# --- MusicBrainzCandidateDialog -----------------------------------------


def test_candidate_dialog_previews_selected_candidate_tracks(qtbot) -> None:
    """候補を切り替えると、右側のトラック一覧プレビューが選択中の
    候補のものに更新されることを確認する。
    """
    from mkhybrid_gui.metadata import TrackMetadata
    from mkhybrid_gui.ui.main_window import MusicBrainzCandidateDialog

    first_candidate = ReleaseCandidate(
        album=AlbumMetadata(
            album="First Album",
            artist="First Artist",
            tracks=[TrackMetadata(title="First Track 1")],
        )
    )
    second_candidate = ReleaseCandidate(
        album=AlbumMetadata(
            album="Second Album",
            artist="Second Artist",
            tracks=[
                TrackMetadata(title="Second Track 1"),
                TrackMetadata(title="Second Track 2"),
            ],
        )
    )

    dialog = MusicBrainzCandidateDialog([first_candidate, second_candidate])
    qtbot.addWidget(dialog)

    assert dialog.selected_index() == 0
    assert dialog.track_preview_table.rowCount() == 1
    assert dialog.track_preview_table.item(0, 1).text() == "First Track 1"

    dialog.candidate_list.setCurrentRow(1)

    assert dialog.selected_index() == 1
    assert dialog.track_preview_table.rowCount() == 2
    assert dialog.track_preview_table.item(1, 1).text() == "Second Track 2"


def test_candidate_dialog_shows_placeholder_for_untitled_tracks(qtbot) -> None:
    from mkhybrid_gui.metadata import TrackMetadata
    from mkhybrid_gui.ui.main_window import MusicBrainzCandidateDialog

    candidate = ReleaseCandidate(
        album=AlbumMetadata(
            album="Album", artist="Artist", tracks=[TrackMetadata(title="")]
        )
    )

    dialog = MusicBrainzCandidateDialog([candidate])
    qtbot.addWidget(dialog)

    assert dialog.track_preview_table.item(0, 1).text() == "(不明)"
