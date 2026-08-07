from assessor.assessment.classify_prompt import classify
from assessor.assessment.compose_prompt import compose, fingerprint_facts, risk_signals


def _collection(n):
    return tuple(f"prompts/p{i}/system.md" for i in range(n))


def test_pure_prompt_collection_is_weak():
    paths = _collection(6) + ("README.md",)
    r = classify({}, paths=paths, meta={"topics": [], "description": "A collection of prompts"})
    assert r["asset_type"] == "prompt" and r["marker_tier"] == "weak"


def test_fabric_like_is_not_an_asset_go_mod():
    paths = tuple(f"data/patterns/p{i}/system.md" for i in range(50)) + ("go.mod", "cmd/main.go")
    assert classify({}, paths=paths, meta={"topics": ["ai"], "description": "framework"}) is None


def test_framework_description_guard():
    paths = _collection(6)
    assert classify({}, paths=paths, meta={"topics": [], "description": "an open-source framework"}) is None


def test_below_threshold_none():
    assert classify({}, paths=_collection(3), meta=None) is None


def test_source_dominated_collection_is_none():
    # 5 prompt files but 10 real source files -> guard #2 (prompt files dominate) fails.
    paths = _collection(5) + tuple(f"pkg/mod{i}/file.py" for i in range(10))
    assert classify({}, paths=paths, meta=None) is None


def test_meta_none_with_no_app_is_weak():
    # guard #3 (declared identity) passes silently when meta is empty/None.
    r = classify({}, paths=_collection(5), meta=None)
    assert r["asset_type"] == "prompt" and r["marker_tier"] == "weak"


# -- compose --------------------------------------------------------------------

def test_compose_counts_and_names():
    paths = _collection(3)
    out = compose({}, paths=paths, meta=None)
    assert out["prompts_count"]["value"] == 3
    assert out["prompt_names"] == ["p0", "p1", "p2"]
    assert out["prompts_complete"] is True
    assert out["prompts_incomplete_reason"] is None
    assert out["format"]["value"] == "system/user-pair"


def test_compose_capped_source_marks_incomplete():
    # R2: source coverage capped/truncated ⇒ inventory NOT complete (mirrors siblings).
    out = compose({}, paths=_collection(3), meta=None, capped=True)
    assert out["prompts_complete"] is False
    assert out["prompts_incomplete_reason"] == "source coverage capped/truncated"
    flags = risk_signals(out)
    assert flags["writes"].get("known_unknown")   # incomplete ⇒ unknown, not fact(False)


def test_compose_prompt_file_extension_format():
    paths = ("prompts/greeting.prompt", "prompts/farewell.prompt.md", "prompts/welcome.prompty",
              "prompts/a.prompt", "prompts/b.prompt")
    out = compose({}, paths=paths, meta=None)
    assert out["format"]["value"] == "prompt-file"


def test_compose_mixed_format():
    paths = _collection(3) + ("misc/one.prompt", "misc/two.prompt")
    out = compose({}, paths=paths, meta=None)
    assert out["format"]["value"] == "mixed"


def test_fingerprint_facts_shape():
    m = {"asset_type": "prompt", "marker_tier": "weak", "evidence": []}
    paths = _collection(2)
    comp = compose({}, paths=paths, meta=None)
    fp = fingerprint_facts(m, comp)
    assert fp == {"marker_tier": "weak", "prompts_count": 2, "prompt_names": ["p0", "p1"]}


def test_risk_signals_all_false_when_complete():
    paths = _collection(3)
    comp = compose({}, paths=paths, meta=None)
    flags = risk_signals(comp)
    assert flags["writes"]["value"] is False
    assert flags["executes_code"]["value"] is False
    assert flags["handles_secrets"]["value"] is False


def test_risk_signals_unknown_when_incomplete():
    paths = _collection(3)
    comp = compose({}, paths=paths, meta=None)
    comp["prompts_complete"] = False
    comp["prompts_incomplete_reason"] = "inventory capped at 1000"
    flags = risk_signals(comp)
    assert flags["writes"].get("known_unknown")
