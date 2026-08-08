"""Builds the production `Source` from `Settings` — the one place the real
adapters are wired together, shared by both entrypoints (`app.create_app`
for HTTP, `mcp_server.create_mcp` for MCP stdio) so the adapter is
constructed in exactly one place. C1's defect was a bare `httpx.Client`
standing in for this wiring; keeping it in one shared function means a
second entrypoint can't reintroduce the same bug by duplicating (and
drifting from) the first.

`build_assess_source` is the selection point added for the CodeRoot-MCP data
plane: `McpSource` (zero-GitHub-cost re-derivation from an already-persisted
snapshot) when `settings.coderoot_mcp_url` is configured, `DirectSource` (a
live GitHub acquisition) otherwise. Selecting here rather than in app.py's
endpoint is what keeps both entrypoints constructing the real adapter in
exactly one place, matching `build_direct_source`'s own reasoning above.

`build_acquire_source` is DELIBERATELY not that selection — it is always a
`DirectSource`, on every deployment. `/v1/acquire`'s whole job is to contact
GitHub (spec §5.1: "The Assessor holds GitHub credentials on CodeRoot's path,
not only on the standalone path. `/v1/acquire` always contacts GitHub"), and
`McpSource.acquire` raises `NotImplementedError` by design — it reads what an
acquisition produced, it never performs one. A single `build_source` used for
both endpoints therefore made `/v1/acquire` an unconditional 500 on the one
deployment shape production actually runs: the compose stack sets BOTH
`GITHUB_TOKENS` and `CODEROOT_MCP_URL` on one assessor service, so selecting
the source globally handed the acquire route an McpSource. Splitting the
selection per capability — rather than per deployment — is what makes the two
endpoints independently correct.

Both entrypoints (`app.build_app`, `mcp_server.build_mcp`) take the two
sources as SEPARATE required arguments rather than one. That is the point:
there is no default that silently reuses the assess source, so a call site
that forgets the split fails at construction with a TypeError instead of
reintroducing the 500.

`build_cache` mirrors `build_assess_source`'s predicate exactly: `McpCache` (reads/
writes the LLM response cache through CodeRoot-MCP's `llm_cache_get`/
`llm_cache_put` tools) when `settings.coderoot_mcp_url` is configured,
`NullCache` (no cache; every derive re-calls the model) otherwise. This is
not a style choice — `ports/cache.py`'s `NullCache` docstring and spec §9.6
both say an uncached retry can have the model return a different citation,
which flips `promoted_types`/`asset_types` and fires a spurious `changed`
webhook on CodeRoot's path, so the source and the cache must always agree
about whether this deployment is MCP-backed. Before this function existed,
both production factories (`app.create_app`, `mcp_server.create_mcp`)
hardcoded `NullCache()` unconditionally — `McpCache` was built and tested
but never constructed outside a test, the same shape of defect as the
bare-`httpx.Client` C1 this module's own top docstring describes.

`build_cache` calls `_build_mcp_client(s)` again rather than sharing the
instance `build_assess_source` constructed: `McpToolClient` (mcp_client.py's module
docstring) holds only a URL and a token and opens-then-tears-down its own
connection on every single tool call, so it carries no persistent connection
state a second instance would fail to reuse. Two independent instances cost
exactly what one instance used twice costs. Keeping `build_cache` a small
selector with the same shape as `build_assess_source` — no shared-construction
plumbing between them — is worth more than a reuse that would save nothing."""
from __future__ import annotations

from .assessment.git_fetch import GitContentFetcher
from .config import Settings
from .http_client import HttpClient
from .ports.cache import CachePort, NullCache
from .ports.mcp_cache import McpCache
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


def build_assess_source(s: Settings) -> Source:
    """The source `/v1/assess` reads snapshots through. MCP-backed when
    configured — that is what preserves zero-GitHub-cost re-derivation."""
    if s.coderoot_mcp_url:
        return McpSource(_build_mcp_client(s))
    return build_direct_source(s)


def build_acquire_source(s: Settings) -> DirectSource:
    """The source `/v1/acquire` acquires through. Always direct, on every
    deployment — see this module's docstring. Deliberately takes no branch on
    `s.coderoot_mcp_url`: there is no configuration under which reading an
    already-persisted snapshot could satisfy a request to go fetch one.

    A deployment with no `GITHUB_TOKENS` is NOT refused here. `HttpClient`
    treats an empty pool as "send no Authorization header" (http_client.py's
    `_auth`) rather than raising, so acquire degrades to anonymous GitHub at
    60 req/hr instead of failing — a real, working standalone configuration
    that predates this split and must keep working. `/readyz` reports which of
    the two it is, so the degradation is visible rather than silent."""
    return build_direct_source(s)


def build_cache(s: Settings) -> CachePort:
    if s.coderoot_mcp_url:
        return McpCache(_build_mcp_client(s))
    return NullCache()
