"""Drift guard for the dep-manifest recall widening (Tasks 2-3 of
docs/superpowers/plans/2026-07-25-agent-candidate-egress-and-recall.md): fetching and
parsing MORE manifests must be INERT where there is no new input, and must make the
bespoke-agent dep gate reachable where there is. A candidate is never a
classification — `agent` must not enter `asset_types` from a probe.

The pinned `content_fingerprint` values below are REGISTRY-VERSION-NORMALIZED: they hash
the real `build_payload()` dict `assemble.build` produces, with `registry_version`
overridden to a fixed sentinel before hashing (see `_fingerprint_normalized` below). A
`REGISTRY_VERSION` bump (0028_skill_positional went 8->9) is therefore INERT to this pin
by construction — it churns every real `content_fingerprint`, but not this normalized
one — while any OTHER payload drift (asset_types, per_type facts, coordinates, spdx)
still fails loudly, which is the whole point of a drift guard. Pinned by measuring this
exact normalized hash against the code as it stands today; they are not `origin/main`
values and are not expected to be."""
import json
import pathlib
from unittest import mock

from assessor.assessment import assemble
from assessor.assessment import fingerprint as fingerprint_mod
from assessor.assessment.content import select_source_paths


from assessor.config import Settings

_S = Settings(assessor_api_token="x")

_REGISTRY_VERSION_SENTINEL = "__REGISTRY_VERSION_SENTINEL__"


def _fingerprint_normalized(repo_url: str, content: dict, paths, **build_kwargs) -> str:
    """`content_fingerprint` for `assemble.build(...)`, with `registry_version` frozen to
    a fixed sentinel so the pin is invariant to REGISTRY_VERSION bumps. Captures the REAL
    payload `assemble.build` hands to `compute_fingerprint` (by spying on the call rather
    than reconstructing the payload independently, which would assert nothing — see the
    brief's Task 2 fix-round IMPORTANT-2), then re-hashes it with `registry_version`
    overridden. Any drift in the OTHER payload fields still changes the result."""
    captured = {}
    real_compute = fingerprint_mod.compute_fingerprint

    def _spy(payload):
        captured["payload"] = payload
        return real_compute(payload)

    with mock.patch("assessor.assessment.assemble.compute_fingerprint", side_effect=_spy):
        assemble.build(repo_url, content, "sha", None, paths=paths, **build_kwargs, settings=_S)
    payload = dict(captured["payload"])
    payload["registry_version"] = _REGISTRY_VERSION_SENTINEL
    return real_compute(payload)


# Registry-version-normalized content_fingerprint (see _fingerprint_normalized above).
# Re-pin by re-running _fingerprint_normalized for each key if THIS pin ever needs to
# move -- a move here means genuine, non-registry-version payload drift.
NORMALIZED_FINGERPRINTS = {
    "openai_prose_only": "773b1d0f507cfaccabdd2aedb43fb3e9dbabdc749f5b4df0f9f4686805d50109",
    "langgraph_root_req": "164f9c2be6e0533499baae7737ca04e505c8eed0007ee717a4ac1011d319532d",
    "crewai_pyproject": "e54ec3796fd944187ad3691b2fda14d2fc4bf13d3ab12f595ee4cb862d957ab1",
    "mcp_sdk_root_pkg": "77450e8a68352ca772e8ae18dc19ae64d40406e32461f26d8710e2510ec3d479",
    "root_pkgjson_plain": "7e9994b5e9d9b21d92e66bb408c7b04c3280a4215247bc8a98c65b283a42c319",
    "plain_lib": "1d628f2e0c364e1b5f549dc5473a0b580c01b09ee4ae2c2b0d1159f28349587b",
}

# Root-only, Python/JS-only manifests: nothing here is newly fetchable or newly
# parseable, so the widening must not touch any of these outcomes.
ROOT_ONLY_CORPUS = {
    # general LLM dep + agent PROSE but no construct: R3 "uses-an-LLM != is-an-agent",
    # so this is NOT an agent — it never was, at REGISTRY_VERSION 8 either.
    "openai_prose_only": ({"README.md": "a python agent",
                           "requirements.txt": "openai>=1.0\n"}, []),
    "langgraph_root_req": ({"README.md": "# thing",
                            "requirements.txt": "langgraph==0.2\n"}, ["agent"]),
    "crewai_pyproject": ({"pyproject.toml": 'dependencies = ["crewai>=0.1"]\n',
                          "README.md": "an ai agent"}, ["agent"]),
    "mcp_sdk_root_pkg": ({"package.json": json.dumps(
        {"dependencies": {"@modelcontextprotocol/sdk": "^1"}})}, ["mcp_server"]),
    "root_pkgjson_plain": ({"package.json": json.dumps(
        {"dependencies": {"express": "^4"}})}, []),
    "plain_lib": ({"README.md": "just a plain library"}, []),
}


def test_widened_deps_do_not_change_classification_on_unchanged_content():
    """Tasks 2-3 widen what we FETCH and PARSE. For content that does not contain
    any newly-fetchable manifest, classification must be byte-identical — the
    widening must be inert where it has no new input."""
    for key, (content, expected_types) in ROOT_ONLY_CORPUS.items():
        rec = assemble.build(f"https://github.com/o/{key}", content, "sha", None,
                             paths=tuple(content), settings=_S)
        assert rec["asset_types"] == expected_types, key


def test_content_fingerprint_is_inert_except_for_registry_version():
    """`registry_version` is a `build_payload` input, so it is EXPECTED to move
    `content_fingerprint` on every `REGISTRY_VERSION` bump — a field served on
    `AssessmentSummary`, so a bump churns every marketplace poller's view of every asset.
    That is unavoidable and, when the bump is a real marker-semantics change (as in
    0028_skill_positional), correct.

    What must NOT move `content_fingerprint` is the dep-manifest recall widening this
    file guards: fetching/parsing more manifests must be inert for content with no new
    manifest. `_fingerprint_normalized` freezes `registry_version` to a sentinel before
    hashing so this pin asserts exactly that — invariant to REGISTRY_VERSION bumps,
    sensitive to everything else."""
    for key, (content, _types) in ROOT_ONLY_CORPUS.items():
        fp = _fingerprint_normalized(f"https://github.com/o/{key}", content, tuple(content))
        assert fp == NORMALIZED_FINGERPRINTS[key], key


def test_subdir_manifest_now_reaches_the_bespoke_gate():
    """gorilla-shaped: dep gate A previously hard-returned because the dep reach was
    empty. With a subdir manifest present, `all_deps_wide` makes it reachable — and the
    result is a CANDIDATE probe only, never a classification."""
    content = {"README.md": "an autonomous agent that calls tools in a loop",
               "goex/requirements.txt": "openai>=1.0\n"}
    rec = assemble.build("https://github.com/ShishirPatil/gorilla", content, "sha", None,
                         paths=tuple(content), bucket_b={"topics": ["llm"], "description": "x"}, settings=_S)
    probes = rec["assessment"]["coverage_probes"]
    assert any(p["type"] == "agent" and p["evidence_state"] == "candidate" for p in probes)
    assert "agent" not in rec["asset_types"]      # INVARIANT: candidate is not a classification


def test_subdir_agent_framework_dep_never_seizes_primary_type():
    """awslabs/mcp- and stripe/agent-toolkit-shaped (63 and 51 non-root dep manifests
    respectively, both currently `primary_type=mcp_server`). Acquire now fetches those
    subdir manifests, so an AGENT_DEPS package named in ANY of them used to become a
    STRONG `agent` match — and PRECEDENCE = ("agent", "mcp_server", ...) would hand the
    partner's core type over to `agent`. The classification reach is root-only, so it
    must not: `mcp_server` stays primary and `agent` stays out of `asset_types`."""
    content = {"README.md": "# tools",
               "package.json": json.dumps({"dependencies": {"@modelcontextprotocol/sdk": "^1"}}),
               "packages/toolkit/package.json": json.dumps({"dependencies": {"crewai": "^1"}}),
               "src/agents/py/requirements.txt": "langgraph==0.2\n"}
    rec = assemble.build("https://github.com/stripe/agent-toolkit", content, "sha", None,
                         paths=tuple(content), settings=_S)
    assert rec["asset_types"] == ["mcp_server"]
    assert rec["asset_type"] == "mcp_server"
    frameworks = [c.get("framework") for c in rec["assessment"]["compositions"].values()]
    assert not any((f or {}).get("value") == "crewai" for f in frameworks)


# A monorepo whose ROOT manifest is unremarkable (`requests`) and whose subpackage
# manifest is agent-native (`crewai`) — the exact shape the marketplace ingests as a
# subdir subject. The two tests below are a PAIR and must be read together: they pin
# the narrow/wide split's whole point, that the SAME repo classifies differently
# depending on which SUBJECT is being assessed.
MONOREPO_SUBPKG = {
    "README.md": "# platform monorepo",
    "pyproject.toml": 'dependencies = ["requests>=2"]\n',
    "packages/agentkit/README.md": "# agentkit",
    "packages/agentkit/pyproject.toml": 'dependencies = ["crewai>=0.1"]\n',
    "packages/agentkit/main.py": "print(1)\n",
}


def _acquired(bodies: dict) -> dict:
    """What acquire would actually store: run the real selector over the tree, then keep
    only the selected bodies (plus READMEs, which the phase-0 pass always fetches).
    Going through the selector is the point — the subdir behaviour below is a
    CONSEQUENCE of the `ALLOWLIST_VERSION` 4->5 widening, and asserting it against a
    hand-built `content` dict would pass on origin/main too and pin nothing."""
    selected, _capped = select_source_paths({p: ("s" * 40, 100) for p in bodies})
    keep = set(selected) | {p for p in bodies if p.rsplit("/", 1)[-1].startswith("README.")}
    return {p: b for p, b in bodies.items() if p in keep}


def test_subdir_subject_classifies_from_its_own_manifest():
    """INTENDED SEMANTICS, not an accident: for a subdir subject its OWN manifest IS its
    root manifest. `subject.filter_content_to_subdir` re-roots
    `packages/agentkit/pyproject.toml` to `pyproject.toml` before classification, so the
    root-only `classify_agent._all_deps` reads the subpackage's own dependencies.

    Measured on origin/main (0ff7d2f), this exact selector+assemble sequence yielded
    `selected == ['packages/agentkit/main.py']` and `not_an_asset` / `[]` — the subdir's
    `pyproject.toml` was never FETCHED (root-exact `_PHASE1`), so the subject fell
    through for lack of the file. That was a coverage artifact reported as a judgement.
    With the widening the file is present and the subject is classified from it: a
    genuine recall improvement for exactly the monorepo subdir assets the marketplace
    ingests."""
    content = _acquired(MONOREPO_SUBPKG)
    assert "packages/agentkit/pyproject.toml" in content      # the widening's precondition

    rec = assemble.build("https://github.com/o/platform", content, "sha", None,
                         paths=tuple(MONOREPO_SUBPKG), subdir="packages/agentkit", settings=_S)
    assert rec["asset_type"] == "agent"
    assert rec["asset_types"] == ["agent"]
    assert rec["classification_confidence"] == 0.95
    markers = [e["marker"] for m in rec["assessment"]["classification"]["matches"]
               for e in m["evidence"]]
    assert "agent-native dep crewai" in markers


def test_whole_repo_is_not_agent_from_a_subpackage_manifest():
    """THE OTHER HALF OF THE PAIR — and the whole point of the narrow/wide split. The
    SAME acquired content assessed as the WHOLE REPO must NOT become an `agent`: the
    repo-root `pyproject.toml` pins only `requests`, and `crewai` lives in a subpackage.
    If this ever flips, a subpackage's framework dep is seizing the repo's
    `primary_type` (PRECEDENCE puts `agent` ahead of `mcp_server`) — the
    auto-classification the owner rejected."""
    content = _acquired(MONOREPO_SUBPKG)
    rec = assemble.build("https://github.com/o/platform", content, "sha", None,
                         paths=tuple(MONOREPO_SUBPKG), settings=_S)
    assert "agent" not in rec["asset_types"]
    assert rec["asset_type"] == "not_an_asset"
    # Registry-version-normalized (see _fingerprint_normalized banner above): this pin
    # is inert to the 0028_skill_positional REGISTRY_VERSION 8->9 bump and asserts only
    # that the dep-manifest-recall widening leaves this whole-repo subject's fingerprint
    # untouched.
    fp = _fingerprint_normalized("https://github.com/o/platform", content, tuple(MONOREPO_SUBPKG))
    assert fp == "41b37ab7514764c2c53b002461b90b59b279f2c2060d8d2c728d1932f839dee5"


def test_wide_dep_reach_is_not_wired_into_classification():
    """Structural guard for the split: the widened reach has exactly ONE consumer.
    If a future edit imports it into a classify_*/compose_* module, that module starts
    changing real `asset_types`/`primary_type`/fingerprint from subdir manifests —
    the auto-classification the owner rejected, arriving indirectly."""
    root = pathlib.Path(__file__).resolve().parents[2] / "assessor" / "assessment"
    users = sorted(p.name for p in root.glob("*.py")
                   if "all_deps_wide" in p.read_text(encoding="utf-8"))
    assert users == ["classify_agent.py", "probes.py"]
