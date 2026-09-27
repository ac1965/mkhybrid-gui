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


# --- CRC v1（位置重み付け合計）とドライブ読み取りオフセット探索 -------------
#
# 実際の音楽CD（20トラック）でトラック2を実際にcd-paranoiaでリッピングし、
# ここで実装したv1（オフセット+6サンプル）が実際にAccurateRipサーバーへ
# 投稿されている値と完全一致することを確認済み。
#
# v2（補充チェックサム）は、per-termでの64bit foldと末尾一括foldの両方を
# 試したが実データと一致せず、正確な式を特定できなかったため実装していない
# （意図的にv1のみ対応。当てずっぽうの式を実装しないこと）。
#
# この照合は、前後にトラックが存在する「中間トラック」でのみ実データ検証
# 済み。ディスクの最初/最後のトラックに適用されるとされる端点トリミング
# （先頭/末尾数千サンプルの除外）は実データで確認していないため、
# 呼び出し側（``audio_cd.rip_and_convert_disc``）は先頭・最終トラックを
# 対象外とすること。


@dataclass(frozen=True)
class AccurateRipMatch:
    """オフセット探索で見つかった一致。"""

    offset: int
    confidence: int
    crc_v1: int


def _build_prefix_sums(samples: list[int]) -> tuple[list[int], list[int]]:
    """``P[n] = sum(samples[0:n])``、``WP[n] = sum(samples[i]*i for i<n)``。

    ``i``はサンプル列内の絶対位置（0始まり）。``_fast_v1``がこれらを
    使って窓ごとのv1をO(1)で計算する。
    """
    n = len(samples)
    prefix_sum = [0] * (n + 1)
    prefix_weighted = [0] * (n + 1)
    running_sum = 0
    running_weighted = 0

    for i, value in enumerate(samples):
        running_sum += value
        running_weighted += value * i
        prefix_sum[i + 1] = running_sum
        prefix_weighted[i + 1] = running_weighted

    return prefix_sum, prefix_weighted


def _fast_v1(
    prefix_sum: list[int],
    prefix_weighted: list[int],
    start: int,
    length: int,
) -> int:
    """``sum(samples[start+k] * (k+1) for k in range(length)) & 0xFFFFFFFF``を
    前置和からO(1)で計算する。

    乗数``(k+1)``は窓の中でのローカル位置（1始まり）であり、``start``
    （オフセット仮説）には依存しない。これは
    ``sum(samples[start+k]*(k+1))``
    ``= sum(samples[start+k]*k) + sum(samples[start+k])``
    ``= [(WP[start+length]-WP[start]) - start*(P[start+length]-P[start])]
        + [P[start+length]-P[start]]``
    という展開により、``P``/``WP``（絶対位置での前置和）だけから
    導出できることを利用している（ランダムデータでの素朴なO(L)実装との
    一致を確認済み）。
    """
    window_sum = prefix_sum[start + length] - prefix_sum[start]
    weighted_by_local_index = (
        prefix_weighted[start + length] - prefix_weighted[start]
    ) - start * window_sum
    return (weighted_by_local_index + window_sum) & 0xFFFFFFFF


def search_offset_v1(
    padded_samples: list[int],
    track_start: int,
    track_length: int,
    candidates: list[AccurateRipTrackEntry],
    search_range: int,
) -> AccurateRipMatch | None:
    """``padded_samples``内の``track_start``位置を基準に、
    ``±search_range``サンプルのオフセットで``crc_v1``が一致する
    ``candidates``（同一トラックへの投稿一覧）を探す。

    ``padded_samples``は、対象トラックの前後に少なくとも
    ``search_range``ぶんの隣接トラックのサンプルを含んでいる必要がある
    （呼び出し側が``audio_cd.read_pcm_samples``で組み立てる）。

    最初に一致したオフセットを返す（複数のcandidatesが同時に一致する
    ことは通常想定しないが、その場合も最初の一致を優先する）。
    範囲内に一致が無ければ``None``を返す。
    """
    prefix_sum, prefix_weighted = _build_prefix_sums(padded_samples)
    targets = {entry.crc_v1: entry for entry in candidates}

    for offset in range(-search_range, search_range + 1):
        start = track_start + offset

        if start < 0 or start + track_length > len(padded_samples):
            continue

        v1 = _fast_v1(prefix_sum, prefix_weighted, start, track_length)

        if v1 in targets:
            entry = targets[v1]
            return AccurateRipMatch(
                offset=offset, confidence=entry.confidence, crc_v1=v1
            )

    return None
