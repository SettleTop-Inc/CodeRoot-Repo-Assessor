"""Helpers vendored from CodeRoot subsystems unrelated to assessment.

`_valid_slug` guards every GitHub URL this service builds, including the
caller-supplied repo_url on /v1/acquire — it is the input validation that keeps
an authenticated endpoint from being a request-forgery primitive. Copied rather
than imported because CodeRoot is not a dependency.

`_decode_contents` is also vendored here, alongside `_valid_slug` (both live in
CodeRoot's `clients.py`): `git_fetch.py`'s own inline blob-decode is byte-identical
to it by design (see git_fetch.py's `_read_paths` comment), and two moved test
files (`test_assessment_content.py`, `test_assessment_git_fetch.py`) import it
directly to assert that parity — not used by any assessment module itself."""
from __future__ import annotations

import base64
import re

_SLUG_RE = re.compile(r"^[A-Za-z0-9._-]+$")


def _valid_slug(s: str) -> bool:
    # '.'/'..' are rejected explicitly: charset alone allows them, and they enable
    # path traversal when interpolated into an api.github.com URL.
    return bool(s) and s not in (".", "..") and bool(_SLUG_RE.match(s))


def _decode_contents(data) -> str | None:
    """Decode a GitHub Contents-API payload. Returns None for >1 MB files
    (which come back as encoding='none' with empty content) — never an empty blob."""
    if not isinstance(data, dict) or data.get("encoding") != "base64" or not data.get("content"):
        return None
    try:
        return base64.b64decode(data["content"]).decode("utf-8", "replace")
    except (ValueError, TypeError):
        return None


_SHA_RE = re.compile(r"^[0-9a-fA-F]{7,64}$")
_REPO_KEY_RE = re.compile(r"^[0-9a-fA-F-]{1,64}$")  # a repo-id UUID; no path separators
