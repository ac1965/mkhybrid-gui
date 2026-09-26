"""``metadata`` モジュール（Disc ID計算・メタデータ構造）のテスト。"""

from __future__ import annotations

from mkhybrid_gui.metadata import (
    AlbumMetadata,
    TrackMetadata,
    compute_disc_id,
    sanitize_filename_component,
)


def test_compute_disc_id_matches_real_musicbrainz_example() -> None:
    """実際のMusicBrainz APIレスポンスと一致することを確認する。

    disc id ``nN2g3a0ZSjovyIgK3bJl6_.j8C4-`` は、
    ``https://musicbrainz.org/ws/2/discid/nN2g3a0ZSjovyIgK3bJl6_.j8C4-``
    への実際の問い合わせで確認済み（1トラックのみのCD、
    ``offsets=[150]``, ``sectors=73241``）。
    """
    disc_id = compute_disc_id(
        first_track=1,
        last_track=1,
        leadout_offset=73241,
        track_offsets=[150],
    )

    assert disc_id == "nN2g3a0ZSjovyIgK3bJl6_.j8C4-"


def test_compute_disc_id_is_28_characters() -> None:
    disc_id = compute_disc_id(1, 3, 65960 + 150, [150, 17462 + 150, 46115 + 150])
    assert len(disc_id) == 28


def test_compute_disc_id_never_uses_plus_slash_equals() -> None:
    """Base64標準の記号(+/=)が、MusicBrainz独自の(._-)に必ず置換されている。"""
    for leadout in range(0, 500, 7):
        disc_id = compute_disc_id(1, 1, leadout, [150])
        assert "+" not in disc_id
        assert "/" not in disc_id
        assert "=" not in disc_id


def test_compute_disc_id_differs_for_different_toc() -> None:
    a = compute_disc_id(1, 1, 73241, [150])
    b = compute_disc_id(1, 2, 73241, [150, 20000])
    assert a != b


def test_compute_disc_id_pads_missing_track_offsets_with_zero() -> None:
    """99トラック分に満たないオフセットは0埋めされる（仕様どおり）。

    2トラック分の情報だけを渡した場合と、明示的に97個の0を追加した場合とで
    同じDisc IDになることを確認する。
    """
    a = compute_disc_id(1, 2, 1000, [150, 500])
    b = compute_disc_id(1, 2, 1000, [150, 500] + [0] * 97)
    assert a == b


def test_album_metadata_track_title_returns_empty_for_out_of_range() -> None:
    album = AlbumMetadata(
        album="Test Album",
        artist="Test Artist",
        tracks=[TrackMetadata(title="One"), TrackMetadata(title="Two")],
    )

    assert album.track_title(1) == "One"
    assert album.track_title(2) == "Two"
    assert album.track_title(0) == ""
    assert album.track_title(3) == ""


def test_sanitize_filename_component_replaces_unsafe_characters() -> None:
    assert sanitize_filename_component("A/B:C*D?E") == "A_B_C_D_E"


def test_sanitize_filename_component_strips_trailing_dots_and_spaces() -> None:
    assert sanitize_filename_component("  Title.  ") == "Title"


def test_sanitize_filename_component_keeps_japanese_text() -> None:
    assert sanitize_filename_component("トラック1") == "トラック1"
