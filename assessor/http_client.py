"""Thin httpx-backed HTTP client the acquisition path calls through.

Ported from CodeRoot's `coderoot_oss/clients.py::HttpClient`. `assessment/
content.py`'s `resolve_head`/`fetch_files`/`fetch_source_subtree` call
`http.get_json(url)` and `http.get_contents(owner, name, path, ref)`
directly — a bare `httpx.Client` has neither method, which is exactly why
every real acquisition 500'd before this adapter existed (see app.py's
`create_app`). This class supplies that duck-typed interface over a real
httpx transport.

Moved code: `get_json`, `get_contents`, `_pick`, `_auth`, `_note_limit` and
the `_blocked` bench-on-exhaustion logic are kept verbatim, per the same
moved-not-rewritten rule as the rest of this extraction. Edits are limited
to imports (`_valid_slug`/`_decode_contents` come from `.vendored`, already
ported here, instead of CodeRoot's `clients.py`) and dropping what pulls in
CodeRoot internals or has no caller in this service: `GitClient` (needs
CodeRoot's `Inventory` and `repo_url` host-allowlist — this service has its
own, in `assessment/git_fetch.py`), and `post_json`/`post_raw`/`_headers`,
none of which anything here calls.
"""
from __future__ import annotations

import time
from collections.abc import Sequence
from urllib.parse import quote, urlsplit

import httpx

from .vendored import _decode_contents, _valid_slug


class HttpClient:
    """httpx-backed JSON client. Attaches a GitHub token only on api.github.com
    requests (the only host this service ever calls); other hosts would be
    unauthenticated, matching the source behaviour."""

    def __init__(self, *, github_token: str | None = None,
                github_tokens: Sequence[str] | None = None, timeout: float = 30.0) -> None:
        """`github_tokens` is the whole configured pool; `github_token` is the older
        single-token form, kept because callers and the live-smoke tests still use it.
        Passing both is fine — they concatenate, deduplicated, order preserved."""
        pool = list(github_tokens or ())
        if github_token:
            pool.insert(0, github_token)
        self._tokens: list[str] = list(dict.fromkeys(t for t in pool if t))
        # token index -> unix ts before which it must not be used again. Populated from
        # the response headers of the request that spent it.
        self._blocked: dict[int, float] = {}
        self._next = 0
        self._client = httpx.Client(timeout=timeout, follow_redirects=True,
                                    headers={"User-Agent": "coderoot-oss"})

    @property
    def _github_token(self) -> str | None:
        """Back-compat read for anything that inspected the old single-token attribute."""
        return self._tokens[0] if self._tokens else None

    def _pick(self) -> int:
        """Round-robin over the pool, skipping tokens known to be rate-limited.

        If EVERY token is blocked we still return one (the soonest to reset) rather than
        falling back to anonymous: an exhausted token yields 403 at 5000/hr, but anonymous
        requests are capped at 60/hr, so going unauthenticated makes the stall far worse.
        The caller's existing retry/backoff handles the 403.

        Deliberately lock-free. Dramatiq runs worker threads in-process, so concurrent
        `_next` increments can race - but the only consequence is two requests reusing one
        token, which costs a little evenness and nothing in correctness. A lock on every
        outbound request would be a worse trade. Note the pool is per-HttpClient-instance,
        so separate worker PROCESSES each rotate independently; that spreads load without
        coordination but means the blocked-token map is not shared between them."""
        now = time.time()
        n = len(self._tokens)
        for _ in range(n):
            i = self._next % n
            self._next += 1
            if self._blocked.get(i, 0.0) <= now:
                return i
        return min(range(n), key=lambda i: self._blocked.get(i, 0.0))

    def _auth(self, url: str) -> tuple[dict, int | None]:
        """(headers, token index) — index is None when no token was attached, which is
        what `_note_limit` keys on to know there is nothing to record."""
        if not self._tokens or urlsplit(url).netloc != "api.github.com":
            return {}, None
        i = self._pick()
        return {"Authorization": f"Bearer {self._tokens[i]}"}, i

    def _note_limit(self, idx: int | None, response: httpx.Response) -> None:
        """Bench a token the moment GitHub says it has nothing left.

        Keyed on `x-ratelimit-remaining == 0` rather than on the 403 status: the quota
        hits zero on the LAST successful (200) response, so watching only failures wastes
        one guaranteed-403 request per token per window."""
        if idx is None:
            return
        remaining = response.headers.get("x-ratelimit-remaining")
        reset = response.headers.get("x-ratelimit-reset")
        if remaining == "0" and reset and reset.strip().isdigit():
            self._blocked[idx] = float(reset)

    def get_json(self, url: str):
        headers, idx = self._auth(url)
        try:
            r = self._client.get(url, headers=headers)
        except httpx.HTTPError:
            return 0, None
        self._note_limit(idx, r)
        return r.status_code, _json_or_none(r)

    def get_contents(self, owner: str, name: str, path: str, ref: str) -> tuple[int, str | None]:
        """GitHub Contents API for one file at a pinned ref → (status, decoded text | None).
        Validates owner/name and percent-encodes each path segment (the repositories row is
        not a trusted URL source)."""
        if not (_valid_slug(owner) and _valid_slug(name)):
            return 422, None
        enc_path = "/".join(quote(seg, safe="") for seg in path.split("/") if seg)
        url = f"https://api.github.com/repos/{owner}/{name}/contents/{enc_path}?ref={quote(ref, safe='')}"
        status, data = self.get_json(url)
        return status, (_decode_contents(data) if status == 200 else None)

    def close(self) -> None:
        self._client.close()


def _json_or_none(response: httpx.Response):
    try:
        return response.json()
    except (ValueError, httpx.HTTPError):
        return None
