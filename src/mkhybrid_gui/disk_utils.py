"""``diskutil list -plist`` の実行・パースを行うモジュール。

UIフレームワーク（PySide6）には依存せず、単体でテスト可能な設計とする。
"""

from __future__ import annotations

import plistlib
import subprocess
from dataclasses import dataclass


class DiskUtilError(RuntimeError):
    """``diskutil`` コマンドの実行に失敗した場合に送出する。"""


@dataclass(frozen=True)
class Volume:
    """マウント済みボリューム（またはパーティション）を表す。"""

    device_identifier: str
    volume_name: str | None
    mount_point: str | None
    size: int
    content: str | None

    @property
    def is_mounted(self) -> bool:
        return bool(self.mount_point)

    @property
    def display_name(self) -> str:
        name = self.volume_name or "(名称未設定)"
        return f"{name} — /dev/{self.device_identifier}"


def parse_diskutil_list(plist_data: bytes) -> list[Volume]:
    """``diskutil list -plist`` の出力（bytes）をパースし、Volumeの一覧を返す。

    固定データを渡せるため、実機のディスクなしにユニットテスト可能。
    """
    try:
        root = plistlib.loads(plist_data)
    except Exception as exc:  # noqa: BLE001 - plistlibの例外を包んで再送出
        raise DiskUtilError(f"diskutil list -plist の出力解析に失敗しました: {exc}") from exc

    volumes: list[Volume] = []
    for disk in root.get("AllDisksAndPartitions", []):
        volumes.extend(_volumes_from_disk_entry(disk))
    return volumes


def _volumes_from_disk_entry(disk: dict) -> list[Volume]:
    volumes: list[Volume] = []

    # ディスク自体（パーティション分割されていないボリューム）を含める
    if "MountPoint" in disk or disk.get("Content"):
        volumes.append(_volume_from_entry(disk))

    for partition in disk.get("Partitions", []):
        volumes.append(_volume_from_entry(partition))

    return volumes


def _volume_from_entry(entry: dict) -> Volume:
    return Volume(
        device_identifier=entry.get("DeviceIdentifier", ""),
        volume_name=entry.get("VolumeName"),
        mount_point=entry.get("MountPoint"),
        size=int(entry.get("Size", 0)),
        content=entry.get("Content"),
    )


def list_volumes(*, mounted_only: bool = True) -> list[Volume]:
    """``diskutil list -plist`` を実行し、Volumeの一覧を返す。

    Parameters
    ----------
    mounted_only:
        Trueの場合、マウントポイントを持つボリュームのみを返す
        （GUIの選択肢としては未マウントのものは扱いにくいため）。
    """
    result = subprocess.run(
        ["diskutil", "list", "-plist"],
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        stderr = result.stderr.decode("utf-8", errors="replace")
        raise DiskUtilError(f"diskutil list の実行に失敗しました: {stderr}")

    volumes = parse_diskutil_list(result.stdout)
    if mounted_only:
        volumes = [v for v in volumes if v.is_mounted]
    return volumes
