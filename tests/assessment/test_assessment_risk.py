from assessor.assessment.risk import assess, raise_only, known_unknowns


def test_write_and_exec_tool_names_flag_deterministically():
    comp = {"tools": [{"name": "write_file"}, {"name": "run_shell"}], "auth": {"value": "api-key"}}
    r = assess(comp)
    assert r["writes"]["value"] and r["executes_code"]["value"] and r["handles_secrets"]["value"]


def test_plain_read_tool_low_risk():
    # R2: a complete inventory of a single non-risky tool is an honest fact(False), not unknown.
    r = assess({"tools": [{"name": "search"}], "tools_complete": True, "tools_incomplete_reason": None,
                "auth": {"value": None, "known_unknown": "x"}})
    assert r["writes"]["value"] is False and r["executes_code"]["value"] is False


def test_llm_can_raise_but_never_lower_a_flag():
    # R2: complete inventory so every base flag is a fact() (raise_only is UNWIRED and doesn't
    # yet handle unknown() bases without a 'source' key — see the seam comment in risk.py).
    base = assess({"tools": [{"name": "write_file"}], "tools_complete": True, "tools_incomplete_reason": None,
                   "auth": {"value": None}})
    merged = raise_only(base, {"writes": False, "network": True})  # LLM tries to clear writes + add network
    assert merged["writes"]["value"] is True   # never lowered
    assert merged["network"]["value"] is True  # raised


def test_known_unknowns_collects_gaps():
    parts = [{"a": {"value": 1}}, {"b": {"value": None, "known_unknown": "no auth declared"}}]
    ku = known_unknowns(parts)
    assert {"no auth declared"} == {k["detail"] for k in ku}


def test_r2_empty_inventory_is_unknown_not_false():
    comp = {"tools": [], "tools_complete": False,
            "tools_incomplete_reason": "no tool registrations statically found", "auth": {}}
    flags = assess(comp)
    for name in ("writes", "executes_code", "network", "handles_secrets"):
        assert flags[name].get("known_unknown"), name
        assert "no tool registrations" in flags[name]["known_unknown"]


def test_r2_incomplete_inventory_hits_still_raise_true():
    comp = {"tools": [{"name": "delete_row"}], "tools_complete": False,
            "tools_incomplete_reason": "dynamic tool list handler present", "auth": {}}
    flags = assess(comp)
    assert flags["writes"]["value"] is True            # partial evidence of risk still counts
    assert flags["network"].get("known_unknown")       # but a no-hit is unknown, not False


def test_complete_inventory_no_hit_is_honest_false():
    comp = {"tools": [{"name": "add"}], "tools_complete": True, "tools_incomplete_reason": None, "auth": {}}
    flags = assess(comp)
    assert flags["executes_code"]["value"] is False and not flags["executes_code"].get("known_unknown")
