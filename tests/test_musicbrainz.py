"""``musicbrainz`` モジュール（MusicBrainzへのDisc ID問い合わせ）のテスト。

実ネットワークは使用せず、``url_opener`` を差し替えたフェイクレスポンスで
検証する。正常系のJSON構造は、実際にMusicBrainz APIへ問い合わせて確認した
レスポンス（disc id ``nN2g3a0ZSjovyIgK3bJl6_.j8C4-``）の構造を元にしている。
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request

import pytest

from mkhybrid_gui.musicbrainz import lookup_releases

# 実際のMusicBrainz APIレスポンス（disc id nN2g3a0ZSjovyIgK3bJl6_.j8C4-、
# 2026年に実機で確認）の構造を元にした、テストに必要な項目だけのフィクスチャ。
REAL_SINGLE_RELEASE_RESPONSE = {
    "id": "nN2g3a0ZSjovyIgK3bJl6_.j8C4-",
    "offsets": [150],
    "sectors": 73241,
    "releases": [
        {
            "title": "æ³o & h³æ",
            "date": "2003-12-04",
            "country": "GB",
            "disambiguation": "",
            "artist-credit": [
                {"name": "Autechre", "joinphrase": " & "},
                {"name": "The Hafler Trio", "joinphrase": ""},
            ],
            "media": [
                {
                    "title": "æ³o",
                    "format": "CD",
                    "tracks": [
                        {"number": "1", "title": "æ³o", "length": 974546},
                    ],
                }
            ],
        }
    ],
}


class _FakeResponse:
    def __init__(self, body: bytes):
        self._body = body

    def read(self) -> bytes:
        return self._body

    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, *exc: object) -> None:
        return None


def _fake_opener_returning(data: dict):
    def opener(request: urllib.request.Request, timeout: float):
        return _FakeResponse(json.dumps(data).encode("utf-8"))

    return opener


@pytest.fixture(autouse=True)
def _reset_rate_limit_clock(monkeypatch: pytest.MonkeyPatch):
    """レート制限の「前回呼び出し時刻」をテスト間でリセットし、
    実際に ``time.sleep`` が呼ばれて遅くならないようにする。
    """
    import mkhybrid_gui.musicbrainz as musicbrainz

    monkeypatch.setattr(musicbrainz, "_last_request_time", 0.0)
    monkeypatch.setattr(
        musicbrainz.time, "monotonic", lambda: 10_000.0
    )
    sleep_calls: list[float] = []
    monkeypatch.setattr(
        musicbrainz.time, "sleep", lambda seconds: sleep_calls.append(seconds)
    )
    yield sleep_calls


def test_lookup_releases_parses_real_response_shape() -> None:
    result = lookup_releases(
        "nN2g3a0ZSjovyIgK3bJl6_.j8C4-",
        url_opener=_fake_opener_returning(REAL_SINGLE_RELEASE_RESPONSE),
    )

    assert result.ok is True
    assert len(result.candidates) == 1

    candidate = result.candidates[0]
    assert candidate.album.album == "æ³o & h³æ"
    assert candidate.album.artist == "Autechre & The Hafler Trio"
    assert candidate.album.year == "2003"
    assert candidate.country == "GB"
    assert [t.title for t in candidate.album.tracks] == ["æ³o"]
    assert "Autechre & The Hafler Trio" in candidate.display_label


def test_lookup_releases_handles_multiple_candidates() -> None:
    data = {
        "releases": [
            REAL_SINGLE_RELEASE_RESPONSE["releases"][0],
            {
                "title": "Another Pressing",
                "date": "2010-01-01",
                "country": "US",
                "disambiguation": "reissue",
                "artist-credit": [{"name": "Someone", "joinphrase": ""}],
                "media": [
                    {
                        "tracks": [
                            {"title": "Track A"},
                        ]
                    }
                ],
            },
        ]
    }

    result = lookup_releases(
        "somediscid",
        url_opener=_fake_opener_returning(data),
    )

    assert result.ok is True
    assert len(result.candidates) == 2
    assert result.candidates[1].album.album == "Another Pressing"
    assert result.candidates[1].disambiguation == "reissue"


def test_lookup_releases_returns_empty_on_404_not_found() -> None:
    def opener(request, timeout):
        raise urllib.error.HTTPError(
            request.full_url, 404, "Not Found", {}, None
        )

    result = lookup_releases("unknown-disc-id", url_opener=opener)

    assert result.ok is True
    assert result.candidates == []


def test_lookup_releases_reports_error_on_http_500() -> None:
    def opener(request, timeout):
        raise urllib.error.HTTPError(
            request.full_url, 500, "Internal Server Error", {}, None
        )

    result = lookup_releases("some-disc-id", url_opener=opener)

    assert result.ok is False
    assert "500" in result.error


def test_lookup_releases_reports_error_on_network_failure() -> None:
    def opener(request, timeout):
        raise urllib.error.URLError("connection refused")

    result = lookup_releases("some-disc-id", url_opener=opener)

    assert result.ok is False
    assert result.candidates == []


def test_lookup_releases_reports_error_on_timeout() -> None:
    def opener(request, timeout):
        raise TimeoutError("timed out")

    result = lookup_releases("some-disc-id", url_opener=opener)

    assert result.ok is False


def test_lookup_releases_reports_error_on_malformed_json() -> None:
    def opener(request, timeout):
        return _FakeResponse(b"not valid json{{{")

    result = lookup_releases("some-disc-id", url_opener=opener)

    assert result.ok is False


def test_lookup_releases_handles_response_without_releases_key() -> None:
    result = lookup_releases(
        "some-disc-id",
        url_opener=_fake_opener_returning({"id": "some-disc-id"}),
    )

    assert result.ok is True
    assert result.candidates == []


def test_lookup_releases_sends_meaningful_user_agent() -> None:
    captured_requests: list[urllib.request.Request] = []

    def opener(request, timeout):
        captured_requests.append(request)
        return _FakeResponse(json.dumps({"releases": []}).encode("utf-8"))

    lookup_releases("some-disc-id", url_opener=opener)

    assert len(captured_requests) == 1
    user_agent = captured_requests[0].get_header("User-agent")
    assert user_agent
    assert "mkhybrid-gui" in user_agent


def test_lookup_releases_url_encodes_disc_id() -> None:
    captured_requests: list[urllib.request.Request] = []

    def opener(request, timeout):
        captured_requests.append(request)
        return _FakeResponse(json.dumps({"releases": []}).encode("utf-8"))

    # Disc IDには "." や "_" や "-" が含まれうるが、稀にURLエンコードが
    # 必要な文字が来ても壊れないことを確認する。
    lookup_releases("abc/def", url_opener=opener)

    assert "abc%2Fdef" in captured_requests[0].full_url


def test_lookup_releases_respects_rate_limit(
    monkeypatch: pytest.MonkeyPatch,
    _reset_rate_limit_clock,
) -> None:
    import mkhybrid_gui.musicbrainz as musicbrainz

    # 直前の呼び出しからまだ0.3秒しか経っていない状態を再現する
    # （残り待機時間は 1.0 - 0.3 = 0.7秒になるはず）。
    monkeypatch.setattr(musicbrainz, "_last_request_time", 9_999.7)

    lookup_releases(
        "some-disc-id",
        url_opener=_fake_opener_returning({"releases": []}),
    )

    assert _reset_rate_limit_clock  # sleepが呼ばれたことを確認
    assert _reset_rate_limit_clock[0] == pytest.approx(0.7, abs=0.01)
