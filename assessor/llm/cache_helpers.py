"""JSON extraction + prompt hashing — the pure helpers `client.py` needs.

These carry no persistence knowledge; the cache itself is `assessor.ports.cache.CachePort`.
"""
from __future__ import annotations

import hashlib
import json


def extract_json(text_out: str) -> str | None:
    """First JSON object in the reply (local models often wrap JSON in prose).

    Uses the stdlib JSON parser so braces/quotes INSIDE string values don't confuse
    the scan (a naive brace-counter truncates on the first `}` inside a string)."""
    start = text_out.find("{")
    if start < 0:
        return None
    try:
        obj, _ = json.JSONDecoder().raw_decode(text_out, start)
    except ValueError:
        return None
    return json.dumps(obj)


def prompt_hash(system: str, user: str, schema_name: str) -> str:
    return hashlib.sha256(f"{schema_name}\x1f{system}\x1f{user}".encode("utf-8")).hexdigest()
