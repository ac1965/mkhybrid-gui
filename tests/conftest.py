"""テスト全体で共有するフィクスチャ。"""

from __future__ import annotations

from pathlib import Path

import pytest

from mkhybrid_gui import config


@pytest.fixture(autouse=True)
def _isolate_app_config(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """全テストを、実行環境に実在するかもしれない設定ファイル
    （``~/.config/mkhybrid-gui/config.toml``）や ``MKHYBRID_GUI_CONFIG``
    環境変数から隔離し、常に既定値のみで動作させる。

    ``config.get_config()`` は ``lru_cache`` されているため、テストが
    このキャッシュ経由で実行環境やテスト実行順序に依存してしまうのを防ぐ。
    """
    monkeypatch.setenv(
        "MKHYBRID_GUI_CONFIG",
        str(tmp_path / "unused-config.toml"),
    )
    config.get_config.cache_clear()
    yield
    config.get_config.cache_clear()
