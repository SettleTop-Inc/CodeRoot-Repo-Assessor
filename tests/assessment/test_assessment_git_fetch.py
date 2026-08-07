"""GitContentFetcher (design spec §2/§3/§8/§9).

Two layers, mirroring `test_git_disk_scan.py`:
  - Pure selection logic (`content.select_manifest_and_entrypoint_paths` /
    `content.select_source_paths`) is tested directly on a candidate map — no git.
  - `GitContentFetcher` mechanics (fetch classification, candidate-map building,
    byte-framed batch reads) are tested with an injected fake `run`/`run_bytes` pair
    so no network is needed and the exact git argv is pinned.
  - A REAL-git integration section (gated only on `git` being on PATH, which it is)
    builds a real repo, serves it over `git daemon` (git:// — NOT gated by the
    fetcher's `protocol.file.allow=never` hardening, unlike a plain local-path
    remote, which IS still 'file' transport and would be refused), and fetches it
    through the real default runner end-to-end.
"""
from __future__ import annotations

import base64
import json
import os
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

from assessor.assessment import content
from assessor.assessment import git_fetch as git_fetch_module
from assessor.assessment.git_fetch import Fetcher, GitContentFetcher, GitFetchError, _read_blobs
from assessor.vendored import _decode_contents

REPO_ID = "11111111-1111-1111-1111-111111111111"
SHA = "a" * 40


# ============================================================================
# Pure selection helpers — no git, no fake runner, just candidate maps.
# ============================================================================

def test_select_manifest_and_entrypoint_includes_phase1_present_candidates():
    candidates = {"README.md": ("s1", 5), "package.json": ("s2", 10)}
    assert content.select_manifest_and_entrypoint_paths(candidates, {}) == ["README.md", "package.json"]


def test_select_manifest_and_entrypoint_includes_entrypoint_outside_is_source():
    pkg = json.dumps({"name": "x", "bin": "dist/server.js"})
    candidates = {"package.json": ("s1", len(pkg)), "dist/server.js": ("s2", 20)}
    selected = content.select_manifest_and_entrypoint_paths(candidates, {"package.json": pkg})
    # dist/ is in _SRC_EXCLUDE (would fail _is_source) but the entrypoint is included anyway.
    assert selected == ["package.json", "dist/server.js"]


def test_select_manifest_and_entrypoint_dedups_against_phase1():
    # langgraph.json is in both _PHASE1 and (via fallback) never an _entrypoints() output,
    # but a _FALLBACK_ENTRYPOINTS member that also happens to be in _PHASE1-adjacent paths
    # must not be double-listed.
    candidates = {"package.json": ("s1", 2), "index.js": ("s2", 2)}
    selected = content.select_manifest_and_entrypoint_paths(candidates, {})
    assert selected.count("index.js") == 1  # _FALLBACK_ENTRYPOINTS candidate, present once


def test_select_source_paths_skip_dedups_langgraph_json_against_agent_manifests():
    # langgraph.json is in BOTH _PHASE1 and _AGENT_MANIFESTS — without the skip dedup
    # it would be counted twice (once via the manifest+entrypoint pass, once again via
    # the _AGENT_MANIFESTS pre-pass here), consuming a spurious extra manifest-cap slot.
    candidates = {"langgraph.json": ("s1", 20)}
    manifest_paths = content.select_manifest_and_entrypoint_paths(candidates, {})
    assert manifest_paths == ["langgraph.json"]
    source_paths, capped = content.select_source_paths(candidates, skip=set(manifest_paths))
    assert source_paths == [] and capped is False


def test_select_source_paths_prefers_denser_directory():
    candidates = {
        "src/a.py": ("s1", 10), "src/b.py": ("s2", 10),   # dense dir: 2 files
        "lib/c.py": ("s3", 1),                              # sparse dir: 1 file, smaller
    }
    selected, capped = content.select_source_paths(candidates)
    assert selected[:2] == ["src/a.py", "src/b.py"] or selected[:2] == ["src/b.py", "src/a.py"]
    assert selected[-1] == "lib/c.py"
    assert capped is False


def test_select_source_paths_respects_skip_and_file_cap():
    """Ported from test_source_subtree_respects_skip_and_file_cap (REST) onto the
    pure git-agnostic selection helper."""
    candidates = {f"src/f{i}.py": (f"s{i}", 10) for i in range(140)}   # > _SRC_MAX_FILES(120)
    selected, capped = content.select_source_paths(candidates, skip={"src/f0.py"})
    assert "src/f0.py" not in selected
    assert len(selected) <= 120
    assert capped is True


def test_select_source_paths_manifest_prepass_beats_density_cap():
    candidates = {f"src/f{i}.py": (f"s{i}", 10) for i in range(125)}  # dense dir fills the 120-file cap
    candidates["src/cfg/agents.yaml"] = ("sA", 20)
    candidates["src/cfg/tasks.yaml"] = ("sB", 10)
    selected, capped = content.select_source_paths(candidates)
    assert "src/cfg/agents.yaml" in selected and "src/cfg/tasks.yaml" in selected
    assert capped is True


def test_select_source_paths_manifest_prepass_overflow_sets_capped():
    candidates = {f"c{i}/agents.yaml": (f"s{i}", 8) for i in range(9)}  # > _MANIFEST_MAX_FILES(8)
    selected, capped = content.select_source_paths(candidates)
    assert len(selected) == 8 and capped is True


def test_select_source_paths_excludes_non_source_extensions():
    candidates = {"src/a.py": ("s1", 10), "README.md": ("s2", 10), "node_modules/x.js": ("s3", 10)}
    selected, _ = content.select_source_paths(candidates)
    assert selected == ["src/a.py"]


# ============================================================================
# _scan_present_blobs — marker scan over already-read blob bodies.
# ============================================================================

def test_scan_present_blobs_collects_hits_and_skips_unscannable():
    from assessor.assessment.git_fetch import _scan_present_blobs

    bodies = {
        "src/llm.py": 'os.environ["ANTHROPIC_API_KEY"]\n',
        "node_modules/pkg/i.js": 'process.env.OPENAI_API_KEY\n',   # vendored -> skipped
        "package-lock.json": '"OPENAI_API_KEY"\n',                  # lockfile -> skipped
        "README.md": "Works with any LLM.\n",                       # no marker -> no hits
    }
    hits = _scan_present_blobs(bodies)
    assert [h["path"] for h in hits] == ["src/llm.py"]
    assert hits[0]["marker"] == "ANTHROPIC_API_KEY"


def test_scan_is_bounded_by_max_hits():
    from assessor.assessment.git_fetch import _MAX_MARKER_HITS, _scan_present_blobs

    body = "ANTHROPIC_API_KEY\n" * (_MAX_MARKER_HITS + 50)
    hits = _scan_present_blobs({"a.py": body})
    assert len(hits) == _MAX_MARKER_HITS


# ============================================================================
# GitContentFetcher mechanics — fake `run`/`run_bytes`, argv + framing pinned.
# ============================================================================

class FakeGit:
    """Fake `run` (text) + `run_bytes` pair for `GitContentFetcher`.

    `blobs`: sha -> raw bytes, used both to answer `cat-file --batch-check`
    (every key reported as a present blob with its real length) and
    `cat-file --batch` (byte-framed body reads). `tree`: ordered
    (mode, type, sha, path) tuples answering `ls-tree -r -z` (NUL-delimited, to
    match the production `-c core.quotePath=false -z` invocation).
    """

    def __init__(self, *, blobs=None, tree=None, fetch_error=None, present_override=None, init_error=None):
        self.calls: list[tuple[str, list[str]]] = []
        self.blobs: dict[str, bytes] = dict(blobs or {})
        self.tree: list[tuple[str, str, str, str]] = list(tree or [])
        self.fetch_error = fetch_error
        self.present_override = present_override
        self.init_error = init_error

    def _sub(self, args):
        # Skip leading global-option pairs ("-c KEY=VAL" / "-C DIR", any order/count)
        # to find the actual subcommand token.
        i = 0
        while i < len(args) and args[i] in ("-c", "-C"):
            i += 2
        return args[i] if i < len(args) else None

    def run(self, args, *, timeout_s=120):
        self.calls.append(("text", list(args)))
        sub = self._sub(args)
        if sub == "init":
            if self.init_error is not None:
                raise self.init_error
            git_dir = Path(args[-1])
            git_dir.mkdir(parents=True, exist_ok=True)
            (git_dir / "HEAD").write_text("ref: refs/heads/main\n")
            return ""
        if sub == "fetch":
            if self.fetch_error is not None:
                raise self.fetch_error
            return ""
        if sub == "cat-file" and any(a.startswith("--batch-check") for a in args):
            if self.present_override is not None:
                return self.present_override
            lines = [f"{sha} blob {len(body)}" for sha, body in self.blobs.items()]
            return "\n".join(lines) + ("\n" if lines else "")
        if sub == "ls-tree":
            return "".join(f"{mode} {typ} {sha}\t{path}\0" for mode, typ, sha, path in self.tree)
        return ""

    def run_bytes(self, args, *, input_data=b"", timeout_s=120):
        self.calls.append(("bytes", list(args)))
        shas = input_data.decode("utf-8").split()
        out = bytearray()
        for sha in shas:
            body = self.blobs.get(sha)
            if body is None:
                out += f"{sha} missing\n".encode("utf-8")
                continue
            out += f"{sha} blob {len(body)}\n".encode("utf-8")
            out += body
            out += b"\n"
        return bytes(out)

    def count(self, sub):
        return sum(1 for _kind, a in self.calls if self._sub(a) == sub)


def _fetcher(tmp_path, fake, **kw):
    return GitContentFetcher(tmp_path / "cache", allowed_hosts=None, run=fake.run, run_bytes=fake.run_bytes, **kw)


def test_fetcher_selects_phase1_entrypoint_and_source_end_to_end(tmp_path):
    pkg = json.dumps({"name": "x", "bin": "dist/server.js"})
    tree = [
        ("100644", "blob", "s_req", "requirements.txt"),
        ("100644", "blob", "s_pkg", "package.json"),
        ("100644", "blob", "s_dist", "dist/server.js"),
        ("100644", "blob", "s_a", "src/a.py"),
        ("100644", "blob", "s_b", "src/b.py"),
        ("100644", "blob", "s_nm", "node_modules/x.js"),
    ]
    blobs = {
        "s_req": b"langgraph\n", "s_pkg": pkg.encode(), "s_dist": b"new McpServer({})",
        "s_a": b"a = 1\n", "s_b": b"b = 2\n", "s_nm": b"excluded\n",
    }
    fake = FakeGit(tree=tree, blobs=blobs)
    files, paths, capped, hits = _fetcher(tmp_path, fake).fetch("local/o/r", REPO_ID, SHA)

    assert files == {
        "requirements.txt": "langgraph\n", "package.json": pkg,
        "dist/server.js": "new McpServer({})", "src/a.py": "a = 1\n", "src/b.py": "b = 2\n",
    }
    assert capped is False
    assert fake.count("init") == 1 and fake.count("fetch") == 1
    # The path inventory includes node_modules/x.js -- excluded from `files` by
    # source selection, but it's still a real file-mode tree entry.
    assert set(paths) == {
        "requirements.txt", "package.json", "dist/server.js",
        "src/a.py", "src/b.py", "node_modules/x.js",
    }


def test_fetcher_excludes_symlink_and_gitlink_modes(tmp_path):
    tree = [
        ("100644", "blob", "s_ok", "src/ok.py"),
        ("120000", "blob", "s_link", "src/link.py"),     # symlink: excluded by mode, even though present
        ("160000", "commit", "s_sub", "vendor/mod"),      # gitlink: excluded by mode, and absent anyway
    ]
    blobs = {"s_ok": b"ok = 1\n", "s_link": b"../target.py"}
    fake = FakeGit(tree=tree, blobs=blobs)
    files, paths, capped, hits = _fetcher(tmp_path, fake).fetch("local/o/r", REPO_ID, SHA)
    assert files == {"src/ok.py": "ok = 1\n"}
    assert capped is False
    # Path inventory excludes symlink/gitlink modes exactly like the candidate map does.
    assert paths == ("src/ok.py",)


def test_fetcher_excludes_blob_absent_from_present_set(tmp_path):
    # Simulates a >1MiB blob dropped by `--filter=blob:limit=`: the tree entry exists
    # but its sha never appears in the batch-check present-set.
    tree = [("100644", "blob", "s_ok", "src/ok.py"), ("100644", "blob", "s_huge", "src/huge.py")]
    blobs = {"s_ok": b"ok = 1\n"}   # s_huge deliberately absent
    fake = FakeGit(tree=tree, blobs=blobs)
    files, paths, capped, hits = _fetcher(tmp_path, fake).fetch("local/o/r", REPO_ID, SHA)
    assert files == {"src/ok.py": "ok = 1\n"}
    assert "src/huge.py" not in files
    # But the path inventory is the FULL tree, independent of what was fetched as a
    # body -- src/huge.py's blob was never even fetched (filtered at fetch time),
    # yet its PATH must still show up so a classifier can see it exists.
    assert "src/huge.py" in paths
    assert "src/ok.py" in paths


def test_fetcher_skip_dedup_entrypoint_not_double_counted_against_source_cap(tmp_path):
    # src/server.py is a _FALLBACK_ENTRYPOINTS member AND a source file living in the
    # same dense dir as the cap-eligible files — it must be selected via the
    # manifest+entrypoint pass and NOT consume one of the _SRC_MAX_FILES(120) slots.
    #
    # It's made the SMALLEST file in the dir (empty body) so it sorts FIRST in the
    # size-ascending source-selection order — deliberately, so this test actually
    # discriminates dedup-on from dedup-off. If server.py instead sorted AFTER all
    # 125 fillers (e.g. a nonempty body bigger than every filler), the 120-file cap
    # would exclude it from the source pass regardless of whether the skip-dedup
    # ran, and `len(files) == 121` would hold either way. With server.py sorting
    # first: dedup working -> it's excluded from the source-cap pool (selected once,
    # via the manifest+entrypoint pass) and all 120 slots go to fillers, for 121
    # total. Dedup broken -> it would ALSO win the very first source-cap slot,
    # displacing a filler and leaving only 120 unique files total.
    tree = [("100644", "blob", "s_server", "src/server.py")]
    tree += [("100644", "blob", f"s_{i}", f"src/f{i}.py") for i in range(125)]
    blobs = {"s_server": b""}
    blobs.update({f"s_{i}": f"x = {i}\n".encode() for i in range(125)})
    fake = FakeGit(tree=tree, blobs=blobs)
    files, paths, capped, hits = _fetcher(tmp_path, fake).fetch("local/o/r", REPO_ID, SHA)
    assert "src/server.py" in files
    assert capped is True
    # 1 entrypoint + 120 capped source files = 121 total (not 120, which is what a
    # dropped-dedup regression would produce with this fixture).
    assert len(files) == 121
    # The path inventory is unaffected by the source-selection cap -- it reflects
    # the whole tree (126 entries here), not the 121-file selection.
    assert len(paths) == 126


# ============================================================================
# Marker-driven source selection wiring (Task 5 fix round 1): the pre-read scan
# over SOURCE candidate bodies that actually lets hits influence selection.
# ============================================================================

def test_source_marker_hit_promotes_file_that_would_otherwise_lose_density_sort(tmp_path):
    # 125 dense filler files in one directory (density 125) + 1 sparse agent file
    # (density 1) = 126 candidates, EXCEEDING the 120-file cap. Without the marker
    # scan wired into selection, the density sort ranks the filler dir first and
    # agent/loop.py -- the lowest-density candidate -- is the one evicted. With the
    # StateGraph( marker in its body actually reaching `select_source_paths` via the
    # new pre-read, it is lifted ahead of the density sort and survives instead.
    tree = [("100644", "blob", "s_agent", "agent/loop.py")]
    tree += [("100644", "blob", f"s_{i}", f"src/common/f{i}.py") for i in range(125)]
    blobs = {"s_agent": b"StateGraph(\n"}
    blobs.update({f"s_{i}": f"x = {i}\n".encode() for i in range(125)})
    fake = FakeGit(tree=tree, blobs=blobs)
    files, paths, capped, hits = _fetcher(tmp_path, fake).fetch("local/o/r", REPO_ID, SHA)
    assert "agent/loop.py" in files
    assert capped is True
    assert len(files) == 120   # agent/loop.py + 119 of the 125 fillers


def test_scan_budget_binding_sets_capped(tmp_path, monkeypatch):
    # Shrink the pre-read scan budget so it binds after the very first (tiny) file --
    # both files are far below _SRC_MAX_FILES/_SRC_MAX_BYTES, so selection itself never
    # caps. `capped` coming back True here can ONLY be explained by `scan_capped`.
    monkeypatch.setattr(git_fetch_module, "_SCAN_MAX_BYTES", 10)
    tree = [
        ("100644", "blob", "s_a", "src/a.py"),
        ("100644", "blob", "s_b", "src/b.py"),
    ]
    blobs = {"s_a": b"x = 1\n", "s_b": b"y = 2\n"}   # 6 bytes each; the pair exceeds the 10-byte budget
    fake = FakeGit(tree=tree, blobs=blobs)
    files, paths, capped, hits = _fetcher(tmp_path, fake).fetch("local/o/r", REPO_ID, SHA)
    # Both still get SELECTED and READ (the scan budget only bounds how many bodies are
    # pre-read for marker hits, not the selection itself -- an unread-but-selected body
    # is fetched afterward via the `unread` fallback).
    assert set(files) == {"src/a.py", "src/b.py"}
    assert capped is True


def test_scan_file_count_binding_sets_capped(tmp_path, monkeypatch):
    # Shrink the pre-read scan's FILE-COUNT budget (independent of the byte budget) so
    # it binds after the very first file -- both files are tiny (far below
    # _SCAN_MAX_BYTES) and far below _SRC_MAX_FILES/_SRC_MAX_BYTES, so neither the byte
    # scan budget nor selection itself can explain a capped result here. `capped` coming
    # back True can ONLY be explained by the file-count bound (_SCAN_MAX_FILES).
    monkeypatch.setattr(git_fetch_module, "_SCAN_MAX_FILES", 1)
    tree = [
        ("100644", "blob", "s_a", "src/a.py"),
        ("100644", "blob", "s_b", "src/b.py"),
    ]
    blobs = {"s_a": b"x = 1\n", "s_b": b"y = 2\n"}
    fake = FakeGit(tree=tree, blobs=blobs)
    files, paths, capped, hits = _fetcher(tmp_path, fake).fetch("local/o/r", REPO_ID, SHA)
    # Both still get SELECTED and READ (the scan's file-count budget only bounds how many
    # bodies are pre-read for marker hits, not the selection itself -- an unread-but-selected
    # body is fetched afterward via the `unread` fallback).
    assert set(files) == {"src/a.py", "src/b.py"}
    assert capped is True


def test_preread_source_body_is_not_read_twice(tmp_path, monkeypatch):
    # agent/loop.py carries a marker hit, so it gets pre-read during the scan pass AND
    # is selected. It must be reused from that pre-read, not fetched again via a second
    # `_read_paths` call -- assert its sha is only ever handed to `_read_paths` once.
    tree = [
        ("100644", "blob", "s_agent", "agent/loop.py"),
        ("100644", "blob", "s_other", "lib/other.py"),
    ]
    blobs = {"s_agent": b"StateGraph(\n", "s_other": b"x = 1\n"}
    fake = FakeGit(tree=tree, blobs=blobs)
    fetcher = _fetcher(tmp_path, fake)

    calls: list[dict] = []
    orig_read_paths = GitContentFetcher._read_paths

    def spy(self, git_dir, path_to_sha):
        calls.append(dict(path_to_sha))
        return orig_read_paths(self, git_dir, path_to_sha)

    monkeypatch.setattr(GitContentFetcher, "_read_paths", spy)

    files, paths, capped, hits = fetcher.fetch("local/o/r", REPO_ID, SHA)
    assert "agent/loop.py" in files and "lib/other.py" in files
    occurrences = sum(1 for c in calls if "agent/loop.py" in c)
    assert occurrences == 1


def test_present_set_ceiling_caps_and_bounds_candidates(tmp_path):
    tree = [("100644", "blob", f"s_{i}", f"src/f{i}.py") for i in range(3)]
    blobs = {f"s_{i}": f"x = {i}\n".encode() for i in range(3)}
    fake = FakeGit(tree=tree, blobs=blobs)
    files, paths, capped, hits = _fetcher(tmp_path, fake, max_entries=2).fetch("local/o/r", REPO_ID, SHA)
    assert capped is True
    assert len(files) <= 2
    # `max_entries` bounds both the present-set AND `_ls_tree` (shared knob), so
    # the path inventory built from `_ls_tree`'s entries is bounded too.
    assert len(paths) <= 2


def test_ls_tree_ceiling_caps_and_bounds_candidates(tmp_path):
    tree = [("100644", "blob", f"s_{i}", f"src/f{i}.py") for i in range(3)]
    blobs = {f"s_{i}": f"x = {i}\n".encode() for i in range(3)}
    fake = FakeGit(tree=tree, blobs=blobs)
    files, paths, capped, hits = _fetcher(tmp_path, fake, max_entries=2).fetch("local/o/r", REPO_ID, SHA)
    assert capped is True
    assert len(files) <= 2
    # tree_capped truncated ls-tree's own entries to max_entries(2) -- the path
    # inventory can't see more than what ls-tree returned.
    assert len(paths) <= 2


def test_fetch_path_inventory_preserves_ls_tree_order(tmp_path):
    tree = [("100644", "blob", "s_b", "b.py"), ("100644", "blob", "s_a", "a.py")]
    blobs = {"s_b": b"b\n", "s_a": b"a\n"}
    fake = FakeGit(tree=tree, blobs=blobs)
    _files, paths, _capped, _hits = _fetcher(tmp_path, fake).fetch("local/o/r", REPO_ID, SHA)
    assert paths == ("b.py", "a.py")   # ls-tree's order, not sorted


def test_fetch_path_inventory_capped_at_tree_paths_cap(tmp_path, monkeypatch):
    monkeypatch.setattr(git_fetch_module, "_TREE_PATHS_CAP", 2)
    tree = [("100644", "blob", f"s_{i}", f"src/f{i}.py") for i in range(3)]
    blobs = {f"s_{i}": f"x = {i}\n".encode() for i in range(3)}
    fake = FakeGit(tree=tree, blobs=blobs)
    _files, paths, capped, hits = _fetcher(tmp_path, fake).fetch("local/o/r", REPO_ID, SHA)
    assert len(paths) == 2
    assert capped is True


def test_read_blobs_byte_frames_a_multiline_body_without_splitting(tmp_path):
    body = b"line one\nline two\n\nline four (no trailing newline)"
    fake = FakeGit(blobs={"shaX": body})
    result = _read_blobs(tmp_path, ["shaX"], run_bytes=fake.run_bytes, timeout_s=10)
    assert result == {"shaX": body}


def test_read_blobs_skips_missing_records(tmp_path):
    fake = FakeGit(blobs={})  # sha not present -> "<sha> missing"
    result = _read_blobs(tmp_path, ["ghost"], run_bytes=fake.run_bytes, timeout_s=10)
    assert result == {}


def test_fetch_classifies_cache_init_git_failure_as_content_unavailable(tmp_path):
    # A root-owned fresh cache volume makes the uid-1000 worker's first `git init`
    # fail (spec §5) — must surface as content.ContentUnavailable (retryable), not
    # the raw GitFetchError.
    fake = FakeGit(init_error=GitFetchError("fatal: cannot mkdir cache: Permission denied"))
    with pytest.raises(content.ContentUnavailable):
        _fetcher(tmp_path, fake).fetch("local/o/r", REPO_ID, SHA)


def test_fetch_classifies_cache_dir_mkdir_eacces_as_content_unavailable(tmp_path, monkeypatch):
    # Same scenario, but the failure surfaces one call earlier — Path.mkdir itself
    # raising OSError (EACCES) on a root-owned volume, before `git init` even runs.
    def _boom_mkdir(self, *a, **kw):
        raise OSError(13, "Permission denied")

    monkeypatch.setattr(Path, "mkdir", _boom_mkdir)
    fake = FakeGit()
    with pytest.raises(content.ContentUnavailable):
        _fetcher(tmp_path, fake).fetch("local/o/r", REPO_ID, SHA)


def test_fetch_classifies_repository_not_found_as_repo_gone(tmp_path):
    fake = FakeGit(fetch_error=GitFetchError(
        "remote: Repository not found.\nfatal: repository 'https://github.com/o/r.git/' not found"))
    with pytest.raises(content.RepoGone):
        _fetcher(tmp_path, fake).fetch("local/o/r", REPO_ID, SHA)


def test_fetch_classifies_other_git_failure_as_content_unavailable(tmp_path):
    fake = FakeGit(fetch_error=GitFetchError(
        "fatal: unable to access 'https://github.com/o/r.git/': Could not resolve host: github.com"))
    with pytest.raises(content.ContentUnavailable):
        _fetcher(tmp_path, fake).fetch("local/o/r", REPO_ID, SHA)


def _exploding_fake():
    def _boom(*a, **kw):
        raise AssertionError("must not call git for a rejected precondition")
    return _boom


def test_fetch_rejects_disallowed_host_before_any_git_call(tmp_path):
    boom = _exploding_fake()
    fetcher = GitContentFetcher(tmp_path / "cache", run=boom, run_bytes=boom)  # default github-only guard
    with pytest.raises(content.ContentUnavailable):
        fetcher.fetch("https://evil.example.com/o/r.git", REPO_ID, SHA)


def test_fetch_rejects_bad_sha_before_any_git_call(tmp_path):
    boom = _exploding_fake()
    fetcher = GitContentFetcher(tmp_path / "cache", allowed_hosts=None, run=boom, run_bytes=boom)
    with pytest.raises(content.ContentUnavailable):
        fetcher.fetch("local/o/r", REPO_ID, "--upload-pack=touch /tmp/x")


def test_fetch_rejects_bad_repo_key_before_any_git_call(tmp_path):
    boom = _exploding_fake()
    fetcher = GitContentFetcher(tmp_path / "cache", allowed_hosts=None, run=boom, run_bytes=boom)
    with pytest.raises(content.ContentUnavailable):
        fetcher.fetch("local/o/r", "../../etc", SHA)


# ============================================================================
# Real-git integration — a live `git fetch` over `git daemon` (git://), gated
# only on `git` being on PATH (it is; no SCAN_NETWORK_TEST / DB markers needed:
# the daemon is 127.0.0.1-only, nothing leaves the machine).
# ============================================================================

_requires_git = pytest.mark.skipif(shutil.which("git") is None, reason="git not on PATH")


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait_for_port(port: int, timeout_s: float = 5.0) -> None:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                return
        except OSError:
            time.sleep(0.05)
    raise RuntimeError(f"git daemon did not start listening on {port} in time")


@pytest.fixture(scope="module")
def git_daemon(tmp_path_factory):
    """A real `git daemon` (git:// protocol) serving a base directory of bare repos.

    git:// is NOT gated by GitContentFetcher's `protocol.file.allow=never` /
    `protocol.ext.allow=never` hardening (verified against real git 2.39: those
    settings block the 'file' transport, and a plain local *path* remote — with no
    scheme at all — is STILL 'file' transport and gets refused even with
    `allowed_hosts=None`; only a genuine network-style transport like git:///http://
    gets through). A local daemon is the only way to exercise the fetcher's real
    default `_run`/`_run_bytes` end-to-end without a live GitHub remote.
    """
    base = tmp_path_factory.mktemp("git-daemon-base")
    port = _free_port()
    proc = subprocess.Popen(
        ["git", "daemon", "--reuseaddr", "--listen=127.0.0.1", f"--port={port}",
         f"--base-path={base}", "--export-all"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        _wait_for_port(port)
    except Exception:
        proc.kill()
        raise
    yield base, port
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
    if sys.platform == "win32":
        # On Windows, the real listening `git-daemon.exe` ends up detached from the
        # `git.exe` dispatcher PID Popen tracked (observed: `taskkill /T` against that
        # PID leaves the actual listener running once it has served >=1 real fetch —
        # git-for-windows appears to re-parent the post-fork handler away from the
        # tree taskkill can walk). Kill by matching OUR unique port in the command
        # line instead of by PID/tree, so this can't affect an unrelated git-daemon
        # a developer happens to be running elsewhere on the same machine.
        subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             f"Get-CimInstance Win32_Process -Filter \"Name='git-daemon.exe'\" | "
             f"Where-Object {{ $_.CommandLine -like '*--port={port}*' }} | "
             f"ForEach-Object {{ Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }}"],
            capture_output=True,
        )


def _git(repo, *args, env=None, input_bytes=None):
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, input=input_bytes,
        env={**os.environ, "GIT_TERMINAL_PROMPT": "0", **(env or {})},
    )


def _hash_object(repo, data: bytes) -> str:
    r = subprocess.run(["git", "-C", str(repo), "hash-object", "-w", "--stdin"],
                        input=data, capture_output=True, check=True)
    return r.stdout.decode().strip()


def _build_big_repo(tmp_path):
    """A repo exercising every real-git edge case the design spec calls out: a
    multi-line file, an invalid-utf-8 file, a >1 MiB file, a source-extension
    symlink, a non-ASCII-named file (exercises the `ls-tree -c
    core.quotePath=false -z` fix — default quotePath would octal-escape +
    double-quote it, diverging the candidate-map key from the raw UTF-8 REST path
    and breaking `_is_source`'s extension check on the trailing quote), and a
    source-file entrypoint (`src/server.py`) inside a >120-file directory. Returns
    (repo_dir, head_sha, {path: raw_bytes} for every path that SHOULD be
    selectable — huge.py/link.py excluded, they must never be read)."""
    repo = tmp_path / "workrepo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "commit.gpgsign", "false")
    # Disable any text/CRLF conversion so committed bytes are exactly what we wrote
    # (this machine's global core.autocrlf=true would otherwise corrupt the
    # multi-line/invalid-utf-8 fixtures on `git add`).
    _git(repo, "config", "core.autocrlf", "false")
    (repo / ".gitattributes").write_bytes(b"* -text\n")

    raw: dict[str, bytes] = {}

    def _write(path: str, data: bytes):
        p = repo / path
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
        raw[path] = data

    _write("src/server.py", b"print('server')\n")                       # fallback entrypoint
    _write("src/multi.py", b"alpha\nbeta\n\ngamma\n")                    # multi-line body
    _write("src/bad_utf8.py", b"# coding\nvalue = '\xff\xfe\x80'\n")     # invalid utf-8
    _write("src/café.py", b"print('cafe')\n")                           # non-ASCII path (ls-tree quoting)
    for i in range(125):                                                 # >120-file directory
        _write(f"src/f{i:03d}.py", f"# filler {i}\n".encode() + b"x = 1\n" * 20)

    huge = b"z" * (1_048_576 + 1)                                        # >1 MiB — never selectable
    _write("src/huge.py", huge)

    _git(repo, "add", "-A")

    # A source-extension SYMLINK via plumbing (no OS symlink privilege required):
    # the tracked tree mode is 120000 regardless of how the checkout represents it.
    link_sha = _hash_object(repo, b"../does-not-matter.py")
    _git(repo, "update-index", "--add", "--cacheinfo", f"120000,{link_sha},src/link.py")

    env = {"GIT_AUTHOR_NAME": "T", "GIT_AUTHOR_EMAIL": "t@x.com",
           "GIT_COMMITTER_NAME": "T", "GIT_COMMITTER_EMAIL": "t@x.com"}
    _git(repo, "commit", "-q", "-m", "c1", env=env)
    sha = _git(repo, "rev-parse", "HEAD").stdout.decode().strip()
    return repo, sha, raw


def _serve(base: Path, name: str, work_repo: Path) -> None:
    """Clone `work_repo` into a bare repo under the daemon's base path, with
    `uploadpack.allowfilter=true` — required for a `--filter=blob:limit=` fetch to
    actually be honored over ANY local-machine transport (file or git://); without
    it git silently ignores the filter ('filtering not recognized by server') and
    fetches every blob regardless of size, verified against real git 2.39."""
    bare = base / f"{name}.git"
    subprocess.run(["git", "clone", "-q", "--bare", str(work_repo), str(bare)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(bare), "config", "uploadpack.allowfilter", "true"], check=True)


@_requires_git
def test_real_git_fetch_end_to_end(tmp_path, git_daemon):
    base, port = git_daemon
    repo, sha, raw = _build_big_repo(tmp_path)
    _serve(base, "bigrepo", repo)
    clone_url = f"git://127.0.0.1:{port}/bigrepo.git"

    fetcher = GitContentFetcher(tmp_path / "cache", allowed_hosts=None)
    files, paths, capped, hits = fetcher.fetch(clone_url, REPO_ID, sha)

    # >1MiB and symlink are never in the result, no matter how they'd otherwise sort.
    assert "src/huge.py" not in files
    assert "src/link.py" not in files

    # The path inventory is the FULL tree's file paths: src/huge.py is there even
    # though its body was filtered at fetch time (never selected into `files`),
    # while src/link.py (a symlink, mode 120000) is excluded from the inventory
    # exactly like it's excluded from the candidate map.
    assert "src/huge.py" in paths
    assert "src/link.py" not in paths
    assert "src/café.py" in paths
    assert all(isinstance(p, str) for p in paths)

    # The fallback entrypoint survives the density cap via the skip-dedup.
    assert "src/server.py" in files
    assert capped is True   # 128 remaining source candidates > _SRC_MAX_FILES(120)

    # The non-ASCII path is selected under its RAW UTF-8 form — not the
    # octal-escaped, double-quoted string git's default `core.quotePath=true`
    # would produce (`"src/caf\303\251.py"`). A dict-key `in`/lookup against the
    # literal Python str below only succeeds if `-c core.quotePath=false -z`
    # actually suppressed that quoting.
    assert "src/café.py" in files
    assert "src/caf\\303\\251.py" not in files  # the quoted/escaped form must NOT appear
    assert not any(p.startswith('"') for p in files)  # no ls-tree record was quoted

    # Byte-exact bodies for the multi-line, invalid-utf-8, and non-ASCII-path
    # fixtures, AND content_sha identical to feeding the same raw bytes through
    # the REST decoder (clients.py:37).
    for path in ("src/server.py", "src/multi.py", "src/bad_utf8.py", "src/café.py"):
        assert path in files, f"{path} should survive the cap (smaller than every filler)"
        expected = _decode_contents({"encoding": "base64", "content": base64.b64encode(raw[path]).decode()})
        assert files[path] == expected
        assert files[path] == raw[path].decode("utf-8", "replace")

    # Total: 1 entrypoint (manifest+entrypoint pass) + 120 source (capped) = 121.
    assert len(files) == 121


@_requires_git
def test_real_git_fetch_rejects_disallowed_host(tmp_path, git_daemon):
    base, port = git_daemon
    repo, sha, _raw = _build_big_repo(tmp_path)
    _serve(base, "hostcheck", repo)
    fetcher = GitContentFetcher(tmp_path / "cache")  # default github-only guard
    with pytest.raises(content.ContentUnavailable):
        fetcher.fetch(f"git://127.0.0.1:{port}/hostcheck.git", REPO_ID, sha)


@_requires_git
def test_real_git_fetch_raises_a_classified_error_for_missing_repo(tmp_path, git_daemon):
    """A real git failure against a missing repo IS classified (never an
    unhandled exception) — but `git daemon --export-all`'s anonymous git://
    protocol says "access denied or repository not exported" for a missing path
    (no "not found" substring), unlike GitHub's smart-HTTP "repository not
    found" — so this exercises ContentUnavailable, not RepoGone. The
    RepoGone-vs-ContentUnavailable *string classification* itself is exercised
    against GitHub's actual wording by the fake-runner unit tests above, where
    the exact stderr text can be pinned."""
    base, port = git_daemon
    fetcher = GitContentFetcher(tmp_path / "cache", allowed_hosts=None, timeout_s=15)
    with pytest.raises(content.ContentUnavailable):
        fetcher.fetch(f"git://127.0.0.1:{port}/does-not-exist.git", REPO_ID, SHA)

# NOTE: CodeRoot's `test_fetcher_doubles_match_the_real_fetch_signature` (and its
# `_fetch_return_arity`/`_double_kwargs` helpers) is deliberately NOT moved here. It
# asserted signature parity between `GitContentFetcher.fetch` and fake-fetcher test
# doubles defined in `test_assessment_acquire.py`, `test_assessment_wiring.py` and
# `test_runner.py` — all three test CodeRoot's `run_acquire`/pipeline-worker wiring
# around `acquire.py`, which this plan explicitly excludes from the move (acquire.py,
# service.py and creation_info.py stay in CodeRoot). None of those three modules exist
# in this repo and never will, so the cross-module assertion has nothing left to check.
