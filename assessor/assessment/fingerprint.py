"""Canonical content_fingerprint over the DETERMINISTIC facts only (spec §Fingerprint).

Mirrors authorship/fingerprint.py: sort everything, fixed separators, sha256.
LLM/NL fields, tool descriptions/side-effects, free-text evidence snippets,
commit_sha and collected_at are NOT inputs.
"""
from __future__ import annotations

import hashlib
import json


def _canon(v):
    """Recursively sort list-of-string leaves (e.g. tool_names) so the hash is invariant
    to a module's internal ordering; dict key order is already handled by json.dumps
    (sort_keys=True) below, so only list VALUES need normalizing here."""
    if isinstance(v, dict):
        return {k: _canon(x) for k, x in v.items()}
    if isinstance(v, list):
        items = [_canon(x) for x in v]
        return sorted(items) if all(isinstance(x, str) for x in items) else items
    return v


def build_payload(*, asset_types, types_checked, registry_version, per_type,
                  coordinates, spdx) -> dict:
    return {
        "asset_types": sorted(asset_types),
        "types_checked": sorted(types_checked),
        "registry_version": registry_version,
        "per_type": _canon(per_type),  # module fingerprint_facts outputs (R4: pre-sorted leaves)
        "spdx": spdx,
        "coordinates": sorted(
            ({"kind": c["kind"], "ref": c["ref"]} for c in coordinates),
            key=lambda c: (c["kind"], c["ref"]),
        ),
    }


def compute_fingerprint(payload: dict) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
