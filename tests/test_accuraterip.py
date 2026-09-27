"""``accuraterip`` モジュール（ID計算・URL組み立て・ネットワーク問い合わせ・
バイナリレスポンスのパース）のテスト。

実ネットワークは使用せず、``url_opener`` を差し替えたフェイクレスポンスで
検証する（``musicbrainz.py``のテストと同じパターン）。

``compute_ids``/``build_query_url``のテストベクタは、実際にAccurateRip
サーバーへ問い合わせて確認した実データではなく、実装済みの計算式
（モジュールのdocstring・コメントに記載された仕様）に基づいて手計算で
導出した自作の小さなTOCである（spec-conformanceテスト）。実際のAccurateRip
サーバーとの整合性そのものは、別の実CDで確認する必要がある
（過去セッションで一度確認されたと記録されているが、その際の生TOCデータは
保存されておらず、本テストでは再現できない）。
"""

from __future__ import annotations

import struct
import urllib.error
import urllib.request

import pytest

from mkhybrid_gui.accuraterip import (
    AccurateRipIds,
    build_query_url,
    compute_ids,
    lookup,
)
from mkhybrid_gui.metadata import compute_disc_id

# 手計算で導出したテストベクタ（3トラック）。
# id1 = (sum(track_offsets) + leadout_offset) & 0xFFFFFFFF
#     = (0 + 15000 + 30000 + 45000) = 90000 = 0x15f90
# id2 = sum(track_number * max(offset, 1)) + (track_count + 1) * leadout_offset
#     = (1*1 + 2*15000 + 3*30000) + 4*45000 = 120001 + 180000 = 300001 = 0x493e1
# freedb: track_start_seconds = [(o+150)//75 for o in offsets] = [2, 202, 402]
#         leadout_seconds = (45000+150)//75 = 602
#         checksum = (digit_sum(2)+digit_sum(202)+digit_sum(402)) % 255 = 12
#         total_seconds = 602 - 2 = 600
#         freedb_id = (12<<24)|(600<<8)|3 = 0xc025803
_SAMPLE_TRACK_OFFSETS = [0, 15000, 30000]
_SAMPLE_LEADOUT_OFFSET = 45000
_SAMPLE_IDS = AccurateRipIds(
    id1=0x15F90, id2=0x493E1, freedb_id=0xC025803
)


def test_compute_ids_matches_hand_derived_vector() -> None:
    ids = compute_ids(_SAMPLE_TRACK_OFFSETS, _SAMPLE_LEADOUT_OFFSET)

    assert ids == _SAMPLE_IDS


def test_compute_ids_does_not_add_lead_in_frames() -> None:
    """id1/id2はMusicBrainz Disc ID（``metadata.compute_disc_id``）とは異なり、
    ``LEAD_IN_FRAMES``(150)を加算しない生セクタ値をそのまま使う。

    この規約の取り違えは、1文字もエラーにならず「見つかりません」という
    結果になるだけの、検出しにくい回帰になる（AGENTS.mdの警告を参照）。
    ここでは、手計算した期待値（``LEAD_IN_FRAMES``を加算しない前提で
    導出した値）と実装の出力が一致することを検証することで、将来
    ``compute_ids``内に誤って``+ LEAD_IN_FRAMES``が追加されるような
    変更を検出する。
    """
    ids = compute_ids(_SAMPLE_TRACK_OFFSETS, _SAMPLE_LEADOUT_OFFSET)

    # 生セクタ値をそのまま使った場合の期待値と一致するはず。
    assert ids.id1 == (
        sum(_SAMPLE_TRACK_OFFSETS) + _SAMPLE_LEADOUT_OFFSET
    ) & 0xFFFFFFFF

    # 加算してしまった場合の（誤った）値とは一致しないはず。
    wrong_offsets = [offset + 150 for offset in _SAMPLE_TRACK_OFFSETS]
    wrong_leadout = _SAMPLE_LEADOUT_OFFSET + 150
    wrong_id1 = (sum(wrong_offsets) + wrong_leadout) & 0xFFFFFFFF

    assert ids.id1 != wrong_id1


def test_compute_ids_uses_different_convention_than_musicbrainz_disc_id() -> (
    None
):
    """同じTOCから計算しても、AccurateRip IDとMusicBrainz Disc IDは
    全く異なる値・形式になる（オフセット規約が異なるため）。両者を
    混同していないことの簡易な確認。
    """
    ids = compute_ids(_SAMPLE_TRACK_OFFSETS, _SAMPLE_LEADOUT_OFFSET)

    mb_disc_id = compute_disc_id(
        first_track=1,
        last_track=len(_SAMPLE_TRACK_OFFSETS),
        leadout_offset=_SAMPLE_LEADOUT_OFFSET + 150,
        track_offsets=[o + 150 for o in _SAMPLE_TRACK_OFFSETS],
    )

    assert isinstance(mb_disc_id, str)
    assert str(ids.id1) not in mb_disc_id


def test_build_query_url_directory_structure() -> None:
    url = build_query_url(len(_SAMPLE_TRACK_OFFSETS), _SAMPLE_IDS)

    filename = "dBAR-003-00015f90-000493e1-0c025803.bin"
    assert url == (
        "http://www.accuraterip.com/accuraterip/"
        f"{filename[16]}/{filename[15]}/{filename[14]}/{filename}"
    )
    # ディレクトリ階層はファイル名の後ろから3〜1文字目
    # （末尾の".bin"を除いた位置ではないことに注意）。
    assert url.endswith(filename)


_HEADER_STRUCT = struct.Struct("<BIII")
_ENTRY_STRUCT = struct.Struct("<BII")


def _build_block(
    track_count: int,
    ids: AccurateRipIds,
    entries: list[tuple[int, int, int]],
) -> bytes:
    """1件の投稿ぶんのバイナリブロックを組み立てる（テスト用フィクスチャ）。"""
    header = _HEADER_STRUCT.pack(track_count, ids.id1, ids.id2, ids.freedb_id)
    body = b"".join(
        _ENTRY_STRUCT.pack(confidence, crc_v1, crc_v2)
        for confidence, crc_v1, crc_v2 in entries
    )
    return header + body


class _FakeResponse:
    def __init__(self, body: bytes):
        self._body = body

    def read(self) -> bytes:
        return self._body

    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, *exc: object) -> None:
        return None


def _fake_opener_returning(data: bytes):
    def opener(request: urllib.request.Request, timeout: float):
        return _FakeResponse(data)

    return opener


def test_lookup_returns_empty_tracks_on_404() -> None:
    def opener(request, timeout):
        raise urllib.error.HTTPError(
            request.full_url, 404, "Not Found", {}, None
        )

    result = lookup(3, _SAMPLE_IDS, url_opener=opener)

    assert result.ok is True
    assert result.tracks == {}


def test_lookup_parses_single_posting() -> None:
    data = _build_block(
        3,
        _SAMPLE_IDS,
        [(2, 0xAAAAAAAA, 0xBBBBBBBB), (1, 0x11111111, 0x22222222), (0, 0, 0)],
    )

    result = lookup(3, _SAMPLE_IDS, url_opener=_fake_opener_returning(data))

    assert result.ok is True
    assert result.tracks[1][0].confidence == 2
    assert result.tracks[1][0].crc_v1 == 0xAAAAAAAA
    assert result.tracks[1][0].crc_v2 == 0xBBBBBBBB
    assert result.tracks[2][0].confidence == 1
    assert result.tracks[3][0].confidence == 0


def test_lookup_parses_multiple_postings_appended() -> None:
    """複数の投稿（同じヘッダーが繰り返される複数ブロック）が、
    トラックごとのリストに正しく追記される。
    """
    block1 = _build_block(
        2, _SAMPLE_IDS, [(3, 0x1, 0x2), (1, 0x3, 0x4)]
    )
    block2 = _build_block(
        2, _SAMPLE_IDS, [(5, 0x5, 0x6), (2, 0x7, 0x8)]
    )

    result = lookup(
        2, _SAMPLE_IDS, url_opener=_fake_opener_returning(block1 + block2)
    )

    assert result.ok is True
    assert len(result.tracks[1]) == 2
    assert [entry.confidence for entry in result.tracks[1]] == [3, 5]
    assert [entry.confidence for entry in result.tracks[2]] == [1, 2]


def test_lookup_ignores_non_matching_header_block() -> None:
    """自分の計算した ``ids`` と一致しないヘッダーのブロックは無視する。"""
    other_ids = AccurateRipIds(id1=0xDEADBEEF, id2=0x1, freedb_id=0x1)
    unrelated_block = _build_block(1, other_ids, [(9, 0x1, 0x1)])
    matching_block = _build_block(1, _SAMPLE_IDS, [(4, 0x2, 0x2)])

    result = lookup(
        1,
        _SAMPLE_IDS,
        url_opener=_fake_opener_returning(unrelated_block + matching_block),
    )

    assert result.ok is True
    assert len(result.tracks[1]) == 1
    assert result.tracks[1][0].confidence == 4


def test_lookup_reports_error_on_http_500() -> None:
    def opener(request, timeout):
        raise urllib.error.HTTPError(
            request.full_url, 500, "Internal Server Error", {}, None
        )

    result = lookup(3, _SAMPLE_IDS, url_opener=opener)

    assert result.ok is False
    assert "500" in result.error


def test_lookup_reports_error_on_network_failure() -> None:
    def opener(request, timeout):
        raise urllib.error.URLError("connection refused")

    result = lookup(3, _SAMPLE_IDS, url_opener=opener)

    assert result.ok is False
    assert result.tracks == {}


def test_lookup_reports_error_on_timeout() -> None:
    def opener(request, timeout):
        raise TimeoutError("timed out")

    result = lookup(3, _SAMPLE_IDS, url_opener=opener)

    assert result.ok is False


def test_lookup_returns_empty_tracks_on_truncated_binary_data() -> None:
    """ヘッダーサイズにも満たない短いデータを受け取っても例外を投げず、
    空の結果として扱う（``_parse_response``の境界チェックにより、
    ``struct.error``が実際に送出されることは無い。``lookup()``の
    ``except struct.error``は防御的な分岐であり、現状は到達しない）。
    """
    result = lookup(
        3,
        _SAMPLE_IDS,
        url_opener=_fake_opener_returning(b"\x01\x02\x03"),
    )

    assert result.ok is True
    assert result.tracks == {}
