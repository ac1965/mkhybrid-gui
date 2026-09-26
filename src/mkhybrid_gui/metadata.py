"""音楽CDのメタデータ（アルバム名・アーティスト名・トラック名等）を表す
データ構造と、MusicBrainzの「Disc ID」計算ロジック。

MusicBrainzのDisc IDは、CDのTOC（トラック数・各トラックの開始位置・
リードアウト位置）から一意に決まるハッシュ値で、これを使って
MusicBrainzのデータベースに問い合わせる（実際の問い合わせ自体は
``musicbrainz.py`` が担う）。

このモジュールはUIフレームワーク・ネットワークI/Oのいずれにも依存しない
（単体でテスト可能な設計とする）。Disc ID計算アルゴリズムは
https://musicbrainz.org/doc/Disc_ID_Calculation の仕様どおりに実装しており、
実際のMusicBrainz APIレスポンス（disc id ``nN2g3a0ZSjovyIgK3bJl6_.j8C4-``、
``sectors=73241``、``offsets=[150]``）と完全に一致することを確認済み。
"""

from __future__ import annotations

import base64
import hashlib
import re
from dataclasses import dataclass, field

#: MusicBrainz/CDDBのDisc ID計算式が想定する最大トラック数。
#: これを超えるトラックのオフセットはハッシュ計算に含めない
#: （オフセット欄は常にこの数だけ0埋めされた固定長になる）。
_MAX_TRACKS_IN_HASH = 99

#: リードイン（曲間の無音区間ではなく、ディスク先頭の管理領域）の長さ。
#: LBA（0始まり）にこのフレーム数を足した値が、Disc ID計算式が使う
#: 「オフセット」になる。1フレーム=1/75秒。
LEAD_IN_FRAMES = 150


@dataclass(frozen=True)
class TrackMetadata:
    """1トラック分のメタデータ。"""

    title: str = ""


@dataclass(frozen=True)
class AlbumMetadata:
    """アルバム全体のメタデータ。"""

    album: str = ""
    artist: str = ""
    year: str | None = None
    tracks: list[TrackMetadata] = field(default_factory=list)

    def track_title(self, track_number: int) -> str:
        """指定トラック（1始まり）のタイトルを返す。範囲外や未入力なら空文字。"""
        index = track_number - 1

        if index < 0 or index >= len(self.tracks):
            return ""

        return self.tracks[index].title


def compute_disc_id(
    first_track: int,
    last_track: int,
    leadout_offset: int,
    track_offsets: list[int],
) -> str:
    """MusicBrainz/CDDB形式のDisc IDを計算する。

    Parameters
    ----------
    first_track, last_track:
        最初/最後のトラック番号（通常は1, トラック総数）。
    leadout_offset:
        リードアウトのオフセット（フレーム、``LEAD_IN_FRAMES`` 加算済みの値）。
    track_offsets:
        各トラック開始位置のオフセット（フレーム、``LEAD_IN_FRAMES`` 加算済み、
        トラック1から順）。

    仕様: https://musicbrainz.org/doc/Disc_ID_Calculation
    """
    parts = [
        f"{first_track:02X}",
        f"{last_track:02X}",
        f"{leadout_offset:08X}",
    ]

    padded_offsets = list(track_offsets) + [0] * (
        _MAX_TRACKS_IN_HASH - len(track_offsets)
    )

    for offset in padded_offsets[:_MAX_TRACKS_IN_HASH]:
        parts.append(f"{offset:08X}")

    hex_string = "".join(parts)
    digest = hashlib.sha1(hex_string.encode("ascii")).digest()

    encoded = base64.b64encode(digest).decode("ascii")
    return (
        encoded.replace("+", ".")
        .replace("/", "_")
        .replace("=", "-")
    )


_FILENAME_UNSAFE_RE = re.compile(r'[\\/:*?"<>|]')


def sanitize_filename_component(text: str) -> str:
    """ファイル名として安全な文字列に変換する。

    macOS上でも問題を起こしうる文字（``/`` に加え、外付けドライブが
    exFAT/FAT32等の場合を考慮し ``\\:*?"<>|`` も含む）を ``_`` に置換し、
    前後の空白・ピリオドを取り除く。
    """
    sanitized = _FILENAME_UNSAFE_RE.sub("_", text).strip().strip(".")
    return sanitized
