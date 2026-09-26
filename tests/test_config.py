"""``config`` モジュールの読み込み・上書き・フォールバック動作のテスト。"""

from __future__ import annotations

from pathlib import Path

import pytest

from mkhybrid_gui import config

# ``conftest.py`` の ``_isolate_app_config`` （autouse）が、実行環境の
# 設定ファイルからテストを隔離し、各テスト前後でキャッシュをクリアする。


def test_get_config_returns_defaults_when_no_file(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv(
        "MKHYBRID_GUI_CONFIG",
        str(tmp_path / "does-not-exist.toml"),
    )

    result = config.get_config()

    assert result == config.AppConfig()
    assert result.media_size.cd_max_bytes == 1_000_000_000
    assert result.media_size.dvd_max_bytes == 10_000_000_000
    assert result.audio_rip.max_attempts == 3


def test_get_config_overrides_from_toml_file(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    config_file = tmp_path / "config.toml"
    config_file.write_text(
        """
        [media_size]
        cd_max_bytes = 111
        dvd_max_bytes = 222

        [audio_rip]
        max_attempts = 9
        """,
        encoding="utf-8",
    )

    monkeypatch.setenv("MKHYBRID_GUI_CONFIG", str(config_file))

    result = config.get_config()

    assert result.media_size.cd_max_bytes == 111
    assert result.media_size.dvd_max_bytes == 222
    assert result.audio_rip.max_attempts == 9


def test_get_config_ignores_unknown_keys(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    config_file = tmp_path / "config.toml"
    config_file.write_text(
        """
        [media_size]
        cd_max_bytes = 111
        totally_unknown_field = "ignored"

        [unknown_section]
        whatever = 1
        """,
        encoding="utf-8",
    )

    monkeypatch.setenv("MKHYBRID_GUI_CONFIG", str(config_file))

    result = config.get_config()

    assert result.media_size.cd_max_bytes == 111
    # 明示的に上書きしていない値は既定値のまま。
    assert result.media_size.dvd_max_bytes == 10_000_000_000


def test_get_config_ignores_wrong_type_values(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """型が合わない値（文字列を期待するintフィールドに文字列等）は無視し、
    既定値にフォールバックする。設定ファイルの記述ミスでアプリが
    起動できなくなることを防ぐため。
    """
    config_file = tmp_path / "config.toml"
    config_file.write_text(
        """
        [media_size]
        cd_max_bytes = "not-a-number"
        """,
        encoding="utf-8",
    )

    monkeypatch.setenv("MKHYBRID_GUI_CONFIG", str(config_file))

    result = config.get_config()

    assert result.media_size.cd_max_bytes == 1_000_000_000


def test_get_config_falls_back_on_malformed_toml(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    config_file = tmp_path / "config.toml"
    config_file.write_text("this is [ not valid toml", encoding="utf-8")

    monkeypatch.setenv("MKHYBRID_GUI_CONFIG", str(config_file))

    result = config.get_config()

    assert result == config.AppConfig()


def test_get_config_falls_back_when_toml_is_not_a_table(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """TOMLとしては妥当でも、トップレベルがテーブルでない場合は既定値を使う。"""
    config_file = tmp_path / "config.toml"
    config_file.write_text('"just a string"', encoding="utf-8")

    monkeypatch.setenv("MKHYBRID_GUI_CONFIG", str(config_file))

    result = config.get_config()

    assert result == config.AppConfig()


def test_config_path_defaults_to_xdg_style_location(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("MKHYBRID_GUI_CONFIG", raising=False)

    assert config._config_path() == config.DEFAULT_CONFIG_PATH
    assert config.DEFAULT_CONFIG_PATH == (
        Path.home() / ".config" / "mkhybrid-gui" / "config.toml"
    )


def test_get_config_is_cached_until_cleared(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    config_file = tmp_path / "config.toml"
    config_file.write_text(
        "[audio_rip]\nmax_attempts = 5\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("MKHYBRID_GUI_CONFIG", str(config_file))

    first = config.get_config()
    assert first.audio_rip.max_attempts == 5

    # ファイル内容を変えても、キャッシュをクリアするまでは反映されない。
    config_file.write_text(
        "[audio_rip]\nmax_attempts = 42\n",
        encoding="utf-8",
    )
    assert config.get_config() is first

    config.get_config.cache_clear()
    assert config.get_config().audio_rip.max_attempts == 42
