"""Commit-anchored, two-phase content acquisition (spec §Foundation A).

Resolves the default-branch commit SHA once, then fetches every allowlisted path
at that pinned ref (atomic snapshot). Phase 2 resolves the real entrypoint(s) from
the manifests. Transient GitHub failures raise ContentUnavailable (the unit retries).
"""
from __future__ import annotations

import json
import re
from collections import Counter

from ..vendored import _valid_slug
from .record import RECORD_BASENAME, RECORD_MAX_BYTES

_PHASE1 = ("README.md", "README.rst", "package.json", "pyproject.toml", "mcp.json",
           "server.json", "smithery.yaml", "smithery.json", "Dockerfile",
           "LICENSE", "LICENSE.md", "LICENSE.txt",
           "langgraph.json", "agent.json", ".well-known/agent-card.json", ".well-known/agent.json",
           "requirements.txt", "requirements-dev.txt")
_FALLBACK_ENTRYPOINTS = ("src/server.ts", "src/index.ts", "index.ts", "index.js",
                         "server.py", "main.py", "__main__.py", "src/server.py")


class ContentUnavailable(Exception):
    """A transient GitHub failure (403/429/5xx/transport) — the assess unit retries."""


class RepoGone(Exception):
    """The repo returned 404 — gone/renamed/private. Terminal for acquire (not retried)."""


def _transient(status: int) -> bool:
    return status == 0 or status == 429 or status == 403 or status >= 500


def _entrypoints(files: dict[str, str]) -> list[str]:
    eps: list[str] = []
    try:
        pkg = json.loads(files.get("package.json", "") or "{}")
    except ValueError:
        pkg = {}
    if not isinstance(pkg, dict):
        pkg = {}
    for key in ("bin", "main", "module"):
        v = pkg.get(key)
        if isinstance(v, str):
            eps.append(v)
        elif isinstance(v, dict):
            eps.extend(x for x in v.values() if isinstance(x, str))
    # Scan ONLY the [project.scripts]/[project.gui-scripts] tables (not [project.urls] etc.,
    # whose `Homepage = "https://..."` would otherwise be fetched as `https.py`).
    py = files.get("pyproject.toml", "")
    in_scripts = False
    for line in py.splitlines():
        stripped = line.strip()
        if stripped.startswith("["):
            in_scripts = stripped in ("[project.scripts]", "[project.gui-scripts]")
            continue
        if in_scripts and "=" in stripped and ":" in stripped:  # name = "module:func"
            mod = stripped.split("=", 1)[1].strip().strip('"').split(":", 1)[0].replace(".", "/")
            if mod and not mod.startswith(("http", "/")):
                eps.append(mod + ".py")
    eps.extend(_FALLBACK_ENTRYPOINTS)
    return list(dict.fromkeys(e.lstrip("./") for e in eps if e))


def resolve_head(http, owner: str, name: str) -> tuple[str, dict]:
    """Resolve the default-branch commit SHA and return the repo object.
    401 -> ContentUnavailable (bad creds; retry+surface, never a silent empty snapshot);
    404 -> RepoGone (terminal); other transient -> ContentUnavailable."""
    if not (_valid_slug(owner) and _valid_slug(name)):
        raise ContentUnavailable(f"invalid owner/name: {owner!r}/{name!r}")
    st, repo = http.get_json(f"https://api.github.com/repos/{owner}/{name}")
    if st == 404:
        raise RepoGone(f"repo {owner}/{name} not found")
    if st == 401:
        raise ContentUnavailable("bad credentials (401)")
    if _transient(st):
        raise ContentUnavailable(f"repo lookup {st}")
    repo = repo or {}
    default_branch = repo.get("default_branch", "main")
    st, commit = http.get_json(f"https://api.github.com/repos/{owner}/{name}/commits/{default_branch}")
    if _transient(st):
        raise ContentUnavailable(f"commit lookup {st}")
    sha = (commit or {}).get("sha") or default_branch
    return sha, repo


def fetch_files(http, owner: str, name: str, sha: str) -> dict[str, str]:
    """Fetch the _PHASE1 manifests + resolved entrypoints at the pinned SHA."""
    files: dict[str, str] = {}

    def _get(path):
        s, text = http.get_contents(owner, name, path, sha)
        if _transient(s):
            raise ContentUnavailable(f"contents {path} {s}")
        if text is not None:
            files[path] = text

    for path in _PHASE1:
        _get(path)
    for ep in _entrypoints(files):
        if ep not in files:
            _get(ep)
    return files


_SRC_EXTS = (".ts", ".tsx", ".js", ".mjs", ".py", ".go", ".rs", ".cs", ".java", ".kt")
_SRC_EXCLUDE = ("node_modules/", "dist/", "build/", "vendor/", "test/", "tests/", "__tests__/",
                ".venv/", "target/", "bin/", "obj/")
# Raised for marker-driven selection. Truncation was systemic, not an OpenHands quirk:
# every one of the eight largest repos in the corpus carried tree_capped=True. The added
# capacity is spent on MARKER HITS (see select_source_paths), so the extra slots carry
# evidence rather than filler.
_SRC_MAX_FILES = 120            # was 60
_SRC_MAX_BYTES = 1_600 * 1024   # was 800 * 1024

ALLOWLIST_VERSION = 8   # bump on every allowlist widening; acquire stores it and
                        # SHA-reuse requires a match so stale snapshots refetch (§4.2)
                        # 4->5: dependency manifests in subdirs + non-Python/JS ecosystems.
                        # 5->6: marker-driven selection + raised source caps.
                        # 6->7: agent_run_shape v3 co-occurrence changes marker-driven selection.
                        # 7->8: asset-record.json budget-neutral selection (authoring MCP spec §6).
_AGENT_MANIFESTS = ("agents.yaml", "tasks.yaml", "langgraph.json", "agent.json", "agent-card.json")
_MANIFEST_MAX_FILES, _MANIFEST_MAX_BYTES = 8, 64 * 1024

# Dependency manifests matched by BASENAME anywhere in the tree (same mechanism as
# _AGENT_MANIFESTS). Root-exact _PHASE1 matching made the bespoke-agent dep gate
# structurally unreachable for monorepos without a root manifest and for every
# non-Python/JS ecosystem — see docs/superpowers/plans/2026-07-25-agent-candidate-egress-and-recall.md.
_DEP_MANIFESTS = ("package.json", "pyproject.toml", "requirements.txt", "requirements-dev.txt",
                  "go.mod", "Cargo.toml", "pom.xml", "build.gradle", "build.gradle.kts",
                  "Gemfile", "composer.json")
_DEP_MANIFEST_SUFFIXES = (".csproj",)   # C# project files are named per-project


def _is_dep_manifest(path: str) -> bool:
    base = path.rsplit("/", 1)[-1]
    return base in _DEP_MANIFESTS or base.endswith(_DEP_MANIFEST_SUFFIXES)


def dep_manifest_paths(paths) -> list[str]:
    """The dependency manifests in `paths`, using the SAME basename + excluded-dir
    rule the acquire pre-passes use. Run over the stored tree inventory and over the
    acquired content, the two counts differ exactly when the dep pre-pass truncated —
    which is how `assemble` states that honestly (dep truncation deliberately does
    NOT flip `capped`; see the pre-pass comments below)."""
    return [p for p in paths
            if _is_dep_manifest(p) and not any(x in p.lower() for x in _SRC_EXCLUDE)]

# SKILL.md bodies (basename-anywhere, e.g. `SKILL.md` or `skills/<name>/SKILL.md`) so the
# `skill` classifier can read their YAML frontmatter; `dataset_infos.json` reserved for
# future dataset work. Neither is a `_SRC_EXTS` extension, so without this pre-pass
# `_is_source` would never select them.
_NEWTYPE_MANIFESTS = ("SKILL.md", "dataset_infos.json")
_SKILL_MAX_FILES = 40

# asset-record.json (spec 2026-08-09-authoring-mcp §7 channel 4): selected OUTSIDE
# every shared budget — never counts toward total/mtotal/src_files, never sets
# `capped` (which asserts SOURCE truncation), never displaces any other file.
# Oversized bodies are skipped (the reader would reject them anyway); overflow
# beyond the file cap is dropped silently — matches `_SKILL_MAX_FILES` (a legitimate
# multi-asset monorepo can legitimately have >20 subdir records, so the cap isn't
# about rejecting that case). The per-file RECORD_MAX_BYTES cap bounds the worst
# case at 640KiB (40 * 16KiB) of budget-neutral reads.
_RECORD_MAX_FILES = 40

# Tool-definition trees (e.g. a TS MCP server's src/tools/*.ts, spread across many
# per-tool subdirectories) and package/module entrypoints (e.g. a monorepo package's
# packages/<name>/src/index.ts) routinely lose the density sort to a single denser,
# non-tool directory (a big lib/ or common/ dir). Both are selected ahead of the
# density-sorted rest so they can't be cap-dropped by a denser sibling.
_TOOL_PATH_RE = re.compile(r'(^|/)tools?/')
_ENTRY_BASENAMES = ("index.ts", "index.js", "index.mjs", "server.ts", "server.py",
                    "main.py", "__main__.py")


def _is_source(path: str) -> bool:
    low = path.lower()
    if not low.endswith(_SRC_EXTS):
        return False
    return not (any(x in low for x in _SRC_EXCLUDE)
                or ".test." in low or ".spec." in low or low.endswith("_test.go"))


def fetch_source_subtree(http, owner: str, name: str, sha: str, *, skip=frozenset()) -> tuple[dict[str, str], bool]:
    """Fetch a bounded set of source files (so tool/transport/auth scanning sees the
    real registration sites). Returns (files, capped): capped=True iff the git tree was
    truncated or the file/byte cap dropped candidate source files."""
    st, tree = http.get_json(f"https://api.github.com/repos/{owner}/{name}/git/trees/{sha}?recursive=1")
    if _transient(st):
        raise ContentUnavailable(f"git tree {st}")
    if st != 200 or not isinstance(tree, dict):
        return {}, False
    if tree.get("truncated"):
        return {}, True                                     # can't enumerate reliably -> honestly capped
    blobs = [e for e in tree.get("tree", [])
             if e.get("type") == "blob" and isinstance(e.get("path"), str)
             and e["path"] not in skip and _is_source(e["path"])]
    # Prefer files in the densest source directory (the implementation concentrates there — e.g.
    # a Go server's pkg/github/*.go, a TS server's src/tools/*.ts), then smaller files first.
    # Size-ascending alone drops the larger tool-definition files on big repos and finds nothing.
    _dir = lambda p: p.rsplit("/", 1)[0] if "/" in p else ""
    density = Counter(_dir(e["path"]) for e in blobs)
    blobs.sort(key=lambda e: (-density[_dir(e["path"])], e.get("size") or 0))
    files: dict[str, str] = {}
    total, capped = 0, False
    # Manifest pre-pass (§4.2): basename-anywhere, before the density-ordered loop, so
    # low-density config/ dirs can't be cap-dropped. Counted toward the byte cap.
    manifests = [e for e in tree.get("tree", [])
                 if e.get("type") == "blob" and isinstance(e.get("path"), str)
                 and e["path"] not in skip
                 and e["path"].rsplit("/", 1)[-1] in _AGENT_MANIFESTS
                 and not any(x in e["path"].lower() for x in _SRC_EXCLUDE)]
    mtotal = 0
    for e in manifests[:_MANIFEST_MAX_FILES]:
        size = e.get("size") or 0
        if mtotal + size > _MANIFEST_MAX_BYTES:
            capped = True
            break
        s, text_body = http.get_contents(owner, name, e["path"], sha)
        if _transient(s):
            raise ContentUnavailable(f"contents {e['path']} {s}")
        if text_body is not None:
            files[e["path"]] = text_body
            mtotal += size
            total += size
    if len(manifests) > _MANIFEST_MAX_FILES:
        capped = True
    # Everything selected so far counts against the SOURCE caps (pre-existing
    # behavior). The dep pre-pass below deliberately does not — see its comment.
    src_files = len(files)
    # Dependency-manifest pre-pass, AFTER the agent-manifest pass so the cap drops
    # advisory dep manifests rather than classification-bearing agent manifests.
    # Dropping one deliberately does NOT set `capped`: `capped` is the SOURCE-coverage
    # signal and downstream it means "the source scan was truncated" (mcp
    # `tools_complete=False`, "agent manifests may be missing"). Dep manifests are
    # advisory input to the bespoke-agent probe, and the pass runs last, so neither
    # claim would be true. Truncation is still stated — `assemble` compares the tree
    # inventory to the acquired content via `dep_manifest_paths` and emits a specific
    # dependency-manifest known-unknown.
    # BUDGET ISOLATION: dep manifests are bounded ONLY by their own file cap and the
    # shared manifest byte budget (`mtotal`/`_MANIFEST_MAX_BYTES`) — exactly the way
    # `mtotal` already isolates the manifest byte budget. They must never consume
    # `_SRC_MAX_FILES` slots or `_SRC_MAX_BYTES`: an advisory manifest evicting a real
    # source file both degrades tool extraction AND (via `capped`) makes us claim the
    # source scan was truncated when it wasn't.
    deps = [e for e in tree.get("tree", [])
            if e.get("type") == "blob" and isinstance(e.get("path"), str)
            and e["path"] not in skip and e["path"] not in files
            and _is_dep_manifest(e["path"])
            and not any(x in e["path"].lower() for x in _SRC_EXCLUDE)]
    # shallowest-first, then path: which manifests survive the cap must not depend on
    # ls-tree ordering (mirrors how `_PHASE1` privileges root manifests).
    deps.sort(key=lambda e: (e["path"].count("/"), e["path"]))
    for e in deps[:_MANIFEST_MAX_FILES]:
        size = e.get("size") or 0
        if mtotal + size > _MANIFEST_MAX_BYTES:
            break
        s, text_body = http.get_contents(owner, name, e["path"], sha)
        if _transient(s):
            raise ContentUnavailable(f"contents {e['path']} {s}")
        if text_body is not None:
            files[e["path"]] = text_body
            mtotal += size                  # NOT `total` — dep bytes are budget-isolated
    for e in blobs:
        size = e.get("size") or 0
        if src_files >= _SRC_MAX_FILES or total + size > _SRC_MAX_BYTES:
            capped = True
            break
        s, text = http.get_contents(owner, name, e["path"], sha)
        if _transient(s):
            raise ContentUnavailable(f"contents {e['path']} {s}")
        if text is not None:
            files[e["path"]] = text
            src_files += 1
            total += size
    return files, capped


def fetch(http, owner: str, name: str) -> dict:
    """Back-compat combined fetch (SHA + files)."""
    sha, _ = resolve_head(http, owner, name)
    return {"commit_sha": sha, "files": fetch_files(http, owner, name, sha)}


# --- Pure, git-agnostic selection helpers (design spec §3) ---------------------
#
# Both operate over a *candidate map* `dict[path, (blob_sha, size)]` — the set of
# paths a `GitContentFetcher` can actually read locally after its git fetch (see
# `assessment/git_fetch.py`). Neither touches git or HTTP. These are the LIVE
# selection path (the REST `fetch_files`/`fetch_source_subtree` above are dead —
# `acquire` fetches only via `GitContentFetcher`). NOTE: `select_source_paths` gained
# the tool/entry-file priority pass (§4 tool-extraction) which the dead REST
# `fetch_source_subtree` does NOT have, so the two are no longer strictly file-set
# equivalent — the git path is authoritative; the REST path would need the same pass
# folded in before any reactivation.


def _candidate_size(candidates: dict, path: str) -> int:
    v = candidates[path]
    return v[1] if isinstance(v, tuple) else v


def select_manifest_and_entrypoint_paths(candidates: dict, manifest_texts: dict[str, str]) -> list[str]:
    """`_PHASE1` paths present in `candidates`, plus `_entrypoints(manifest_texts)`
    present in `candidates` — entrypoints included regardless of `_is_source` (mirrors
    `fetch_files`: entrypoints like `dist/server.js` live in excluded dirs)."""
    selected = [p for p in _PHASE1 if p in candidates]
    seen = set(selected)
    for ep in _entrypoints(manifest_texts):
        if ep in candidates and ep not in seen:
            selected.append(ep)
            seen.add(ep)
    return selected


def select_source_paths(candidates: dict, *, skip=frozenset(), hits=()) -> tuple[list[str], bool]:
    """Mirrors `fetch_source_subtree`'s density-sort + caps + `_AGENT_MANIFESTS`
    pre-pass, over a candidate map instead of a REST git-tree listing. Tool-path
    (`_TOOL_PATH_RE`) and entry-basename (`_ENTRY_BASENAMES`) candidates are moved
    ahead of the density sort so a large, thinly-spread tool tree or a package's
    entrypoint can't be evicted by a denser unrelated sibling directory (spec §4).

    `skip` (paths already selected by `select_manifest_and_entrypoint_paths`) is
    excluded from BOTH the manifest pre-pass and the density-sorted source loop —
    the load-bearing dedup (spec §3): `_FALLBACK_ENTRYPOINTS` routinely injects real
    source files (`src/server.py`, `index.js`, …) into the manifest+entrypoint set,
    and top-level `langgraph.json`/`agent.json` are in both `_PHASE1` and
    `_AGENT_MANIFESTS`. Without the dedup a source-file entrypoint consumes a cap
    slot/byte budget and manifests double-count, dropping a different tail file than
    REST would.

    A terminal pass then adds any asset-record.json candidates (`_RECORD_MAX_FILES`,
    `RECORD_MAX_BYTES`) outside every budget above — see the comment at their
    definitions for why that pass never counts toward SOURCE totals or `capped`.
    """
    blob_paths = [p for p in candidates if p not in skip and _is_source(p)]

    def _is_priority(p: str) -> bool:
        return bool(_TOOL_PATH_RE.search(p)) or p.rsplit("/", 1)[-1] in _ENTRY_BASENAMES

    # Tool-path / entry-file candidates win selection over the density-sorted rest
    # (spec §4): sorted tool-path matches first, then entry-file-only matches, each
    # group by size ascending -- so they can't be evicted by a denser sibling dir.
    priority_paths = [p for p in blob_paths if _is_priority(p)]
    rest_paths = [p for p in blob_paths if not _is_priority(p)]
    priority_paths.sort(key=lambda p: (0 if _TOOL_PATH_RE.search(p) else 1,
                                        _candidate_size(candidates, p)))

    # Marker-driven promotion (spec §4 Stage 1), ADDITIVE by construction: the tool/entry
    # reservation above keeps its ordering, and hit paths are merely lifted out of the
    # density-sorted `rest_paths` so the capacity added by the raised cap buys EVIDENCE
    # rather than whichever files a dense unrelated directory happened to contribute.
    #
    # D7 tiebreak: agent-class hits go AHEAD of the tool reservation. Agents are the
    # priority type, and when a budget genuinely cannot hold both, agent evidence is what
    # this change exists to capture. Provider-identifier hits stay BEHIND the reservation
    # so they can never regress MCP tool extraction.
    hit_paths = [h["path"] for h in hits if h.get("path") in candidates]
    agent_hits = [p for p, h in ((h["path"], h) for h in hits)
                  if p in candidates
                  and h.get("marker_class") in ("agent_run_shape", "agent_host_config")]
    other_hits = [p for p in hit_paths if p not in agent_hits]

    def _lift(names):
        """Pull `names` (in order, deduped) out of rest_paths, preserving the rest."""
        wanted = [p for p in dict.fromkeys(names) if p in rest_paths]
        for p in wanted:
            rest_paths.remove(p)
        return wanted

    lifted_agent = _lift(agent_hits)
    lifted_other = _lift(other_hits)

    # BOUND on the D7 lift: it is a tiebreak, not a takeover. Agent-class hits can name up to
    # `git_fetch._MAX_MARKER_HITS` (~500) distinct paths, so an UNBOUNDED lift could occupy
    # all `_SRC_MAX_FILES` slots ahead of the reservation and evict every tool/entrypoint file
    # on an mcp_server repo that also ships agent code — the one regression spec §5 forbids
    # outright ("the 12 mcp_server repos keep their primary_type and tool counts").
    # A THIRD of the cap is the bound: establishing agent run-shape needs a handful of files,
    # not forty, so this is far above any real repo's need, while the remaining two thirds
    # still comfortably hold the tool reservation (30 files on the densest corpus repo) plus
    # the density-sorted rest. Overflow is NOT dropped — it is demoted to just behind the
    # reservation, where it still outranks the density sort, so evidence is only ever
    # reordered, never lost.
    # The bound is on BOTH files and bytes. A file bound alone does not hold: blobs are
    # fetched up to 1MiB each, so a handful of large lifted files can exhaust
    # `_SRC_MAX_BYTES` while sitting far inside the 40-file cap — and the selection loop
    # below `break`s on byte overflow, so the reservation that follows would never be
    # reached at all. Measured on the real corpus (2026-08-06): goose already spends 43%
    # of the source budget on 10 lifted files, one of them 212KB. Same third-of-the-budget
    # reasoning as the file cap, and the same disposal — overflow is DEMOTED behind the
    # reservation, never dropped, so evidence is only ever reordered.
    _agent_lift_cap = _SRC_MAX_FILES // 3
    _agent_lift_bytes = _SRC_MAX_BYTES // 3
    _kept, agent_overflow, _lift_bytes = [], [], 0
    for _p in lifted_agent:
        _size = _candidate_size(candidates, _p)
        # Demote (not `break`) so one oversized file costs only itself its priority slot.
        if len(_kept) >= _agent_lift_cap or _lift_bytes + _size > _agent_lift_bytes:
            agent_overflow.append(_p)
        else:
            _kept.append(_p)
            _lift_bytes += _size
    lifted_agent = _kept
    priority_paths = lifted_agent + priority_paths + agent_overflow + lifted_other

    _dir = lambda p: p.rsplit("/", 1)[0] if "/" in p else ""
    density = Counter(_dir(p) for p in rest_paths)
    rest_paths.sort(key=lambda p: (-density[_dir(p)], _candidate_size(candidates, p)))
    blob_paths = priority_paths + rest_paths

    manifest_paths = [p for p in candidates
                       if p not in skip
                       and p.rsplit("/", 1)[-1] in _AGENT_MANIFESTS
                       and not any(x in p.lower() for x in _SRC_EXCLUDE)]

    # SKILL.md / dataset_infos.json pre-pass (basename-anywhere, excluded-dir guarded),
    # selected regardless of `_is_source` (they're .md/.json, not source extensions).
    # Own file cap (`_SKILL_MAX_FILES`), but shares the manifest-pass byte budget
    # (`mtotal`/`_MANIFEST_MAX_BYTES`) below.
    skill_paths = [p for p in candidates
                   if p not in skip
                   and p.rsplit("/", 1)[-1] in _NEWTYPE_MANIFESTS
                   and not any(x in p.lower() for x in _SRC_EXCLUDE)]

    # Dependency-manifest pre-pass (basename-anywhere, excluded-dir guarded), selected
    # regardless of `_is_source`. Deliberately LAST of the three manifest passes: these
    # are advisory input to the bespoke-agent probe, whereas agent/skill manifests drive
    # real classification — so when the shared manifest byte budget binds it is dep
    # manifests that get dropped.
    # BUDGET ISOLATION: dep manifests are bounded ONLY by their own file cap
    # (`_MANIFEST_MAX_FILES`) and the shared manifest byte budget (`mtotal` /
    # `_MANIFEST_MAX_BYTES`). They must NEVER consume `_SRC_MAX_FILES` slots or
    # `_SRC_MAX_BYTES` — an advisory manifest evicting a real source file degrades tool
    # extraction (the highest-volume type) and would flip `capped`, which asserts SOURCE
    # truncation. That would be false here (a plain 9-workspace JS monorepo would
    # otherwise report mcp `tools_complete=False` and "agent manifests may be missing").
    # The honest signal is `assemble`'s dependency-manifest known-unknown, derived by
    # comparing `dep_manifest_paths` over the tree inventory against the acquired content.
    dep_paths = [p for p in candidates
                 if p not in skip
                 and p not in manifest_paths and p not in skill_paths
                 and _is_dep_manifest(p)
                 and not any(x in p.lower() for x in _SRC_EXCLUDE)]
    # shallowest-first, then path: which manifests survive the cap must not depend on
    # candidate-map ordering (mirrors how `_PHASE1` privileges root manifests).
    dep_paths.sort(key=lambda p: (p.count("/"), p))

    selected: list[str] = []
    total = 0
    capped = False

    mtotal = 0
    for p in manifest_paths[:_MANIFEST_MAX_FILES]:
        size = _candidate_size(candidates, p)
        if mtotal + size > _MANIFEST_MAX_BYTES:
            capped = True
            break
        selected.append(p)
        mtotal += size
        total += size
    if len(manifest_paths) > _MANIFEST_MAX_FILES:
        capped = True

    for p in skill_paths[:_SKILL_MAX_FILES]:
        size = _candidate_size(candidates, p)
        if mtotal + size > _MANIFEST_MAX_BYTES:
            capped = True
            break
        selected.append(p)
        mtotal += size
        total += size
    if len(skill_paths) > _SKILL_MAX_FILES:
        capped = True

    # Everything selected so far counts against the SOURCE caps (pre-existing
    # behavior). The dep pre-pass below deliberately does not — see its comment above.
    src_files = len(selected)

    for p in dep_paths[:_MANIFEST_MAX_FILES]:
        size = _candidate_size(candidates, p)
        if mtotal + size > _MANIFEST_MAX_BYTES:
            break
        selected.append(p)
        mtotal += size                      # NOT `total` — dep bytes are budget-isolated

    for p in blob_paths:
        size = _candidate_size(candidates, p)
        if src_files >= _SRC_MAX_FILES or total + size > _SRC_MAX_BYTES:
            capped = True
            break
        selected.append(p)
        src_files += 1
        total += size

    record_paths = sorted(
        p for p in candidates
        if p.rsplit("/", 1)[-1] == RECORD_BASENAME
        and p not in skip and p not in selected
        and not any(x in p.lower() for x in _SRC_EXCLUDE)
        and _candidate_size(candidates, p) <= RECORD_MAX_BYTES)
    selected.extend(record_paths[:_RECORD_MAX_FILES])
    return selected, capped
