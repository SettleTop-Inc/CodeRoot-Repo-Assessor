#!/usr/bin/env python
"""Acceptance gate for the CodeRoot-MCP data plane: prove that assessing a
repository through `source: "mcp"` reproduces the record CodeRoot itself
recorded for that repository, making ZERO GitHub requests.

This is the whole justification for the data-plane sub-project: a registry bump
must be able to re-derive the corpus for free, reading from what CodeRoot
already persisted instead of re-hitting GitHub.

WHY THIS COMPARES AGAINST CODEROOT, NOT AGAINST `source: "direct"`
------------------------------------------------------------------
The first version of this script compared the mcp path against the direct path
and required the two records to be byte-identical. That criterion was ill-posed
and could never have passed, for a reason that is by design rather than a bug:

    `DirectSource.metrics()` returns None -- a standalone deployment has no
    Aveloxis, so it has no licence/release metrics to report. `McpSource.metrics()`
    returns CodeRoot's real collected metrics.

So `assessment.versions` legitimately differs on every repo that has release
history, and `content_fingerprint` itself diverges whenever `license_spdx` is
null on one side. The 12/15 result that first run produced was not 3 failures;
it was 3 repos where the two sources genuinely had different inputs. Forcing
agreement would have meant degrading one path to match the other.

The comparison that carries meaning is the one sub-project 1 already
established and passed on all 130 repos (`tests/test_parity.py`): does this
service reproduce CODEROOT'S OWN recorded output? Sub-project 1 answered that
for a snapshot handed to the handler directly. This script answers it for a
snapshot delivered over the real MCP transport -- which is precisely the new
thing the data plane adds, and the only thing this sub-project needs to prove.

Two further properties fall out of the reframing, both of them wanted:

  * The stored record is a FIXED ground truth, so upstream HEAD drift is no
    longer a confound. The previous script had to make live GitHub calls to
    detect and skip drifted repos (13 of 130 had drifted); the MCP path reads
    the persisted snapshot at the persisted SHA, and the stored record was
    computed from that same snapshot, so both sides are pinned to the same
    commit no matter what upstream did afterwards.
  * The gate needs NO GITHUB CREDENTIAL. Not as a convenience -- it is the
    claim under test. A run that cannot authenticate to GitHub and still
    reproduces the corpus is direct evidence of free re-derivation.

WHAT IS COMPARED
----------------
Exactly the rule `tests/test_parity.py::_check_record` applies, against the
same columns:

  * `content_fingerprint` -- compared UNCONDITIONALLY. `assemble.build`
    computes it from the deterministic classification only, before any
    citation-backed promotion, so a promotion can never move it. Any mismatch
    is a real divergence.
  * `asset_types` -- compared against `stored asset_types MINUS
    stored promoted_types`. This runs with `llm_provider=none` and cannot
    reproduce an LLM promotion, so the deterministic portion is what it can
    legitimately be held to. Repos with a promotion say so in their output
    line rather than silently dropping the type.

PROVING ZERO GITHUB REQUESTS
----------------------------
`watch_outbound_hosts` records every outbound request across BOTH installed
httpx libraries -- `httpx` (0.28.x, what the assessor uses for GitHub) and
`httpx2` (2.9.x, MCP SDK 2.0's own fork, which is what the MCP transport
actually uses) -- and both their sync and async clients. See that function for
why one library is not enough; in short, instrumenting only `httpx` both hides
all MCP traffic (destroying the positive control) and leaves a GitHub request
issued via `httpx2` uncounted.

The run asserts BOTH directions, because either alone is worthless:

  * total observed calls > 0 -- the instrument is live. Without this, "zero
    GitHub hosts observed" is indistinguishable from "the recorder never ran",
    and a silently broken recorder reports a perfect result. This fired on the
    first run of the reframed script and caught exactly that.
  * GitHub calls == 0 -- the actual claim.

All patches are pass-through wrappers; none alters request behaviour.

`GET https://api.github.com/rate_limit` is deliberately NOT used to measure
this. It was tried first and rejected: a direct A/B check showed the per-response
`x-ratelimit-remaining` header decrementing normally (4639 -> 4635) while
`/rate_limit`'s own body stayed frozen at "5000/5000" throughout, confirming it
lags real usage in this environment. Trusting it would have made a false
zero-request claim unfalsifiable.

The zero-request claim is corroborated structurally as well: the mcp-path
`Settings` carries no GitHub token at all, which the script asserts before
starting, so `McpSource` has no means to make an authenticated GitHub call even
by accident.

USAGE
-----
    .venv/Scripts/python.exe scripts/verify_mcp_parity.py \\
        --mcp-url http://127.0.0.1:8300/mcp

CodeRoot-MCP must already be running (streamable-http transport) pointed at a
live CodeRoot API; this script does not start it.
"""
from __future__ import annotations

import argparse
import contextlib
import json
import subprocess
import sys
from pathlib import Path

import httpx

try:  # MCP SDK 2.0 ships its own fork; see watch_outbound_hosts.
    import httpx2
except ImportError:  # pragma: no cover - httpx2 is an mcp==2.0.0 dependency
    httpx2 = None

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from assessor.config import Settings  # noqa: E402
from assessor import wiring  # noqa: E402
from assessor.handlers import assess_handler  # noqa: E402
from assessor.ports.cache import NullCache  # noqa: E402

PSQL_CMD = ["docker", "exec", "coderoot-oss-postgres-1", "psql", "-U", "coderoot", "-d", "coderoot"]


# --- the outbound-request recorder --------------------------------------------

@contextlib.contextmanager
def watch_outbound_hosts():
    """Record `(library, host)` for every outbound HTTP request, across BOTH
    installed httpx libraries and both their sync and async clients, for the
    duration of the `with` block. Yields the list it fills; every original is
    restored on exit regardless of exceptions.

    TWO LIBRARIES ARE IN PLAY, and covering only one is how this proof fails
    silently. This environment has both installed, serving different callers:

        httpx  0.28.x  -- `assessor/http_client.py::HttpClient`, sync, every
                          GitHub REST call the direct path makes.
        httpx2 2.9.x   -- MCP SDK 2.0's own fork. `create_mcp_http_client`
                          returns an `httpx2.AsyncClient` (confirmed by reading
                          `mcp.shared._httpx_utils`, whose signature is typed
                          `-> httpx2.AsyncClient`), so ALL MCP transport
                          traffic goes through httpx2, not httpx.

    Patching only `httpx` -- what the first version of this script did -- has
    two consequences, and the second one is a hole in the claim rather than a
    missing nicety:

      1. No MCP traffic is observed at all, so the recorder looks dead. That is
         the positive control, and its absence made "0 GitHub requests"
         indistinguishable from "recorder never ran". This was caught on the
         first run of the reframed script precisely because the liveness
         assertion below was added.
      2. A GitHub request issued through `httpx2` would NOT have been counted.
         Nothing in the assessor does that today, but the zero-request claim is
         supposed to hold against the whole process, not against the one client
         library we happened to instrument. Both libraries are patched so the
         claim means what it says.

    All wrappers are pass-through; none alters request behaviour."""
    seen: list[tuple[str, str]] = []
    originals: list[tuple[type, str, object]] = []

    def _install(mod, mod_name: str) -> None:
        if mod is None:
            return
        for cls_name in ("Client", "AsyncClient"):
            cls = getattr(mod, cls_name, None)
            if cls is None:
                continue
            orig = cls.send
            originals.append((cls, "send", orig))
            if cls_name == "AsyncClient":
                async def patched(self, request, *a, _orig=orig, _m=mod_name, **kw):
                    seen.append((_m, request.url.host))
                    return await _orig(self, request, *a, **kw)
            else:
                def patched(self, request, *a, _orig=orig, _m=mod_name, **kw):
                    seen.append((_m, request.url.host))
                    return _orig(self, request, *a, **kw)
            cls.send = patched

    _install(httpx, "httpx")
    _install(httpx2, "httpx2")
    try:
        yield seen
    finally:
        for cls, attr, orig in originals:
            setattr(cls, attr, orig)


def _github_hosts(seen: list[tuple[str, str]]) -> list[tuple[str, str]]:
    return [(lib, h) for lib, h in seen if h and ("github.com" in h)]


# --- CodeRoot's recorded assessments (the ground truth) -----------------------

_BASE_SQL = """
SELECT json_build_object(
  'repo_id', r.id::text,
  'repo_url', 'https://' || r.host || '/' || r.owner || '/' || r.name,
  'owner', r.owner,
  'name', r.name,
  'stored_sha', a.commit_sha,
  'content_fingerprint', ra.content_fingerprint,
  'asset_types', COALESCE(to_json(ra.asset_types), '[]'::json),
  'promoted_types', COALESCE((SELECT json_agg(DISTINCT e->>'asset_type')
      FROM jsonb_array_elements(ra.assessment->'promoted_types') e
      WHERE jsonb_typeof(ra.assessment->'promoted_types') = 'array'), '[]'::json)
)
FROM coderoot.repo_assessment ra
JOIN coderoot.repositories r ON r.id = ra.repo_id
JOIN coderoot.repo_acquisition a ON a.repo_id = ra.repo_id
WHERE ra.subdir = ''
"""


def _rows(sql: str) -> list[dict]:
    """psql emits one JSON object per line (`-t -A`), so the array and JSONB
    columns survive intact -- parsing a delimited text dump would have to
    re-parse Postgres array literals by hand and would corrupt any value
    containing the delimiter."""
    out = subprocess.run([*PSQL_CMD, "-t", "-A", "-c", sql],
                         capture_output=True, text=True, check=True)
    return [json.loads(line) for line in out.stdout.splitlines() if line.strip()]


def fetch_expected(limit: int | None) -> list[dict]:
    sql = _BASE_SQL + " ORDER BY a.acquired_at DESC"
    if limit is not None:
        sql += f" LIMIT {int(limit)}"
    return _rows(sql + ";")


def fetch_specific(slugs: list[str]) -> list[dict]:
    """Look up specific `owner/name` repos, in the order given, for reproducing
    a finding on demand rather than scanning the corpus."""
    by_slug: dict[str, dict] = {}
    for slug in slugs:
        owner, _, name = slug.partition("/")
        found = _rows(f"{_BASE_SQL} AND r.owner = '{owner}' AND r.name = '{name}';")
        if not found:
            print(f"WARNING: {slug!r} has no recorded assessment in CodeRoot -- skipping",
                  file=sys.stderr)
            continue
        by_slug[slug] = found[0]
    return [by_slug[s] for s in slugs if s in by_slug]


# --- the comparison -----------------------------------------------------------

def compare(record: dict, expected: dict) -> list[str]:
    """Mirror of `tests/test_parity.py::_check_record`, returning failure
    strings instead of raising so one bad repo does not end the run. Keeping
    the two in step matters: this is the same acceptance rule sub-project 1
    passed on 130 repos, applied to a record that arrived over MCP."""
    failures: list[str] = []

    if record["content_fingerprint"] != expected["content_fingerprint"]:
        failures.append(
            f"content_fingerprint: coderoot={expected['content_fingerprint']} "
            f"mcp={record['content_fingerprint']}")

    promoted = set(expected.get("promoted_types") or [])
    deterministic = sorted(set(expected["asset_types"]) - promoted)
    if sorted(record["asset_types"]) != deterministic:
        note = (f" ({len(promoted)} LLM-promoted type(s) excluded from this "
                f"llm_provider=none comparison: {sorted(promoted)})" if promoted else "")
        failures.append(
            f"asset_types: coderoot deterministic={deterministic} "
            f"mcp={sorted(record['asset_types'])}{note}")

    return failures


# --- main ---------------------------------------------------------------------

def main() -> int:
    # Repo content (release names, descriptions, ...) is arbitrary untrusted
    # text and can contain characters outside Windows' default console
    # codepage -- reconfigure stdout so one emoji cannot crash the run partway
    # through instead of reporting the comparison it already computed.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--mcp-url", required=True,
                    help="Base URL of a running CodeRoot-MCP Streamable-HTTP server, "
                         "e.g. http://127.0.0.1:8300/mcp")
    ap.add_argument("--count", type=int, default=None,
                    help="Compare only the N most recently acquired repos "
                         "(default: the entire recorded corpus). Ignored when --repo is given.")
    ap.add_argument("--repo", action="append", default=None,
                    help="Compare one specific repo, as 'owner/name' (repeatable). "
                         "Bypasses the corpus scan -- use to reproduce a finding on demand.")
    args = ap.parse_args()

    mcp_settings = Settings(llm_provider="none", coderoot_mcp_url=args.mcp_url,
                            assessor_allow_anonymous=True)
    # Structural corroboration of the zero-request claim, asserted before any
    # work happens: the mcp-path Settings carries no GitHub credential at all.
    assert mcp_settings.github_token_list == [], (
        "mcp_settings unexpectedly carries a GitHub token -- the zero-request "
        "claim below would not be structurally sound")

    mcp_source = wiring.build_source(mcp_settings)
    assert type(mcp_source).__name__ == "McpSource"
    cache = NullCache()

    expected = fetch_specific(args.repo) if args.repo else fetch_expected(args.count)
    if not expected:
        print("ERROR: no recorded assessments matched -- nothing to compare", file=sys.stderr)
        return 2

    print(f"mcp source: {type(mcp_source).__name__} -> {args.mcp_url} "
          f"(github_tokens configured: {bool(mcp_settings.github_token_list)})")
    print(f"comparing {len(expected)} repo(s) against CodeRoot's recorded assessments\n")

    # --- run the mcp path under the recorder ---------------------------------
    records: dict[str, dict] = {}
    errors: dict[str, str] = {}
    with watch_outbound_hosts() as hosts:
        for e in expected:
            subject = {"repo_url": e["repo_url"], "subject_key": e["repo_id"],
                       "commit_sha": "", "subdir": ""}
            try:
                records[e["repo_id"]] = assess_handler(mcp_source, cache, mcp_settings, subject)
            except Exception as exc:  # noqa: BLE001
                errors[e["repo_id"]] = repr(exc)

    github_hosts = _github_hosts(hosts)
    by_lib: dict[str, int] = {}
    for lib, _ in hosts:
        by_lib[lib] = by_lib.get(lib, 0) + 1
    print(f"outbound HTTP calls observed during the run: {len(hosts)}")
    print(f"  by library: {by_lib or '{}'}")
    print(f"  distinct hosts: {sorted({h for _, h in hosts}) or '[]'}")
    print(f"==> GitHub requests attributable to the mcp path: {len(github_hosts)}"
          f"{'  ' + str(sorted(set(github_hosts))) if github_hosts else ''}")
    if not hosts:
        print("WARNING: the recorder observed ZERO outbound calls of any kind. The mcp path "
              "must produce httpx2 traffic to the MCP server, so this means the recorder "
              "is not working -- treat the GitHub count above as unproven, not as zero.")
    print()

    # --- compare against CodeRoot's recorded output ---------------------------
    n_match = 0
    for e in expected:
        rid, slug = e["repo_id"], f"{e['owner']}/{e['name']}"
        if rid in errors:
            print(f"{slug}: ERROR {errors[rid]}")
            continue
        failures = compare(records[rid], e)
        promoted = e.get("promoted_types") or []
        note = f"  [{len(promoted)} promoted type(s) excluded: {sorted(promoted)}]" if promoted else ""
        if failures:
            print(f"{slug}: MISMATCH ({len(failures)} field(s)){note}")
            for f in failures:
                print(f"    {f}")
        else:
            n_match += 1
            print(f"{slug}: MATCHES CodeRoot{note}")

    total = len(expected)
    instrument_live = bool(hosts)
    print()
    print("=" * 72)
    print(f"SUMMARY: {n_match}/{total} repos reproduce CodeRoot's recorded assessment "
          f"through source=mcp")
    if errors:
        print(f"         {len(errors)} repo(s) raised an error and were not compared")
    print(f"GitHub requests on the mcp path: {len(github_hosts)}")
    print(f"recorder liveness (total outbound calls observed): {len(hosts)}"
          f"{'' if instrument_live else '  <-- INSTRUMENT NOT LIVE'}")
    print("=" * 72)

    ok = (n_match == total) and not errors and not github_hosts and instrument_live
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
