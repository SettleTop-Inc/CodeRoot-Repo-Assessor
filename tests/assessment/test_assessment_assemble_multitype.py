import json

import pytest

from assessor.assessment import assemble, probes
from assessor.assessment.registry import TIER_CONF


from conftest import _S

HYBRID = {  # §13 true hybrid: agent-native dep AND an MCP server construct
    "package.json": json.dumps({"dependencies": {"@openai/agents": "^1",
                                                 "@modelcontextprotocol/sdk": "^1"}}),
    "src/server.ts": 'const s = new McpServer({}); s.registerTool("search", {});',
    "src/agent.ts": 'const a = run(model="claude-sonnet-5")'}

CONSUMER_ONLY = {  # §13/§12: mcp CLIENT dep only — pins the accepted false hybrid
    "pyproject.toml": '[project]\ndependencies = ["mcp", "openai-agents"]\n',
    "src/app.py": "params = StdioServerParameters(command='npx')\n"}


def test_true_hybrid_multitype_record():
    rec = assemble.build("https://github.com/o/hy", HYBRID, "sha", None, settings=_S)
    assert rec["asset_types"] == ["agent", "mcp_server"]
    assert rec["asset_type"] == "agent"                       # tie -> precedence
    a = rec["assessment"]
    assert set(a["compositions"]) == {"agent", "mcp_server"}
    assert a["composition"] == a["compositions"]["agent"]     # legacy = primary's
    assert a["classification"]["primary_tiebroken"] is True
    assert a["classification"]["tie_set"] == ["agent", "mcp_server"]
    assert a["classification"]["types_checked"] == ["agent", "mcp_server", "prompt", "skill"]


def test_consumer_only_pins_accepted_false_hybrid():
    rec = assemble.build("https://github.com/o/co", CONSUMER_ONLY, "sha", None, settings=_S)
    assert "mcp_server" in rec["asset_types"]                 # documented §12 limitation


def test_not_an_asset_reshape():
    rec = assemble.build("https://github.com/o/x", {"README.md": "a plain lib"}, "sha", None, settings=_S)
    assert rec["is_asset"] is False and rec["asset_types"] == []
    a = rec["assessment"]
    assert a["compositions"] == {} and a["composition"] is None and a["risk"] == {}
    assert a["classification"]["types_checked"] == ["agent", "mcp_server", "prompt", "skill"]


def test_known_unknowns_owned_and_deduped():
    rec = assemble.build("https://github.com/o/hy", HYBRID, "sha", None, settings=_S)
    entries = rec["assessment"]["known_unknowns"]
    assert all("asset_type" in e for e in entries)
    assert len({(e["asset_type"], e["detail"]) for e in entries}) == len(entries)


def test_topics_fact_from_bucket_b():
    rec = assemble.build("https://github.com/o/n", {"README.md": "x"}, "sha", None,
                         bucket_b={"topics": ["MCP-Server", "AI"], "description": "d"}, settings=_S)
    topics = rec["assessment"]["topics"]
    assert topics["value"] == ["ai", "mcp-server"]          # lowercased + sorted
    assert topics["source"] == "repo object (github topics)"
    assert "confidence" not in topics                        # it is a Fact, not AssessedField


def test_topics_unknown_when_absent():
    rec = assemble.build("https://github.com/o/n", {"README.md": "x"}, "sha", None, settings=_S)
    topics = rec["assessment"]["topics"]
    assert topics["value"] is None and topics["known_unknown"] == "no repo topics declared"


def test_topics_not_in_content_fingerprint():
    # topics is a served curation candidate, not structural identity — it must NOT move the fingerprint
    a = assemble.build("https://github.com/o/n", {"README.md": "x"}, "sha", None,
                       bucket_b={"topics": ["ai"]}, settings=_S)
    b = assemble.build("https://github.com/o/n", {"README.md": "x"}, "sha", None,
                       bucket_b={"topics": ["ml", "llm"]}, settings=_S)
    assert a["content_fingerprint"] == b["content_fingerprint"]


# -- declared-identity discriminate suppressor wired through assemble (§8.3) -----

SKILL_LIKE = {  # anthropics/skills shape: strong skill, mcp only via prose README mention
    "SKILL.md": "---\nname: pdf\ndescription: Work with PDFs\n---\nbody\n",
    "README.md": "# Agent Skills\nExample skills include MCP server generation and PDF processing.",
}


def test_declared_identity_suppressor_drops_prose_only_mcp():
    rec = assemble.build("https://github.com/o/skills", SKILL_LIKE, "sha", None,
                         bucket_b={"topics": ["agent-skills"], "description": "Agent Skills"},
                         paths=tuple(SKILL_LIKE), settings=_S)
    assert rec["asset_types"] == ["skill"]
    suppressed = rec["assessment"]["classification"]["suppressed"]
    assert any(s["asset_type"] == "mcp_server" for s in suppressed)


def test_taxonomy_uncovered_after_skill_prompt():
    rec = assemble.build("https://github.com/o/x", {"README.md": "a plain lib"}, "sha", None, settings=_S)
    cls = rec["assessment"]["classification"]
    assert cls["registry_version"] == 9
    assert cls["taxonomy_uncovered"] == ["dataset", "model", "tool", "workflow"]
    assert cls["types_checked"] == ["agent", "mcp_server", "prompt", "skill"]


# -- subdir path-scoping (marketplace subdir hybrid, Task B2) -------------------

_SKILL = "---\nname: pdf\ndescription: d\n---\nbody"


def test_subdir_scoped_skill_match():
    content = {"README.md": "x", "skills/pdf/SKILL.md": _SKILL, "src/server.ts": "new McpServer({})"}
    rec = assemble.build("https://github.com/o/n", content, "sha", None,
                         paths=tuple(content), subdir="skills/pdf", settings=_S)
    assert rec["asset_type"] == "skill"                     # scoped to the subdir subtree only
    a = rec["assessment"]
    assert a["subdir"] == "skills/pdf" and a["asset_id"] and \
           a["source_url"] == "https://github.com/o/n/tree/sha/skills/pdf"


def test_subdir_no_path_local_marker_is_indeterminate():
    content = {"README.md": "x", "src/server.ts": "new McpServer({})"}   # mcp marker is repo-root, not in subdir
    rec = assemble.build("https://github.com/o/n", content, "sha", None,
                         paths=tuple(content), subdir="docs", settings=_S)
    assert rec["is_asset"] is False
    details = [k["detail"] for k in rec["assessment"]["known_unknowns"]]
    assert "subdir_composition_indeterminate" in details


def test_whole_repo_unchanged_when_subdir_empty():
    content = {"src/server.ts": "new McpServer({})", "README.md": "an mcp server"}
    rec = assemble.build("https://github.com/o/n", content, "sha", None, paths=tuple(content), settings=_S)
    assert rec["assessment"].get("subdir", "") == "" and rec["asset_type"] == "mcp_server"


def test_whole_repo_fingerprint_unchanged_with_or_without_subdir_kwarg():
    content = {"src/server.ts": "new McpServer({})", "README.md": "an mcp server"}
    with_kwarg = assemble.build("https://github.com/o/n", content, "sha", None,
                                paths=tuple(content), subdir="", settings=_S)
    without_kwarg = assemble.build("https://github.com/o/n", content, "sha", None,
                                   paths=tuple(content), settings=_S)
    assert with_kwarg["content_fingerprint"] == without_kwarg["content_fingerprint"]


def test_declared_identity_suppressor_keeps_corroborated_mcp():
    # description "mcp tool" corroborates via our suppressor's _corroborated() but
    # does NOT match classify_mcp's own (stricter) declared-mcp promotion regex —
    # so this exercises the suppressor's corroboration path specifically, not
    # classify_mcp's independent strong-promotion shortcut.
    rec = assemble.build("https://github.com/o/skills", SKILL_LIKE, "sha", None,
                         bucket_b={"topics": [], "description": "mcp tool"},
                         paths=tuple(SKILL_LIKE), settings=_S)
    assert rec["asset_types"] == ["mcp_server", "skill"]
    mcp_match = next(m for m in rec["assessment"]["classification"]["matches"]
                      if m["asset_type"] == "mcp_server")
    assert mcp_match["marker_tier"] == "weak"
    suppressed = rec["assessment"]["classification"]["suppressed"]
    assert not any(s["asset_type"] == "mcp_server" for s in suppressed)


# -- BA3: bespoke-agent candidate is additive-only (spec §8/§10) ----------------

# litellm dep + agent topic + agentic README, no recognized framework/construct
# (classify_agent's GENERAL_LLM_DEPS/AGENT_DEPS do not include "litellm", so this
# never trips classify_agent itself — only the BA1 shape in probes.py).
BESPOKE_AGENT = {
    "pyproject.toml": '[project]\ndependencies = ["litellm"]\n',
    "README.md": "An autonomous agent that plans and executes tasks using an LLM.",
}


def test_bespoke_agent_candidate_is_additive_only(monkeypatch):
    kwargs = dict(bucket_b={"topics": ["agent"], "description": "bespoke LLM agent"},
                  paths=tuple(BESPOKE_AGENT))
    rec = assemble.build("https://github.com/o/bespoke", BESPOKE_AGENT, "sha", None, **kwargs, settings=_S)

    candidate = next((p for p in rec["assessment"]["coverage_probes"]
                      if p["type"] == "agent" and p["evidence_state"] == "candidate"), None)
    assert candidate is not None
    assert candidate["candidate_confidence"] in ("weak", "strong")
    assert "agent" not in rec["asset_types"]

    monkeypatch.setattr(probes, "_bespoke_agent_candidate", lambda *a, **k: None)
    suppressed_rec = assemble.build("https://github.com/o/bespoke", BESPOKE_AGENT, "sha", None,
                                    **kwargs, settings=_S)
    assert not any(p["type"] == "agent" and p["evidence_state"] == "candidate"
                  for p in suppressed_rec["assessment"]["coverage_probes"])

    # Classification-affecting fields are byte-identical with & without the
    # candidate — only coverage_probes differs (the whole point of candidate-only).
    assert rec["asset_types"] == suppressed_rec["asset_types"]
    assert rec["asset_type"] == suppressed_rec["asset_type"]
    assert rec["content_fingerprint"] == suppressed_rec["content_fingerprint"]
    assert rec["assessment"]["risk"] == suppressed_rec["assessment"]["risk"]
    assert "agent" not in suppressed_rec["asset_types"]


# -- Task 7: promotion of a citation-backed coverage-probe candidate ------------
#
# The four rules below are INVARIANTS of `registry.promote_from_probes` + its wiring
# in `assemble.build`; each has its own test. The LLM may only move INSIDE the
# envelope the deterministic pass established — it can strengthen a candidate the
# scan already surfaced, never invent a type, never outrank a real marker, and never
# move `content_fingerprint`.

# Every fixture below carries `_LOOP_FILE` so the citation RESOLVES — a citation naming
# a file we do not hold is refused outright (see the RULE 1 resolution tests).
_LOOP_FILE = "src/loop.py"
_LOOP_SRC = "def run(client):\n    while True:\n        client.chat()\n"
_CITATION = f"{_LOOP_FILE}:run"

PROMOTABLE_AGENT = {**BESPOKE_AGENT, _LOOP_FILE: _LOOP_SRC}

STRONG_MCP = {"package.json": json.dumps({"dependencies": {"@modelcontextprotocol/sdk": "^1"}}),
              _LOOP_FILE: _LOOP_SRC}

# A deterministic WEAK mcp_server match (prose + a bare `new Server(` construct, no SDK
# dep) — the case that exposed a promotion seizing the primary slot: `weak` vs `weak`
# ties on (tier, confidence) and falls through to PRECEDENCE, where `agent` is index 0.
WEAK_MCP = {"README.md": "a small mcp server for things",
            "src/index.ts": "const s = new Server({});\n",
            _LOOP_FILE: _LOOP_SRC}


def _recon(citation):
    """The `llm_reconciliation` envelope `probes._refine_bespoke_agent` emits
    (`shapes.assessed`) — the citation lives at value.citation."""
    return {"value": {"location": "agent loop", "citation": citation, "why": "w",
                      "note": "advisory — does not classify"},
            "confidence": 0.3, "evidence": [{"path": "llm", "marker": "bespoke_agent_reconciliation"}]}


def _probe(asset_type="agent", evidence_state="candidate", citation=_CITATION):
    return {"type": asset_type, "evidence_state": evidence_state,
            "candidate_confidence": "strong", "declared_by": ["topics"],
            "probe": "possible bespoke agent — litellm + agent topic. Curator review.",
            "llm_reconciliation": _recon(citation) if citation is not None else None}


def _build_with_probes(monkeypatch, probe_list, content=PROMOTABLE_AGENT,
                       url="https://github.com/o/bespoke", paths=None):
    monkeypatch.setattr(assemble.probes, "detect", lambda *a, **kw: list(probe_list))
    return assemble.build(url, content, "sha", None,
                          bucket_b={"topics": ["agent"], "description": "bespoke LLM agent"},
                          paths=tuple(content) if paths is None else paths, settings=_S)


def test_promotion_end_to_end_through_the_real_detector(monkeypatch):
    """Happy path with the REAL probe detector: only the gateway is stubbed, so this
    pins the whole chain — BA1 shape -> BA2 citation -> promotion -> asset_types."""
    monkeypatch.setattr(probes, "complete_json", lambda *a, **kw: {
        "tools_location": "the loop in src/loop.py", "code_citation": _CITATION,
        "reasoning": "a loop that calls the LLM and applies edits"})
    rec = assemble.build("https://github.com/o/bespoke", PROMOTABLE_AGENT, "sha", None,
                         bucket_b={"topics": ["agent"], "description": "bespoke LLM agent"},
                         paths=tuple(PROMOTABLE_AGENT), settings=_S)

    assert rec["asset_types"] == ["agent"]
    assert rec["is_asset"] is True and rec["asset_type"] == "agent"
    assert rec["classification_confidence"] == 0.6
    assert rec["assessment"]["promoted_types"] == [
        {"asset_type": "agent", "from_evidence_state": "candidate",
         "citation": _CITATION, "confidence": 0.6}]
    # The `absent` declared-identity agent probe in the same list carries a citation
    # too (same stubbed gateway) — only the `candidate` one may promote, and the
    # promotion never mutates the probe entries it read.
    states = {p["evidence_state"] for p in rec["assessment"]["coverage_probes"] if p["type"] == "agent"}
    assert states == {"absent", "candidate"}
    # Composition is NOT manufactured for a promoted type — the gap is named instead.
    assert "agent" not in rec["assessment"]["compositions"]
    assert any(k["asset_type"] == "agent" and "promoted from a coverage-probe candidate" in k["detail"]
               for k in rec["assessment"]["known_unknowns"])


# RULE 1 -----------------------------------------------------------------------

def test_rule1_candidate_with_citation_promotes(monkeypatch):
    rec = _build_with_probes(monkeypatch, [_probe()])
    assert "agent" in rec["asset_types"]


def test_rule1_undetermined_never_promotes_even_with_a_citation(monkeypatch):
    """`undetermined` means we did not scan enough to have a finding at all; a model
    citation over content we never read must not manufacture one."""
    rec = _build_with_probes(monkeypatch, [_probe(evidence_state="undetermined")])
    assert "agent" not in rec["asset_types"]
    assert rec["assessment"]["promoted_types"] == []
    assert rec["is_asset"] is False


def test_rule1_empty_citation_never_promotes(monkeypatch):
    """The prose-only case: the model had nothing but the repo's self-description,
    which is exactly the injection surface this gate exists to close."""
    rec = _build_with_probes(monkeypatch, [_probe(citation="")])
    assert "agent" not in rec["asset_types"] and rec["assessment"]["promoted_types"] == []


def test_rule1_whitespace_only_citation_never_promotes(monkeypatch):
    rec = _build_with_probes(monkeypatch, [_probe(citation="   ")])
    assert "agent" not in rec["asset_types"]


def test_rule1_absent_reconciliation_never_promotes(monkeypatch):
    """Gateway off -> `llm_reconciliation` is None; the deterministic candidate stands
    as a candidate, exactly as before this feature existed."""
    rec = _build_with_probes(monkeypatch, [_probe(citation=None)])
    assert "agent" not in rec["asset_types"] and rec["assessment"]["promoted_types"] == []


# RULE 1b — the citation must RESOLVE, not merely be non-empty --------------------
#
# Non-emptiness alone made the "deterministic envelope" far thinner than it sounds: a
# repo with one dependency line and one adjective, holding no source code at all,
# promoted on a fabricated path. Requiring the cited file to be something we actually
# acquired forces a code-level commitment — the line deterministic classification draws.

def test_citation_must_name_a_file_we_actually_hold(monkeypatch):
    rec = _build_with_probes(monkeypatch, [_probe(citation=f"{_LOOP_FILE}:run")])
    assert rec["asset_types"] == ["agent"]


def test_citation_naming_a_file_we_do_not_hold_never_promotes(monkeypatch):
    """The hallucinated-path case: plausible `file:symbol` shape, file not in the repo."""
    rec = _build_with_probes(monkeypatch, [_probe(citation="src/agent.py:run")])
    assert "agent" not in rec["asset_types"] and rec["assessment"]["promoted_types"] == []


def test_malformed_citation_without_a_colon_never_promotes(monkeypatch):
    """No `:` means no symbol named — prose dressed as a reference. Refuse even when
    the bare string happens to be a real path."""
    rec = _build_with_probes(monkeypatch, [_probe(citation=_LOOP_FILE)])
    assert "agent" not in rec["asset_types"] and rec["assessment"]["promoted_types"] == []


def test_citation_resolves_against_the_path_inventory_not_just_fetched_content(monkeypatch):
    """`known_paths` is content ∪ the ls-tree inventory: a file the selection did not
    fetch a BODY for is still a file the repo demonstrably has, so citing it resolves."""
    inventory_only = "src/only_in_tree.py"
    rec = _build_with_probes(monkeypatch, [_probe(citation=f"{inventory_only}:run")],
                             paths=tuple(PROMOTABLE_AGENT) + (inventory_only,))
    assert rec["asset_types"] == ["agent"]


def test_citation_with_a_double_colon_symbol_still_resolves(monkeypatch):
    """`file.rs:Type::method` — every colon-delimited prefix is tried, so we do not
    have to guess which colon separates path from symbol."""
    rec = _build_with_probes(monkeypatch, [_probe(citation=f"{_LOOP_FILE}:Runner::run")])
    assert rec["asset_types"] == ["agent"]


def test_citation_with_line_and_column_still_resolves(monkeypatch):
    """`file.ts:120:4` — same reason."""
    rec = _build_with_probes(monkeypatch, [_probe(citation=f"{_LOOP_FILE}:120:4")])
    assert rec["asset_types"] == ["agent"]


def test_citation_naming_the_readme_itself_never_promotes(monkeypatch):
    """THE narrowed exploit. Existence alone was not enough: the attacker-controlled
    README is itself a file we hold, so an injected document could steer the model to
    cite ITSELF in one token and classify the repo. A README alone must never classify."""
    rec = _build_with_probes(monkeypatch, [_probe(citation="README.md:run")])
    assert "agent" not in rec["asset_types"] and rec["assessment"]["promoted_types"] == []


def test_citation_naming_a_manifest_never_promotes(monkeypatch):
    """Same reasoning for any non-source file we hold — manifests, lockfiles, docs."""
    rec = _build_with_probes(monkeypatch, [_probe(citation="pyproject.toml:run")],
                             content={**PROMOTABLE_AGENT, "package.json": "{}"})
    assert "agent" not in rec["asset_types"]
    rec = _build_with_probes(monkeypatch, [_probe(citation="package.json:run")],
                             content={**PROMOTABLE_AGENT, "package.json": "{}"})
    assert "agent" not in rec["asset_types"]


def test_citation_smuggling_a_real_source_path_behind_a_readme_prefix_never_promotes(monkeypatch):
    """`README.md:src/loop.py:run` — the first colon-prefix is a real but non-source
    file, and `README.md:src/loop.py` is not a path we hold. Neither prefix qualifies."""
    rec = _build_with_probes(monkeypatch,
                             [_probe(citation=f"README.md:{_LOOP_FILE}:run")])
    assert "agent" not in rec["asset_types"] and rec["assessment"]["promoted_types"] == []


def test_citation_with_an_empty_symbol_tail_never_promotes(monkeypatch):
    """`src/loop.py:` — a real source file, but the citation names nothing inside it.
    Trailing whitespace after the colon counts as empty."""
    for citation in (f"{_LOOP_FILE}:", f"{_LOOP_FILE}:   ", "README.md:"):
        rec = _build_with_probes(monkeypatch, [_probe(citation=citation)])
        assert "agent" not in rec["asset_types"], citation
        assert rec["assessment"]["promoted_types"] == [], citation


# RULE 1c — a refusal is a NAMED gap, never silence ------------------------------
#
# Without this, "no candidates existed" and "candidates existed but every citation was
# rejected" both render as `promoted_types == []` with nothing to tell them apart.

@pytest.mark.parametrize("citation,fragment", [
    ("src/agent.py:run", "does not name a file in this snapshot"),
    ("README.md:run", "does not name a source file in this snapshot"),
    (f"{_LOOP_FILE}:", "names no symbol after the file path"),
    (_LOOP_FILE, "names no symbol after the file path"),
    ("", "returned no code citation"),
])
def test_every_refusal_reason_is_recorded_in_known_unknowns(monkeypatch, citation, fragment):
    rec = _build_with_probes(monkeypatch, [_probe(citation=citation)])
    entries = [k for k in rec["assessment"]["known_unknowns"]
               if k["detail"].startswith("agent candidate not promoted:")]
    assert len(entries) == 1, rec["assessment"]["known_unknowns"]
    assert fragment in entries[0]["detail"]
    assert entries[0]["asset_type"] == "agent" and entries[0]["code"] == "incomplete"
    if citation:
        assert citation in entries[0]["detail"]      # quoted verbatim for the curator


def test_a_successful_promotion_records_no_refusal(monkeypatch):
    rec = _build_with_probes(monkeypatch, [_probe()])
    assert rec["asset_types"] == ["agent"]
    assert not any(k["detail"].startswith("agent candidate not promoted:")
                   for k in rec["assessment"]["known_unknowns"])


def test_gateway_off_records_no_refusal(monkeypatch):
    """`llm_reconciliation is None` means the gateway never ran — off by default, so
    recording a refusal here would put a note on essentially every candidate in the
    corpus and drown the refusals that actually mean something."""
    rec = _build_with_probes(monkeypatch, [_probe(citation=None)])
    assert not any("candidate not promoted" in k["detail"]
                   for k in rec["assessment"]["known_unknowns"])


def test_reviewer_repro_dep_plus_adjective_readme_no_source_never_promotes(monkeypatch):
    """THE REPRO. One dependency line + one adjective, no source code anywhere. This
    promoted end-to-end (`asset_types: ['agent']`, `is_asset` False -> True) on the
    fabricated citation `src/agent.py:run`. Run through the REAL detector with only the
    gateway stubbed, exactly as the reviewer ran it."""
    content = {"package.json": json.dumps({"dependencies": {"openai": "^1"}}),
               "README.md": "MyLib is a fast JSON parser.\n\nIt is agentic and autonomous.\n"}
    monkeypatch.setattr(probes, "complete_json", lambda *a, **kw: {
        "tools_location": "an agent loop", "code_citation": "src/agent.py:run",
        "reasoning": "the README says it is agentic"})

    rec = assemble.build("https://github.com/o/mylib", content, "sha", None,
                         bucket_b={"topics": [], "description": "a fast JSON parser"},
                         paths=tuple(content), settings=_S)

    assert rec["asset_types"] == []
    assert rec["is_asset"] is False
    assert rec["asset_type"] == "not_an_asset"
    assert rec["assessment"]["promoted_types"] == []
    # The candidate itself still surfaces for a curator — refusing to PROMOTE is not
    # refusing to REPORT.
    assert any(p["type"] == "agent" and p["evidence_state"] == "candidate"
               for p in rec["assessment"]["coverage_probes"])


# RULE 2 -----------------------------------------------------------------------

def test_rule2_promotion_is_weak_tier_and_loses_to_a_deterministic_strong_match(monkeypatch):
    """`agent` outranks `mcp_server` in PRECEDENCE, so if a promotion were stamped
    `strong` it would seize the primary slot from a real marker. It must not."""
    rec = _build_with_probes(monkeypatch, [_probe()], content=STRONG_MCP,
                             url="https://github.com/o/foo-mcp")
    assert rec["asset_types"] == ["agent", "mcp_server"]

    promoted = next(m for m in rec["assessment"]["classification"]["matches"]
                    if m["asset_type"] == "agent")
    assert promoted["marker_tier"] == "weak"
    assert promoted["confidence"] == TIER_CONF["weak"] == 0.6
    assert promoted["promoted"] is True
    assert promoted["evidence"] == [{"path": "llm",
                                     "marker": f"promoted coverage-probe candidate — citation: {_CITATION}"}]

    mcp = next(m for m in rec["assessment"]["classification"]["matches"]
               if m["asset_type"] == "mcp_server")
    assert mcp["marker_tier"] == "strong"
    assert rec["asset_type"] == "mcp_server"                 # strong still wins
    assert rec["classification_confidence"] == TIER_CONF["strong"]
    assert rec["assessment"]["classification"]["primary_tiebroken"] is False


def test_rule2_promotion_loses_the_primary_contest_to_a_deterministic_WEAK_match(monkeypatch):
    """Weak tier alone was NOT enough. A promotion is stamped `weak`, so against a
    deterministic weak match it tied on (tier, confidence) and fell through to
    PRECEDENCE — where `agent` is index 0 and seized the primary slot from a real
    marker. The visible damage: `assessment["composition"]` went None (no composition
    is derived for a promoted type) and the served `classification.evidence` was
    replaced by the single LLM marker, losing both deterministic markers — on a
    `primary_type` the LOCKED marketplace feed serves with no promotion provenance."""
    rec = _build_with_probes(monkeypatch, [_probe()], content=WEAK_MCP,
                             url="https://github.com/o/thing")
    assert rec["asset_types"] == ["agent", "mcp_server"]

    mcp = next(m for m in rec["assessment"]["classification"]["matches"]
               if m["asset_type"] == "mcp_server")
    assert mcp["marker_tier"] == "weak"                       # same tier as the promotion
    assert rec["asset_type"] == "mcp_server"                  # deterministic still wins
    assert rec["classification_confidence"] == TIER_CONF["weak"]

    a = rec["assessment"]
    assert a["composition"] is not None                       # not blanked
    assert a["composition"] == a["compositions"]["mcp_server"]
    assert a["classification"]["evidence"] == mcp["evidence"]  # deterministic markers kept
    assert all(e["path"] != "llm" for e in a["classification"]["evidence"])


def test_promotion_may_still_be_primary_when_nothing_deterministic_matched(monkeypatch):
    """The other side of the same rule — the bespoke-agent (goose/aider) case this
    feature exists for. With NO deterministic match, the promotion IS the record."""
    rec = _build_with_probes(monkeypatch, [_probe()])
    assert rec["asset_types"] == ["agent"]
    assert rec["asset_type"] == "agent" and rec["is_asset"] is True
    assert rec["classification_confidence"] == TIER_CONF["weak"]
    assert rec["assessment"]["composition"] is None            # named in known_unknowns


def test_promotion_never_wins_a_precedence_tiebreak(monkeypatch):
    """`_pick_primary`'s tie_set must not contain the promoted type when a
    deterministic match exists — a promotion is not a co-equal candidate for primary."""
    rec = _build_with_probes(monkeypatch, [_probe()], content=WEAK_MCP,
                             url="https://github.com/o/thing")
    cls = rec["assessment"]["classification"]
    assert cls["primary_tiebroken"] is False and cls["tie_set"] == []


# RULE 3 -----------------------------------------------------------------------

def test_rule3_never_adds_a_type_with_no_probe_of_that_type(monkeypatch):
    """A probe list with NO agent entry: the mcp_server candidate promotes (proving
    the step ran and is type-generic), and `agent` — which the repo's content and
    topics would otherwise suggest — is nowhere near `asset_types`."""
    rec = _build_with_probes(monkeypatch, [_probe(asset_type="mcp_server")])
    assert rec["asset_types"] == ["mcp_server"]
    assert "agent" not in rec["asset_types"]
    assert [p["asset_type"] for p in rec["assessment"]["promoted_types"]] == ["mcp_server"]


def test_rule3_empty_probe_list_promotes_nothing(monkeypatch):
    rec = _build_with_probes(monkeypatch, [])
    assert rec["asset_types"] == [] and rec["assessment"]["promoted_types"] == []


def test_rule3_unknown_type_never_promotes(monkeypatch):
    """`workflow` is in FULL_TAXONOMY but has no TypeModule — promoting it would put a
    type in `asset_types` that nothing in the registry can classify or compose."""
    rec = _build_with_probes(monkeypatch, [_probe(asset_type="workflow")])
    assert rec["asset_types"] == [] and rec["assessment"]["promoted_types"] == []


def test_rule3_already_classified_type_is_not_duplicated(monkeypatch):
    rec = _build_with_probes(monkeypatch, [_probe(asset_type="mcp_server")], content=STRONG_MCP,
                             url="https://github.com/o/foo-mcp")
    assert rec["asset_types"] == ["mcp_server"]
    assert rec["assessment"]["promoted_types"] == []
    assert [m["marker_tier"] for m in rec["assessment"]["classification"]["matches"]] == ["strong"]


def test_rule3_deterministically_suppressed_type_is_not_reinstated(monkeypatch):
    """A suppressor DROPPED mcp_server on this repo (weak keyword-only next to a
    strong skill). A citation reinstating it would be the LLM overriding deterministic
    evidence rather than moving inside it."""
    monkeypatch.setattr(assemble.probes, "detect",
                        lambda *a, **kw: [_probe(asset_type="mcp_server")])
    # `_LOOP_FILE` is included so the citation RESOLVES — otherwise this test would pass
    # for the wrong reason (refused as unresolvable) and never exercise the suppressor gate.
    content = {**SKILL_LIKE, _LOOP_FILE: _LOOP_SRC}
    rec = assemble.build("https://github.com/o/skills", content, "sha", None,
                         bucket_b={"topics": ["agent-skills"], "description": "Agent Skills"},
                         paths=tuple(content), settings=_S)
    assert any(s["asset_type"] == "mcp_server"
               for s in rec["assessment"]["classification"]["suppressed"])
    assert rec["asset_types"] == ["skill"]
    assert rec["assessment"]["promoted_types"] == []


# RULE 4 -----------------------------------------------------------------------

def test_rule4_content_fingerprint_is_identical_with_and_without_a_promotion(monkeypatch):
    """THE churn-free property: `build_payload` receives the DETERMINISTIC types only.
    Same repo, same content, same probes — only the citation differs, so one run
    promotes and the other does not. `content_fingerprint` must not move, or every
    marketplace poller sees every asset change on the day this ships."""
    promoted = _build_with_probes(monkeypatch, [_probe(citation=_CITATION)])
    not_promoted = _build_with_probes(monkeypatch, [_probe(citation="")])

    assert promoted["asset_types"] == ["agent"]              # the promotion really happened
    assert not_promoted["asset_types"] == []
    assert promoted["content_fingerprint"] == not_promoted["content_fingerprint"]


def test_rule4_fingerprint_identical_alongside_a_deterministic_match(monkeypatch):
    """Same property with a real match present, so the payload is non-trivial: the
    promoted type must not reach `asset_types`/`per_type` in the hashed payload."""
    promoted = _build_with_probes(monkeypatch, [_probe(citation=_CITATION)], content=STRONG_MCP,
                                  url="https://github.com/o/foo-mcp")
    not_promoted = _build_with_probes(monkeypatch, [_probe(citation="")], content=STRONG_MCP,
                                      url="https://github.com/o/foo-mcp")

    assert promoted["asset_types"] == ["agent", "mcp_server"]
    assert not_promoted["asset_types"] == ["mcp_server"]
    assert promoted["content_fingerprint"] == not_promoted["content_fingerprint"]
    # risk is derived from the deterministic compositions and must not move either
    assert promoted["assessment"]["risk"] == not_promoted["assessment"]["risk"]


# PROVENANCE -------------------------------------------------------------------

def test_llm_used_is_true_when_asset_types_exist_only_because_of_a_citation(monkeypatch):
    """`llm_used` is the only provenance bit near the classification. A record whose
    `asset_types` exists solely because of an LLM citation must not report False."""
    promoted = _build_with_probes(monkeypatch, [_probe()])
    not_promoted = _build_with_probes(monkeypatch, [_probe(citation="")])

    assert promoted["asset_types"] == ["agent"] and promoted["llm_used"] is True
    assert not_promoted["asset_types"] == [] and not_promoted["llm_used"] is False
