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
