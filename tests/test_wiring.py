"""Discrimination tests for `wiring.build_assess_source` — the CodeRoot-MCP data
plane's selection point. `assessor/mcp_client.py` (the real MCP transport
adapter `build_assess_source`'s configured branch constructs) is a later task in
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
from assessor.ports.cache import NullCache
from assessor.ports.mcp_cache import McpCache
from assessor.ports.mcp_source import McpSource
from assessor.ports.source import DirectSource


def test_build_assess_source_selects_direct_source_when_unconfigured():
    s = Settings(assessor_api_token="x")
    src = wiring.build_assess_source(s)
    assert isinstance(src, DirectSource)
    assert isinstance(src.http, HttpClient)


def test_build_assess_source_selects_mcp_source_when_configured(monkeypatch):
    sentinel_client = object()
    monkeypatch.setattr(wiring, "_build_mcp_client", lambda s: sentinel_client)
    s = Settings(assessor_api_token="x", coderoot_mcp_url="http://mcp.local")
    src = wiring.build_assess_source(s)
    assert isinstance(src, McpSource)
    assert src.client is sentinel_client


def test_build_assess_source_never_touches_the_mcp_client_seam_when_unconfigured(monkeypatch):
    """A configured-vs-not test that only checked the happy path could still
    pass if the unconfigured branch accidentally called the mcp seam too (and
    just discarded the result). Assert it is never invoked."""
    called = []
    monkeypatch.setattr(wiring, "_build_mcp_client", lambda s: called.append(s))
    wiring.build_assess_source(Settings(assessor_api_token="x"))
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

    def spy_app(settings, source, cache, *, acquire_source):
        captured["http"], captured["http_acquire"] = source, acquire_source
        return real_build_app(settings, source, cache, acquire_source=acquire_source)

    def spy_mcp(settings, source, cache, *, acquire_source):
        captured["mcp"], captured["mcp_acquire"] = source, acquire_source
        return real_build_mcp(settings, source, cache, acquire_source=acquire_source)

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
    is a later task and does not exist yet — wiring.build_assess_source correctly
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


# --- fix round 2: McpCache was built and tested but never constructed in --
# production -- both app.create_app() and mcp_server.create_mcp() hardcoded
# NullCache() regardless of settings.coderoot_mcp_url, so a CodeRoot-backed
# deployment never actually shared cached LLM responses (spec §9.6: an
# uncached retry can have the model return a different citation, flipping
# asset_types and firing a spurious `changed` webhook for a repo that did
# not change). `wiring.build_cache` closes that by selecting on the same
# predicate `build_assess_source` already uses.

def test_build_cache_selects_null_cache_when_unconfigured():
    s = Settings(assessor_api_token="x")
    cache = wiring.build_cache(s)
    assert isinstance(cache, NullCache)


def test_build_cache_selects_mcp_cache_when_configured(monkeypatch):
    sentinel_client = object()
    monkeypatch.setattr(wiring, "_build_mcp_client", lambda s: sentinel_client)
    s = Settings(assessor_api_token="x", coderoot_mcp_url="http://mcp.local")
    cache = wiring.build_cache(s)
    assert isinstance(cache, McpCache)
    assert cache.client is sentinel_client


def test_build_cache_never_touches_the_mcp_client_seam_when_unconfigured(monkeypatch):
    """Same shape as the build_assess_source guard above: a configured-vs-not test
    that only checked the happy path could still pass if the unconfigured
    branch accidentally called the mcp seam too and discarded the result."""
    called = []
    monkeypatch.setattr(wiring, "_build_mcp_client", lambda s: called.append(s))
    wiring.build_cache(Settings(assessor_api_token="x"))
    assert called == []


def test_source_and_cache_never_disagree_about_mcp_vs_direct(monkeypatch):
    """The bug class this fix exists to close: McpSource paired with
    NullCache, or DirectSource paired with McpCache. Asserts the boolean
    equivalence directly (rather than each type separately) so a build_cache
    that used a different predicate than build_assess_source -- not just one that
    was unconditionally NullCache -- would still be caught here."""
    monkeypatch.setattr(wiring, "_build_mcp_client", lambda s: object())
    for kwargs in ({}, {"coderoot_mcp_url": "http://mcp.local"}):
        s = Settings(assessor_api_token="x", **kwargs)
        source_is_mcp = isinstance(wiring.build_assess_source(s), McpSource)
        cache_is_mcp = isinstance(wiring.build_cache(s), McpCache)
        assert source_is_mcp == cache_is_mcp, (
            f"disagreement for coderoot_mcp_url={kwargs.get('coderoot_mcp_url')!r}: "
            f"source_is_mcp={source_is_mcp}, cache_is_mcp={cache_is_mcp}")


# --- fix round 2, entrypoint level: extend the round-1 agreement tests -----
#
# The round-1 helper already spies on build_app/build_mcp to capture the
# constructed Source; extend it to capture the constructed Cache too, so a
# regression at the FACTORY level (create_app/create_mcp reverting to a
# literal NullCache(), independent of what wiring.build_cache itself does)
# is caught the same way test_wiring.py already catches it for Source.

def _spy_both_factories_with_cache(monkeypatch):
    import assessor.app as app_module
    import assessor.mcp_server as mcp_module

    app_module.get_settings.cache_clear()
    mcp_module.get_settings.cache_clear()
    captured = {}
    real_build_app, real_build_mcp = app_module.build_app, mcp_module.build_mcp

    def spy_app(settings, source, cache, *, acquire_source):
        captured["http_source"], captured["http_cache"] = source, cache
        return real_build_app(settings, source, cache, acquire_source=acquire_source)

    def spy_mcp(settings, source, cache, *, acquire_source):
        captured["mcp_source"], captured["mcp_cache"] = source, cache
        return real_build_mcp(settings, source, cache, acquire_source=acquire_source)

    monkeypatch.setattr(app_module, "build_app", spy_app)
    monkeypatch.setattr(mcp_module, "build_mcp", spy_mcp)
    return app_module, mcp_module, captured


def test_create_app_and_create_mcp_use_null_cache_when_unconfigured(monkeypatch):
    monkeypatch.setenv("ASSESSOR_API_TOKEN", "x")
    app_module, mcp_module, captured = _spy_both_factories_with_cache(monkeypatch)
    try:
        app_module.create_app()
        mcp_module.create_mcp()
    finally:
        app_module.get_settings.cache_clear()
        mcp_module.get_settings.cache_clear()

    assert isinstance(captured["http_cache"], NullCache)
    assert isinstance(captured["mcp_cache"], NullCache)


def test_create_app_and_create_mcp_use_mcp_cache_when_configured(monkeypatch):
    """The regression itself, at the real production factories: both
    create_app() and create_mcp() previously hardcoded NullCache()
    (app.py:173, mcp_server.py:86) regardless of coderoot_mcp_url. This must
    fail if either factory reverts its build_cache(s) call back to a literal
    NullCache()."""
    monkeypatch.setenv("ASSESSOR_API_TOKEN", "x")
    monkeypatch.setenv("CODEROOT_MCP_URL", "http://mcp.local:9000")
    monkeypatch.setattr(wiring, "_build_mcp_client", lambda s: object())
    app_module, mcp_module, captured = _spy_both_factories_with_cache(monkeypatch)
    try:
        app_module.create_app()
        mcp_module.create_mcp()
    finally:
        app_module.get_settings.cache_clear()
        mcp_module.get_settings.cache_clear()

    assert isinstance(captured["http_cache"], McpCache)
    assert isinstance(captured["mcp_cache"], McpCache)
