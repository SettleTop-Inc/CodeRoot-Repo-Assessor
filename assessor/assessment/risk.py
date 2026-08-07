"""Coarse risk flags — DETERMINISTIC only (spec §Risk).

Computed from tool names + auth. The LLM may only monotonically RAISE a flag
(add a concern), never lower/clear one — so injected content can't scrub a
write/exec signal. Every flag is a Fact.
"""
from __future__ import annotations

from .shapes import fact, unknown

_KEYWORDS = {
    "writes": ("write", "create", "update", "delete", "put", "insert", "save", "edit", "mkdir", "rm"),
    "executes_code": ("run", "exec", "shell", "eval", "spawn", "command", "bash", "python"),
    "network": ("fetch", "http", "request", "download", "url", "curl", "get", "post", "api"),
}


def _signals(names: str, complete: bool, basis: str, secrets_hit: bool) -> dict:
    """Shared flag derivation over a pre-normalized tool-name surface (R1: reads no
    composition keys). R2: no-hit over an empty/incomplete inventory is unknown()."""
    flags: dict[str, dict] = {}
    for flag, kws in _KEYWORDS.items():
        if any(k in names for k in kws):
            flags[flag] = fact(True, "tool-name side-effects",
                               [{"path": "composition", "marker": f"{flag} keyword"}])
        elif complete:
            flags[flag] = fact(False, "tool-name side-effects (complete inventory)", [])
        else:
            flags[flag] = unknown(f"not derivable: {basis}")
    if secrets_hit:
        flags["handles_secrets"] = fact(True, "auth mode + tool names",
                                        [{"path": "composition", "marker": "auth/secret"}])
    elif complete:
        flags["handles_secrets"] = fact(False, "auth mode + tool names (complete inventory)", [])
    else:
        flags["handles_secrets"] = unknown(f"not derivable: {basis}")
    return flags


def assess(composition: dict) -> dict:
    """mcp_server risk_signals: the shared helper over tools + auth (R2 since rev 2)."""
    names = " ".join(t.get("name", "") for t in composition.get("tools", [])).lower()
    complete = bool(composition.get("tools_complete"))
    basis = composition.get("tools_incomplete_reason") or "tool inventory empty/incomplete"
    auth = composition.get("auth", {}).get("value")
    secrets = bool(auth) or any(s in names for s in ("token", "key", "secret"))
    return _signals(names, complete, basis, secrets)


# UNWIRED seam: no llm_flags producer exists; when wired it must handle unknown() bases (no 'source' key).
def raise_only(base: dict, llm_flags: dict) -> dict:
    """Merge LLM-suggested flags: may set True, never clear (monotonic-raise)."""
    out = dict(base)
    for key, val in out.items():
        if bool(llm_flags.get(key)):
            out[key] = fact(True, val["source"] + " + llm", val.get("evidence", []) + [{"marker": "llm-raised"}])
    for key, suggested in llm_flags.items():
        if key not in out and suggested:
            out[key] = fact(True, "llm", [{"marker": "llm-raised"}])
    return out


def known_unknowns(parts: list[dict]) -> list[dict]:
    out: list[dict] = []
    seen: set[str] = set()
    for part in parts:
        for field in part.values():
            if isinstance(field, dict) and field.get("known_unknown"):
                detail = field["known_unknown"]
                if detail not in seen:
                    seen.add(detail)
                    out.append({"code": "undeclared", "detail": detail})
    return out
