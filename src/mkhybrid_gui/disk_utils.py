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


class DiskUtilError(RuntimeError):
    """``diskutil`` コマンドの実行に失敗した場合に送出する。"""


_PARTITION_SUFFIX_RE = re.compile(r"s\d+$")


def whole_disk_raw_device(device_identifier: str) -> str:
    """パーティション識別子（例: ``disk5s1``）から、ディスク全体の生デバイス
    パス（例: ``/dev/rdisk5``）を求める。

    ``cdparanoia`` 等、光学ドライブへ直接アクセスするツールは特定パーティション
    ではなくディスク全体の生デバイスを要求するため、この変換が必要になる。
    """
    whole_disk = _PARTITION_SUFFIX_RE.sub("", device_identifier)
    return f"/dev/r{whole_disk}"


class MediaType(str, Enum):
    """光学メディアの種別。"""

    CD_DATA = "データCD"
    CD_AUDIO = "音楽CD"
    DVD = "DVD"
    BD = "Blu-ray (BD/BDXL/M-DISC)"


# 判定用のサイズ閾値（バイト）。実際の規格上限より少し余裕を持たせてある。
_CD_MAX_BYTES = 1_000_000_000  # 約900MB CDメディア相当
_DVD_MAX_BYTES = 10_000_000_000  # 約9.4GB 二層DVDメディア相当


def detect_media_type(volume: Volume, filesystem_type: str | None) -> MediaType:
    """ボリュームのファイルシステム種別・サイズからメディア種別を推定する。

    BDXL・M-DISCは、OSからは通常のBD-R/BD-REと同じファイルシステムでマウントされ
    ソフトウェア的な区別がないため、このサイズベースの判定がそのまま適用できる
    （容量が大きいだけの通常のBlu-rayとして ``MediaType.BD`` に分類される）。
    """
    fs = (filesystem_type or "").lower()

    if fs == "cddafs":
        return MediaType.CD_AUDIO
    if fs == "cd9660":
        return MediaType.CD_DATA if volume.size <= _CD_MAX_BYTES else MediaType.DVD
    if fs == "udf":
        return MediaType.DVD if volume.size <= _DVD_MAX_BYTES else MediaType.BD

    # ファイルシステム情報が取得できない場合はサイズのみから推定する
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
        prefix = f"[{self.media_type.value}] " if self.media_type is not None else ""
        return f"{prefix}{name} — /dev/{self.device_identifier}"


def parse_diskutil_list(plist_data: bytes) -> list[Volume]:
    """``diskutil list -plist`` の出力（bytes）をパースし、Volumeの一覧を返す。

    固定データを渡せるため、実機のディスクなしにユニットテスト可能。
    メディア種別（``media_type``）はここでは判定しない
    （``diskutil info`` の追加呼び出しが必要なため、``list_volumes`` 側で行う）。
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


def get_filesystem_type(device_identifier: str) -> str | None:
    """``diskutil info -plist <device>`` から ``FilesystemType`` を取得する。

    取得できない場合（コマンド失敗・解析失敗）は ``None`` を返し、
    呼び出し側（``detect_media_type``）でサイズベースの推定にフォールバックする。
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
    except Exception:  # noqa: BLE001 - 解析失敗時はNone扱いにする
        return None
    return info.get("FilesystemType")


def list_volumes(*, mounted_only: bool = True) -> list[Volume]:
    """``diskutil list -plist`` を実行し、メディア種別を付与したVolumeの一覧を返す。

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
    return [_with_media_type(v) for v in volumes]


def _with_media_type(volume: Volume) -> Volume:
    filesystem_type = get_filesystem_type(volume.device_identifier)
    media_type = detect_media_type(volume, filesystem_type)
    return dataclasses.replace(volume, filesystem_type=filesystem_type, media_type=media_type)
