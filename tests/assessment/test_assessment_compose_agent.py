from assessor.assessment.compose_agent import compose, fingerprint_facts, risk_signals


def test_framework_and_literal_tools():
    content = {"requirements.txt": "langgraph\n",
               "src/app.py": ('from x import a, b\n'
                              'agent = create_react_agent(model, tools=[search_web, run_sql])\n')}
    out = compose(content)
    assert out["framework"]["value"] == "langgraph"
    names = [t["name"] for t in out["tools_consumed"]]
    assert names == ["run_sql", "search_web"]
    assert out["tools_complete"] is True and out["agents_multiple"] is False


def test_connected_mcp_server_marks_incomplete():
    content = {"src/app.py": "params = StdioServerParameters(command='npx')\n"}
    out = compose(content)
    assert out["tools_complete"] is False
    assert "out-of-repo" in out["tools_incomplete_reason"]


def test_dynamic_loader_marks_incomplete():
    out = compose({"src/app.py": "tools = toolkit.get_tools()\n"})
    assert out["tools_complete"] is False and "dynamic" in out["tools_incomplete_reason"]


def test_zero_found_never_complete():
    out = compose({"src/app.py": "print('hi')\n"})
    assert out["tools_consumed"] == [] and out["tools_complete"] is False


def test_models_recognized_family_is_fact_comment_ignored():
    src = '# model = "gpt-4o"  (comment)\nllm = init(model="claude-sonnet-5")\n'
    out = compose({"src/app.py": src})
    assert out["models"]["value"] == ["claude-sonnet-5"]
    assert "confidence" not in out["models"]                       # Fact, not assessed


def test_models_unrecognized_family_is_assessed_not_dropped():
    out = compose({"src/app.py": 'llm = init(model="totally-custom-1")\n'})
    assert out["models"]["value"] == ["totally-custom-1"]
    assert out["models"]["confidence"] == 0.5                      # assessed('unrecognized…')


def test_models_mixed_recognized_and_unrecognized_keeps_both_as_assessed():
    src = 'a = init(model="claude-sonnet-5")\nb = init(model="totally-custom-1")\n'
    out = compose({"src/app.py": src})
    assert out["models"]["value"] == ["claude-sonnet-5", "totally-custom-1"]
    assert out["models"]["confidence"] == 0.5          # any unfamiliar id degrades the whole field


def test_models_dynamic_is_unknown():
    out = compose({"src/app.py": "llm = init(model=settings.MODEL)\n"})
    assert out["models"].get("known_unknown")


def test_agents_multiple_on_two_constructs_or_cap():
    two = {"src/a.py": "AgentExecutor(x)\n", "src/b.py": "Crew(agents=[])\n"}
    assert compose(two)["agents_multiple"] is True
    assert compose({"src/a.py": "AgentExecutor(x)"}, capped=True)["agents_multiple"] is True


def test_fingerprint_facts_shape():
    m = {"asset_type": "agent", "marker_tier": "strong", "evidence": []}
    comp = compose({"requirements.txt": "crewai\n", "src/a.py": "Crew(agents=[])"})
    fp = fingerprint_facts(m, comp)
    assert fp["marker_tier"] == "strong" and fp["framework"] == "crewai"
    assert fp["models"] == [] and fp["tool_names"] == sorted(fp["tool_names"])


def test_risk_signals_r2_and_secrets():
    incomplete = compose({"src/app.py": "params = StdioServerParameters(command='x')\n"})
    flags = risk_signals(incomplete)
    assert flags["writes"].get("known_unknown")                    # R2 over incomplete BOM
    hit = compose({"src/app.py": "agent = init(tools=[delete_file], model='claude-sonnet-5')\n"})
    assert risk_signals(hit)["writes"]["value"] is True
