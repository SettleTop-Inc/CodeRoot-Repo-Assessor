"""Discrimination tests for `wiring.build_source` — the CodeRoot-MCP data
plane's selection point. `assessor/mcp_client.py` (the real MCP transport
adapter `build_source`'s configured branch constructs) is a later task in
this same plan and does not exist yet, so the configured-branch test below
monkeypatches `wiring._build_mcp_client` — the wiring-internal seam that
isolates "which client got built" from "which Source type got selected" —
rather than driving a real network client. This proves the SELECTION logic
(McpSource vs DirectSource, chosen from `settings.coderoot_mcp_url`) without
depending on a module this task does not own."""
from __future__ import annotations

import assessor.wiring as wiring
from assessor.config import Settings
from assessor.http_client import HttpClient
from assessor.ports.mcp_source import McpSource
from assessor.ports.source import DirectSource


def test_build_source_selects_direct_source_when_unconfigured():
    s = Settings(assessor_api_token="x")
    src = wiring.build_source(s)
    assert isinstance(src, DirectSource)
    assert isinstance(src.http, HttpClient)


def test_build_source_selects_mcp_source_when_configured(monkeypatch):
    sentinel_client = object()
    monkeypatch.setattr(wiring, "_build_mcp_client", lambda s: sentinel_client)
    s = Settings(assessor_api_token="x", coderoot_mcp_url="http://mcp.local")
    src = wiring.build_source(s)
    assert isinstance(src, McpSource)
    assert src.client is sentinel_client


def test_build_source_never_touches_the_mcp_client_seam_when_unconfigured(monkeypatch):
    """A configured-vs-not test that only checked the happy path could still
    pass if the unconfigured branch accidentally called the mcp seam too (and
    just discarded the result). Assert it is never invoked."""
    called = []
    monkeypatch.setattr(wiring, "_build_mcp_client", lambda s: called.append(s))
    wiring.build_source(Settings(assessor_api_token="x"))
    assert called == []


# --- fix round 1: the two production entrypoints must not diverge ----------
#
# app.py's create_app() and mcp_server.py's create_mcp() each build their own
# Source by calling get_settings() then a wiring function, with no shared
# call site a test can intercept once. Reviewed drift: create_app() moved to
# wiring.build_source while create_mcp() kept calling build_direct_source
# directly, so a deployment with CODEROOT_MCP_URL set got McpSource on HTTP
# and silently kept doing live GitHub acquisitions on the MCP stdio surface —
# worse than the usual case because assess_repository has no `source`
# parameter, so a caller cannot even ask for the behaviour it silently
# didn't get. These two tests drive BOTH real factories under identical
# settings and compare the ACTUAL constructed source's type, the same
# spy-on-build_app/build_mcp pattern test_http_client.py's C1 tests use.

def _spy_both_factories(monkeypatch):
    import assessor.app as app_module
    import assessor.mcp_server as mcp_module

    app_module.get_settings.cache_clear()
    mcp_module.get_settings.cache_clear()
    captured = {}
    real_build_app, real_build_mcp = app_module.build_app, mcp_module.build_mcp

    def spy_app(settings, source, cache):
        captured["http"] = source
        return real_build_app(settings, source, cache)

    def spy_mcp(settings, source, cache):
        captured["mcp"] = source
        return real_build_mcp(settings, source, cache)

    monkeypatch.setattr(app_module, "build_app", spy_app)
    monkeypatch.setattr(mcp_module, "build_mcp", spy_mcp)
    return app_module, mcp_module, captured


def test_create_app_and_create_mcp_agree_when_unconfigured(monkeypatch):
    monkeypatch.setenv("ASSESSOR_API_TOKEN", "x")
    app_module, mcp_module, captured = _spy_both_factories(monkeypatch)
    try:
        app_module.create_app()
        mcp_module.create_mcp()
    finally:
        app_module.get_settings.cache_clear()
        mcp_module.get_settings.cache_clear()

    assert isinstance(captured["http"], DirectSource)
    assert type(captured["http"]) is type(captured["mcp"])


def test_create_app_and_create_mcp_agree_when_mcp_is_configured(monkeypatch):
    """The configured branch's real transport (assessor.mcp_client.McpToolClient)
    is a later task and does not exist yet — wiring.build_source correctly
    raises ModuleNotFoundError today rather than silently falling back to
    DirectSource, and that must stay true (see the tests above). So this
    monkeypatches the SAME wiring-internal seam those tests use, to prove
    the two entrypoints agree on SELECTION without depending on a module
    this task does not own."""
    monkeypatch.setenv("ASSESSOR_API_TOKEN", "x")
    monkeypatch.setenv("CODEROOT_MCP_URL", "http://mcp.local:9000")
    monkeypatch.setattr(wiring, "_build_mcp_client", lambda s: object())
    app_module, mcp_module, captured = _spy_both_factories(monkeypatch)
    try:
        app_module.create_app()
        mcp_module.create_mcp()
    finally:
        app_module.get_settings.cache_clear()
        mcp_module.get_settings.cache_clear()

    assert isinstance(captured["http"], McpSource)
    assert type(captured["http"]) is type(captured["mcp"])
