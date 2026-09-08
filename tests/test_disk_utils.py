"""``disk_utils`` のパース処理を、固定データを用いてテストする。"""

from __future__ import annotations

import plistlib

import pytest

from mkhybrid_gui.disk_utils import DiskUtilError, list_volumes, parse_diskutil_list

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

    cd_volume = next(v for v in volumes if v.device_identifier == "disk2s0")
    assert cd_volume.volume_name == "SAMPLE_CD"
    assert cd_volume.mount_point == "/Volumes/SAMPLE_CD"
    assert cd_volume.is_mounted is True
    assert "SAMPLE_CD" in cd_volume.display_name


def test_parse_diskutil_list_invalid_data_raises() -> None:
    with pytest.raises(DiskUtilError):
        parse_diskutil_list(b"not a plist")


def test_list_volumes_filters_unmounted(monkeypatch: pytest.MonkeyPatch) -> None:
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

    class FakeCompletedProcess:
        returncode = 0
        stdout = plistlib.dumps(data)
        stderr = b""

    def fake_run(*args, **kwargs):
        return FakeCompletedProcess()

    monkeypatch.setattr("mkhybrid_gui.disk_utils.subprocess.run", fake_run)

    volumes = list_volumes(mounted_only=True)
    device_ids = {v.device_identifier for v in volumes}
    assert "disk3s1" not in device_ids
    assert "disk2s0" in device_ids


def test_list_volumes_raises_on_nonzero_returncode(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeCompletedProcess:
        returncode = 1
        stdout = b""
        stderr = b"diskutil: command not found"

    def fake_run(*args, **kwargs):
        return FakeCompletedProcess()

    monkeypatch.setattr("mkhybrid_gui.disk_utils.subprocess.run", fake_run)

    with pytest.raises(DiskUtilError):
        list_volumes()
