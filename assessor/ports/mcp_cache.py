"""Read/write the LLM response cache through CodeRoot-MCP's `llm_cache_get`/
`llm_cache_put` tools, instead of the in-process cache CodeRoot itself uses.

A cache is an optimisation, not a dependency: if CodeRoot is unreachable the
derive must still run — slower, and non-deterministic on retry (spec §9.6),
but it must not fail the request. Both methods degrade to "no cache" on any
upstream trouble rather than raising: `get` returns None (a miss), `put`
returns without effect.

Two distinct "no value" shapes have to be handled, both confirmed against the
real tool implementations in CodeRoot-MCP's `coderoot_mcp/server.py`:
  - A genuine miss/hit is a *successful* structured payload:
    `{"hit": bool, "response": dict | None}`.
  - An upstream failure is NOT raised across the MCP boundary — every one of
    the six tools traps it and returns `{"error": "upstream_error",
    "status_code": ..., "detail": ...}` instead (`_upstream_error_payload`).
    That shape must be treated as a miss, not as a cached value with a
    confusing key ("error") mixed into it.
A raising client is *also* tolerated defensively, in case a future transport
does raise (e.g. a connection failure before any tool response is formed)."""
from __future__ import annotations


class McpCache:
    def __init__(self, client) -> None:
        self.client = client

    def get(self, model: str, prompt_sha256: str) -> dict | None:
        try:
            result = self.client.llm_cache_get(model, prompt_sha256)
        except Exception:
            return None
        if not isinstance(result, dict) or "error" in result:
            return None
        return result.get("response") if result.get("hit") else None

    def put(self, model: str, prompt_sha256: str, response: dict) -> None:
        try:
            self.client.llm_cache_put(model, prompt_sha256, response)
        except Exception:
            return None
