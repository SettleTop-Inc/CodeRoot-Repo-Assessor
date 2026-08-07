import json

from assessor.assessment.classify_agent import classify, _all_deps


def _pkg(deps):
    return json.dumps({"dependencies": deps})


def test_agent_native_dep_is_strong():
    r = classify({"package.json": _pkg({"@openai/agents": "^1"})})
    assert r["asset_type"] == "agent" and r["marker_tier"] == "strong"


def test_requirements_txt_native_dep_is_strong():
    r = classify({"requirements.txt": "langgraph==0.2.1\nrequests>=2\n# comment\n"})
    assert r and r["marker_tier"] == "strong"


def test_pyproject_native_dep_is_strong():
    py = '[project]\ndependencies = [\n  "crewai>=0.5",\n  "requests",\n]\n'
    r = classify({"pyproject.toml": py})
    assert r and r["marker_tier"] == "strong"


def test_llama_index_agent_subpackage_strong_but_core_is_not():
    assert classify({"requirements.txt": "llama-index-agent-openai\n"})["marker_tier"] == "strong"
    assert classify({"requirements.txt": "llama-index\n"}) is None            # RAG false-positive guard


def test_general_dep_plus_construct_is_weak():
    r = classify({"requirements.txt": "langchain\n",
                  "src/app.py": "graph = StateGraph(State)\ngraph.add_node('a', f)"})
    assert r["asset_type"] == "agent" and r["marker_tier"] == "weak"


def test_dep_alone_construct_alone_prose_alone_none():
    assert classify({"requirements.txt": "langchain\n"}) is None
    assert classify({"src/app.py": "AgentExecutor(agent, tools)"}) is None
    assert classify({"README.md": "an autonomous agent for x"}) is None


def test_crewai_manifests_strong_only_as_siblings_with_shape():
    good = {"src/cfg/agents.yaml": "researcher:\n  role: R\n  goal: G\n",
            "src/cfg/tasks.yaml": "t1:\n  description: d\n"}
    assert classify(good)["marker_tier"] == "strong"
    lone = {"tasks.yaml": "step: build\n"}                                    # Tekton-style
    assert classify(lone) is None
    unshaped = {"a/agents.yaml": "foo: bar\n", "a/tasks.yaml": "t: x\n"}      # no role:/goal:
    assert classify(unshaped) is None


def test_agent_card_manifests():
    assert classify({".well-known/agent-card.json": "{}"})["marker_tier"] == "strong"
    ok = json.dumps({"name": "x", "url": "https://a", "skills": []})
    assert classify({"agent.json": ok})["marker_tier"] == "strong"
    assert classify({"agent.json": json.dumps({"foo": 1})}) is None           # fails AgentCard shape


def test_prose_is_evidence_only_on_existing_match():
    r = classify({"package.json": _pkg({"@mastra/core": "^1"}),
                  "README.md": "a tool-calling agent"})
    assert r["marker_tier"] == "strong"
    assert any("prose" in e["marker"] for e in r["evidence"])


def test_all_deps_normalizes():
    deps = _all_deps({"requirements.txt": "Pydantic_AI==1.0\n"})
    assert "pydantic-ai" in deps
