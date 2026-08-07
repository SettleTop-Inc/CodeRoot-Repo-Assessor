"""C1 regression: `create_app()` used to build a bare `httpx.Client` and pass
it as `DirectSource`'s `http`, but `content.resolve_head` calls
`http.get_json(...)` — a method `httpx.Client` does not have. Every real
`/v1/acquire` and `/v1/assess` 500'd. Nothing else in this repo constructs
the production path: `test_ports_source.py` monkeypatches `resolve_head`
itself, `test_app_http.py`/`test_mcp_surface.py` build `DirectSource(s, None,
None)` on paths that never reach `acquire()`, and the parity harness's fixed
source raises if `acquire` is even called. This file is the first thing in
the repo that builds the real `HttpClient` adapter and drives
`DirectSource.acquire` through it against a stubbed transport
(`httpx.MockTransport`) — no network, but `get_json`/`get_contents`/`_auth`/
`_pick`/`_note_limit` are the genuine production code, genuinely exercised.

Reverting `assessor/wiring.py` (or `assessor/http_client.py`) back to a bare
`httpx.Client` makes `test_direct_source_acquire_via_the_real_adapter_and_a_
stubbed_transport` fail with `AttributeError: 'Client' object has no
attribute 'get_json'` — the exact failure the review found.

That test still never calls `create_app()`/`create_mcp()` themselves, so it
could not catch a regression at the one line each factory actually adds
(`app.py:150`, `mcp_server.py:81`) — e.g. reverting `create_app()` to build a
`DirectSource` around a bare `httpx.Client` directly, bypassing
`wiring.build_direct_source` entirely. The two tests at the bottom of this
file close that gap: they call the real factories (env-configured via
monkeypatch) and inspect the `Source` each one actually wires.
"""
from __future__ import annotations

import httpx
import pytest

from assessor.assessment.content import ALLOWLIST_VERSION
from assessor.config import Settings
from assessor.http_client import HttpClient
from assessor.ports.source import DirectSource
from assessor.wiring import build_direct_source

_REPO_URL = "https://api.github.com/repos/o/n"
_COMMIT_URL = "https://api.github.com/repos/o/n/commits/main"


class _RefusingFetcher:
    """A `Fetcher` that fails the test if `fetch` is ever called — the
    SHA-reuse short-circuit means it must not be, so this both keeps the
    test network-free and proves the short-circuit actually engaged."""
    def fetch(self, clone_url, repo_id, sha):
        raise AssertionError("git fetch should not run: prior matched, "
                             "acquire() must take the SHA-reuse short-circuit")


def _mock_client(handler, **kwargs) -> HttpClient:
    """A real HttpClient with its internal transport swapped for a
    MockTransport. `_pick`/`_auth`/`_note_limit`/`get_json`/`get_contents`
    are all the genuine, unmodified methods — only the network socket is
    stubbed."""
    c = HttpClient(**kwargs)
    c._client = httpx.Client(transport=httpx.MockTransport(handler), timeout=5)
    return c


def test_direct_source_acquire_via_the_real_adapter_and_a_stubbed_transport():
    """The production path: Settings -> HttpClient -> DirectSource.acquire(),
    exactly what `wiring.build_direct_source` wires for both entrypoints. If
    `http` were a bare httpx.Client (the C1 bug), the first `get_json` call
    inside `resolve_head` would raise AttributeError before this handler is
    ever invoked."""
    seen_paths = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_paths.append(request.url.path)
        if request.url.path == "/repos/o/n":
            return httpx.Response(200, json={"default_branch": "main",
                                              "description": "d", "topics": ["mcp"],
                                              "license": None})
        if request.url.path == "/repos/o/n/commits/main":
            return httpx.Response(200, json={"sha": "deadbeef"})
        raise AssertionError(f"unexpected request: {request.url}")

    s = Settings(assessor_api_token="x")
    http = _mock_client(handler, github_tokens=s.github_token_list, timeout=s.acquire_timeout_s)
    src = DirectSource(s, http, _RefusingFetcher())

    result = src.acquire("https://github.com/o/n",
                         prior={"commit_sha": "deadbeef", "allowlist_version": ALLOWLIST_VERSION})

    assert result["status"] == "unchanged"
    assert result["commit_sha"] == "deadbeef"
    assert result["metadata"]["description"] == "d"
    assert seen_paths == ["/repos/o/n", "/repos/o/n/commits/main"]


def test_direct_source_acquire_maps_a_real_404_to_repo_gone():
    """Same production path, the RepoGone branch: content.RepoGone (raised by
    resolve_head on a real 404) must cross into assessor.errors.RepoGone —
    ports/source.py's re-raise, exercised here through the real adapter
    rather than a test double."""
    from assessor.errors import RepoGone

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"message": "Not Found"})

    s = Settings(assessor_api_token="x")
    http = _mock_client(handler)
    src = DirectSource(s, http, _RefusingFetcher())

    with pytest.raises(RepoGone):
        src.acquire("https://github.com/o/n", prior=None)


def test_get_contents_decodes_a_real_base64_payload_through_the_adapter():
    import base64

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/repos/o/n/contents/README.md"
        assert request.url.params["ref"] == "deadbeef"
        body = base64.b64encode(b"hello world").decode()
        return httpx.Response(200, json={"encoding": "base64", "content": body})

    http = _mock_client(handler)
    status, text = http.get_contents("o", "n", "README.md", "deadbeef")
    assert status == 200
    assert text == "hello world"


def test_get_json_returns_zero_status_on_a_transport_error_not_a_raise():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom", request=request)

    http = _mock_client(handler)
    status, data = http.get_json("https://api.github.com/repos/o/n")
    assert status == 0 and data is None


# --- I1: GITHUB_TOKENS is a pool, not just index [0] ------------------------

def test_pick_round_robins_across_the_whole_pool_not_just_index_zero():
    """Direct test of the ported `_pick`: with three tokens configured,
    successive picks must visit all three, not pin to the first."""
    http = HttpClient(github_tokens=["tok-a", "tok-b", "tok-c"])
    picks = [http._pick() for _ in range(6)]
    assert picks == [0, 1, 2, 0, 1, 2]


def test_wiring_loads_the_whole_configured_pool_not_just_the_first_token():
    """The literal I1 regression: app.py used to attach only
    `github_token_list[0]` as a static header. `wiring.build_direct_source`
    is what both entrypoints now use; assert the adapter it builds actually
    holds every configured token, not a single one."""
    s = Settings(assessor_api_token="x", github_tokens="tok-a,tok-b,tok-c")
    src = build_direct_source(s)
    assert isinstance(src.http, HttpClient)
    assert src.http._tokens == ["tok-a", "tok-b", "tok-c"]


def test_two_consecutive_github_calls_use_different_tokens():
    """End-to-end confirmation that the fix is real at the call site, not
    just in `_pick` isolation: `resolve_head` makes two GitHub calls
    (repo lookup, then commit lookup) — with a multi-token pool, those two
    calls must carry different Authorization headers."""
    seen_auth = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_auth.append(request.headers.get("authorization"))
        if request.url.path == "/repos/o/n":
            return httpx.Response(200, json={"default_branch": "main"})
        return httpx.Response(200, json={"sha": "deadbeef"})

    s = Settings(assessor_api_token="x", github_tokens="tok-a,tok-b,tok-c")
    http = _mock_client(handler, github_tokens=s.github_token_list, timeout=s.acquire_timeout_s)

    # This is exactly what DirectSource.acquire() calls internally to resolve
    # HEAD; called directly here (rather than through DirectSource.acquire())
    # to isolate the token-rotation assertion from the SHA-reuse
    # short-circuit exercised by the tests above.
    from assessor.assessment.content import resolve_head
    resolve_head(http, "o", "n")

    assert len(seen_auth) == 2
    assert seen_auth[0] == "Bearer tok-a"
    assert seen_auth[1] == "Bearer tok-b"
    assert seen_auth[0] != seen_auth[1]


# --- C1, closed at the root: the factories nothing else ever calls ---------
#
# Every test above (and every other test in this repo) drives
# `wiring.build_direct_source` directly or injects a double via `build_app`/
# `build_mcp`. Nothing calls `app.create_app()` or `mcp_server.create_mcp()` —
# the two production entrypoints (`Dockerfile`'s uvicorn CMD, and
# `mcp_server.main()`'s console script) — so a regression at `app.py:150` or
# `mcp_server.py:81` specifically (as opposed to inside
# `wiring.build_direct_source` itself) was invisible to CI. These two tests
# call the real factories and inspect the `Source` each one actually
# constructs, asserting on capability (`get_json`/`get_contents`) rather than
# `isinstance` alone, since capability is what a bare `httpx.Client` lacks
# and what actually made every real request 500.

def test_create_app_wires_a_real_http_client_not_a_bare_httpx_client(monkeypatch):
    """Spies on `build_app` to capture the `source` `create_app()` wires it
    with — `create_app()` itself returns only a FastAPI app, and `source` is
    a closure variable with no other way out. `ASSESSOR_API_TOKEN` is set via
    monkeypatch (env-var only, `get_settings()`'s fail-closed validator would
    otherwise raise) and `get_settings`'s `lru_cache` is cleared before and
    after so this test neither reads a stale cached Settings nor leaks its
    monkeypatched token into any test that runs after it."""
    import assessor.app as app_module

    monkeypatch.setenv("ASSESSOR_API_TOKEN", "x")
    app_module.get_settings.cache_clear()
    captured = {}
    real_build_app = app_module.build_app

    def spy(settings, source, cache):
        captured["source"] = source
        return real_build_app(settings, source, cache)

    monkeypatch.setattr(app_module, "build_app", spy)
    try:
        app_module.create_app()
    finally:
        app_module.get_settings.cache_clear()

    http = captured["source"].http
    assert isinstance(http, HttpClient), (
        f"create_app() wired a {type(http).__name__}, not the HttpClient "
        "adapter — this is the C1 regression")
    assert callable(getattr(http, "get_json", None))
    assert callable(getattr(http, "get_contents", None))
    # A bare httpx.Client — the actual C1 bug — has neither method, which is
    # why every real acquisition 500'd on the first call inside resolve_head.
    assert not hasattr(httpx.Client(), "get_json")


def test_create_mcp_wires_a_real_http_client_not_a_bare_httpx_client(monkeypatch):
    """Same regression, same gap, on the MCP entrypoint: `create_mcp()` is
    `mcp_server.main()`'s only caller (the `coderoot-repo-assessor-mcp`
    console script) and was likewise never driven by any test."""
    import assessor.mcp_server as mcp_module

    monkeypatch.setenv("ASSESSOR_API_TOKEN", "x")
    mcp_module.get_settings.cache_clear()
    captured = {}
    real_build_mcp = mcp_module.build_mcp

    def spy(settings, source, cache):
        captured["source"] = source
        return real_build_mcp(settings, source, cache)

    monkeypatch.setattr(mcp_module, "build_mcp", spy)
    try:
        mcp_module.create_mcp()
    finally:
        mcp_module.get_settings.cache_clear()

    http = captured["source"].http
    assert isinstance(http, HttpClient), (
        f"create_mcp() wired a {type(http).__name__}, not the HttpClient "
        "adapter — this is the C1 regression")
    assert callable(getattr(http, "get_json", None))
    assert callable(getattr(http, "get_contents", None))
    assert not hasattr(httpx.Client(), "get_json")
