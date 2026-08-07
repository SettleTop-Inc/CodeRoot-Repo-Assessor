"""Builds the production `Source` from `Settings` — the one place the real
adapters are wired together, shared by both entrypoints (`app.create_app`
for HTTP, `mcp_server.create_mcp` for MCP stdio) so the adapter is
constructed in exactly one place. C1's defect was a bare `httpx.Client`
standing in for this wiring; keeping it in one shared function means a
second entrypoint can't reintroduce the same bug by duplicating (and
drifting from) the first.

`build_source` is the selection point added for the CodeRoot-MCP data plane:
`McpSource` (zero-GitHub-cost re-derivation from an already-persisted
snapshot) when `settings.coderoot_mcp_url` is configured, `DirectSource` (a
live GitHub acquisition) otherwise. Selecting here rather than in app.py's
endpoint is what keeps both entrypoints constructing the real adapter in
exactly one place, matching `build_direct_source`'s own reasoning above."""
from __future__ import annotations

from .assessment.git_fetch import GitContentFetcher
from .config import Settings
from .http_client import HttpClient
from .ports.mcp_source import McpSource
from .ports.source import DirectSource, Source


def build_direct_source(s: Settings) -> DirectSource:
    http = HttpClient(github_tokens=s.github_token_list, timeout=s.acquire_timeout_s)
    fetcher = GitContentFetcher(s.acquire_cache_dir, blob_limit=s.blob_limit_bytes,
                                timeout_s=s.acquire_timeout_s,
                                max_entries=s.max_tree_entries)
    return DirectSource(s, http, fetcher)


def _build_mcp_client(s: Settings):
    # Deferred import, deliberately: `assessor.mcp_client.McpToolClient` is
    # the real MCP transport adapter, built in a later task of this same
    # plan (the CodeRoot-MCP data plane's Task 9) and does not exist yet.
    # Importing it lazily, only on this branch, means an unconfigured
    # deployment — DirectSource below, the only path exercised by every
    # existing test — never touches this module, and importing
    # `assessor.wiring` itself never fails. Until that module lands, a
    # deployment that sets `coderoot_mcp_url` will fail here with
    # `ModuleNotFoundError` rather than silently falling back to
    # DirectSource, which would perform a live GitHub acquisition instead of
    # the zero-cost re-derivation the configuration asked for.
    from .mcp_client import McpToolClient
    return McpToolClient(s.coderoot_mcp_url, s.coderoot_mcp_token)


def build_source(s: Settings) -> Source:
    if s.coderoot_mcp_url:
        return McpSource(_build_mcp_client(s))
    return build_direct_source(s)
