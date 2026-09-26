"""``disk_utils`` のパース処理を、固定データを用いてテストする。"""

from __future__ import annotations

import plistlib

import pytest

from mkhybrid_gui.disk_utils import (
    DiskUtilError,
    MediaType,
    Volume,
    detect_media_type,
    list_volumes,
    parse_diskutil_list,
    whole_disk_raw_device,
)


def test_whole_disk_raw_device_strips_partition_suffix() -> None:
    assert whole_disk_raw_device("disk5s1") == "/dev/rdisk5"


def test_whole_disk_raw_device_handles_whole_disk_already() -> None:
    assert whole_disk_raw_device("disk5") == "/dev/rdisk5"


def test_whole_disk_raw_device_handles_multi_digit_partition() -> None:
    assert whole_disk_raw_device("disk12s3") == "/dev/rdisk12"


SAMPLE_DISKUTIL_LIST = {
    "AllDisks": ["disk0", "disk0s1", "disk2", "disk2s0"],
    "AllDisksAndPartitions": [
        {
            "Content": "GUID_partition_scheme",
            "DeviceIdentifier": "disk0",
            "Size": 500_000_000_000,
            "Partitions": [
                {
                    "Content": "Apple_APFS",
                    "DeviceIdentifier": "disk0s1",
                    "MountPoint": "/",
                    "Size": 500_000_000_000,
                    "VolumeName": "Macintosh HD",
                },
            ],
        },
        {
            "Content": "",
            "DeviceIdentifier": "disk2",
            "Size": 700_000_000,
            "Partitions": [
                {
                    "Content": "ISO9660",
                    "DeviceIdentifier": "disk2s0",
                    "MountPoint": "/Volumes/SAMPLE_CD",
                    "Size": 700_000_000,
                    "VolumeName": "SAMPLE_CD",
                },
            ],
        },
    ],
    "VolumesFromDisks": ["Macintosh HD", "SAMPLE_CD"],
    "WholeDisks": ["disk0", "disk2"],
}


def _sample_plist_bytes() -> bytes:
    return plistlib.dumps(SAMPLE_DISKUTIL_LIST)


def test_parse_diskutil_list_returns_all_volumes() -> None:
    volumes = parse_diskutil_list(_sample_plist_bytes())

    device_ids = {v.device_identifier for v in volumes}

    assert "disk0s1" in device_ids
    assert "disk2s0" in device_ids


def test_parse_diskutil_list_extracts_cd_volume_fields() -> None:
    volumes = parse_diskutil_list(_sample_plist_bytes())

    cd_volume = next(
        v for v in volumes if v.device_identifier == "disk2s0"
    )

    assert cd_volume.volume_name == "SAMPLE_CD"
    assert cd_volume.mount_point == "/Volumes/SAMPLE_CD"
    assert cd_volume.is_mounted is True
    assert "SAMPLE_CD" in cd_volume.display_name


def test_parse_diskutil_list_invalid_data_raises() -> None:
    with pytest.raises(DiskUtilError):
        parse_diskutil_list(b"not a plist")


def _fake_diskutil_run(
    list_data: dict,
    filesystem_types: dict[str, str],
    disk_infos: dict[str, dict] | None = None,
):
    """``diskutil list -plist`` と ``diskutil info -plist <device>`` を
    引数に応じて振り分けるフェイクの ``subprocess.run``。
    """

    class FakeCompletedProcess:
        def __init__(
            self,
            stdout: bytes,
            returncode: int = 0,
            stderr: bytes = b"",
        ):
            self.stdout = stdout
            self.returncode = returncode
            self.stderr = stderr

    disk_infos = disk_infos or {}

    def fake_run(cmd, **kwargs):
        if cmd[:2] == ["diskutil", "list"]:
            return FakeCompletedProcess(
                plistlib.dumps(list_data)
            )

        assert cmd[:2] == ["diskutil", "info"]

        device = cmd[-1].removeprefix("/dev/")

        if device in disk_infos:
            return FakeCompletedProcess(
                plistlib.dumps(disk_infos[device])
            )

        fs_type = filesystem_types.get(device)

        if fs_type is None:
            return FakeCompletedProcess(
                b"",
                returncode=1,
                stderr=b"not found",
            )

        return FakeCompletedProcess(
            plistlib.dumps({"FilesystemType": fs_type})
        )

    return fake_run


def test_list_volumes_filters_unmounted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data = SAMPLE_DISKUTIL_LIST | {
        "AllDisksAndPartitions": [
            *SAMPLE_DISKUTIL_LIST["AllDisksAndPartitions"],
            {
                "Content": "",
                "DeviceIdentifier": "disk3",
                "Size": 1_000_000,
                "Partitions": [
                    {
                        "Content": "Apple_Free",
                        "DeviceIdentifier": "disk3s1",
                        "Size": 1_000_000,
                    },
                ],
            },
        ]
    }

    monkeypatch.setattr(
        "mkhybrid_gui.disk_utils.subprocess.run",
        _fake_diskutil_run(
            data,
            {"disk2s0": "cd9660"},
        ),
    )

    volumes = list_volumes(mounted_only=True)
    device_ids = {v.device_identifier for v in volumes}

    assert "disk3s1" not in device_ids
    assert "disk2s0" in device_ids


def test_list_volumes_attaches_media_type(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "mkhybrid_gui.disk_utils.subprocess.run",
        _fake_diskutil_run(
            SAMPLE_DISKUTIL_LIST,
            {"disk2s0": "cd9660"},
        ),
    )

    volumes = list_volumes(mounted_only=True)

    cd_volume = next(
        v for v in volumes if v.device_identifier == "disk2s0"
    )

    assert cd_volume.filesystem_type == "cd9660"
    assert cd_volume.media_type == MediaType.CD_DATA
    assert "[データCD]" in cd_volume.display_name


def test_list_volumes_media_type_unavailable_falls_back_to_size(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """diskutil info が失敗してもサイズからメディア種別を推定する。"""

    monkeypatch.setattr(
        "mkhybrid_gui.disk_utils.subprocess.run",
        _fake_diskutil_run(
            SAMPLE_DISKUTIL_LIST,
            {},
        ),
    )

    volumes = list_volumes(mounted_only=True)

    cd_volume = next(
        v for v in volumes if v.device_identifier == "disk2s0"
    )

    assert cd_volume.filesystem_type is None
    assert cd_volume.media_type == MediaType.CD_DATA


def test_list_volumes_recovers_optical_volume_from_all_disks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """macOSの光学メディアでAllDisksAndPartitionsのPartitionsが空でも、
    AllDisks + diskutil infoからマウント済みCD-ROMを復元できることを確認する。

    実機で確認した以下の構造を再現する。

        AllDisks:
            disk4
            disk4s0

        AllDisksAndPartitions:
            disk4
            Partitions: []

        diskutil info disk4s0:
            VolumeName: PCW2000_03
            MountPoint: /Volumes/PCW2000_03
            FilesystemType: cd9660
            OpticalMediaType: CD-ROM
    """
    optical_list = {
        "AllDisks": ["disk4", "disk4s0"],
        "AllDisksAndPartitions": [
            {
                "Content": "CD_partition_scheme",
                "DeviceIdentifier": "disk4",
                "OSInternal": False,
                "Partitions": [],
                "Size": 550_191_600,
            }
        ],
        "VolumesFromDisks": ["PCW2000_03"],
        "WholeDisks": ["disk4"],
    }

    disk4_info = {
        "DeviceIdentifier": "disk4",
        "Content": "CD_partition_scheme",
        "Size": 550_191_600,
        "WholeDisk": True,
        "Removable": True,
        "RemovableMedia": True,
        "Ejectable": True,
    }

    disk4s0_info = {
        "DeviceIdentifier": "disk4s0",
        "DeviceNode": "/dev/disk4s0",
        "Content": "CD_ROM_Mode_1",
        "FilesystemName": "ISO Joliet",
        "FilesystemType": "cd9660",
        "MountPoint": "/Volumes/PCW2000_03",
        "VolumeName": "PCW2000_03",
        "Size": 479_078_400,
        "VolumeSize": 478_771_200,
        "WholeDisk": False,
        "ParentWholeDisk": "disk4",
        "Removable": True,
        "RemovableMedia": True,
        "Ejectable": True,
        "OpticalMediaType": "CD-ROM",
        "MediaName": "ASUS SDRW-08U9M-U",
    }

    monkeypatch.setattr(
        "mkhybrid_gui.disk_utils.subprocess.run",
        _fake_diskutil_run(
            optical_list,
            {},
            disk_infos={
                "disk4": disk4_info,
                "disk4s0": disk4s0_info,
            },
        ),
    )

    volumes = list_volumes(mounted_only=True)

    assert len(volumes) == 1

    volume = volumes[0]

    assert volume.device_identifier == "disk4s0"
    assert volume.volume_name == "PCW2000_03"
    assert volume.mount_point == "/Volumes/PCW2000_03"
    assert volume.size == 479_078_400
    assert volume.content == "CD_ROM_Mode_1"
    assert volume.filesystem_type == "cd9660"
    assert volume.media_type == MediaType.CD_DATA
    assert volume.is_mounted is True
    assert volume.display_name == (
        "[データCD] PCW2000_03 — /dev/disk4s0"
    )


def test_list_volumes_does_not_duplicate_existing_volume(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AllDisksAndPartitionsに既に存在するボリュームを、
    AllDisksの補完処理で二重登録しないことを確認する。
    """
    monkeypatch.setattr(
        "mkhybrid_gui.disk_utils.subprocess.run",
        _fake_diskutil_run(
            SAMPLE_DISKUTIL_LIST,
            {
                "disk0s1": "apfs",
                "disk2s0": "cd9660",
            },
            disk_infos={
                "disk0s1": {
                    "DeviceIdentifier": "disk0s1",
                    "FilesystemType": "apfs",
                    "MountPoint": "/",
                    "VolumeName": "Macintosh HD",
                    "Size": 500_000_000_000,
                },
                "disk2s0": {
                    "DeviceIdentifier": "disk2s0",
                    "FilesystemType": "cd9660",
                    "MountPoint": "/Volumes/SAMPLE_CD",
                    "VolumeName": "SAMPLE_CD",
                    "Size": 700_000_000,
                },
            },
        ),
    )

    volumes = list_volumes(mounted_only=True)

    device_ids = [
        volume.device_identifier
        for volume in volumes
    ]

    # disk0s1（apfs、内蔵ボリューム）は光学メディアではないため、
    # list_volumes()の結果には含まれない。
    assert "disk0s1" not in device_ids
    assert device_ids.count("disk2s0") == 1


def test_detect_media_type_cddafs_is_audio_cd() -> None:
    assert (
        detect_media_type(
            _volume(700_000_000),
            "cddafs",
        )
        == MediaType.CD_AUDIO
    )


def test_detect_media_type_cd9660_small_is_cd_data() -> None:
    assert (
        detect_media_type(
            _volume(700_000_000),
            "cd9660",
        )
        == MediaType.CD_DATA
    )


def test_detect_media_type_cd9660_large_is_dvd() -> None:
    # DVDもISO9660（cd9660）でマウントされることがあるため、
    # サイズで判別する。
    assert (
        detect_media_type(
            _volume(4_500_000_000),
            "cd9660",
        )
        == MediaType.DVD
    )


def test_detect_media_type_udf_small_is_dvd() -> None:
    assert (
        detect_media_type(
            _volume(4_500_000_000),
            "udf",
        )
        == MediaType.DVD
    )


def test_detect_media_type_udf_large_is_bd() -> None:
    assert (
        detect_media_type(
            _volume(25_000_000_000),
            "udf",
        )
        == MediaType.BD
    )


def test_detect_media_type_bdxl_capacity_is_bd() -> None:
    # BDXL(最大128GB)やM-DISCのBD-Rも、OS上は通常のBDと同じUDFで
    # マウントされる。
    assert (
        detect_media_type(
            _volume(100_000_000_000),
            "udf",
        )
        == MediaType.BD
    )


def test_detect_media_type_unknown_filesystem_falls_back_to_size() -> None:
    assert (
        detect_media_type(
            _volume(700_000_000),
            None,
        )
        == MediaType.CD_DATA
    )

    assert (
        detect_media_type(
            _volume(4_500_000_000),
            None,
        )
        == MediaType.DVD
    )

    assert (
        detect_media_type(
            _volume(25_000_000_000),
            None,
        )
        == MediaType.BD
    )


def test_detect_media_type_known_non_optical_filesystem_returns_none() -> None:
    """apfs/hfs+/exfat等、光学メディアでは使われないファイルシステムが
    判明している場合は、サイズによらず光学メディアとして扱わない
    （``None`` を返す）ことを確認する。

    実機で確認された回帰: 500GBの内蔵APFS起動ボリューム
    （Macintosh HD）が、サイズだけで「Blu-ray」に誤分類され、
    GUIのドライブ選択肢に表示されてしまっていた。
    """
    assert (
        detect_media_type(
            _volume(500_000_000_000),
            "apfs",
        )
        is None
    )

    assert (
        detect_media_type(
            _volume(500_000),
            "hfs+",
        )
        is None
    )

    assert (
        detect_media_type(
            _volume(32_000_000_000),
            "exfat",
        )
        is None
    )


def test_list_volumes_excludes_internal_apfs_volumes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """内蔵APFS起動ボリュームが光学メディアの選択肢に紛れ込まないことを
    確認する（実機のディスク構成を模した回帰テスト）。
    """
    data = SAMPLE_DISKUTIL_LIST | {
        "AllDisksAndPartitions": [
            *SAMPLE_DISKUTIL_LIST["AllDisksAndPartitions"],
        ],
    }

    monkeypatch.setattr(
        "mkhybrid_gui.disk_utils.subprocess.run",
        _fake_diskutil_run(
            data,
            {
                "disk0s1": "apfs",
                "disk2s0": "cd9660",
            },
        ),
    )

    volumes = list_volumes(mounted_only=True)
    device_ids = {volume.device_identifier for volume in volumes}

    assert "disk0s1" not in device_ids
    assert "disk2s0" in device_ids
    assert all(volume.media_type is not None for volume in volumes)


def test_list_volumes_raises_on_nonzero_returncode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeCompletedProcess:
        returncode = 1
        stdout = b""
        stderr = b"diskutil: command not found"

    def fake_run(*args, **kwargs):
        return FakeCompletedProcess()

    monkeypatch.setattr(
        "mkhybrid_gui.disk_utils.subprocess.run",
        fake_run,
    )

    with pytest.raises(DiskUtilError):
        list_volumes()


def _volume(size: int) -> Volume:
    return Volume(
        device_identifier="disk2s0",
        volume_name="SAMPLE",
        mount_point="/Volumes/SAMPLE",
        size=size,
        content=None,
    )
