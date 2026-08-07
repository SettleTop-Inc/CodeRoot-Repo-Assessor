from assessor.assessment.classify_skill import classify
from assessor.assessment.compose_skill import compose, fingerprint_facts, risk_signals

_FM = "---\nname: pdf\ndescription: Work with PDFs\n---\nbody\n"


def test_root_skill_md_is_strong():
    r = classify({"SKILL.md": _FM}, paths=("SKILL.md", "README.md"))
    assert r["asset_type"] == "skill" and r["marker_tier"] == "strong"


def test_collection_skill_md_strong():
    content = {"skills/a/SKILL.md": _FM, "skills/b/SKILL.md": _FM}
    r = classify(content, paths=tuple(content) + ("README.md",))
    assert r["marker_tier"] == "strong"


def test_skill_md_path_present_body_absent_still_strong_incomplete():
    r = classify({}, paths=("skills/a/SKILL.md",))
    assert r and r["marker_tier"] == "strong"   # composition flags skills_complete=False
    comp = compose({}, paths=("skills/a/SKILL.md",))
    assert comp["skills_complete"] is False
    assert "not acquired" in comp["skills_incomplete_reason"]


def test_frontmatter_without_name_is_not_a_skill():
    assert classify({"SKILL.md": "---\ndescription: x\n---\n"}, paths=("SKILL.md",)) is None


def test_no_skill_md_at_all_is_none():
    assert classify({"README.md": "hello"}, paths=("README.md",)) is None


def test_evidence_capped_at_three_paths_plus_count_marker():
    content = {f"skills/s{i}/SKILL.md": _FM for i in range(5)}
    r = classify(content, paths=tuple(content))
    path_ev = [e for e in r["evidence"] if e["path"] != "skills"]
    count_ev = [e for e in r["evidence"] if e["path"] == "skills"]
    assert len(path_ev) == 3
    assert len(count_ev) == 1 and "5" in count_ev[0]["marker"]


# -- compose --------------------------------------------------------------------

def test_compose_builds_skills_list_and_count():
    content = {"skills/a/SKILL.md": _FM, "skills/b/SKILL.md":
               "---\nname: web\ndescription: Browse the web\n---\n"}
    out = compose(content, paths=tuple(content))
    assert out["skills_count"]["value"] == 2
    names = [s["name"] for s in out["skills"]]
    assert names == ["pdf", "web"]   # sorted by path (skills/a < skills/b)
    assert out["skills_complete"] is True
    assert out["skills_incomplete_reason"] is None


def test_compose_capped_source_marks_incomplete():
    # R2: source coverage capped/truncated ⇒ inventory NOT complete, even with bodies
    # present — mirrors compose_mcp/compose_agent. Guards against claim-safe-when-unknown.
    content = {"SKILL.md": _FM}
    out = compose(content, paths=("SKILL.md",), capped=True)
    assert out["skills_complete"] is False
    assert out["skills_incomplete_reason"] == "source coverage capped/truncated"


def test_risk_unknown_when_source_capped_even_if_tools_declared():
    content = {"SKILL.md": "---\nname: x\ndescription: d\nallowed-tools: [read]\n---\n"}
    out = compose(content, paths=("SKILL.md",), capped=True)
    flags = risk_signals(out)
    # capped ⇒ not complete ⇒ no-hit flags are unknown(), never fact(False)
    assert flags["writes"].get("known_unknown")
    assert flags["executes_code"].get("known_unknown")


def test_compose_skips_unconfirmed_skill_md_from_skills_list():
    content = {"SKILL.md": _FM, "skills/bad/SKILL.md": "---\ndescription: no name\n---\n"}
    out = compose(content, paths=tuple(content))
    assert out["skills_count"]["value"] == 1
    assert [s["name"] for s in out["skills"]] == ["pdf"]


def test_allowed_tools_parsed_inline_and_declared():
    content = {"SKILL.md": "---\nname: pdf\ndescription: d\nallowed-tools: Read, Write, Bash\n---\n"}
    out = compose(content, paths=tuple(content))
    assert out["skills"][0]["allowed_tools"] == ["Read", "Write", "Bash"]
    assert out["allowed_tools_declared"]["value"] is True


def test_allowed_tools_parsed_as_yaml_list():
    content = {"SKILL.md": "---\nname: pdf\ndescription: d\nallowed-tools:\n  - Read\n  - Write\n---\n"}
    out = compose(content, paths=tuple(content))
    assert out["skills"][0]["allowed_tools"] == ["Read", "Write"]


def test_no_allowed_tools_means_not_declared():
    out = compose({"SKILL.md": _FM}, paths=("SKILL.md",))
    assert out["skills"][0]["allowed_tools"] is None
    assert out["allowed_tools_declared"]["value"] is False


def test_fingerprint_facts_shape():
    m = {"asset_type": "skill", "marker_tier": "strong", "evidence": []}
    content = {"SKILL.md": _FM}
    comp = compose(content, paths=("SKILL.md",))
    fp = fingerprint_facts(m, comp)
    assert fp == {"marker_tier": "strong", "skills_count": 1, "skill_names": ["pdf"]}


def test_risk_signals_undeclared_allowed_tools_is_unknown_not_false():
    out = compose({"SKILL.md": _FM}, paths=("SKILL.md",))
    flags = risk_signals(out)
    assert flags["writes"].get("known_unknown")
    assert flags["executes_code"].get("known_unknown")


def test_risk_signals_allowed_tools_drive_flags():
    content = {"SKILL.md": "---\nname: pdf\ndescription: d\nallowed-tools: Bash, delete_file\n---\n"}
    out = compose(content, paths=("SKILL.md",))
    flags = risk_signals(out)
    assert flags["executes_code"]["value"] is True
    assert flags["writes"]["value"] is True


def test_risk_signals_secrets_hit_from_tool_names():
    content = {"SKILL.md": "---\nname: pdf\ndescription: d\nallowed-tools: read_api_token\n---\n"}
    out = compose(content, paths=("SKILL.md",))
    assert risk_signals(out)["handles_secrets"]["value"] is True


def test_risk_signals_complete_when_declared_and_bodies_present():
    content = {"SKILL.md": "---\nname: pdf\ndescription: d\nallowed-tools: Read\n---\n"}
    out = compose(content, paths=("SKILL.md",))
    flags = risk_signals(out)
    assert flags["handles_secrets"]["value"] is False   # complete inventory, no secret-ish names


def test_agent_host_config_dirs_are_not_authored_skills():
    # Agent-CONFIGURATION directories: evidence the repo HOSTS agents, not that it
    # authors a skill. Real paths from openai/codex, cline/cline, qwen-code, suna.
    for p in (".codex/skills/code-review/SKILL.md",
              ".cline/skills/publish-cli/SKILL.md",
              ".agents/skills/create-pull-request/SKILL.md",
              ".claude/skills/brand-guidelines/SKILL.md",
              ".qwen/skills/autofix/SKILL.md"):
        assert classify({p: _FM}, paths=(p, "README.md")) is None, p


def test_evals_fixture_does_not_make_a_repo_a_skill():
    # aaif-goose/goose was classified `skill` at 0.95 on exactly this one file.
    p = "evals/harbor/.agents/skills/compare_tasks/SKILL.md"
    assert classify({p: _FM}, paths=(p, "README.md")) is None


def test_internal_builtin_skills_are_not_authored_skills():
    # charmbracelet/crush ships builtin skills under an internal package tree.
    p = "internal/skills/builtin/crush-config/SKILL.md"
    assert classify({p: _FM}, paths=(p, "README.md")) is None


def test_nested_plugin_skills_are_not_authored_skills():
    # stripe/agent-toolkit and upstash/context7 nest skills under provider plugin dirs.
    for p in ("providers/claude/plugin/skills/stripe-docs/SKILL.md",
              "plugins/claude/context7/skills/context7-mcp/SKILL.md"):
        assert classify({p: _FM}, paths=(p, "README.md")) is None, p


def test_authored_locations_still_classify_strong():
    # The two documented locations must keep working: root, and skills/<name>/.
    assert classify({"SKILL.md": _FM}, paths=("SKILL.md",))["marker_tier"] == "strong"
    p = "skills/brand-guidelines/SKILL.md"
    assert classify({p: _FM}, paths=(p,))["marker_tier"] == "strong"


def test_mixed_repo_counts_only_authored_paths():
    # upstash/context7: 3 authored under skills/, 5 nested under plugins/ + packages/.
    content = {"skills/find-docs/SKILL.md": _FM,
               "skills/context7-mcp/SKILL.md": _FM,
               "plugins/cursor/context7/skills/context7-mcp/SKILL.md": _FM,
               "packages/pi/skills/context7-docs/SKILL.md": _FM}
    r = classify(content, paths=tuple(content))
    count_ev = [e for e in r["evidence"] if e["path"] == "skills"]
    assert count_ev[0]["marker"] == "2 SKILL.md files"
