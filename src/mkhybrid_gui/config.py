"""アプリケーション全体の設定値（サイズ閾値・リトライ回数・GUIの既定値等）。

これまでコード内（``disk_utils.py``/``audio_cd.py``）に散らばっていた
マジックナンバーをこのモジュールに集約する。既定値はこのモジュール内の
データクラスに定義し、外部設定ファイル（TOML、既定は環境変数
``XDG_CACHE_HOME``（未設定時は ``~/.cache``）配下の
``mkhybrid/config.toml``。環境変数 ``MKHYBRID_GUI_CONFIG`` でパス自体を
上書き可能）が存在すればその値で上書きする。

TOMLを採用しているのは、本プロジェクトが既にPython 3.11以降を要求して
おり（``pyproject.toml``）、標準ライブラリの ``tomllib`` だけで読み込め、
追加の依存パッケージが不要なため。またフラットな整数値中心の設定内容には
型があいまいにならないTOMLの方が適しており、YAMLのような暗黙の型変換
（例: ``yes``/``no`` の真偽値化）の落とし穴もない。

設定ファイルが存在しない・壊れている・想定外の形式の場合は、既定値のみで
動作する（設定ファイルの不備でアプリの起動を妨げない）。

``get_config()`` による読み込みに加え、``save_config()`` でTOMLファイルへ
書き戻すこともできる。これはGUI側（``ui/main_window.py``）が、前回終了時
のオプション選択（Joliet/UDF等のチェックボックス、書き出し形式、検証の
有無、出力先フォルダ）を ``UiPreferences`` として次回起動時に復元する
ために使う。標準ライブラリの ``tomllib`` は読み込み専用のため、書き込みは
本モジュールが手書きでシリアライズする（対象はすべてフラットな
``bool``/``int``/``float``/``str`` のフィールドのみなので、TOML専用の
外部ライブラリを新たに依存追加するほどの複雑さはない）。
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
class AccurateRipSettings:
    """AccurateRip照合（``accuraterip``）の既定値。

    ドライブの読み取りオフセットは機種ごとに異なるため、既知のCRCと
    一致するまで``±search_range_samples``の範囲でオフセットを探索する。
    既定値の1000は、実機（ASUS SDRW-08U9M-U相当のUSB接続ドライブ）での
    検証時に使用し、実際のオフセット（+6サンプル）を範囲内で発見できた
    値。
    """

    search_range_samples: int = 1000


@dataclass(frozen=True)
class UiPreferences:
    """GUIのオプション選択のうち、次回起動時にも復元したい既定値。

    ``media_size``/``audio_rip`` が「実行のたびに変わることのない
    チューニング値」であるのに対し、こちらは実行のたびにユーザーが
    変更しうるGUIの状態（チェックボックス等）のスナップショットで
    ある。``MainWindow`` がウィンドウを閉じる際に現在の状態を
    ``save_config()`` で書き出し、次回起動時の ``get_config()`` で
    読み込んで各ウィジェットへ反映する。

    ``audio_format`` は表示用のラベル（例:
    ``"Apple Lossless (ALAC)"``）ではなく、``AudioFormat`` の
    メンバー名（例: ``"ALAC"``）で保存する。表示ラベルは将来的な
    文言変更の影響を受けうるため、設定ファイルの安定した識別子には
    向かない。

    ``audio_rip_mode`` も同様に、``"ACCURATE"``（cd-paranoiaによる
    トラックごとの正確なリッピング、既定）または``"CDRDAO_IMAGE"``
    （cdrdaoによるTOC+BINディスクイメージ作成）のいずれかをコードで
    保存する。
    """

    last_output_directory: str = ""
    joliet: bool = True
    rock: bool = True
    udf: bool = False
    verify: bool = True
    audio_format: str = "ALAC"
    audio_rip_mode: str = "ACCURATE"


@dataclass(frozen=True)
class AppConfig:
    """アプリケーション全体の設定。"""

    media_size: MediaSizeThresholds = MediaSizeThresholds()
    audio_rip: AudioRipSettings = AudioRipSettings()
    accuraterip: AccurateRipSettings = AccurateRipSettings()
    ui: UiPreferences = UiPreferences()


_CONFIG_PATH_ENV_VAR = "MKHYBRID_GUI_CONFIG"
_XDG_CACHE_HOME_ENV_VAR = "XDG_CACHE_HOME"
_CACHE_DIR_NAME = "mkhybrid"
_CONFIG_FILE_NAME = "config.toml"


def _xdg_cache_home() -> Path:
    """``XDG_CACHE_HOME`` を返す（未設定時は ``~/.cache``。XDG Base
    Directory 仕様の既定フォールバックに従う）。
    """
    override = os.environ.get(_XDG_CACHE_HOME_ENV_VAR)
    return Path(override) if override else Path.home() / ".cache"


def default_config_path() -> Path:
    """既定の設定ファイルパスを返す（``MKHYBRID_GUI_CONFIG`` 環境変数に
    よる上書きを考慮しない、素の既定値）。

    ``XDG_CACHE_HOME``（未設定時は ``~/.cache``）配下の ``mkhybrid``
    ディレクトリに ``config.toml`` を置く。
    """
    return _xdg_cache_home() / _CACHE_DIR_NAME / _CONFIG_FILE_NAME


def _config_path() -> Path:
    """設定ファイルのパスを返す（``MKHYBRID_GUI_CONFIG`` 環境変数で
    上書き可能。未設定時は ``default_config_path()``）。
    """
    override = os.environ.get(_CONFIG_PATH_ENV_VAR)
    return Path(override) if override else default_config_path()


def get_config_path() -> Path:
    """現在有効な設定ファイルのパスを返す。

    GUI（``ui/main_window.py`` の設定タブ）が、設定ファイルの実際の
    保存先をユーザーへ表示する際に使う公開API。
    """
    return _config_path()


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
    accuraterip_data = data.get("accuraterip")
    ui_data = data.get("ui")

    media_size = _merge(
        MediaSizeThresholds(),
        media_size_data if isinstance(media_size_data, dict) else None,
    )
    audio_rip = _merge(
        AudioRipSettings(),
        audio_rip_data if isinstance(audio_rip_data, dict) else None,
    )
    accuraterip = _merge(
        AccurateRipSettings(),
        accuraterip_data if isinstance(accuraterip_data, dict) else None,
    )
    ui = _merge(
        UiPreferences(),
        ui_data if isinstance(ui_data, dict) else None,
    )

    return AppConfig(
        media_size=media_size,
        audio_rip=audio_rip,
        accuraterip=accuraterip,
        ui=ui,
    )


def _toml_format_value(value: Any) -> str:
    """設定値1つをTOMLのリテラル表現（文字列）に変換する。

    対象フィールドはすべて ``bool``/``int``/``float``/``str`` の
    いずれかであるという前提（``AppConfig`` のデータクラス定義を参照）。
    ``bool`` は ``int`` のサブクラスのため、``bool`` の判定を
    ``int`` より先に行う必要がある。
    """
    if isinstance(value, bool):
        return "true" if value else "false"

    if isinstance(value, int):
        return str(value)

    if isinstance(value, float):
        return repr(value)

    if isinstance(value, str):
        escaped = (
            value.replace("\\", "\\\\")
            .replace('"', '\\"')
            .replace("\n", "\\n")
            .replace("\t", "\\t")
            .replace("\r", "\\r")
        )
        return f'"{escaped}"'

    raise TypeError(f"TOMLへシリアライズできない型です: {type(value)!r}")


def to_toml_string(app_config: AppConfig) -> str:
    """``AppConfig`` をTOML文字列にシリアライズする（``save_config`` が使用）。

    フィールドはすべてフラットな ``bool``/``int``/``float``/``str`` の
    ため、外部ライブラリを追加せず手書きでシリアライズする（読み込みに
    使う標準ライブラリの ``tomllib`` は書き込みに対応していないため）。
    """
    sections: list[str] = []

    for section_field in fields(app_config):
        section_value = getattr(app_config, section_field.name)
        lines = [f"[{section_field.name}]"]

        for value_field in fields(section_value):
            value = getattr(section_value, value_field.name)
            lines.append(f"{value_field.name} = {_toml_format_value(value)}")

        sections.append("\n".join(lines))

    return "\n\n".join(sections) + "\n"


def save_config(app_config: AppConfig, path: Path | None = None) -> None:
    """``app_config`` をTOMLファイルへ保存する。

    保存先ディレクトリ（既定: ``$XDG_CACHE_HOME/mkhybrid/``）が存在しない
    場合は作成する。書き込み中にプロセスが異常終了しても既存の設定
    ファイルが壊れた状態で残らないよう、同じディレクトリ内の一時ファイル
    へ書き出してから ``Path.replace`` でアトミックに置き換える。

    保存後は ``get_config()`` の ``lru_cache`` をクリアし、次回の呼び出し
    で保存内容が反映されるようにする。
    """
    target = path if path is not None else _config_path()
    target.parent.mkdir(parents=True, exist_ok=True)

    tmp_path = target.with_name(target.name + ".tmp")
    tmp_path.write_text(to_toml_string(app_config), encoding="utf-8")
    tmp_path.replace(target)

    get_config.cache_clear()
