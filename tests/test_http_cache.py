"""The GitHub cache has to actually hit, and the socket pool has to fit.

Discovery is bounded by GitHub round trips, so the whole design assumes the
one-hour `requests_cache` in front of it. It was not caching a single
response: GitHub answers with `Vary: Accept, Authorization, ...`, `Authorization`
is in requests-cache's default `ignored_parameters` (so it is redacted from the
stored request rather than written to disk), and requests-cache treats a `Vary`
header that overlaps `ignored_parameters` as an automatic miss. Every request
carries a token, so every lookup missed and every listing re-read every file.

These tests pin the two properties that make the cache worth having: a repeated
request is served from it, and the token still never reaches the cache file.
"""

import io
import json

import requests
from requests.adapters import HTTPAdapter
from urllib3 import HTTPResponse

from apps.dishacled.storage.dishacled_httpstore import (
    FETCH_WORKERS,
    DishacledHttpStorageManager,
)


# the two headers that broke it, verbatim from api.github.com
GITHUB_HEADERS = {
    "Content-Type": "application/json",
    "Cache-Control": "no-cache",
    "Vary": (
        "Accept, Authorization, Cookie, X-GitHub-OTP,Accept-Encoding, Accept, "
        "X-Requested-With"
    ),
}


def _github_response(adapter, request, status=200):
    """What api.github.com answers with, headers included.

    Built through the real `HTTPAdapter.build_response` so that the cache sees
    the urllib3 raw response it stores from, rather than a stub of one.
    """
    body = json.dumps({"items": [], "total_count": 0}).encode()
    raw = HTTPResponse(
        body=io.BytesIO(body),
        headers=GITHUB_HEADERS,
        status=status,
        preload_content=False,
        request_url=request.url,
    )
    return adapter.build_response(request, raw)


class _CountingAdapter(HTTPAdapter):
    """Stands in for the network, below the cache and below the hooks."""

    def __init__(self):
        super().__init__()
        self.sent = []
        self.status = 200

    def send(self, request, **kwargs):
        self.sent.append(request.url)
        return _github_response(self, request, self.status)


def _store_with_memory_cache():
    """The real store, its cache in memory so the test writes no files."""
    store = DishacledHttpStorageManager()
    store.session = store._build_session("test", backend="memory")
    adapter = _CountingAdapter()
    store.session.mount("https://", adapter)
    store.session.mount("http://", adapter)
    return store, adapter


class TestAuthorizedResponsesAreCached:
    def test_a_repeated_request_is_served_from_the_cache(self):
        store, adapter = _store_with_memory_cache()
        url = f"{store.github_api_url}/search/repositories"

        first = store.session.get(url, headers=store._get_headers())
        second = store.session.get(url, headers=store._get_headers())

        assert first.from_cache is False
        assert second.from_cache is True
        assert len(adapter.sent) == 1

    def test_the_token_is_not_written_to_the_cache(self):
        store, adapter = _store_with_memory_cache()
        url = f"{store.github_api_url}/search/repositories"

        store.session.get(url, headers=store._get_headers())

        cached = list(store.session.cache.responses.values())
        assert cached
        assert all(
            "Bearer" not in str(entry.request.headers) for entry in cached
        )


class TestNotFoundIsAlsoAnAnswer:
    def test_a_missing_manifest_is_only_asked_about_once(self):
        """Most repositories lack one of the two manifests, so this is the
        common case, and requests-cache caches only 200s by default."""
        store, adapter = _store_with_memory_cache()
        url = f"{store.github_api_url}/repos/o/r/contents/pyproject.toml"
        adapter.status = 404

        first = store.session.get(url, headers=store._get_headers())
        second = store.session.get(url, headers=store._get_headers())

        assert first.status_code == 404
        assert second.status_code == 404
        assert second.from_cache is True
        assert len(adapter.sent) == 1


class TestTheSocketPoolFitsTheWorkers:
    def test_the_pool_is_at_least_as_big_as_the_fetch_fan_out(self):
        """Otherwise every worker past the tenth re-does the TLS handshake.

        `requests` pools ten connections per host by default; discovery fans out
        to `COMPONENT_FETCH_WORKERS`. The surplus connections are opened,
        used once and discarded -- a handshake per file, which is most of what
        a cold listing spends.
        """
        store = DishacledHttpStorageManager()
        adapter = store.session.get_adapter("https://api.github.com")
        assert adapter._pool_maxsize >= FETCH_WORKERS
