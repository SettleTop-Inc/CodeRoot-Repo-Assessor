from assessor.assessment import assemble
from assessor.assessment.content import (ALLOWLIST_VERSION, _MANIFEST_MAX_BYTES,
                                             _MANIFEST_MAX_FILES, _SRC_MAX_FILES,
                                             dep_manifest_paths, select_source_paths)


from conftest import _S


def _cand(paths):  # path -> (blob_sha, size)
    return {p: ("s" * 40, 100) for p in paths}


def _sized(sizes: dict):  # {path: size} -> candidate map
    return {p: ("s" * 40, n) for p, n in sizes.items()}


def select_paths(paths):
    """The live selector (`GitContentFetcher` path) over a candidate map."""
    selected, _capped = select_source_paths(_cand(paths))
    return selected


def test_selects_subdir_dependency_manifests():
    """gorilla-shaped: no root manifest, real manifests in subdirs. Previously
    selected nothing, so the bespoke-agent dep gate could never fire."""
    paths = ("README.md", "goex/requirements.txt", "berkeley-function-call-leaderboard/pyproject.toml")
    selected = select_paths(paths)
    assert "goex/requirements.txt" in selected
    assert "berkeley-function-call-leaderboard/pyproject.toml" in selected


def test_selects_non_python_js_manifests():
    """Go/Rust/Java/C# repos were structurally invisible to dep detection."""
    paths = ("README.md", "go.mod", "crates/agent/Cargo.toml", "pom.xml", "src/App.csproj")
    selected = select_paths(paths)
    for p in ("go.mod", "crates/agent/Cargo.toml", "pom.xml", "src/App.csproj"):
        assert p in selected


def test_agent_manifests_still_win_under_cap():
    """REGRESSION GUARD: dependency manifests share the `_MANIFEST_MAX_BYTES` budget
    with `_AGENT_MANIFESTS`, so ORDER of the two pre-passes is load-bearing — a repo
    with many dep manifests must not starve agents.yaml, which feeds classify_agent.

    The sizes below are chosen so the SHARED BYTE budget actually binds (the file caps
    are per-pass slices and can never starve anything): `_MANIFEST_MAX_FILES` dep
    manifests of exactly `_MANIFEST_MAX_BYTES / _MANIFEST_MAX_FILES` bytes consume the
    whole budget to the byte. With the correct order (agent pass FIRST) agents.yaml
    takes its 100 bytes and 7 dep manifests follow; move the dep pre-pass ahead of the
    agent pre-pass and agents.yaml no longer fits — this test then FAILS (verified by
    mutation)."""
    dep_size = _MANIFEST_MAX_BYTES // _MANIFEST_MAX_FILES
    sizes = {"README.md": 100, "agents.yaml": 100}
    sizes |= {f"pkg{i:02d}/package.json": dep_size for i in range(20)}
    selected, _capped = select_source_paths(_sized(sizes))
    assert "agents.yaml" in selected
    # the dep pre-pass really did run and really was byte-bound (not a vacuous pass)
    assert 0 < len(dep_manifest_paths(selected)) < _MANIFEST_MAX_FILES


def test_dep_manifests_do_not_consume_the_source_file_budget():
    """Dep manifests are advisory; source files are the product. A repo at the
    `_SRC_MAX_FILES` limit must still get ALL of its source files (and report
    capped=False) no matter how many dep manifests it also carries — otherwise every
    monorepo silently loses up to `_MANIFEST_MAX_FILES` real source files and then
    (via capped=True) reports mcp `tools_complete=False`."""
    src = tuple(f"src/f{i:03d}.ts" for i in range(_SRC_MAX_FILES))
    deps = tuple(f"packages/p{i}/package.json" for i in range(_MANIFEST_MAX_FILES))
    selected, capped = select_source_paths(_cand(src + deps))
    assert all(p in selected for p in src)
    assert capped is False


def test_dep_manifest_overflow_does_not_flip_source_coverage_capped():
    """`capped` is the SOURCE-coverage signal: downstream it forces mcp
    `tools_complete=False` ("tool list not fully scanned") and an "agent manifests
    may be missing" known-unknown. An ordinary JS monorepo (9 workspace
    `package.json` + a fully-selected source tree) truncates only ADVISORY dep
    manifests, so it must not claim the source scan was truncated."""
    paths = (tuple(f"packages/p{i}/package.json" for i in range(9))
             + tuple(f"src/f{i}.ts" for i in range(10)))
    selected, capped = select_source_paths(_cand(paths))
    assert capped is False
    assert all(f"src/f{i}.ts" in selected for i in range(10))       # every source file selected


def test_dropped_dependency_manifests_are_not_silent():
    """Truncation must stay honest: the dropped manifests are still visible by
    comparing the tree inventory against what was fetched — that comparison is what
    `assemble` turns into a specific known-unknown (see the assemble test below)."""
    paths = tuple(f"pkg{i}/package.json" for i in range(20))
    selected, _capped = select_source_paths(_cand(paths))
    assert len(dep_manifest_paths(paths)) == 20
    assert len(dep_manifest_paths(selected)) == 8                   # _MANIFEST_MAX_FILES


def test_truncated_dep_manifests_emit_a_specific_known_unknown():
    """The honest signal is more specific than the shared `capped` one: it names the
    dep-manifest shortfall, not "agent manifests may be missing" (the dep pass runs
    LAST, so agent manifests are never the ones dropped). It states only the COUNT —
    naming the acquire cap as the CAUSE would over-claim, since the partial clone's
    `--filter=blob:limit=1MiB` can drop a manifest before it ever becomes a candidate."""
    content = {"README.md": "x"} | {f"pkg{i}/package.json": "{}" for i in range(8)}
    paths = tuple(content) + tuple(f"pkg{i}/package.json" for i in range(8, 20))
    rec = assemble.build("https://github.com/o/n", content, "sha", None, paths=paths, settings=_S)
    details = [k["detail"] for k in rec["assessment"]["known_unknowns"]]
    assert any(d.startswith("dependency manifests incomplete (8 of 20 acquired)")
               for d in details)
    assert not any("agent manifests may be missing" in d for d in details)
    # honesty: no CAUSE is asserted (the cap is only one of the ways a manifest can be
    # missing from `content` — an oversized blob never entered `candidates` at all).
    assert not any("acquire cap" in d or "truncated at" in d for d in details)


def test_missing_path_inventory_states_coverage_undetermined():
    """A known-unknown must not go SILENT exactly when coverage is least verifiable.
    `paths` is `repo_acquisition.tree_paths`, which can be NULL/empty; then `dep_seen`
    is 0, `dep_seen > dep_got` can never fire, and a repo whose every dep manifest was
    dropped would report nothing at all — implying completeness by omission. With no
    denominator the honest statement is that coverage is UNDETERMINED."""
    content = {"README.md": "x", "pkg0/package.json": "{}"}
    rec = assemble.build("https://github.com/o/n", content, "sha", None, paths=(), settings=_S)
    details = [k["detail"] for k in rec["assessment"]["known_unknowns"]]
    assert any(d.startswith("dependency-manifest coverage undetermined") for d in details)
    # honesty: "undetermined" must not masquerade as a counted shortfall, and no CAUSE
    # for the missing inventory is asserted.
    assert not any(d.startswith("dependency manifests incomplete") for d in details)


def test_present_path_inventory_does_not_claim_undetermined():
    """The undetermined signal is about a MISSING denominator only — with an inventory
    present, a complete acquisition still reports nothing."""
    content = {"README.md": "x", "pkg0/package.json": "{}"}
    rec = assemble.build("https://github.com/o/n", content, "sha", None, paths=tuple(content), settings=_S)
    details = [k["detail"] for k in rec["assessment"]["known_unknowns"]]
    assert not any(d.startswith("dependency-manifest coverage undetermined") for d in details)


def test_complete_dep_manifests_emit_no_truncation_known_unknown():
    content = {"README.md": "x", "pkg0/package.json": "{}"}
    rec = assemble.build("https://github.com/o/n", content, "sha", None, paths=tuple(content), settings=_S)
    details = [k["detail"] for k in rec["assessment"]["known_unknowns"]]
    assert not any(d.startswith("dependency manifests incomplete") for d in details)


def test_dependency_manifests_excluded_in_vendor_dirs():
    """`node_modules/*/package.json` is a vendored dep of a dep, not this repo's manifest."""
    selected = select_paths(("node_modules/x/package.json", "vendor/y/go.mod", "go.mod"))
    assert selected == ["go.mod"]


def test_allowlist_version_bumped_for_dep_manifest_widening():
    assert ALLOWLIST_VERSION >= 5   # 4->5 landed this widening; later bumps (5->6, ...) only raise it further
