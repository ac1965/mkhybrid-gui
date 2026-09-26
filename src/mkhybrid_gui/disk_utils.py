"""``diskutil`` の実行・パース、光学メディア種別の判定を行うモジュール。

UIフレームワーク（PySide6）には依存せず、単体でテスト可能な設計とする。
"""

from __future__ import annotations

import dataclasses
import plistlib
import re
import subprocess
from dataclasses import dataclass
from enum import Enum
from typing import Any


class DiskUtilError(RuntimeError):
    """``diskutil`` コマンドの実行に失敗した場合に送出する。"""


_PARTITION_SUFFIX_RE = re.compile(r"s\d+$")


def whole_disk_raw_device(device_identifier: str) -> str:
    """パーティション識別子からディスク全体の生デバイスパスを求める。

    例:
        ``disk5s1`` -> ``/dev/rdisk5``
        ``disk5``   -> ``/dev/rdisk5``

    ``cdparanoia`` 等、光学ドライブへ直接アクセスするツールは
    特定パーティションではなくディスク全体の生デバイスを要求するため、
    この変換が必要になる。
    """
    whole_disk = _PARTITION_SUFFIX_RE.sub("", device_identifier)
    return f"/dev/r{whole_disk}"


class MediaType(str, Enum):
    """光学メディアの種別。"""

    CD_DATA = "データCD"
    CD_AUDIO = "音楽CD"
    DVD = "DVD"
    BD = "Blu-ray (BD/BDXL/M-DISC)"


# 判定用のサイズ閾値（バイト）。
_CD_MAX_BYTES = 1_000_000_000
_DVD_MAX_BYTES = 10_000_000_000


def detect_media_type(volume: Volume, filesystem_type: str | None) -> MediaType:
    """ボリュームのファイルシステム種別・サイズからメディア種別を推定する。

    BDXL・M-DISCは、OSからは通常のBD-R/BD-REと同じファイルシステムで
    マウントされるため、容量ベースでBlu-rayとして分類する。
    """
    fs = (filesystem_type or "").lower()

    if fs == "cddafs":
        return MediaType.CD_AUDIO

    if fs == "cd9660":
        return (
            MediaType.CD_DATA
            if volume.size <= _CD_MAX_BYTES
            else MediaType.DVD
        )

    if fs == "udf":
        return (
            MediaType.DVD
            if volume.size <= _DVD_MAX_BYTES
            else MediaType.BD
        )

    # ファイルシステム情報が取得できない場合はサイズから推定する。
    if volume.size <= _CD_MAX_BYTES:
        return MediaType.CD_DATA

    if volume.size <= _DVD_MAX_BYTES:
        return MediaType.DVD

    return MediaType.BD


@dataclass(frozen=True)
class Volume:
    """マウント済みボリューム（またはパーティション）を表す。"""

    device_identifier: str
    volume_name: str | None
    mount_point: str | None
    size: int
    content: str | None
    filesystem_type: str | None = None
    media_type: MediaType | None = None

    @property
    def is_mounted(self) -> bool:
        return bool(self.mount_point)

    @property
    def display_name(self) -> str:
        name = self.volume_name or "(名称未設定)"
        prefix = (
            f"[{self.media_type.value}] "
            if self.media_type is not None
            else ""
        )
        return f"{prefix}{name} — /dev/{self.device_identifier}"


def parse_diskutil_list(plist_data: bytes) -> list[Volume]:
    """``diskutil list -plist`` の出力をパースする。

    ``AllDisksAndPartitions`` に実際のパーティション情報が含まれている
    通常のディスクについては、ここでボリュームを取得する。

    注意:
        macOSの光学メディアでは、``AllDisks`` には ``disk4s0`` が存在する
        一方で、``AllDisksAndPartitions`` の ``Partitions`` が空になる
        ケースがある。その場合の補完は ``list_volumes()`` で
        ``diskutil info -plist`` を使って行う。
    """
    try:
        root = plistlib.loads(plist_data)
    except Exception as exc:  # noqa: BLE001
        raise DiskUtilError(
            f"diskutil list -plist の出力解析に失敗しました: {exc}"
        ) from exc

    return _parse_diskutil_root(root)


def _parse_diskutil_root(root: dict[str, Any]) -> list[Volume]:
    volumes: list[Volume] = []

    for disk in root.get("AllDisksAndPartitions", []):
        volumes.extend(_volumes_from_disk_entry(disk))

    return volumes


def _volumes_from_disk_entry(disk: dict[str, Any]) -> list[Volume]:
    volumes: list[Volume] = []

    # ディスク自体（パーティション分割されていないボリューム）を含める。
    if "MountPoint" in disk or disk.get("Content"):
        volume = _volume_from_entry(disk)
        if volume.device_identifier:
            volumes.append(volume)

    for partition in disk.get("Partitions", []):
        volume = _volume_from_entry(partition)
        if volume.device_identifier:
            volumes.append(volume)

    return volumes


def _volume_from_entry(entry: dict[str, Any]) -> Volume:
    return Volume(
        device_identifier=entry.get("DeviceIdentifier", ""),
        volume_name=entry.get("VolumeName"),
        mount_point=entry.get("MountPoint"),
        size=int(entry.get("Size", 0)),
        content=entry.get("Content"),
    )


def get_disk_info(device_identifier: str) -> dict[str, Any] | None:
    """``diskutil info -plist`` の結果を辞書として取得する。

    コマンド失敗・plist解析失敗時は ``None`` を返す。
    """
    result = subprocess.run(
        ["diskutil", "info", "-plist", f"/dev/{device_identifier}"],
        capture_output=True,
        check=False,
    )

    if result.returncode != 0:
        return None

    try:
        info = plistlib.loads(result.stdout)
    except Exception:  # noqa: BLE001
        return None

    if not isinstance(info, dict):
        return None

    return info


def get_filesystem_type(device_identifier: str) -> str | None:
    """``diskutil info -plist`` から ``FilesystemType`` を取得する。"""
    info = get_disk_info(device_identifier)

    if info is None:
        return None

    filesystem_type = info.get("FilesystemType")
    return filesystem_type if isinstance(filesystem_type, str) else None


def _volume_from_info(info: dict[str, Any]) -> Volume | None:
    """``diskutil info -plist`` の結果からVolumeを生成する。

    光学メディアでは ``diskutil list -plist`` の
    ``AllDisksAndPartitions`` にパーティションが現れない場合があるため、
    ``diskutil info`` の情報をVolumeへ変換する。
    """
    device_identifier = info.get("DeviceIdentifier")

    if not isinstance(device_identifier, str) or not device_identifier:
        return None

    size = info.get("Size", info.get("VolumeSize", 0))

    try:
        size = int(size)
    except (TypeError, ValueError):
        size = 0

    volume_name = info.get("VolumeName")
    if not isinstance(volume_name, str):
        volume_name = None

    mount_point = info.get("MountPoint")
    if not isinstance(mount_point, str):
        mount_point = None

    content = info.get("Content")
    if not isinstance(content, str):
        content = None

    filesystem_type = info.get("FilesystemType")
    if not isinstance(filesystem_type, str):
        filesystem_type = None

    return Volume(
        device_identifier=device_identifier,
        volume_name=volume_name,
        mount_point=mount_point,
        size=size,
        content=content,
        filesystem_type=filesystem_type,
    )


def _supplement_missing_volumes(
    root: dict[str, Any],
    volumes: list[Volume],
) -> list[Volume]:
    """``AllDisks`` を使って不足しているボリューム情報を補完する。

    macOSの光学メディアでは、例えば次のような状態になることがある。

        AllDisks:
            disk4
            disk4s0

        AllDisksAndPartitions:
            disk4
            Partitions: []

    この場合、``disk4s0`` は ``diskutil info -plist`` から取得する。
    """
    known_ids = {
        volume.device_identifier
        for volume in volumes
        if volume.device_identifier
    }

    supplemented = list(volumes)

    for device_identifier in root.get("AllDisks", []):
        if not isinstance(device_identifier, str):
            continue

        if device_identifier in known_ids:
            continue

        info = get_disk_info(device_identifier)
        if info is None:
            continue

        volume = _volume_from_info(info)
        if volume is None:
            continue

        # VolumeName / MountPoint / FilesystemType のいずれかが存在する
        # 実ボリュームだけを補完対象とする。
        if not any(
            (
                volume.volume_name,
                volume.mount_point,
                volume.filesystem_type,
            )
        ):
            continue

        supplemented.append(volume)
        known_ids.add(volume.device_identifier)

    return supplemented


def list_volumes(*, mounted_only: bool = True) -> list[Volume]:
    """``diskutil list -plist`` を実行してVolume一覧を返す。

    ``AllDisksAndPartitions`` に現れない光学メディアについては、
    ``AllDisks`` のデバイスを ``diskutil info -plist`` で補完する。

    Parameters
    ----------
    mounted_only:
        Trueの場合、マウントポイントを持つボリュームのみを返す。
        GUIの通常利用ではこの値を使用する。
    """
    result = subprocess.run(
        ["diskutil", "list", "-plist"],
        capture_output=True,
        check=False,
    )

    if result.returncode != 0:
        stderr = result.stderr.decode("utf-8", errors="replace")
        raise DiskUtilError(
            f"diskutil list の実行に失敗しました: {stderr}"
        )

    try:
        root = plistlib.loads(result.stdout)
    except Exception as exc:  # noqa: BLE001
        raise DiskUtilError(
            f"diskutil list -plist の出力解析に失敗しました: {exc}"
        ) from exc

    if not isinstance(root, dict):
        raise DiskUtilError(
            "diskutil list -plist のルート要素が辞書ではありません"
        )

    volumes = _parse_diskutil_root(root)

    # macOSの光学メディア等でAllDisksAndPartitionsから
    # パーティション情報が欠落するケースを補完する。
    volumes = _supplement_missing_volumes(root, volumes)

    if mounted_only:
        volumes = [volume for volume in volumes if volume.is_mounted]

    return [_with_media_type(volume) for volume in volumes]


def _with_media_type(volume: Volume) -> Volume:
    """VolumeにFilesystemTypeとMediaTypeを付与する。"""
    filesystem_type = volume.filesystem_type

    if filesystem_type is None:
        filesystem_type = get_filesystem_type(
            volume.device_identifier
        )

    media_type = detect_media_type(volume, filesystem_type)

    return dataclasses.replace(
        volume,
        filesystem_type=filesystem_type,
        media_type=media_type,
    )
