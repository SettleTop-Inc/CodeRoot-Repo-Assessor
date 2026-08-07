#!/usr/bin/env python
"""Task 10 acceptance gate: prove that assessing a CodeRoot-acquired repository
through `source: "mcp"` produces a record byte-identical to `source: "direct"`,
with zero GitHub requests on the MCP path.

This is the whole justification for the CodeRoot-MCP data plane sub-project: a
registry bump must be able to re-derive the corpus for free, reading from what
CodeRoot already persisted instead of re-hitting GitHub.

WHAT THIS SCRIPT DOES
----------------------
For each of `--count` repositories CodeRoot has already acquired (queried live
from `coderoot.repo_acquisition` joined to `coderoot.repositories`, via
`docker exec coderoot-oss-postgres-1 psql`):

  1. Confirms the repo's GitHub HEAD has not moved since CodeRoot acquired it
     (a live 2-call GitHub check). If it has, the repo is skipped -- comparing
     a fresh HEAD fetch against a day-old persisted snapshot would produce a
     content difference that has nothing to do with the source transport, and
     would misattribute ordinary upstream churn to a McpSource/DirectSource
     bug. This is NOT in the task brief; it was discovered while building this
     script (13 of the 130 acquired repos had already drifted) and is exactly
     the kind of confound the brief warns the harness must isolate.
  2. Assesses it via `source: "mcp"` (McpSource -> McpToolClient -> a REAL
     Streamable-HTTP CodeRoot-MCP server -> CodeRoot's API) and via
     `source: "direct"` (DirectSource -> live GitHub, using a real token).
  3. Compares the FULL returned record (not just content_fingerprint/
     asset_types) and reports every differing field, if any.

The LLM is off on both paths (`llm_provider=none`), so the deterministic core
is what's measured -- an LLM-derived promotion is non-deterministic and would
fail the comparison for reasons unrelated to the transport.

GitHub requests on the MCP-path are measured by monkeypatching `httpx.Client.send`
(the sync client `assessor/http_client.py::HttpClient` uses for every GitHub REST
call) for the duration of that phase and recording every outbound host -- NOT by
polling `GET https://api.github.com/rate_limit`. That endpoint was tried first and
rejected: a direct A/B check (5 back-to-back real GitHub calls) showed the
per-response `x-ratelimit-remaining` HEADER decrementing normally (4639 -> 4635)
while `/rate_limit`'s OWN response body stayed frozen at "5000/5000" throughout --
confirming it lags/caches independently of real usage on this token, in this
environment (matching a previously-documented gotcha with this same endpoint).
Trusting it would have made a false "zero requests" claim unfalsifiable. The
transport-level counter is corroborated structurally too: the MCP-path Settings
carries no GitHub token at all, so McpSource has no way to make an authenticated
GitHub call even by accident, and it is exercised as a sanity check on the direct
path (which must show real api.github.com traffic, or the counter itself would be
suspect). Note this instrumentation only sees httpx traffic -- DirectSource's git
clone is a separate `git` subprocess speaking the git protocol directly, not an
HTTP call this counter observes; see the printed caveat below.

USAGE
-----
    .venv/Scripts/python.exe scripts/verify_mcp_parity.py \\
        --mcp-url http://127.0.0.1:8300/mcp \\
        --github-token-file <path-to-a-file-containing-one-token> \\
        --count 5

CodeRoot-MCP must already be running (streamable-http transport) pointed at a
live CodeRoot API; this script does not start it. See the Task 10 report for
the exact commands used to bring it up.
"""
from __future__ import annotations

import argparse
import contextlib
import json
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

import httpx

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from assessor.config import Settings  # noqa: E402
from assessor import wiring  # noqa: E402
from assessor.handlers import assess_handler  # noqa: E402
from assessor.ports.cache import NullCache  # noqa: E402

PSQL_CMD = ["docker", "exec", "coderoot-oss-postgres-1", "psql", "-U", "coderoot", "-d", "coderoot"]


# --- the reliable GitHub-request counter --------------------------------------

@contextlib.contextmanager
def watch_outbound_hosts():
    """Monkeypatches `httpx.Client.send` (sync client -- what
    `assessor/http_client.py::HttpClient` uses for every GitHub REST call) for
    the duration of the `with` block, yielding a list this fills with every
    request host seen. Restored on exit regardless of exceptions.

    Deliberately does NOT touch `httpx.AsyncClient` -- `McpToolClient` (the mcp
    path's own transport, `assessor/mcp_client.py`) uses `create_mcp_http_client`,
    which is async, so this patch cannot intercept or interfere with genuine MCP
    traffic; it only ever sees synchronous GitHub REST calls."""
    seen: list[str] = []
    orig_send = httpx.Client.send

    def patched_send(self, request, *a, **kw):
        seen.append(request.url.host)
        return orig_send(self, request, *a, **kw)

    httpx.Client.send = patched_send
    try:
        yield seen
    finally:
        httpx.Client.send = orig_send


def _github_hosts(hosts: list[str]) -> list[str]:
    return [h for h in hosts if h and ("github.com" in h)]


# --- CodeRoot's acquired-repo inventory -------------------------------------

def fetch_candidates(limit: int) -> list[dict]:
    """Most-recently-acquired repos, from CodeRoot's own Postgres, via the
    exact `docker exec ... psql` path the task brief specifies."""
    sql = (
        "SELECT r.id, r.host, r.owner, r.name, a.commit_sha "
        "FROM coderoot.repo_acquisition a "
        "JOIN coderoot.repositories r ON r.id = a.repo_id "
        f"ORDER BY a.acquired_at DESC LIMIT {limit};"
    )
    return _rows(sql)


def fetch_specific(slugs: list[str]) -> list[dict]:
    """Look up specific `owner/name` repos (in the order given), for re-running
    the comparison against a known repo rather than the N-most-recent scan --
    e.g. to reproduce a specific finding on demand."""
    rows_by_slug: dict[str, dict] = {}
    for slug in slugs:
        owner, _, name = slug.partition("/")
        sql = (
            "SELECT r.id, r.host, r.owner, r.name, a.commit_sha "
            "FROM coderoot.repo_acquisition a "
            "JOIN coderoot.repositories r ON r.id = a.repo_id "
            f"WHERE r.owner = '{owner}' AND r.name = '{name}';"
        )
        found = _rows(sql)
        if not found:
            print(f"WARNING: {slug!r} has no acquisition row in CodeRoot -- skipping",
                 file=sys.stderr)
            continue
        rows_by_slug[slug] = found[0]
    return [rows_by_slug[s] for s in slugs if s in rows_by_slug]


def _rows(sql: str) -> list[dict]:
    out = subprocess.run([*PSQL_CMD, "-t", "-A", "-F", "|", "-c", sql],
                         capture_output=True, text=True, check=True)
    rows = []
    for line in out.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        repo_id, host, owner, name, sha = line.split("|")
        rows.append({"repo_id": repo_id, "host": host, "owner": owner, "name": name,
                    "repo_url": f"https://{host}/{owner}/{name}", "stored_sha": sha})
    return rows


# --- live-HEAD drift check ----------------------------------------------------
#
# NOTE: this deliberately does NOT use `GET /rate_limit` to detect anything --
# that endpoint was tried during development and found to be unreliable in this
# environment (see the module docstring). The GitHub-request PROOF below
# (`watch_outbound_hosts`) uses per-call httpx instrumentation instead.

def github_live_head(token: str, owner: str, name: str) -> str | None:
    """Two calls, mirroring exactly what `assessment/content.py::resolve_head`
    does on the direct path -- used only to detect drift BEFORE spending a git
    clone on a repo we're going to skip anyway."""
    def get(url):
        req = urllib.request.Request(
            url, headers={"Authorization": f"Bearer {token}", "User-Agent": "coderoot-mcp-parity-check"})
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            raise
    repo = get(f"https://api.github.com/repos/{owner}/{name}")
    if repo is None:
        return None
    branch = repo.get("default_branch", "main")
    commit = get(f"https://api.github.com/repos/{owner}/{name}/commits/{branch}")
    return (commit or {}).get("sha")


# --- record diff ---------------------------------------------------------------

def _short(v, limit=200):
    s = repr(v)
    return s if len(s) <= limit else s[:limit] + f"...<{len(s)} chars>"


def diff_records(a, b, path: str = "") -> list[tuple[str, str, str]]:
    """Recursive structural diff. Dicts compare by key (order-insensitive,
    matching Python dict equality); lists compare element-wise IN ORDER --
    deliberately, since `tree_paths` ordering was flagged as a likely failure
    mode and must not be silently tolerated by an order-blind comparison."""
    diffs: list[tuple[str, str, str]] = []
    if isinstance(a, dict) and isinstance(b, dict):
        for k in sorted(set(a) | set(b)):
            p = f"{path}.{k}" if path else k
            if k not in a:
                diffs.append((p, "<missing>", _short(b[k])))
            elif k not in b:
                diffs.append((p, _short(a[k]), "<missing>"))
            else:
                diffs.extend(diff_records(a[k], b[k], p))
    elif isinstance(a, list) and isinstance(b, list):
        if a != b:
            if len(a) != len(b):
                diffs.append((path, f"<list len={len(a)}>", f"<list len={len(b)}>"))
            else:
                for i, (av, bv) in enumerate(zip(a, b)):
                    if av != bv:
                        diffs.extend(diff_records(av, bv, f"{path}[{i}]"))
    else:
        if a != b:
            diffs.append((path, _short(a), _short(b)))
    return diffs


# --- main --------------------------------------------------------------------

def main() -> int:
    # Repo content (release names, tags, descriptions, ...) is arbitrary
    # untrusted text and can contain characters outside Windows' default
    # console codepage (e.g. emoji in a GitHub release name) -- reconfigure
    # stdout so a diff line never crashes the run partway through instead of
    # reporting the comparison it already computed.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--mcp-url", required=True,
                    help="Base URL of a running CodeRoot-MCP Streamable-HTTP server, "
                         "e.g. http://127.0.0.1:8300/mcp")
    ap.add_argument("--github-token-file", required=True,
                    help="Path to a file containing one GitHub PAT (used for the direct "
                         "path AND to prove the mcp path makes zero GitHub requests). "
                         "Never pass the token as a bare CLI argument.")
    ap.add_argument("--count", type=int, default=5,
                    help="How many non-drifted repos to compare (default 5, the brief's floor). "
                         "Ignored when --repo is given.")
    ap.add_argument("--candidate-limit", type=int, default=60,
                    help="How many recently-acquired repos to consider before giving up "
                         "looking for --count non-drifted ones. Ignored when --repo is given.")
    ap.add_argument("--repo", action="append", default=None,
                    help="Compare one specific already-acquired repo, as 'owner/name' "
                         "(repeatable). Bypasses the most-recently-acquired scan -- use this "
                         "to reproduce a specific finding on demand. Still subject to the "
                         "same drift check as the scan path.")
    ap.add_argument("--acquire-cache-dir", default=None,
                    help="Scratch dir for DirectSource's git cache. Defaults to a short "
                         "path under the system temp dir -- on Windows, a deeply-nested "
                         "default temp dir can exceed MAX_PATH for the .git cache "
                         "directory name; override this if git fails with "
                         "'Filename too long'.")
    args = ap.parse_args()

    token = Path(args.github_token_file).read_text(encoding="utf-8").strip()
    if not token:
        print("ERROR: github token file is empty", file=sys.stderr)
        return 2

    cache_dir = args.acquire_cache_dir or str(Path(tempfile.gettempdir()) / "cr-mcp-parity-cache")
    Path(cache_dir).mkdir(parents=True, exist_ok=True)

    direct_settings = Settings(llm_provider="none", github_tokens=token,
                               acquire_cache_dir=cache_dir, assessor_allow_anonymous=True)
    mcp_settings = Settings(llm_provider="none", coderoot_mcp_url=args.mcp_url,
                            assessor_allow_anonymous=True)
    # Structural corroboration, printed once: the mcp-path Settings carries no
    # GitHub credential at all, so McpSource has no means to make an
    # authenticated GitHub call even by accident.
    assert mcp_settings.github_token_list == [], (
        "mcp_settings unexpectedly carries a GitHub token -- the zero-request "
        "claim below would not be structurally sound")

    direct_source = wiring.build_source(direct_settings)
    mcp_source = wiring.build_source(mcp_settings)
    assert type(direct_source).__name__ == "DirectSource"
    assert type(mcp_source).__name__ == "McpSource"
    cache = NullCache()

    print(f"direct source: {type(direct_source).__name__} (github_tokens configured: "
         f"{bool(direct_settings.github_token_list)})")
    print(f"mcp    source: {type(mcp_source).__name__} -> {args.mcp_url} "
         f"(github_tokens configured: {bool(mcp_settings.github_token_list)})")
    print()

    # --- phase 1: find non-drifted candidates ---------------------------------
    if args.repo:
        candidates = fetch_specific(args.repo)
        want = len(candidates)
        print(f"targeting {want} specific repo(s): {', '.join(args.repo)}")
    else:
        candidates = fetch_candidates(args.candidate_limit)
        want = args.count
        print(f"considering {len(candidates)} most-recently-acquired repos for drift...")
    chosen, skipped = [], []
    for c in candidates:
        if len(chosen) >= want:
            break
        try:
            live_sha = github_live_head(token, c["owner"], c["name"])
        except Exception as exc:  # noqa: BLE001
            skipped.append((c, f"drift-check error: {exc!r}"))
            continue
        if live_sha is None:
            skipped.append((c, "drift-check: repo not found on GitHub"))
            continue
        if live_sha != c["stored_sha"]:
            skipped.append((c, f"HEAD drifted since acquisition (stored={c['stored_sha'][:12]} "
                               f"live={live_sha[:12]})"))
            continue
        chosen.append(c)

    for c, reason in skipped:
        print(f"  SKIP  {c['owner']}/{c['name']}: {reason}")
    if len(chosen) < want:
        hint = ("all requested --repo targets must be non-drifted" if args.repo
                else "Raise --candidate-limit.")
        print(f"\nERROR: only found {len(chosen)} non-drifted candidates among "
             f"{len(candidates)} considered; wanted {want}. {hint}",
             file=sys.stderr)
        return 2
    print(f"\nselected {len(chosen)} non-drifted repos for parity comparison:")
    for c in chosen:
        print(f"  {c['owner']}/{c['name']}  {c['repo_url']}  repo_id={c['repo_id']}  "
             f"commit={c['stored_sha'][:12]}")
    print()

    # --- phase 2: run ONLY the mcp path, under the httpx-level watch ----------
    mcp_records: dict[str, dict] = {}
    with watch_outbound_hosts() as hosts_during_mcp:
        for c in chosen:
            subject = {"repo_url": c["repo_url"], "subject_key": c["repo_id"],
                      "commit_sha": "", "subdir": ""}
            mcp_records[c["repo_id"]] = assess_handler(mcp_source, cache, mcp_settings, subject)
    mcp_github_hosts = _github_hosts(hosts_during_mcp)
    mcp_github_requests = len(mcp_github_hosts)
    print(f"httpx.Client.send calls observed during mcp-path assessments: {len(hosts_during_mcp)} "
         f"(hosts: {sorted(set(hosts_during_mcp)) or '[]'})")
    print(f"==> GitHub requests attributable to the mcp path: {mcp_github_requests}")
    print()

    # --- phase 3: run the direct path (this DOES use GitHub) ------------------
    direct_records: dict[str, dict] = {}
    with watch_outbound_hosts() as hosts_during_direct:
        for c in chosen:
            subject = {"repo_url": c["repo_url"], "subject_key": c["repo_id"],
                      "commit_sha": "", "subdir": ""}
            direct_records[c["repo_id"]] = assess_handler(direct_source, cache, direct_settings, subject)
    direct_github_hosts = _github_hosts(hosts_during_direct)
    print(f"httpx.Client.send calls observed during direct-path assessments: {len(hosts_during_direct)} "
         f"(hosts: {sorted(set(hosts_during_direct)) or '[]'})")
    print(f"==> GitHub REST (httpx) requests attributable to the direct path: {len(direct_github_hosts)} "
         f"(expected: resolve_head's 2 REST calls x {len(chosen)} repos = {2 * len(chosen)}; "
         f"the git clone itself is a separate `git` subprocess speaking the git protocol, "
         f"not an httpx call, so it is NOT counted here -- this number undercounts the "
         f"direct path's true GitHub usage, it does not overcount it)")
    if len(direct_github_hosts) == 0:
        print("WARNING: the direct path made zero observed httpx calls to GitHub -- that is "
             "unexpected (resolve_head should always call api.github.com) and would mean this "
             "instrumentation itself is not trustworthy; treat the mcp-path zero-count above "
             "with equivalent suspicion if this fires.")
    print()

    # --- phase 4: compare full records ----------------------------------------
    n_identical = 0
    results = []
    for c in chosen:
        rid = c["repo_id"]
        d, m = direct_records[rid], mcp_records[rid]
        diffs = diff_records(d, m)
        identical = not diffs
        n_identical += identical
        results.append((c, identical, diffs))
        status = "IDENTICAL" if identical else f"DIFFERS ({len(diffs)} field(s))"
        print(f"{c['owner']}/{c['name']}: {status}")
        print(f"  direct content_fingerprint: {d['content_fingerprint']}")
        print(f"  mcp    content_fingerprint: {m['content_fingerprint']}")
        print(f"  direct asset_types: {d['asset_types']}")
        print(f"  mcp    asset_types: {m['asset_types']}")
        if diffs:
            for p, av, bv in diffs:
                print(f"    DIFF {p}:")
                print(f"      direct = {av}")
                print(f"      mcp    = {bv}")
        print()

    print("=" * 72)
    print(f"SUMMARY: {n_identical}/{len(chosen)} repos byte-identical between "
         f"source=direct and source=mcp")
    print(f"GitHub requests on the mcp path: {mcp_github_requests}")
    print("=" * 72)

    return 0 if n_identical == len(chosen) and mcp_github_requests == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
