"""MusicBrainz Web ServiceへのDisc ID問い合わせ（メタデータのオンライン検索）。

標準ライブラリの ``urllib.request`` のみを使用し、追加の依存パッケージは
必要としない。ネットワーク通信は必ずユーザーの明示的な操作（GUI上の
「オンラインで検索」ボタン）をトリガーとし、自動・暗黙のうちに実行しては
ならない（送信されるのはディスクの識別情報のみで、個人情報は含まれない
が、通信が発生すること自体はユーザーに明示する）。

MusicBrainz API の利用条件（https://musicbrainz.org/doc/MusicBrainz_API）:

- 意味のある ``User-Agent`` を送ること。
- 1秒あたり1リクエストを超えないこと（超過するとIPアドレスが
  ブロックされる可能性がある）。
- Disc IDによる検索に認証は不要。

このモジュールはUIフレームワークに依存しない設計とし、ネットワークI/O部分
（``url_opener``）を差し替え可能にすることで、実ネットワークを使わずに
単体テストできるようにしている。
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Callable

from mkhybrid_gui.metadata import AlbumMetadata, TrackMetadata

_API_BASE = "https://musicbrainz.org/ws/2/discid"
_USER_AGENT = "mkhybrid-gui/0.1.0 (https://github.com/ac1965/mkhybrid-gui)"

#: MusicBrainzの利用規約が定める最低リクエスト間隔（秒）。
_MIN_REQUEST_INTERVAL_SECONDS = 1.0

#: ``urllib.request.urlopen(request, timeout=...)`` と同じ形の呼び出し可能
#: オブジェクト。戻り値は ``with ... as response: response.read()`` の
#: ように使えるコンテキストマネージャ（``http.client.HTTPResponse`` 互換）
#: であればよく、テストでは差し替えたフェイクを渡す。
UrlOpener = Callable[[urllib.request.Request, float], Any]


@dataclass(frozen=True)
class ReleaseCandidate:
    """MusicBrainzが返した候補リリース1件分。"""

    album: AlbumMetadata
    country: str | None = None
    disambiguation: str = ""

    @property
    def display_label(self) -> str:
        """候補選択UIに表示する1行の説明文。"""
        parts = [f"{self.album.artist} - {self.album.album}"]

        details = []
        if self.album.year:
            details.append(self.album.year)
        if self.country:
            details.append(self.country)
        if self.disambiguation:
            details.append(self.disambiguation)

        if details:
            parts.append(f"({', '.join(details)})")

        return " ".join(parts)


@dataclass(frozen=True)
class LookupResult:
    """MusicBrainz問い合わせの結果。"""

    candidates: list[ReleaseCandidate] = field(default_factory=list)
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None


_last_request_time: float = 0.0


def _wait_for_rate_limit() -> None:
    """MusicBrainzの「1秒1リクエスト」制限を守るため、必要なら待機する。"""
    global _last_request_time

    elapsed = time.monotonic() - _last_request_time
    remaining = _MIN_REQUEST_INTERVAL_SECONDS - elapsed

    if remaining > 0:
        time.sleep(remaining)

    _last_request_time = time.monotonic()


def _parse_artist_credit(artist_credit: list[dict]) -> str:
    """MusicBrainzの ``artist-credit`` 配列を1つの表示用文字列に組み立てる。"""
    parts: list[str] = []

    for credit in artist_credit:
        name = credit.get("name", "")
        parts.append(name)
        parts.append(credit.get("joinphrase", ""))

    return "".join(parts)


def _release_to_candidate(release: dict) -> ReleaseCandidate | None:
    media_list = release.get("media")

    if not isinstance(media_list, list) or not media_list:
        return None

    # 複数メディア（マルチディスクセット）の場合、ディスクIDが一致した
    # ディスクを収録しているメディアが1つ選ばれてAPIから返される想定だが、
    # 念のため先頭を採用する。
    media = media_list[0]
    tracks_data = media.get("tracks")

    if not isinstance(tracks_data, list):
        return None

    tracks = [
        TrackMetadata(title=track.get("title", ""))
        for track in tracks_data
    ]

    artist_credit = release.get("artist-credit")
    artist = (
        _parse_artist_credit(artist_credit)
        if isinstance(artist_credit, list)
        else ""
    )

    date = release.get("date")
    year = date.split("-")[0] if isinstance(date, str) and date else None

    album = AlbumMetadata(
        album=release.get("title", ""),
        artist=artist,
        year=year,
        tracks=tracks,
    )

    return ReleaseCandidate(
        album=album,
        country=release.get("country"),
        disambiguation=release.get("disambiguation", ""),
    )


def lookup_releases(
    disc_id: str,
    *,
    timeout: float = 10.0,
    url_opener: UrlOpener | None = None,
) -> LookupResult:
    """MusicBrainzにDisc IDを問い合わせ、候補リリースの一覧を返す。

    ネットワークエラー・タイムアウト・0件ヒット・JSON解析失敗のいずれも
    例外を送出せず、``LookupResult`` の ``error``/``candidates`` として返す
    （呼び出し側の処理を止めないため）。
    """
    opener = url_opener or (
        lambda request, timeout: urllib.request.urlopen(
            request, timeout=timeout
        )
    )

    url = (
        f"{_API_BASE}/{urllib.parse.quote(disc_id, safe='')}"
        "?fmt=json&inc=recordings+artist-credits"
    )
    request = urllib.request.Request(
        url,
        headers={"User-Agent": _USER_AGENT},
    )

    _wait_for_rate_limit()

    try:
        with opener(request, timeout) as response:
            raw = response.read()
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return LookupResult(candidates=[])

        return LookupResult(
            error=f"MusicBrainzへの問い合わせに失敗しました（HTTP {exc.code}）。"
        )
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return LookupResult(
            error=f"MusicBrainzへ接続できませんでした: {exc}"
        )

    try:
        data = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        return LookupResult(
            error=f"MusicBrainzの応答を解析できませんでした: {exc}"
        )

    if not isinstance(data, dict):
        return LookupResult(error="MusicBrainzの応答が想定外の形式でした。")

    releases = data.get("releases")

    if not isinstance(releases, list):
        return LookupResult(candidates=[])

    candidates = [
        candidate
        for release in releases
        if isinstance(release, dict)
        and (candidate := _release_to_candidate(release)) is not None
    ]

    return LookupResult(candidates=candidates)


try:
    from PySide6.QtCore import QThread, Signal
except ImportError:  # pragma: no cover - PySide6未インストール時
    QThread = None  # type: ignore[assignment,misc]


if QThread is not None:

    class MetadataLookupWorker(QThread):  # type: ignore[misc]
        """MusicBrainzへの問い合わせをバックグラウンドスレッドで実行するワーカー。

        ネットワークI/O（数秒かかりうる）でGUIをブロックしないようにする。
        """

        finished_lookup = Signal(object)  # LookupResult

        def __init__(self, disc_id: str, parent=None) -> None:
            super().__init__(parent)
            self._disc_id = disc_id

        def run(self) -> None:  # noqa: D102 - QThreadのオーバーライド
            result = lookup_releases(self._disc_id)
            self.finished_lookup.emit(result)
