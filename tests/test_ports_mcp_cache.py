from assessor.ports.cache import CachePort
from assessor.ports.mcp_cache import McpCache


class _Tools:
    """Mirrors the REAL MCP tool contract (verified against
    D:/Development/SettleTop/CodeRoot-MCP/coderoot_mcp/server.py's
    llm_cache_get/llm_cache_put), not a raw pass-through: a miss is
    {"hit": False, "response": None}, a hit is {"hit": True, "response": ...},
    and a store call returns {"stored": True}. See task-8-report.md for why
    the brief's own double (a bare dict.get pass-through with no hit/response
    wrapper) does not match this and was corrected here."""
    def __init__(self): self.store, self.puts = {}, 0

    def llm_cache_get(self, model, prompt_sha256):
        r = self.store.get((model, prompt_sha256))
        return {"hit": r is not None, "response": r}

    def llm_cache_put(self, model, prompt_sha256, response):
        self.puts += 1
        self.store[(model, prompt_sha256)] = response
        return {"stored": True}


def test_satisfies_the_cache_port():
    assert isinstance(McpCache(_Tools()), CachePort)


def test_round_trip_through_the_tools():
    c = McpCache(_Tools())
    assert c.get("m", "h") is None
    c.put("m", "h", {"a": 1})
    assert c.get("m", "h") == {"a": 1}


def test_an_upstream_failure_degrades_to_a_miss_not_a_crash():
    """A cache is an optimisation. If CodeRoot is unreachable the derive should still
    run — slower, and non-deterministic on retry, but it must not fail the request."""
    class _Broken:
        def llm_cache_get(self, *a): raise RuntimeError("upstream down")
        def llm_cache_put(self, *a): raise RuntimeError("upstream down")
    c = McpCache(_Broken())
    assert c.get("m", "h") is None
    c.put("m", "h", {"a": 1})     # must not raise


def test_an_upstream_error_payload_is_a_miss_not_a_cached_value():
    """The real MCP tools do NOT raise on an upstream failure — they return a
    structured payload instead (coderoot_mcp/server.py's
    _upstream_error_payload: {"error": "upstream_error", "status_code": ...,
    "detail": ...}, the same shape every one of the six tools uses). A test
    that only drives a raising double (above) would miss this failure mode
    entirely, since the real client never raises here — it returns this
    shape with a 200-equivalent structured body."""
    class _ErrorTools:
        def llm_cache_get(self, model, prompt_sha256):
            return {"error": "upstream_error", "status_code": 503, "detail": "down"}

        def llm_cache_put(self, model, prompt_sha256, response):
            return {"error": "upstream_error", "status_code": 503, "detail": "down"}
    c = McpCache(_ErrorTools())
    assert c.get("m", "h") is None
    c.put("m", "h", {"a": 1})     # must not raise
