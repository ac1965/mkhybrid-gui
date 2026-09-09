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
)

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


def _fake_diskutil_run(list_data: dict, filesystem_types: dict[str, str]):
    """``diskutil list -plist`` と ``diskutil info -plist <device>`` を
    引数に応じて振り分けるフェイクの ``subprocess.run``。
    """

    class FakeCompletedProcess:
        def __init__(self, stdout: bytes, returncode: int = 0, stderr: bytes = b""):
            self.stdout = stdout
            self.returncode = returncode
            self.stderr = stderr

    def fake_run(cmd, **kwargs):
        if cmd[:2] == ["diskutil", "list"]:
            return FakeCompletedProcess(plistlib.dumps(list_data))

        assert cmd[:2] == ["diskutil", "info"]
        device = cmd[-1].removeprefix("/dev/")
        fs_type = filesystem_types.get(device)
        if fs_type is None:
            return FakeCompletedProcess(b"", returncode=1, stderr=b"not found")
        return FakeCompletedProcess(plistlib.dumps({"FilesystemType": fs_type}))

    return fake_run


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

    monkeypatch.setattr(
        "mkhybrid_gui.disk_utils.subprocess.run",
        _fake_diskutil_run(data, {"disk2s0": "cd9660"}),
    )

    volumes = list_volumes(mounted_only=True)
    device_ids = {v.device_identifier for v in volumes}
    assert "disk3s1" not in device_ids
    assert "disk2s0" in device_ids


def test_list_volumes_attaches_media_type(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "mkhybrid_gui.disk_utils.subprocess.run",
        _fake_diskutil_run(SAMPLE_DISKUTIL_LIST, {"disk2s0": "cd9660"}),
    )

    volumes = list_volumes(mounted_only=True)
    cd_volume = next(v for v in volumes if v.device_identifier == "disk2s0")
    assert cd_volume.filesystem_type == "cd9660"
    assert cd_volume.media_type == MediaType.CD_DATA
    assert "[データCD]" in cd_volume.display_name


def test_list_volumes_media_type_unavailable_falls_back_to_size(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # diskutil info が失敗した場合でも、サイズから推定したメディア種別が付与される
    monkeypatch.setattr(
        "mkhybrid_gui.disk_utils.subprocess.run",
        _fake_diskutil_run(SAMPLE_DISKUTIL_LIST, {}),
    )

    volumes = list_volumes(mounted_only=True)
    cd_volume = next(v for v in volumes if v.device_identifier == "disk2s0")
    assert cd_volume.filesystem_type is None
    assert cd_volume.media_type == MediaType.CD_DATA


def _volume(size: int) -> Volume:
    return Volume(
        device_identifier="disk2s0",
        volume_name="SAMPLE",
        mount_point="/Volumes/SAMPLE",
        size=size,
        content=None,
    )


def test_detect_media_type_cddafs_is_audio_cd() -> None:
    assert detect_media_type(_volume(700_000_000), "cddafs") == MediaType.CD_AUDIO


def test_detect_media_type_cd9660_small_is_cd_data() -> None:
    assert detect_media_type(_volume(700_000_000), "cd9660") == MediaType.CD_DATA


def test_detect_media_type_cd9660_large_is_dvd() -> None:
    # DVDもISO9660（cd9660）でマウントされることがあるため、サイズで判別する
    assert detect_media_type(_volume(4_500_000_000), "cd9660") == MediaType.DVD


def test_detect_media_type_udf_small_is_dvd() -> None:
    assert detect_media_type(_volume(4_500_000_000), "udf") == MediaType.DVD


def test_detect_media_type_udf_large_is_bd() -> None:
    assert detect_media_type(_volume(25_000_000_000), "udf") == MediaType.BD


def test_detect_media_type_bdxl_capacity_is_bd() -> None:
    # BDXL(最大128GB)やM-DISCのBD-Rも、OS上は通常のBDと同じUDFでマウントされる
    assert detect_media_type(_volume(100_000_000_000), "udf") == MediaType.BD


def test_detect_media_type_unknown_filesystem_falls_back_to_size() -> None:
    assert detect_media_type(_volume(700_000_000), None) == MediaType.CD_DATA
    assert detect_media_type(_volume(4_500_000_000), None) == MediaType.DVD
    assert detect_media_type(_volume(25_000_000_000), None) == MediaType.BD


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
