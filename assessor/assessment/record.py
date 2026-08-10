"""Tolerant reading of asset-record.json — the authoring-time foundational record
(spec 2026-08-09-authoring-mcp-design.md §4, §9 D1).

Malformed = absent (never raises, never fails an assessment): a record-level
defect (bad JSON, wrong/missing record_version, oversized body, non-dict top
level) returns None; a FIELD-level defect drops that field and keeps the rest.

TRUST RULE (spec §7): nothing in this module feeds classification. declared_block
produces fact envelopes for the served `declared` section only."""
from __future__ import annotations

import json

from .shapes import fact

RECORD_BASENAME = "asset-record.json"
RECORD_MAX_BYTES = 16 * 1024
_MAX_STR = 200
_MAX_DEP = 100
_MAX_DEPS = 50
_SOURCE = "declared: asset-record.json"


def _clean_str(v) -> str | None:
    return v if isinstance(v, str) and 0 < len(v) <= _MAX_STR else None


def _clean_model_access(v) -> dict | None:
    if not isinstance(v, dict):
        return None
    mode = v.get("mode")
    if mode not in ("pinned", "byo"):
        return None
    provider, model = _clean_str(v.get("provider")), _clean_str(v.get("model"))
    if mode == "pinned" and not (provider and model):
        return None                     # a pin that names no model asserts nothing
    if mode == "byo":
        provider = model = None
    return {"mode": mode, "provider": provider, "model": model}


def _clean_technologies(v) -> dict | None:
    if not isinstance(v, dict):
        return None
    deps_in = v.get("dependencies")
    kept = [d for d in deps_in if isinstance(d, str) and 0 < len(d) <= _MAX_DEP] \
        if isinstance(deps_in, list) else []
    # A list that survives filtering to empty (every entry rejected, or the list was
    # empty to start) collapses to None — never an affirmative empty [] that would
    # read as a declared "zero dependencies" fact.
    deps = (kept[:_MAX_DEPS] or None) if isinstance(deps_in, list) else None
    out = {"language": _clean_str(v.get("language")),
           "framework": _clean_str(v.get("framework")),
           "runtime": _clean_str(v.get("runtime")),
           "dependencies": deps}
    return out if any(x is not None for x in out.values()) else None


def _clean_source_repo(v) -> dict | None:
    if not isinstance(v, dict):
        return None
    host, owner, name = (_clean_str(v.get(k)) for k in ("host", "owner", "name"))
    return {"host": host, "owner": owner, "name": name} if host and owner and name else None


def parse_record(content: dict[str, str]) -> dict | None:
    """The validated record, or None. Reads content[RECORD_BASENAME] — after
    filter_content_to_subdir a subdir's record re-roots to exactly this key, so
    one lookup serves both whole-repo and subdir subjects."""
    body = content.get(RECORD_BASENAME)
    if not isinstance(body, str) or len(body.encode("utf-8")) > RECORD_MAX_BYTES:
        return None
    try:
        raw = json.loads(body)
    except (ValueError, TypeError, RecursionError):
        # RecursionError can occur on deeply-nested JSON; treat as malformed
        return None
    if not isinstance(raw, dict):
        return None
    rv = raw.get("record_version")
    # Exclude booleans: True == 1 but is not a valid version
    if not (isinstance(rv, int) and not isinstance(rv, bool) and rv == 1):
        return None
    conf = raw.get("confirmation")
    return {
        "record_version": 1,
        "created_by": _clean_str(raw.get("created_by")),
        "created_at": _clean_str(raw.get("created_at")),
        "maintained_by": _clean_str(raw.get("maintained_by")),
        "source_repo": _clean_source_repo(raw.get("source_repo")),
        "technologies": _clean_technologies(raw.get("technologies")),
        "model_access": _clean_model_access(raw.get("model_access")),
        "confirmation": conf if isinstance(conf, dict) else None,
    }


def _facts(section: str, values: dict) -> dict:
    return {k: fact(v, _SOURCE, [{"path": RECORD_BASENAME, "marker": f"{section}.{k}"}])
            for k, v in values.items() if v is not None}


def declared_block(content: dict[str, str]) -> dict | None:
    """The `declared` section of the assessment payload: technologies +
    model_access as fact envelopes (spec §4 — these two land on the ASSESSMENT;
    created_by/maintained_by/created_at land on creation-info via repo_meta and
    are deliberately NOT emitted here)."""
    rec = parse_record(content)
    if rec is None:
        return None
    out: dict = {}
    if rec["technologies"]:
        out["technologies"] = _facts("technologies", rec["technologies"])
    if rec["model_access"]:
        out["model_access"] = _facts("model_access", rec["model_access"])
    return out or None
