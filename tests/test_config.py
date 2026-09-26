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
    assert result.ui == config.UiPreferences()
    assert result.ui.joliet is True
    assert result.ui.udf is False
    assert result.ui.audio_format == "ALAC"
    assert result.ui.audio_rip_mode == "ACCURATE"


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


def test_get_config_overrides_ui_section_from_toml_file(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    config_file = tmp_path / "config.toml"
    config_file.write_text(
        """
        [ui]
        last_output_directory = "/Volumes/Backup"
        joliet = false
        udf = true
        verify = false
        audio_format = "FLAC"
        audio_rip_mode = "CDRDAO_IMAGE"
        """,
        encoding="utf-8",
    )

    monkeypatch.setenv("MKHYBRID_GUI_CONFIG", str(config_file))

    result = config.get_config()

    assert result.ui.last_output_directory == "/Volumes/Backup"
    assert result.ui.joliet is False
    assert result.ui.udf is True
    assert result.ui.verify is False
    assert result.ui.audio_format == "FLAC"
    assert result.ui.audio_rip_mode == "CDRDAO_IMAGE"
    # 明示的に上書きしていない値は既定値のまま。
    assert result.ui.rock is True


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


def test_config_path_defaults_to_xdg_cache_location(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.delenv("MKHYBRID_GUI_CONFIG", raising=False)
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))

    assert config._config_path() == tmp_path / "mkhybrid" / "config.toml"
    assert config.default_config_path() == tmp_path / "mkhybrid" / "config.toml"
    assert config.get_config_path() == tmp_path / "mkhybrid" / "config.toml"


def test_config_path_falls_back_to_home_cache_when_xdg_cache_home_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("MKHYBRID_GUI_CONFIG", raising=False)
    monkeypatch.delenv("XDG_CACHE_HOME", raising=False)

    assert config.default_config_path() == (
        Path.home() / ".cache" / "mkhybrid" / "config.toml"
    )


def test_config_path_env_var_overrides_xdg_cache_home(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    override = tmp_path / "custom" / "config.toml"
    monkeypatch.setenv("MKHYBRID_GUI_CONFIG", str(override))

    assert config._config_path() == override
    assert config.get_config_path() == override


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


# --- save_config / to_toml_string ------------------------------------------


def test_to_toml_string_round_trips_through_tomllib() -> None:
    """``to_toml_string`` が出力するTOMLは ``tomllib`` で正しく読み戻せる。"""
    import tomllib

    app_config = config.AppConfig(
        media_size=config.MediaSizeThresholds(
            cd_max_bytes=111, dvd_max_bytes=222
        ),
        audio_rip=config.AudioRipSettings(max_attempts=9),
        ui=config.UiPreferences(
            last_output_directory="/Volumes/Output",
            joliet=False,
            rock=True,
            udf=True,
            verify=False,
            audio_format="FLAC",
            audio_rip_mode="CDRDAO_IMAGE",
        ),
    )

    parsed = tomllib.loads(config.to_toml_string(app_config))

    assert parsed["media_size"]["cd_max_bytes"] == 111
    assert parsed["media_size"]["dvd_max_bytes"] == 222
    assert parsed["audio_rip"]["max_attempts"] == 9
    assert parsed["ui"]["last_output_directory"] == "/Volumes/Output"
    assert parsed["ui"]["joliet"] is False
    assert parsed["ui"]["udf"] is True
    assert parsed["ui"]["audio_format"] == "FLAC"
    assert parsed["ui"]["audio_rip_mode"] == "CDRDAO_IMAGE"


def test_to_toml_string_escapes_special_characters_in_strings() -> None:
    """パスに含まれうる二重引用符・バックスラッシュ・改行を正しくエスケープする。"""
    import tomllib

    app_config = config.AppConfig(
        ui=config.UiPreferences(
            last_output_directory='C:\\temp\\"weird" path\nsecond line'
        )
    )

    parsed = tomllib.loads(config.to_toml_string(app_config))

    assert (
        parsed["ui"]["last_output_directory"]
        == 'C:\\temp\\"weird" path\nsecond line'
    )


def test_save_config_creates_parent_directories(tmp_path: Path) -> None:
    target = tmp_path / "nested" / "dir" / "config.toml"
    assert not target.parent.exists()

    config.save_config(config.AppConfig(), path=target)

    assert target.exists()


def test_save_config_round_trips_via_get_config(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    config_file = tmp_path / "config.toml"
    monkeypatch.setenv("MKHYBRID_GUI_CONFIG", str(config_file))
    config.get_config.cache_clear()

    saved = config.AppConfig(
        media_size=config.MediaSizeThresholds(
            cd_max_bytes=555, dvd_max_bytes=666
        ),
        audio_rip=config.AudioRipSettings(max_attempts=1),
        ui=config.UiPreferences(
            last_output_directory="/Users/example/Desktop",
            joliet=False,
            rock=False,
            udf=True,
            verify=False,
            audio_format="WAV",
            audio_rip_mode="CDRDAO_IMAGE",
        ),
    )

    config.save_config(saved)

    # save_config() はキャッシュをクリアするため、追加のcache_clear()なしで
    # 保存内容が反映される。
    assert config.get_config() == saved


def test_save_config_overwrites_existing_file(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    config_file = tmp_path / "config.toml"
    config_file.write_text(
        "[audio_rip]\nmax_attempts = 99\n", encoding="utf-8"
    )
    monkeypatch.setenv("MKHYBRID_GUI_CONFIG", str(config_file))
    config.get_config.cache_clear()

    config.save_config(config.AppConfig())

    assert config.get_config() == config.AppConfig()
