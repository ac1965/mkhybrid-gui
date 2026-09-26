"""[AccurateRip](http://www.accuraterip.com/) への問い合わせ。

AccurateRipは、CDのTOC（トラック数・各トラックの開始位置・リードアウト）
から計算した独自のディスク識別子を使い、他の利用者が同じCDをリッピング
した際のチェックサム（CRC）と照合することで、リッピング結果が正確かどうか
を検証するサービス。本モジュールが計算するID（``id1``/``id2``/freedb ID）は、
2026年に実際にAccurateRipサーバーへ問い合わせて確認したもの
（44トラックの実CDのTOCから ``id1=0x00469648``, ``id2=0x082b1e94``,
``freedb=0x960a2d2c`` を計算し、サーバーが返した2045バイトのレスポンス
ヘッダーと1バイトも違わず一致することを確認済み）。

**重要**: ここで使う ``id1``/``id2`` のオフセットは生セクタ値（リードイン
150フレームの加算をしない）であり、[metadata.py](metadata.py) の
MusicBrainz Disc ID計算（生セクタ+150フレーム）とは異なる規約である。
取り違えると、無音で「見つかりません」という結果になるため注意すること。

ネットワーク通信は標準ライブラリの ``urllib.request`` のみを使用する。
[musicbrainz.py](musicbrainz.py) と同様、ネットワークI/O部分（``url_opener``）
を差し替え可能にし、実ネットワークを使わずにテストできるようにしている。
"""

from __future__ import annotations

import struct
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Callable

from mkhybrid_gui.metadata import LEAD_IN_FRAMES

_SERVER = "www.accuraterip.com"

#: ``urllib.request.urlopen(request, timeout=...)`` と同じ形の呼び出し可能
#: オブジェクト。戻り値は ``with ... as response: response.read()`` の
#: ように使えるコンテキストマネージャであればよい。
UrlOpener = Callable[[urllib.request.Request, float], Any]


@dataclass(frozen=True)
class AccurateRipIds:
    """ディスクを識別する3つの値。"""

    id1: int
    id2: int
    freedb_id: int


def _cddb_digit_sum(value: int) -> int:
    total = 0
    while value > 0:
        total += value % 10
        value //= 10
    return total


def compute_ids(
    track_offsets: list[int], leadout_offset: int
) -> AccurateRipIds:
    """TOC（生セクタ値）からAccurateRipのディスク識別子を計算する。

    ``track_offsets``/``leadout_offset`` は ``audio_cd.DiscToc`` が
    ``cd-paranoia -Q`` の出力から得る、リードイン加算前の生セクタ値
    （トラック1は通常0から始まる）。

    ``id1``/``id2`` はこの生セクタ値をそのまま使う（MusicBrainzの
    Disc ID計算とは異なり、``LEAD_IN_FRAMES`` を加算しない）。
    freedb（classic CDDB）ディスクIDのみ、各トラック開始位置を秒に
    変換する際に ``LEAD_IN_FRAMES`` を加算する慣習に従う。
    """
    track_numbers = list(range(1, len(track_offsets) + 1))

    id1 = (sum(track_offsets) + leadout_offset) & 0xFFFFFFFF
    id2 = (
        sum(
            number * max(offset, 1)
            for number, offset in zip(track_numbers, track_offsets)
        )
        + (len(track_offsets) + 1) * leadout_offset
    ) & 0xFFFFFFFF

    track_start_seconds = [
        (offset + LEAD_IN_FRAMES) // 75 for offset in track_offsets
    ]
    leadout_seconds = (leadout_offset + LEAD_IN_FRAMES) // 75

    checksum = (
        sum(_cddb_digit_sum(seconds) for seconds in track_start_seconds)
        % 255
    )
    total_seconds = leadout_seconds - track_start_seconds[0]
    freedb_id = (
        (checksum << 24) | (total_seconds << 8) | len(track_offsets)
    ) & 0xFFFFFFFF

    return AccurateRipIds(id1=id1, id2=id2, freedb_id=freedb_id)


def _disc_id_filename(track_count: int, ids: AccurateRipIds) -> str:
    return (
        f"dBAR-{track_count:03d}-{ids.id1:08x}-{ids.id2:08x}-"
        f"{ids.freedb_id:08x}.bin"
    )


def build_query_url(track_count: int, ids: AccurateRipIds) -> str:
    """AccurateRipサーバーへの問い合わせURLを組み立てる。

    ファイル名文字列の特定位置（後ろから数えて3〜1文字目）を
    ディレクトリ階層として使う、AccurateRipプロトコル固有の規約。
    実データで検証済み（44トラックのTOCから
    ``accuraterip/8/4/6/dBAR-044-00469648-082b1e94-960a2d2c.bin``）。
    """
    filename = _disc_id_filename(track_count, ids)
    return (
        f"http://{_SERVER}/accuraterip/"
        f"{filename[16]}/{filename[15]}/{filename[14]}/{filename}"
    )


@dataclass(frozen=True)
class AccurateRipTrackEntry:
    """1トラックぶんの投稿1件。"""

    confidence: int
    crc_v1: int
    crc_v2: int


@dataclass(frozen=True)
class AccurateRipLookupResult:
    """AccurateRip問い合わせの結果。"""

    #: トラック番号（1始まり）→そのトラックに対する投稿一覧。
    tracks: dict[int, list[AccurateRipTrackEntry]] = field(
        default_factory=dict
    )
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None


_HEADER_STRUCT = struct.Struct("<BIII")
_ENTRY_STRUCT = struct.Struct("<BII")


def _parse_response(data: bytes, ids: AccurateRipIds) -> dict[
    int, list[AccurateRipTrackEntry]
]:
    """実データで確認済みのバイナリ形式をパースする。

    ヘッダー（トラック数1バイト + id1/id2/freedb各4バイト、リトル
    エンディアン）に続けてトラック数ぶんのエントリ（信頼度1バイト +
    crc/crc2各4バイト）が並ぶブロックが、投稿件数ぶん繰り返される。
    ヘッダーが自分の計算した ``ids`` と一致するブロックのみ採用する。
    """
    tracks: dict[int, list[AccurateRipTrackEntry]] = {}
    offset = 0

    while offset + _HEADER_STRUCT.size <= len(data):
        track_count, id1, id2, freedb_id = _HEADER_STRUCT.unpack_from(
            data, offset
        )
        offset += _HEADER_STRUCT.size

        matches = (
            id1 == ids.id1
            and id2 == ids.id2
            and freedb_id == ids.freedb_id
        )

        for track_number in range(1, track_count + 1):
            if offset + _ENTRY_STRUCT.size > len(data):
                return tracks

            confidence, crc_v1, crc_v2 = _ENTRY_STRUCT.unpack_from(
                data, offset
            )
            offset += _ENTRY_STRUCT.size

            if matches:
                tracks.setdefault(track_number, []).append(
                    AccurateRipTrackEntry(
                        confidence=confidence,
                        crc_v1=crc_v1,
                        crc_v2=crc_v2,
                    )
                )

    return tracks


def lookup(
    track_count: int,
    ids: AccurateRipIds,
    *,
    timeout: float = 10.0,
    url_opener: UrlOpener | None = None,
) -> AccurateRipLookupResult:
    """AccurateRipにディスクを問い合わせ、投稿済みのチェックサムを取得する。

    ネットワークエラー・0件（該当ディスクが未投稿）・不正な応答の
    いずれも例外を送出せず、``AccurateRipLookupResult`` として返す
    （呼び出し側の処理を止めないため）。
    """
    opener = url_opener or (
        lambda request, timeout: urllib.request.urlopen(
            request, timeout=timeout
        )
    )

    url = build_query_url(track_count, ids)
    request = urllib.request.Request(url)

    try:
        with opener(request, timeout) as response:
            raw = response.read()
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return AccurateRipLookupResult(tracks={})

        return AccurateRipLookupResult(
            error=f"AccurateRipへの問い合わせに失敗しました（HTTP {exc.code}）。"
        )
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return AccurateRipLookupResult(
            error=f"AccurateRipへ接続できませんでした: {exc}"
        )

    try:
        tracks = _parse_response(raw, ids)
    except struct.error as exc:
        return AccurateRipLookupResult(
            error=f"AccurateRipの応答を解析できませんでした: {exc}"
        )

    return AccurateRipLookupResult(tracks=tracks)
