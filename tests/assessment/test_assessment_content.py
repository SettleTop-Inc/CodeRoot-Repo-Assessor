import base64
import json

import pytest

import pytest

from assessor.assessment import content
from assessor.assessment.content import (
    fetch, resolve_head, fetch_files, fetch_source_subtree, ContentUnavailable, RepoGone,
    select_source_paths, ALLOWLIST_VERSION)
from assessor.vendored import _decode_contents, _valid_slug


class _TreeHttp:
    def __init__(self, tree, files, *, truncated=False):
        self._tree, self._files, self._trunc = tree, files, truncated

    def get_json(self, url):
        if "/git/trees/" in url:
            return 200, {"truncated": self._trunc,
                         "tree": [{"type": "blob", "path": p, "size": len(b)} for p, b in self._tree.items()]}
        return 404, None

    def get_contents(self, owner, name, path, ref):
        return (200, self._files[path]) if path in self._files else (404, None)


def test_source_subtree_fetches_source_files_and_skips_excluded():
    tree = {"src/tools.ts": "a" * 10, "src/server.ts": "b" * 10,
            "node_modules/x.js": "c" * 10, "src/server.test.ts": "d" * 10, "README.md": "e" * 10}
    http = _TreeHttp(tree, {"src/tools.ts": "TOOLS", "src/server.ts": "SERVER"})
    files, capped = fetch_source_subtree(http, "o", "n", "sha")
    assert set(files) == {"src/tools.ts", "src/server.ts"}
    assert capped is False


def test_source_subtree_includes_go_but_not_go_tests():
    tree = {"pkg/github/issues.go": "a" * 10, "pkg/github/issues_test.go": "b" * 10, "main.go": "c" * 10}
    http = _TreeHttp(tree, {"pkg/github/issues.go": "SRC", "main.go": "SRC"})
    files, capped = fetch_source_subtree(http, "o", "n", "sha")
    assert set(files) == {"pkg/github/issues.go", "main.go"}   # .go in; *_test.go excluded


def test_source_subtree_includes_rust_cs_java_excludes_build_dirs():
    tree = {"src/lib.rs": "a", "Tools/W.cs": "b", "src/main/java/T.java": "c", "Main.kt": "d",
            "target/debug/x.rs": "e", "obj/gen.cs": "f", "bin/Release/y.cs": "g"}
    http = _TreeHttp(tree, {p: "SRC" for p in tree})
    files, capped = fetch_source_subtree(http, "o", "n", "sha")
    assert set(files) == {"src/lib.rs", "Tools/W.cs", "src/main/java/T.java", "Main.kt"}  # target/obj/bin excluded


def test_source_subtree_truncated_tree_is_capped():
    files, capped = fetch_source_subtree(_TreeHttp({}, {}, truncated=True), "o", "n", "sha")
    assert files == {} and capped is True


def test_source_subtree_respects_skip_and_file_cap():
    tree = {f"src/f{i}.py": "x" for i in range(140)}   # > _SRC_MAX_FILES(120)
    http = _TreeHttp(tree, {p: "SRC" for p in tree})
    files, capped = fetch_source_subtree(http, "o", "n", "sha", skip={"src/f0.py"})
    assert "src/f0.py" not in files and len(files) <= 120 and capped is True


class _FakeHttp:
    def __init__(self, *, files, default_branch="main", sha="abc123", repo_status=200):
        self._files = files
        self._db = default_branch
        self._sha = sha
        self._repo_status = repo_status
        self.refs_seen = set()

    def get_json(self, url):
        if url.endswith("/o/n"):
            return self._repo_status, {"default_branch": self._db}
        if "/commits/" in url:
            return 200, {"sha": self._sha}
        return 404, None

    def get_contents(self, owner, name, path, ref):
        self.refs_seen.add(ref)
        return (200, self._files[path]) if path in self._files else (404, None)


def test_fetch_anchors_single_sha_and_resolves_entrypoint():
    files = {"package.json": json.dumps({"name": "x", "bin": "dist/server.js"}),
             "dist/server.js": "new McpServer({})"}
    http = _FakeHttp(files=files)
    out = fetch(http, "o", "n")
    assert out["commit_sha"] == "abc123"
    assert http.refs_seen == {"abc123"}            # every fetch pinned to one SHA
    assert "dist/server.js" in out["files"]         # entrypoint resolved from package.json bin


def test_transient_repo_call_raises_content_unavailable():
    with pytest.raises(ContentUnavailable):
        fetch(_FakeHttp(files={}, repo_status=403), "o", "n")


def test_resolve_head_returns_sha_and_repo_object():
    sha, repo = resolve_head(_FakeHttp(files={}, default_branch="main", sha="abc123"), "o", "n")
    assert sha == "abc123" and repo["default_branch"] == "main"


def test_manifest_prepass_overflow_sets_capped():
    # 9 manifest candidates > _MANIFEST_MAX_FILES(8): the dropped one must flip capped=True.
    tree = {f"c{i}/agents.yaml": "role: r" for i in range(9)}
    http = _TreeHttp(tree, {p: "B" for p in tree})
    files, capped = fetch_source_subtree(http, "o", "n", "sha")
    assert len(files) == 8 and capped is True


def test_resolve_head_404_is_repo_gone():
    with pytest.raises(RepoGone):
        resolve_head(_FakeHttp(files={}, repo_status=404), "o", "n")


def test_resolve_head_401_is_content_unavailable():
    with pytest.raises(ContentUnavailable):
        resolve_head(_FakeHttp(files={}, repo_status=401), "o", "n")


def test_fetch_files_pins_the_given_sha():
    http = _FakeHttp(files={"package.json": "{}"})
    fetch_files(http, "o", "n", "pinnedsha")
    assert http.refs_seen == {"pinnedsha"}


def test_decode_contents_and_slug():
    payload = {"content": base64.b64encode(b"hello").decode(), "encoding": "base64"}
    assert _decode_contents(payload) == "hello"
    assert _decode_contents({"encoding": "none", "content": ""}) is None   # >1MB guard
    assert _valid_slug("owner-name.dot_1")
    assert not _valid_slug("../evil") and not _valid_slug("..") and not _valid_slug(".")


def test_fetch_rejects_traversal_owner():
    class _H:
        def get_json(self, url):
            raise AssertionError("must not fetch with an invalid owner/name")
    with pytest.raises(ContentUnavailable):
        fetch(_H(), "..", "n")


def test_agent_manifest_prepass_beats_density_cap():
    tree = {f"src/f{i}.py": "x" for i in range(125)}                 # dense dir fills the 120-file cap
    tree["src/cfg/agents.yaml"] = "researcher:\n  role: R\n  goal: G"
    tree["src/cfg/tasks.yaml"] = "t: x"
    http = _TreeHttp(tree, {p: "BODY" for p in tree})
    files, capped = fetch_source_subtree(http, "o", "n", "sha")
    assert "src/cfg/agents.yaml" in files and "src/cfg/tasks.yaml" in files   # pre-pass won
    assert capped is True


def test_manifest_prepass_respects_exclusions():
    tree = {"node_modules/x/agents.yaml": "role: r", "src/a.py": "x"}
    http = _TreeHttp(tree, {p: "B" for p in tree})
    files, _ = fetch_source_subtree(http, "o", "n", "sha")
    assert "node_modules/x/agents.yaml" not in files


def test_phase1_fetches_requirements_and_langgraph_json():
    files = {"requirements.txt": "langgraph\n", "langgraph.json": "{}"}
    out = fetch(_FakeHttp(files=files), "o", "n")
    assert "requirements.txt" in out["files"] and "langgraph.json" in out["files"]


def test_allowlist_version_is_7():
    assert ALLOWLIST_VERSION == 7   # 6->7: agent_run_shape v3 co-occurrence changes marker-driven selection


def test_select_source_paths_selects_skill_md_bodies_independent_of_is_source():
    # SKILL.md is neither in _SRC_EXTS nor a "source" file by _is_source's rules --
    # the new _NEWTYPE_MANIFESTS pre-pass must select it purely by basename, at any
    # depth, alongside ordinary source files.
    candidates = {
        "skills/a/SKILL.md": ("s1", 20),
        "skills/b/SKILL.md": ("s2", 15),
        "SKILL.md": ("s3", 10),
        "src/main.py": ("s4", 10),
    }
    selected, capped = select_source_paths(candidates)
    assert {"skills/a/SKILL.md", "skills/b/SKILL.md", "SKILL.md"} <= set(selected)
    assert "src/main.py" in selected
    assert capped is False


def test_select_source_paths_excludes_skill_md_in_excluded_dir():
    candidates = {"node_modules/x/SKILL.md": ("s1", 10), "src/main.py": ("s2", 10)}
    selected, _capped = select_source_paths(candidates)
    assert "node_modules/x/SKILL.md" not in selected
    assert "src/main.py" in selected


def _cand(paths):  # path -> (blob_sha, size)
    return {p: ("s" * 40, 100) for p in paths}


def test_tool_dir_files_survive_cap_over_denser_nontool_dir():
    # DISCRIMINATING: 30 tool files spread thin across 10 subdirs (low per-dir density) + a dense
    # 100-file common/ dir = 130 candidates, EXCEEDING the 120-file cap so eviction actually happens.
    # Without the priority pass the density sort picks common/ first and only ~20 tool files survive;
    # with it, all 30 tool files are selected. (This is the mongodb src/tools/** scenario.)
    paths = [f"src/tools/d{d}/t{i}.ts" for d in range(10) for i in range(3)] \
        + [f"src/common/f{i}.ts" for i in range(100)]
    sel, capped = select_source_paths(_cand(paths))
    assert sum(p.startswith("src/tools/") for p in sel) == 30   # ALL tool files, not just the densest slice
    assert capped is True


def test_package_entry_file_survives_cap_over_denser_lib():
    # DISCRIMINATING: 1 package-entry index.ts + 130 lib files = 131 candidates > 120-file cap. Without
    # the priority pass the dense lib/ dir fills the cap and index.ts is evicted; with it, the entry
    # file is kept. (This is the context7 packages/mcp/src/index.ts scenario.)
    paths = ["packages/mcp/src/index.ts"] + [f"packages/mcp/src/lib/f{i}.ts" for i in range(130)]
    sel, capped = select_source_paths(_cand(paths))
    assert "packages/mcp/src/index.ts" in sel
    assert capped is True


def test_src_max_files_is_120():
    assert content._SRC_MAX_FILES == 120


_SHAPE = {"marker": "StateGraph", "marker_class": "agent_run_shape",
          "line_no": 1, "line": "StateGraph("}


def test_hits_absent_preserves_existing_priority_behaviour():
    # Default hits=() must leave today's tool/entrypoint-first ordering untouched.
    cands = {"src/tools/a.ts": 10, "src/index.ts": 10, "lib/x.ts": 10}
    chosen, _ = select_source_paths(cands)
    assert chosen.index("src/tools/a.ts") < chosen.index("lib/x.ts")
    assert chosen.index("src/index.ts") < chosen.index("lib/x.ts")


def test_marker_hit_path_outranks_the_density_sorted_rest():
    cands = {"agent/loop.py": 10, "lib/a.ts": 10, "lib/b.ts": 10, "lib/c.ts": 10}
    chosen, _ = select_source_paths(cands, hits=[{**_SHAPE, "path": "agent/loop.py"}])
    assert chosen[0] == "agent/loop.py"


def test_agent_class_hit_outranks_a_tool_reservation_d7():
    # D7: agents are the priority; when both compete, agent evidence takes the slot.
    cands = {"src/tools/a.ts": 10, "agent/loop.py": 10}
    chosen, _ = select_source_paths(cands, hits=[{**_SHAPE, "path": "agent/loop.py"}])
    assert chosen.index("agent/loop.py") < chosen.index("src/tools/a.ts")


def test_non_agent_hit_does_not_displace_a_tool_reservation():
    # A provider-identifier hit is additive only -- it must not evict tool files.
    hit = {"path": "cfg/settings.py", "marker": "OPENAI_API_KEY",
           "marker_class": "provider_identifier", "line_no": 1, "line": "OPENAI_API_KEY"}
    cands = {"src/tools/a.ts": 10, "cfg/settings.py": 10}
    chosen, _ = select_source_paths(cands, hits=[hit])
    assert chosen.index("src/tools/a.ts") < chosen.index("cfg/settings.py")


def test_hit_paths_that_are_not_candidates_are_ignored_and_allowlist_is_7():
    cands = {"lib/a.ts": 10}
    chosen, _ = select_source_paths(cands, hits=[{**_SHAPE, "path": "gone/x.py"}])
    assert chosen == ["lib/a.ts"]
    assert ALLOWLIST_VERSION == 7


def test_agent_lift_is_capped_so_the_tool_reservation_always_survives():
    # DISCRIMINATING: 130 agent-class hit paths + 10 tool files = 140 candidates, exceeding the
    # 120-file cap. With an UNBOUNDED D7 lift the 130 agent paths sit ahead of the reservation
    # and fill all 120 slots, selecting ZERO tool files -- the mcp_server regression spec §5
    # forbids. The cap (_SRC_MAX_FILES // 3 == 40) bounds the lift, so the reservation is
    # reached and every tool file survives; the overflow is demoted, not dropped.
    agents = [f"agent/loop{i}.py" for i in range(130)]
    tools = [f"src/tools/t{i}.ts" for i in range(10)]
    hits = [{**_SHAPE, "path": p} for p in agents]
    chosen, capped = select_source_paths(_cand(agents + tools), hits=hits)
    assert capped is True
    assert set(tools) <= set(chosen)                       # reservation intact
    cap = content._SRC_MAX_FILES // 3
    assert chosen[:cap] == agents[:cap]                    # lift keeps exactly its bound...
    assert chosen[cap:cap + len(tools)] == tools           # ...then the reservation
    assert chosen[cap + len(tools)] == agents[cap]         # overflow demoted, not dropped


def test_agent_lift_is_byte_bounded_so_large_hit_files_cannot_starve_the_reservation():
    # DISCRIMINATING: the FILE cap alone does not protect the reservation. Blobs are fetched
    # up to 1MiB each, so a few large lifted files exhaust _SRC_MAX_BYTES while sitting well
    # inside the 40-file cap -- and the selection loop `break`s on byte overflow, so the
    # reservation that follows is never reached and every tool file is lost (spec §5).
    # Here: 10 agent hits x 200KB = 2MB, over the 1.6MB source budget but only a quarter of
    # the file cap, so the count bound never engages. Without the byte bound the lift eats
    # the budget and ZERO tool files are selected; with it, the lift keeps only what fits in
    # _SRC_MAX_BYTES // 3 and demotes the rest behind the reservation.
    # (Not synthetic: goose already spends 43% of the budget on 10 lifted files, one 212KB.)
    big = 200_000
    agents = [f"agent/loop{i}.py" for i in range(10)]
    tools = [f"src/tools/t{i}.ts" for i in range(5)]
    cands = {p: ("s" * 40, big) for p in agents}
    cands.update({p: ("s" * 40, 100) for p in tools})
    chosen, _capped = select_source_paths(
        cands, hits=[{**_SHAPE, "path": p} for p in agents])
    assert set(tools) <= set(chosen), "agent lift starved the tool reservation"
    fits = content._SRC_MAX_BYTES // 3 // big              # how many 200KB files the bound allows
    assert fits == 2, fits                                 # guards the arithmetic above
    assert chosen[:fits] == agents[:fits]                  # lift keeps exactly its byte bound...
    assert chosen[fits:fits + len(tools)] == tools         # ...then the reservation
    assert agents[fits] in chosen                          # overflow demoted, not dropped
