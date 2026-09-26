"""アプリケーション全体の設定値（サイズ閾値・リトライ回数等）。

これまでコード内（``disk_utils.py``/``audio_cd.py``）に散らばっていた
マジックナンバーをこのモジュールに集約する。既定値はこのモジュール内の
データクラスに定義し、外部設定ファイル（TOML、既定は
``~/.config/mkhybrid-gui/config.toml``。環境変数 ``MKHYBRID_GUI_CONFIG``
でパスを上書き可能）が存在すればその値で上書きする。

TOMLを採用しているのは、本プロジェクトが既にPython 3.11以降を要求して
おり（``pyproject.toml``）、標準ライブラリの ``tomllib`` だけで読み込め、
追加の依存パッケージが不要なため。またフラットな整数値中心の設定内容には
型があいまいにならないTOMLの方が適しており、YAMLのような暗黙の型変換
（例: ``yes``/``no`` の真偽値化）の落とし穴もない。

設定ファイルが存在しない・壊れている・想定外の形式の場合は、既定値のみで
動作する（設定ファイルの不備でアプリの起動を妨げない）。
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, fields, replace
from functools import lru_cache
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class MediaSizeThresholds:
    """メディア種別判定（データCD/DVD/Blu-ray）に使うサイズ閾値（バイト）。

    ``disk_utils.detect_media_type`` が、``FilesystemType`` だけでは
    区別できないメディア（例: DVDもcd9660でマウントされうる、
    BDXL/M-DISCもudfでマウントされうる）をサイズで判定する際に使用する。
    """

    cd_max_bytes: int = 1_000_000_000
    dvd_max_bytes: int = 10_000_000_000


@dataclass(frozen=True)
class AudioRipSettings:
    """音楽CDリッピング（``audio_cd``）の既定値。"""

    #: 検証時に最大何回まで独立してリッピングを試みるか
    #: （既定2回、不一致なら最大この回数まで）。
    max_attempts: int = 3


@dataclass(frozen=True)
class AppConfig:
    """アプリケーション全体の設定。"""

    media_size: MediaSizeThresholds = MediaSizeThresholds()
    audio_rip: AudioRipSettings = AudioRipSettings()


DEFAULT_CONFIG_PATH = Path.home() / ".config" / "mkhybrid-gui" / "config.toml"

_CONFIG_PATH_ENV_VAR = "MKHYBRID_GUI_CONFIG"


def _config_path() -> Path:
    """設定ファイルのパスを返す（環境変数で上書き可能）。"""
    override = os.environ.get(_CONFIG_PATH_ENV_VAR)
    return Path(override) if override else DEFAULT_CONFIG_PATH


def _merge(default: Any, overrides: dict[str, Any] | None) -> Any:
    """データクラス ``default`` のフィールドのうち、``overrides`` に
    存在するものだけを上書きした新しいインスタンスを返す。

    未知のキー・型の合わない値は無視する（設定ファイルの記述ミスで
    アプリが起動できなくなることを避けるため）。
    """
    if not overrides:
        return default

    kwargs: dict[str, Any] = {}

    for f in fields(default):
        if f.name not in overrides:
            continue

        value = overrides[f.name]
        current_value = getattr(default, f.name)

        if not isinstance(value, type(current_value)):
            continue

        kwargs[f.name] = value

    if not kwargs:
        return default

    return replace(default, **kwargs)


def _load_toml(path: Path) -> dict[str, Any]:
    try:
        raw = path.read_bytes()
    except OSError:
        return {}

    try:
        data = tomllib.loads(raw.decode("utf-8"))
    except (tomllib.TOMLDecodeError, UnicodeDecodeError):
        return {}

    return data if isinstance(data, dict) else {}


@lru_cache(maxsize=1)
def get_config() -> AppConfig:
    """アプリケーション設定を取得する（プロセス内でキャッシュされる）。

    テスト等で設定ファイルの読み込みをやり直したい場合は
    ``get_config.cache_clear()`` を呼んでから再度実行すること。
    """
    data = _load_toml(_config_path())

    media_size_data = data.get("media_size")
    audio_rip_data = data.get("audio_rip")

    media_size = _merge(
        MediaSizeThresholds(),
        media_size_data if isinstance(media_size_data, dict) else None,
    )
    audio_rip = _merge(
        AudioRipSettings(),
        audio_rip_data if isinstance(audio_rip_data, dict) else None,
    )

    return AppConfig(media_size=media_size, audio_rip=audio_rip)
