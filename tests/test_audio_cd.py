"""``audio_cd`` のトラック検出・書き出しロジックのテスト。

実際の音楽CDは使用せず、CDDAFSマウントを模したディレクトリ構造で検証する。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from mkhybrid_gui.audio_cd import AudioCdError, extract_audio_tracks, list_audio_tracks


def test_list_audio_tracks_returns_sorted_aiff(tmp_path: Path) -> None:
    (tmp_path / "2 Audio Track.aiff").write_bytes(b"track2")
    (tmp_path / "1 Audio Track.aiff").write_bytes(b"track1")
    (tmp_path / ".TOC.plist").write_bytes(b"not a track")

    tracks = list_audio_tracks(tmp_path)

    assert [t.name for t in tracks] == ["1 Audio Track.aiff", "2 Audio Track.aiff"]


def test_list_audio_tracks_missing_mount_point_raises() -> None:
    with pytest.raises(AudioCdError):
        list_audio_tracks("/no/such/mount/point")


def test_list_audio_tracks_no_tracks_raises(tmp_path: Path) -> None:
    with pytest.raises(AudioCdError):
        list_audio_tracks(tmp_path)


def test_extract_audio_tracks_copies_all_files_and_reports_progress(tmp_path: Path) -> None:
    source = tmp_path / "Audio CD"
    source.mkdir()
    (source / "1 Audio Track.aiff").write_bytes(b"track1-data")
    (source / "2 Audio Track.aiff").write_bytes(b"track2-data")

    dest = tmp_path / "out"
    progress_lines: list[str] = []

    result = extract_audio_tracks(source, dest, on_progress=progress_lines.append)

    assert result.ok is True
    assert len(result.copied) == 2
    assert (dest / "1 Audio Track.aiff").read_bytes() == b"track1-data"
    assert (dest / "2 Audio Track.aiff").read_bytes() == b"track2-data"
    assert len(progress_lines) == 2


def test_extract_audio_tracks_creates_destination_dir(tmp_path: Path) -> None:
    source = tmp_path / "Audio CD"
    source.mkdir()
    (source / "1 Audio Track.aiff").write_bytes(b"data")

    dest = tmp_path / "nested" / "out"
    result = extract_audio_tracks(source, dest)

    assert result.ok is True
    assert dest.is_dir()
