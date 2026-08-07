"""Builds the production `DirectSource` from `Settings` — the one place the
real HTTP client and git fetcher are wired together, shared by both
entrypoints (`app.create_app` for HTTP, `mcp_server.create_mcp` for MCP
stdio) so the adapter is constructed in exactly one place. C1's defect was a
bare `httpx.Client` standing in for this wiring; keeping it in one shared
function means a second entrypoint can't reintroduce the same bug by
duplicating (and drifting from) the first."""
from __future__ import annotations

from .assessment.git_fetch import GitContentFetcher
from .config import Settings
from .http_client import HttpClient
from .ports.source import DirectSource


def build_direct_source(s: Settings) -> DirectSource:
    http = HttpClient(github_tokens=s.github_token_list, timeout=s.acquire_timeout_s)
    fetcher = GitContentFetcher(s.acquire_cache_dir, blob_limit=s.blob_limit_bytes,
                                timeout_s=s.acquire_timeout_s,
                                max_entries=s.max_tree_entries)
    return DirectSource(s, http, fetcher)
