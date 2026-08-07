"""`GitContentFetcher` (design spec §2/§3/§9): replaces `acquire`'s REST
Contents-API burst (~60 calls/repo — trips GitHub's secondary rate limit) with
ONE git fetch of the pinned commit + purely-local reads.

Reuses `scan.git_disk_scan`'s hardening pattern (`_HARDENING`/`_ENV`, host/SHA/
repo-key validation, a per-repo bare cache) but is a net-new module: scan only
ever runs `rev-list`/`log` + a content-free `cat-file -e`. This module adds the
present-object size enumeration, the `ls-tree` candidate-map build, and — the
genuinely new piece — a **byte-framed** `cat-file --batch` reader. Scan's
`_run_git` text-decodes the whole stdout stream, which is fine for line-oriented
log output but would corrupt a multi-line file body read via `cat-file --batch`
(the framing is `<sha> <type> <size>\\n` + exactly `<size>` raw bytes + `\\n`, and
naive newline-splitting truncates at the body's first embedded newline). So the
batch reader runs git in raw-bytes mode (no `text=True`) and parses by declared
length instead.
"""
from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING, Callable, Protocol, runtime_checkable

from . import content
from . import markers
from ..vendored import _REPO_KEY_RE, _SHA_RE

_ALLOWED_HOSTS = frozenset({"github.com"})

# Same hardening as GitDiskScanner (no local/ext transports, no credential prompts,
# no background gc) plus GIT_NO_LAZY_FETCH=1: any access to an object the partial
# fetch didn't bring down (e.g. a >1MiB blob filtered by blob:limit) must hard-error
# rather than silently reaching the network for it.
_HARDENING = [
    "-c", "protocol.file.allow=never",
    "-c", "protocol.ext.allow=never",
    "-c", "credential.helper=",
    "-c", "core.askpass=",
    "-c", "gc.auto=0",
]
_ENV = {
    **os.environ,
    "GIT_TERMINAL_PROMPT": "0",
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_ASKPASS": "",
    "GIT_NO_LAZY_FETCH": "1",
}

DEFAULT_BLOB_LIMIT = 1_048_576      # 1 MiB — exactly REST Contents' >1MB cutoff (§2 step 1)
DEFAULT_TIMEOUT_S = 600
DEFAULT_MAX_TREE_ENTRIES = 200_000  # bounds ls-tree/batch-check memory on a hostile repo (§6)
_TREE_PATHS_CAP = 20_000            # bounds the full-tree path inventory returned to callers

# Bounds the marker pre-scan pass (Task 5 fix round 1): purely local `cat-file --batch`
# reads over a pack already on disk from the partial fetch above, so this costs no extra
# network or git call, only local IO/CPU on a hostile-or-huge repo's candidate list.
_SCAN_MAX_BYTES = 8 * 1024 * 1024
# Companion bound to _SCAN_MAX_BYTES: the byte budget alone doesn't stop a repo of very
# many tiny source files from pushing up to `max_entries` (200,000) shas into a single
# `cat-file --batch` call. Whichever bound trips first stops accumulation.
_SCAN_MAX_FILES = 2000

TextRunner = Callable[..., str]
BytesRunner = Callable[..., bytes]


@runtime_checkable
class Fetcher(Protocol):
    """The content-fetch seam `run_acquire` consumes (`Deps.fetcher`).

    Exists to give that seam ONE declared signature. `Deps.fetcher` used to be typed
    `object`, and every test defined its own duck-typed double; when `fetch` grew a
    fourth return value (marker hits) the doubles kept returning three and acquire's
    unpacking bug stayed invisible until runtime. Doubles are bound to this Protocol
    (statically, plus a runtime arity check in `tests/test_assessment_git_fetch.py`)
    so a future arity change surfaces at the seam rather than in production."""

    def fetch(self, clone_url: str, repo_id: str, sha: str
              ) -> tuple[dict[str, str], tuple[str, ...], bool, list[dict]]:
        """Returns (files, paths, capped, hits) — see `GitContentFetcher.fetch`."""
        ...


class GitFetchError(Exception):
    """A git invocation failed (non-zero exit or timeout). Message is the decoded stderr."""


def _run_git_text(args: list[str], *, timeout_s: int = 120) -> str:
    try:
        proc = subprocess.run(
            ["git", *_HARDENING, *args],
            capture_output=True, timeout=timeout_s, check=False, env=_ENV,
        )
    except subprocess.TimeoutExpired as exc:
        raise GitFetchError(f"git {args[:2]} timed out after {timeout_s}s") from exc
    if proc.returncode != 0:
        msg = (proc.stderr or proc.stdout or b"").decode("utf-8", "replace").strip()
        raise GitFetchError(msg)
    return (proc.stdout or b"").decode("utf-8", "replace")


def _run_git_bytes(args: list[str], *, input_data: bytes = b"", timeout_s: int = 120) -> bytes:
    """Same invocation as `_run_git_text` but WITHOUT `text=True`, so stdout comes
    back as raw bytes — required for the byte-framed `cat-file --batch` reader."""
    try:
        proc = subprocess.run(
            ["git", *_HARDENING, *args], input=input_data,
            capture_output=True, timeout=timeout_s, check=False, env=_ENV,
        )
    except subprocess.TimeoutExpired as exc:
        raise GitFetchError(f"git {args[:2]} timed out after {timeout_s}s") from exc
    if proc.returncode != 0:
        msg = (proc.stderr or b"").decode("utf-8", "replace").strip()
        raise GitFetchError(msg)
    return proc.stdout or b""


def _validate_sha(sha: str) -> str:
    if not sha or not _SHA_RE.match(sha):
        raise content.ContentUnavailable(f"invalid commit sha: {sha!r}")
    return sha


def _validate_repo_key(repo_id: str) -> str:
    if not repo_id or not _REPO_KEY_RE.match(str(repo_id)):
        raise content.ContentUnavailable(f"invalid repo key: {repo_id!r}")
    return str(repo_id)


def _validate_clone_url(clone_url: str, allowed_hosts: frozenset[str] | None) -> str:
    if allowed_hosts is None:
        return clone_url
    m = re.match(r"^https://([^/]+)/", clone_url or "")
    host = m.group(1).lower() if m else None
    if host not in allowed_hosts:
        raise content.ContentUnavailable(f"disallowed clone host for {clone_url!r}")
    return clone_url


def _read_blobs(git_dir: Path, shas: list[str], *, run_bytes: BytesRunner, timeout_s: int) -> dict[str, bytes]:
    """Byte-framed `cat-file --batch` reader (design §2 step 6). Header line is
    `<sha> <type> <size>\\n` for a present object or `<sha> missing\\n` otherwise;
    body is exactly `<size>` raw bytes followed by one `\\n`. Parsed strictly by
    declared length — NEVER newline-split, or a multi-line file body would
    truncate at its first embedded newline."""
    if not shas:
        return {}
    stdin = ("\n".join(shas) + "\n").encode("utf-8")
    out = run_bytes(["-C", str(git_dir), "cat-file", "--batch"], input_data=stdin, timeout_s=timeout_s)
    result: dict[str, bytes] = {}
    pos, n = 0, len(out)
    while pos < n:
        nl = out.index(b"\n", pos)
        header = out[pos:nl].decode("utf-8", "replace")
        pos = nl + 1
        parts = header.split(" ")
        if parts[-1] == "missing":
            continue  # defensive: cannot happen for a blob already in the present-set
        sha, _type, size_s = parts
        size = int(size_s)
        result[sha] = out[pos:pos + size]
        pos += size + 1  # skip the body's trailing '\n'
    return result


# Hard bound so a hostile or generated repo cannot produce an unbounded hit list. The cap is
# per-repo across all files; hits are consumed for ranking and evidence, and no consumer
# needs more than a few hundred.
# TRUNCATION ORDER: `_scan_present_blobs` walks `sorted(bodies)`, so the cap always drops the
# ALPHABETICAL TAIL of the path space. On a hit-dense repo that means files late in the sort
# (`utils/`, `tools/`, `src/z*`) lose their ranking influence while `agents/` or `api/` keep
# theirs — a deterministic bias, not a random sample. Raise the cap rather than reorder if a
# real repo is ever seen to lose evidence this way.
_MAX_MARKER_HITS = 500


def _scan_present_blobs(bodies: dict[str, str]) -> list[dict]:
    """Marker hits across already-read blob bodies, in path order. Pure over its argument:
    the caller decides which blobs are present and affordable to read."""
    hits: list[dict] = []
    for path in sorted(bodies):
        if not markers.is_scannable(path):
            continue
        for h in markers.scan_text(path, bodies[path]):
            hits.append(h)
            if len(hits) >= _MAX_MARKER_HITS:
                return hits
    return hits


class GitContentFetcher:
    """Fetches exactly one pinned SHA (partial: `--depth=1 --filter=blob:limit=`)
    into a per-repo bare cache and reads the selected files purely locally —
    replacing `acquire`'s ~60 REST Contents-API calls/repo with one git fetch.

    `allowed_hosts=None` disables the host guard (tests against a local remote);
    production keeps the default GitHub-only guard.

    Implements `Fetcher` — the signature every test double is bound to."""

    def __init__(
        self,
        cache_dir,
        *,
        allowed_hosts: frozenset[str] | None = _ALLOWED_HOSTS,
        blob_limit: int = DEFAULT_BLOB_LIMIT,
        timeout_s: int = DEFAULT_TIMEOUT_S,
        max_entries: int = DEFAULT_MAX_TREE_ENTRIES,
        run: TextRunner = _run_git_text,
        run_bytes: BytesRunner = _run_git_bytes,
    ) -> None:
        self.cache_dir = Path(cache_dir)
        self.allowed_hosts = allowed_hosts
        self.blob_limit = blob_limit
        self.timeout_s = timeout_s
        self.max_entries = max_entries
        self._run = run
        self._run_bytes = run_bytes

    def fetch(self, clone_url: str, repo_id: str, sha: str
              ) -> tuple[dict[str, str], tuple[str, ...], bool, list[dict]]:
        """Returns (files, paths, capped, hits). `files`/`capped` are the same shape
        `run_acquire` got before this method also returned an inventory. `paths`
        is the FULL tree's file-mode paths (not just the fetched/selected bodies
        in `files`) — the whole point being that a classifier can see a path
        exists (e.g. a large or non-selected file) even though its body was
        never fetched. Preserves `ls-tree`'s order; capped at `_TREE_PATHS_CAP`.

        `hits` are marker matches over the bodies this method already read -- no extra
        git or network work, and restricted to the present set so GIT_NO_LAZY_FETCH=1
        can never be tripped by a filtered >1MiB blob."""
        # Hard preconditions, BEFORE any git call (§2).
        _validate_sha(sha)
        _validate_repo_key(repo_id)
        _validate_clone_url(clone_url, self.allowed_hosts)

        git_dir = self.cache_dir / f"{repo_id}.git"
        try:
            if not (git_dir / "HEAD").exists():
                self.cache_dir.mkdir(parents=True, exist_ok=True)
                self._run(["init", "--bare", "--", str(git_dir)])
        except (GitFetchError, OSError) as exc:
            # A fresh/root-owned cache volume makes the uid-1000 worker's first
            # `mkdir`/`git init` fail with EACCES (spec §5) — that's a retryable
            # infra condition (content.ContentUnavailable), never a raw crash.
            # Kept out of the fetch try/except below so a local init failure can
            # never be misclassified as content.RepoGone by the "not found"
            # substring match (which is meant only for the remote fetch's stderr).
            raise content.ContentUnavailable(f"git cache init failed: {exc}") from exc

        try:
            self._run(
                ["-C", str(git_dir), "fetch", "--depth=1", f"--filter=blob:limit={self.blob_limit}",
                 "--no-tags", "--", clone_url, f"+{sha}:refs/acquired/{sha}"],
                timeout_s=self.timeout_s,
            )
        except GitFetchError as exc:
            msg = str(exc).lower()
            if "repository not found" in msg or "not found" in msg:
                raise content.RepoGone(f"repo not found: {clone_url}") from exc
            raise content.ContentUnavailable(f"git fetch failed: {exc}") from exc

        try:
            present, present_capped = self._present_sizes(git_dir)
            entries, tree_capped = self._ls_tree(git_dir, sha)

            candidates: dict[str, tuple[str, int]] = {}
            for mode, blob_sha, path in entries:
                if mode not in ("100644", "100755"):
                    continue  # excludes 120000 symlink, 160000 gitlink/submodule
                size = present.get(blob_sha)
                if size is None:
                    continue  # absent from the present-set: a >=1MiB blob filtered at fetch time
                candidates[path] = (blob_sha, size)

            # Full-tree path inventory (independent of the present-set / selection
            # caps above): every file-mode path, fetched or not. Order preserved
            # from `_ls_tree`; truncated (not sorted) at `_TREE_PATHS_CAP`.
            inventory = [path for mode, _sha, path in entries if mode in ("100644", "100755")]
            paths_capped = len(inventory) > _TREE_PATHS_CAP
            inventory = tuple(inventory[:_TREE_PATHS_CAP])

            manifest_present = [p for p in content._PHASE1 if p in candidates]
            manifest_bodies = self._read_paths(git_dir, {p: candidates[p][0] for p in manifest_present})

            manifest_paths = content.select_manifest_and_entrypoint_paths(candidates, manifest_bodies)
            skip_set = set(manifest_paths)
            # Manifest bodies are already in hand, so scanning them costs nothing -- but
            # NONE of `_PHASE1` (README.md, package.json, ...) passes `content._is_source`,
            # and every manifest path is in `skip_set`, so a manifest-only hit can never
            # promote a SOURCE file: `select_source_paths` only lifts hit paths out of its
            # density-sorted `rest_paths`, which excludes everything in `skip`. To actually
            # inform selection, SOURCE candidate bodies must be scanned too -- BEFORE
            # selection runs, since selection is what the hits are meant to influence.
            #
            # Pre-read is doubly bounded, walking `candidates`' own order and stopping at
            # whichever bound trips first: `_SCAN_MAX_FILES` (a count cap, so very many tiny
            # source files can't pile up in one batch) or `_SCAN_MAX_BYTES` (a byte budget,
            # decided from `candidates`' sizes via `content._candidate_size` before any body
            # is read, stopping at the first candidate that would exceed it). Either way the
            # reads are purely local `cat-file --batch` reads over the pack the partial fetch
            # above already brought down -- no extra network or git call, only local IO/CPU
            # bounded by those two caps.
            manifest_hits = _scan_present_blobs(manifest_bodies)
            source_candidate_paths = [p for p in candidates if p not in skip_set and content._is_source(p)]
            scan_paths: list[str] = []
            scan_bytes = 0
            scan_capped = False
            for p in source_candidate_paths:
                if len(scan_paths) >= _SCAN_MAX_FILES:
                    scan_capped = True
                    break
                size = content._candidate_size(candidates, p)
                if scan_bytes + size > _SCAN_MAX_BYTES:
                    scan_capped = True
                    break
                scan_paths.append(p)
                scan_bytes += size
            source_bodies = self._read_paths(git_dir, {p: candidates[p][0] for p in scan_paths})
            source_hits = _scan_present_blobs(source_bodies)

            source_paths, src_capped = content.select_source_paths(
                candidates, skip=skip_set, hits=manifest_hits + source_hits)

            selected = manifest_paths + source_paths
            # Reuse pre-read bodies for selected paths already read above -- never re-read
            # a body `_read_paths` already fetched. `manifest_bodies` only covers _PHASE1
            # members (entrypoints resolved from them, e.g. `dist/server.js`, are NOT in
            # it), and `source_bodies` only covers the pre-scan's `scan_paths`, so anything
            # selected but not already in hand still needs one `_read_paths` call.
            files = dict(manifest_bodies)
            unread = {}
            for p in selected:
                if p in files:
                    continue
                if p in source_bodies:
                    files[p] = source_bodies[p]
                else:
                    unread[p] = candidates[p][0]
            if unread:
                files.update(self._read_paths(git_dir, unread))
        except GitFetchError as exc:
            raise content.ContentUnavailable(f"git read failed: {exc}") from exc

        capped = present_capped or tree_capped or src_capped or paths_capped or scan_capped
        # Re-scan over the FULL selected set. The two earlier scans covered `_PHASE1`
        # manifests and the pre-scan's `scan_paths` respectively; what neither covered is
        # everything SELECTED from outside those sets — resolved entrypoints (`dist/server.js`
        # and friends), non-`_PHASE1` manifests, and any source file selection kept that the
        # capped pre-scan did not reach. Those are read here for the first time, so this is
        # the only scan that sees them.
        hits = _scan_present_blobs(files)
        return files, inventory, capped, hits

    def _present_sizes(self, git_dir: Path) -> tuple[dict[str, int], bool]:
        """§2 step 2: enumerate PRESENT objects offline — the only listing that
        yields sizes without lazy-fetching. Blobs only; capped at `max_entries`."""
        out = self._run(
            ["-C", str(git_dir), "cat-file", "--batch-all-objects",
             "--batch-check=%(objectname) %(objecttype) %(objectsize)"],
            timeout_s=self.timeout_s,
        )
        sizes: dict[str, int] = {}
        capped = False
        for line in out.splitlines():
            if not line:
                continue
            parts = line.split(" ")
            if len(parts) != 3 or parts[1] != "blob":
                continue
            if len(sizes) >= self.max_entries:
                capped = True
                break
            sizes[parts[0]] = int(parts[2])
        return sizes, capped

    def _ls_tree(self, git_dir: Path, sha: str) -> tuple[list[tuple[str, str, str]], bool]:
        """§2 step 3: list the tree with NO `-l` (no lazy size fill). Runs with
        `-c core.quotePath=false` AND `-z` (NUL-delimited records, no C-quoting —
        verified against real git 2.39: `-z` alone already disables ls-tree's path
        quoting, but core.quotePath=false is pinned too so the fix doesn't
        silently regress on a git version where that isn't true) so a non-ASCII or
        tab/newline path round-trips as the exact raw UTF-8 bytes REST would
        report. Default `core.quotePath=true` octal-escapes + double-quotes such a
        path (e.g. `"src/caf\\303\\251.py"`), which would diverge the
        candidate-map key from REST (repo_content.path / fingerprint mismatch) and
        break `_is_source`'s extension check on the trailing `"`. Record format:
        `<mode> SP <type> SP <objectname> TAB <path>\\0`. Capped at `max_entries`."""
        out = self._run(
            ["-c", "core.quotePath=false", "-C", str(git_dir), "ls-tree", "-r", "-z", sha],
            timeout_s=self.timeout_s,
        )
        entries: list[tuple[str, str, str]] = []
        capped = False
        for record in out.split("\0"):
            if not record:
                continue
            meta, sep, path = record.partition("\t")
            if not sep:
                continue
            mode, _type, blob_sha = meta.split(" ")
            if len(entries) >= self.max_entries:
                capped = True
                break
            entries.append((mode, blob_sha, path))
        return entries, capped

    def _read_paths(self, git_dir: Path, path_to_sha: dict[str, str]) -> dict[str, str]:
        shas = list(dict.fromkeys(path_to_sha.values()))  # dedup, preserve first-seen order
        blobs = _read_blobs(git_dir, shas, run_bytes=self._run_bytes, timeout_s=self.timeout_s)
        # Byte-identical to clients._decode_contents (clients.py:37) so content_sha /
        # the acquisition fingerprint match the REST path exactly.
        return {p: blobs[s].decode("utf-8", "replace") for p, s in path_to_sha.items() if s in blobs}


if TYPE_CHECKING:                                   # static-only: no runtime cost
    # Fails type-check if `GitContentFetcher.fetch` ever drifts from `Fetcher.fetch`.
    _real_fetcher_conforms: type[Fetcher] = GitContentFetcher
