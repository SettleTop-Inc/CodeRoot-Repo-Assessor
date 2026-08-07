# Final fix wave — CodeRoot-Repo-Assessor, `feat/standalone-service`

Applied against the whole-branch adversarial review at
`final-review.md` (reviewed HEAD `43edb70`). Baseline before this pass:
**481 passed, 2 skipped**.

All commands below were run for real, in this repo, with `.venv/Scripts/python.exe`
(never bare `python`). Output is pasted verbatim except where noted as truncated.

---

## C1 (Critical) — ported the real HTTP adapter, wired it, and proved it end-to-end

### The bug, reproduced first

```
$ .venv/Scripts/python.exe -c "..."
AttributeError: 'Client' object has no attribute 'get_json'
```

Confirmed via a full `create_app()` → `TestClient` → `/v1/assess` call before any fix
landed; traceback bottomed out at `assessor/assessment/content.py:73` exactly as the
review described.

### Fix

- **`assessor/http_client.py` (new)** — `HttpClient` ported from
  `D:/Development/SettleTop/CodeRoot-OpenSource/.claude/worktrees/unruffled-borg-58b7bf/service/coderoot_oss/clients.py`.
  Kept verbatim: `__init__`, `_github_token`, `_pick`, `_auth`, `_note_limit`
  (the `_blocked` bench-on-exhaustion logic), `get_json`, `get_contents`,
  `close`, and the module-level `_json_or_none`. Dropped: `GitClient` (pulls
  in CodeRoot's `Inventory`/`repo_url` host-allowlist — this service has its
  own in `assessment/git_fetch.py`), `post_json` (no caller here),
  `post_raw`/`_headers` (only `post_raw`'s caller, also absent). Imports
  `_valid_slug`/`_decode_contents` from the already-ported `assessor/vendored.py`
  instead of re-defining them.
- **`assessor/wiring.py` (new)** — one `build_direct_source(settings)` function
  that builds `HttpClient(github_tokens=s.github_token_list, ...)` +
  `GitContentFetcher(...)` + `DirectSource(...)`. Shared by both entrypoints so
  a second surface can't reintroduce C1 by duplicating the wiring.
- **`assessor/app.py`** — `create_app()` now calls `build_direct_source(s)`
  instead of constructing a bare `httpx.Client`.
- **`assessor/mcp_server.py`** — new `create_mcp()` factory uses the same
  `build_direct_source`, so the MCP stdio entrypoint (added for minor #2, see
  below) doesn't get its own copy of the bug.

### The test — `tests/test_http_client.py` (new, 7 tests)

Drives `DirectSource.acquire()` through the real, unmodified `HttpClient`
against a `httpx.MockTransport`-stubbed transport — the class of test the repo
had none of. Covers: the production wiring shape end-to-end (SHA-reuse path,
so no git fetch is needed to isolate the HTTP call); a real 404 → `RepoGone`
mapping; `get_contents` base64 decode; a transport error → `(0, None)`; `_pick`
round-robin in isolation; `wiring.build_direct_source` loading the *whole*
token pool (I1); and two consecutive GitHub calls using two different tokens.

```
$ .venv/Scripts/python.exe -m pytest -q tests/test_http_client.py -v
============================= test session starts =============================
collected 7 items

tests\test_http_client.py .......                                        [100%]

============================== 7 passed in 1.60s ==============================
```

### Revert verification (both layers)

**1. Adapter-level revert** — reproduced the exact original defect through the
real `create_app()` (not a test double):

```
$ ASSESSOR_API_TOKEN=changeme .venv/Scripts/python.exe -c "
from assessor.app import create_app
from fastapi.testclient import TestClient
app = create_app()
c = TestClient(app)
r = c.post('/v1/assess', json={...octocat/Hello-World...}, headers={'authorization':'Bearer changeme'})
"
...
  File "assessor\assessment\content.py", line 73, in resolve_head
    st, repo = http.get_json(f"https://api.github.com/repos/{owner}/{name}")
AttributeError: 'Client' object has no attribute 'get_json'
```

**2. Wiring-level revert** — temporarily rewrote `assessor/wiring.py`'s
`build_direct_source` to build a bare `httpx.Client` again (the literal
pre-fix code), then ran the new wiring-sensitive test:

```
$ .venv/Scripts/python.exe -m pytest -q tests/test_http_client.py::test_wiring_loads_the_whole_configured_pool_not_just_the_first_token -v
FAILED tests/test_http_client.py::test_wiring_loads_the_whole_configured_pool_not_just_the_first_token
AssertionError: assert isinstance(src.http, HttpClient)
  where <httpx.Client object at 0x...> = <DirectSource ...>.http
```

`assessor/wiring.py` was then restored (`mv wiring.py.orig wiring.py`) and the
full suite re-confirmed green (488 passed at that point, before the handlers
test was added).

### Real, live, authenticated request — not a mock

Ran the actual production factory against the real `octocat/Hello-World` repo
on GitHub, no tokens configured (anonymous rate limit, which is fine for one
request):

```
$ ASSESSOR_API_TOKEN=changeme ACQUIRE_CACHE_DIR=/tmp/assessor-live-cache .venv/Scripts/python.exe -c "..."
status: 200
{
  "is_asset": false,
  "asset_type": "not_an_asset",
  "asset_types": [],
  "content_fingerprint": "7cd0c842523a01e08f6240f4794495e21502e180ecae259be7c94dd6f247ce9b"
}
```

```
$ ... c.post('/v1/acquire', json={'repo_url':'https://github.com/octocat/Hello-World','prior':None}, ...)
acquire status: 200
['status', 'commit_sha', 'metadata', 'allowlist_version', 'snapshot']
```

Both the exact README-quickstart request shape and `/v1/acquire` now succeed
against a real repository through the unmodified production factory.

---

## I1 — token pool: confirmed fixed, rotation tested

`wiring.build_direct_source` passes `github_tokens=s.github_token_list` (the
whole list) to `HttpClient`, not `[0]`. `HttpClient._pick`/`_auth` round-robin
over the pool (verbatim ported logic — see C1). Covered by three of the seven
tests in `tests/test_http_client.py`:

- `test_pick_round_robins_across_the_whole_pool_not_just_index_zero` — direct
  `_pick()` sequence assertion: `[0, 1, 2, 0, 1, 2]` for a 3-token pool.
- `test_wiring_loads_the_whole_configured_pool_not_just_the_first_token` —
  `build_direct_source(...).http._tokens == ["tok-a", "tok-b", "tok-c"]`.
- `test_two_consecutive_github_calls_use_different_tokens` — drives
  `resolve_head` through the real adapter and asserts its two GitHub calls
  (repo lookup, commit lookup) carried `Bearer tok-a` then `Bearer tok-b`.

All three pass (see the 7/7 run above); the wiring-revert failure above is
also direct evidence I1 is exercised, not just C1.

---

## I2 — README amended, not the MCP tool signature

Per the review's own "considered and dismissed" verdict (the em-dash clause in
the old sentence technically held), took the cheaper, honest fix: amended
`README.md`'s MCP section to state precisely what mirrors (handlers +
typed-error mapping) and to say plainly that `prior`/incremental re-acquire is
HTTP-only today. `acquire_repository`'s signature is unchanged — the capability
gap stays recorded, as instructed.

---

## I3 — Dockerfile now installs from `uv.lock`, verified by a real build + run

```dockerfile
COPY pyproject.toml uv.lock ./
COPY assessor ./assessor
RUN uv export --locked --no-dev --no-hashes --no-emit-project -o requirements.txt \
    && uv pip install --system --no-cache -r requirements.txt \
    && uv pip install --system --no-cache --no-deps .
```

`--locked` fails the build if `uv.lock` has drifted from `pyproject.toml`
(same guarantee as CI's `uv sync --locked`). `--no-emit-project` + a separate
`--no-deps .` install keeps every dependency pinned to its exact locked
version while installing the local package itself without a re-resolve.
Verified the export works against this repo's real lock:

```
$ uv export --locked --no-dev --no-hashes --no-emit-project
Resolved 43 packages in 1ms
# This file was autogenerated by uv via the following command:
#    uv export --locked --no-dev --no-hashes --no-emit-project
annotated-doc==0.0.5
...
```

Then actually built and ran the image:

```
$ docker build -t coderoot-repo-assessor:fixtest .
...
#11 3.159  + coderoot-repo-assessor==0.1.0 (from file:///app)
#14 DONE 1.8s

$ docker run -d --rm --name assessor-fixtest -p 18081:8081 -e ASSESSOR_API_TOKEN=changeme coderoot-repo-assessor:fixtest
$ curl -s http://localhost:18081/healthz
{"status":"ok"}

$ curl -s -X POST http://localhost:18081/v1/assess -H 'content-type: application/json' \
    -H 'authorization: Bearer changeme' -d '{"subject":{"repo_url":"https://github.com/octocat/Hello-World",...}}'
{"is_asset":false,"asset_type":"not_an_asset",...,"assessed_commit":{"value":"7fd1a60b01f91b314f59955a4e4d4e80d8edf11d",...}}
```

That's the *built image* — from the lock, not a live `pip resolve` — serving a
real authenticated request end to end. Also re-confirmed the CI fail-closed
gate still holds against the new image:

```
$ timeout 15s docker run --rm coderoot-repo-assessor:fixtest
...
assessor.config.ConfigError: refusing to start unauthenticated: set ASSESSOR_API_TOKEN, or set ASSESSOR_ALLOW_ANONYMOUS=true to opt out deliberately
```

Cleaned up the test container/image afterward (`docker stop`, `docker rmi`).

---

## Before-merge minors

**#1 — README "Honesty" overstatement.** Did **not** touch
`assessor/assessment/versions.py` (moved, behaviour-preserved). Amended the
README sentence to state the real behavior: `release_count` is emitted as a
**Fact of `0`**, not a `known_unknown`, when `metrics()` is `None` — a narrow,
named gap in that one field, distinct from `latest_release`/assessment history
which do become honest `known_unknown`s.

**#2 — `build_mcp` had no runner.** Checked the installed `mcp==2.0.0` API
directly rather than guessing: `MCPServer.run(transport="stdio")` (default
`"stdio"`) exists and calls `anyio.run(self.run_stdio_async)`, which opens
`mcp.server.stdio.stdio_server()`. Added `create_mcp()` (factory, mirrors
`app.create_app()`) and `main()` to `assessor/mcp_server.py`, plus
`[project.scripts] coderoot-repo-assessor-mcp = "assessor.mcp_server:main"` in
`pyproject.toml`. Verified both the module entrypoint and the installed
console script start clean and exit clean on stdin EOF:

```
$ ASSESSOR_API_TOKEN=x timeout 8 .venv/Scripts/python.exe -m assessor.mcp_server < /dev/null
exit: 0

$ .venv/Scripts/python.exe -m pip install -e . --no-deps -q
$ ls .venv/Scripts | grep coderoot
coderoot-repo-assessor-mcp.exe

$ ASSESSOR_API_TOKEN=x timeout 8 .venv/Scripts/coderoot-repo-assessor-mcp.exe < /dev/null
exit: 0
```

Also re-ran `uv sync --locked --all-extras` after adding `[project.scripts]`
to confirm it doesn't desync the lock — it doesn't (script entries aren't part
of the dependency graph):

```
$ uv sync --locked --all-extras
Resolved 43 packages in 1ms
...
 ~ coderoot-repo-assessor==0.1.0 (from file:///D:/Development/SettleTop/CodeRoot-Repo-Assessor)
```

**#3 — `handlers.py` metrics wiring had no regression test.** Added
`test_metrics_license_and_releases_reach_assemble_build` to
`tests/test_handlers_assess.py`. Verified it actually catches the mutation the
review described: typo'd both keys (`"license"→"licence"`,
`"releases"→"release"`) in `assessor/handlers.py`, reran just the new test —
it failed (`AssertionError: assert None == 'MIT'`) — then restored the file
(confirmed via `git diff assessor/handlers.py` → empty) and reran the full
suite green.

---

## Final verification

```
$ .venv/Scripts/python.exe -m pytest -q
........................................................................ [ 14%]
........................................................................ [ 29%]
........................................................................ [ 43%]
........................................................................ [ 58%]
........................................................................ [ 73%]
........................................................................ [ 87%]
....................................ss.....................              [100%]
489 passed, 2 skipped in 8.86s

$ uv run pytest -q      # exact CI invocation
489 passed, 2 skipped in 7.59s
```

489 = 481 baseline + 7 (`tests/test_http_client.py`) + 1
(`test_metrics_license_and_releases_reach_assemble_build`). The 2 skips are
pre-existing (`network`/`corpus` markers, unrelated to this pass).

`git diff --stat -- assessor/assessment assessor/llm` is empty — neither
directory was touched, per the brief.

## Files touched

- `assessor/http_client.py` (new) — ported `HttpClient` adapter
- `assessor/wiring.py` (new) — shared production `DirectSource` factory
- `assessor/app.py` — `create_app()` now uses `wiring.build_direct_source`
- `assessor/mcp_server.py` — `create_mcp()` + stdio `main()`
- `pyproject.toml` — `[project.scripts]` console entry
- `Dockerfile` — installs from `uv.lock` via `uv export --locked`
- `README.md` — MCP mirroring claim, `prior` capability gap, stdio run
  instructions, Honesty section's `release_count` claim
- `tests/test_http_client.py` (new) — 7 tests: C1 wiring + I1 rotation
- `tests/test_handlers_assess.py` — 1 new test: metrics→assemble.build wiring

## Disagreements / things I'd flag back

- **None on scope.** Every fix matches what the review asked for; I didn't
  find a place where I disagreed with the review's diagnosis or prescribed
  fix.
- **One judgment call worth surfacing:** for I3 I chose `uv export --locked
  ... | uv pip install --system` over `uv sync --locked --no-dev` to preserve
  the image's existing no-venv, direct-`uvicorn` layout with a minimal diff.
  `uv sync` was the other option the review explicitly named as acceptable; if
  a project-venv layout is preferred going forward, that's a bigger Dockerfile
  change (CMD would need to invoke through the venv or `uv run`) than this
  pass made.
- **Docstring in `README.md`'s Honesty section is now longer than ideal.** I
  kept it accurate (stating the real Fact-of-0 gap) rather than terse; a
  future pass could shorten it once the underlying `versions.py` behavior is
  actually revisited (explicitly out of scope here).
