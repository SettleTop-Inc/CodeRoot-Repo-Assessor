"""`/v1/acquire` and `/v1/assess` do not share a source.

The defect: `wiring` selected ONE source for the whole deployment from
`settings.coderoot_mcp_url`, and `McpSource.acquire` raises
`NotImplementedError` by design ("this source reads what acquisition
produced, it never performs one"). The compose stack runs a single assessor
service with BOTH `GITHUB_TOKENS` and `CODEROOT_MCP_URL` set, so every
production `POST /v1/acquire` was an unconditional 500 — and CodeRoot's
`assessor_client` maps a 5xx to `AssessorUnavailable`, i.e. "retry", so every
acquire unit in the corpus ground through its attempts and terminalized
`failed` with nothing to show for it.

Spec §5.1 settles the intent: "The Assessor holds GitHub credentials on
CodeRoot's path, not only on the standalone path. `/v1/acquire` always
contacts GitHub." So the selection is per-CAPABILITY, not per-deployment.
"""
from __future__ import annotations

from fastapi.testclient import TestClient

import assessor.wiring as wiring
from assessor.app import build_app
from assessor.config import Settings
from assessor.mcp_server import build_mcp
from assessor.ports.cache import NullCache
from assessor.ports.mcp_source import McpSource
from assessor.ports.source import DirectSource

_MCP_SETTINGS = dict(assessor_api_token="tok", coderoot_mcp_url="http://mcp.local:9000",
                     github_tokens="gh-token")


# --- the selection itself ---------------------------------------------------

def test_acquire_source_is_direct_even_when_mcp_is_configured(monkeypatch):
    """The core inversion. `build_assess_source` may return an McpSource;
    `build_acquire_source` must never."""
    monkeypatch.setattr(wiring, "_build_mcp_client", lambda s: object())
    s = Settings(**_MCP_SETTINGS)
    assert isinstance(wiring.build_assess_source(s), McpSource)
    assert isinstance(wiring.build_acquire_source(s), DirectSource)


def test_acquire_source_never_touches_the_mcp_client_seam(monkeypatch):
    """Stronger than a type check: building the acquire source must not even
    construct an MCP client. A `build_acquire_source` that built one and then
    discarded it would pass the type assertion above."""
    called = []
    monkeypatch.setattr(wiring, "_build_mcp_client", lambda s: called.append(s))
    wiring.build_acquire_source(Settings(**_MCP_SETTINGS))
    assert called == []


def test_the_two_selections_disagree_exactly_when_mcp_is_configured(monkeypatch):
    """Expresses the invariant as a relation rather than two separate facts,
    so a revert that re-collapsed them into one selector fails here whichever
    direction it collapsed in."""
    monkeypatch.setattr(wiring, "_build_mcp_client", lambda s: object())
    unconfigured = Settings(assessor_api_token="tok")
    configured = Settings(**_MCP_SETTINGS)
    assert type(wiring.build_assess_source(unconfigured)) is \
        type(wiring.build_acquire_source(unconfigured))
    assert type(wiring.build_assess_source(configured)) is not \
        type(wiring.build_acquire_source(configured))


# --- both production factories, end to end ---------------------------------

def _spy_factories(monkeypatch):
    import assessor.app as app_module
    import assessor.mcp_server as mcp_module

    app_module.get_settings.cache_clear()
    mcp_module.get_settings.cache_clear()
    captured = {}
    real_build_app, real_build_mcp = app_module.build_app, mcp_module.build_mcp

    def spy_app(settings, source, cache, *, acquire_source):
        captured["http"], captured["http_acquire"] = source, acquire_source
        return real_build_app(settings, source, cache, acquire_source=acquire_source)

    def spy_mcp(settings, source, cache, *, acquire_source):
        captured["mcp"], captured["mcp_acquire"] = source, acquire_source
        return real_build_mcp(settings, source, cache, acquire_source=acquire_source)

    monkeypatch.setattr(app_module, "build_app", spy_app)
    monkeypatch.setattr(mcp_module, "build_mcp", spy_mcp)
    return app_module, mcp_module, captured


def test_both_entrypoints_wire_a_direct_acquire_source_under_mcp_settings(monkeypatch):
    """`create_app()`/`create_mcp()` are what production actually calls, and
    neither was ever driven with CODEROOT_MCP_URL set AND the acquire route
    exercised — which is how the 500 shipped. Both surfaces, because
    `assess_repository` on the stdio surface has no `source` parameter at all,
    so a caller there could not even observe which path it got."""
    monkeypatch.setenv("ASSESSOR_API_TOKEN", "tok")
    monkeypatch.setenv("CODEROOT_MCP_URL", "http://mcp.local:9000")
    monkeypatch.setenv("GITHUB_TOKENS", "gh-token")
    monkeypatch.setattr(wiring, "_build_mcp_client", lambda s: object())
    app_module, mcp_module, captured = _spy_factories(monkeypatch)
    try:
        app_module.create_app()
        mcp_module.create_mcp()
    finally:
        app_module.get_settings.cache_clear()
        mcp_module.get_settings.cache_clear()

    assert isinstance(captured["http"], McpSource)
    assert isinstance(captured["mcp"], McpSource)
    assert isinstance(captured["http_acquire"], DirectSource)
    assert isinstance(captured["mcp_acquire"], DirectSource)


# --- the observable failure: the request itself ----------------------------

class _AcquiringSource:
    """A DirectSource stand-in that records an acquire and returns a
    well-formed AcquireResult. Deliberately NOT a `Source` that can snapshot:
    if the route ever reads through it for anything else the test notices."""

    def __init__(self):
        self.acquired = []

    def acquire(self, repo_url, *, prior):
        self.acquired.append(repo_url)
        return {"status": "acquired", "snapshot": None, "commit_sha": "abc123",
                "metadata": {}, "repo_meta": {}, "allowlist_version": 7}


def _mcp_backed_source():
    """A real `McpSource`. Its `acquire` is the actual production refusal
    (`NotImplementedError`), not a double's approximation of one — which
    matters, because the whole defect was that this refusal was reachable."""
    return McpSource(client=object())


def test_an_mcp_configured_deployment_can_still_acquire_over_http():
    """The 500, reproduced at the boundary that returned it. Before the split
    this app was built with the McpSource on BOTH routes and this POST was a
    500 with no discriminated body at all."""
    s = Settings(**_MCP_SETTINGS)
    acquire_source = _AcquiringSource()
    c = TestClient(build_app(s, _mcp_backed_source(), NullCache(),
                             acquire_source=acquire_source))
    r = c.post("/v1/acquire", json={"repo_url": "https://github.com/o/n"},
               headers={"Authorization": "Bearer tok"})
    assert r.status_code == 200, r.text
    assert r.json()["commit_sha"] == "abc123"
    # Not just "did not 500": it reached the acquire source, once.
    assert acquire_source.acquired == ["https://github.com/o/n"]


def test_an_mcp_configured_deployment_can_still_acquire_over_mcp_stdio():
    """Same property on the other surface. `acquire_repository` is the tool a
    local MCP client calls; it went through the identical broken selection."""
    import asyncio
    import json

    s = Settings(**_MCP_SETTINGS)
    acquire_source = _AcquiringSource()
    server = build_mcp(s, _mcp_backed_source(), NullCache(),
                       acquire_source=acquire_source)
    result = asyncio.run(server.call_tool(
        "acquire_repository", {"repo_url": "https://github.com/o/n"}))
    payload = json.loads(result.content[0].text)
    assert payload["commit_sha"] == "abc123"
    assert "error" not in payload
    assert acquire_source.acquired == ["https://github.com/o/n"]


def test_the_assess_route_still_reads_through_the_mcp_source():
    """The other half of the split, so a "fix" that pointed BOTH routes at the
    direct source would fail here. A direct-source assess re-clones from
    GitHub on every registry bump — the exact cost the data plane exists to
    avoid — so this is not a redundant assertion."""
    class _RecordingMcpSource(McpSource):
        def __init__(self):
            super().__init__(client=object())
            self.snapshots = []

        def snapshot(self, subject):
            self.snapshots.append(subject["subject_key"])
            return {"commit_sha": "abc123",
                    "metadata": {"description": None, "homepage": None,
                                 "topics": [], "license_spdx": None},
                    "tree_paths": (), "tree_capped": False, "marker_hits": (),
                    "files": {}, "source_coverage_capped": False,
                    "allowlist_version": 7}

        def metrics(self, subject):
            return None

    assess_source = _RecordingMcpSource()
    s = Settings(**_MCP_SETTINGS)
    c = TestClient(build_app(s, assess_source, NullCache(),
                             acquire_source=_AcquiringSource()))
    r = c.post("/v1/assess",
               json={"subject": {"repo_url": "https://github.com/o/n",
                                 "subject_key": "rid-1", "commit_sha": "", "subdir": ""},
                     "source": "mcp"},
               headers={"Authorization": "Bearer tok"})
    assert r.status_code == 200, r.text
    assert assess_source.snapshots == ["rid-1"]


# --- the no-credentials decision, made explicit ----------------------------

def test_acquire_is_not_refused_when_no_github_tokens_are_configured():
    """DECISION: a token-less deployment is NOT refused.

    `HttpClient._auth` returns `({}, None)` for an empty pool rather than
    raising, so acquire degrades to GitHub's anonymous 60 req/hr — a real,
    working standalone configuration that predates this split. Refusing it
    would be a behaviour regression smuggled in under a bug fix. (And a 501
    would be actively worse than the status quo: CodeRoot's `assessor_client`
    maps >=500 to `AssessorUnavailable`, so a permanent misconfiguration would
    become an infinite retry loop.) `/readyz` reports the state instead — see
    the test below."""
    s = Settings(assessor_api_token="tok", coderoot_mcp_url="http://mcp.local:9000")
    assert s.github_token_list == []
    acquire_source = _AcquiringSource()
    c = TestClient(build_app(s, _mcp_backed_source(), NullCache(),
                             acquire_source=acquire_source))
    r = c.post("/v1/acquire", json={"repo_url": "https://github.com/o/n"},
               headers={"Authorization": "Bearer tok"})
    assert r.status_code == 200, r.text
    assert acquire_source.acquired == ["https://github.com/o/n"]


def test_readyz_reports_whether_acquisition_is_authenticated():
    """The degradation is made VISIBLE instead of refused, matching how
    /readyz already distinguishes a silent model gateway. Without this an
    operator sees only acquire units exhausting their attempts."""
    anon = TestClient(build_app(Settings(assessor_api_token="tok"), _mcp_backed_source(),
                                NullCache(), acquire_source=_AcquiringSource()))
    assert anon.get("/readyz").json()["acquire"] == "anonymous"

    authed = TestClient(build_app(Settings(assessor_api_token="tok", github_tokens="gh"),
                                  _mcp_backed_source(), NullCache(),
                                  acquire_source=_AcquiringSource()))
    assert authed.get("/readyz").json()["acquire"] == "authenticated"
