"""Tolerant reading of asset-record.json (spec §4, §9 D1).

A malformed record is an ABSENT record (Global Constraint 6): this module
never raises on bad input and never fails an assessment."""
import json

import pytest

from assessor.assessment.record import (RECORD_BASENAME, RECORD_MAX_BYTES,
                                        declared_block, parse_record)

GOOD = {
    "record_version": 1,
    "created_by": "settletop-niles",
    "created_at": "2026-08-09T00:00:00Z",
    "source_repo": {"host": "github.com", "owner": "SettleTop-Inc", "name": "example"},
    "maintained_by": "SettleTop-Inc",
    "technologies": {"language": "python", "framework": "mcp",
                     "runtime": "python>=3.11", "dependencies": ["mcp", "httpx"]},
    "model_access": {"mode": "byo", "provider": None, "model": None},
    "confirmation": {"mode": "elicitation",
                     "confirmed": ["created_by", "maintained_by", "model_access.mode"],
                     "complete": True},
}


def _content(rec) -> dict[str, str]:
    return {RECORD_BASENAME: json.dumps(rec), "README.md": "hi"}


def test_parse_good_record():
    rec = parse_record(_content(GOOD))
    assert rec["created_by"] == "settletop-niles"
    assert rec["model_access"]["mode"] == "byo"
    assert rec["technologies"]["dependencies"] == ["mcp", "httpx"]


def test_absent_file_is_none():
    assert parse_record({"README.md": "hi"}) is None


def test_malformed_json_is_none():
    assert parse_record({RECORD_BASENAME: "{not json"}) is None


def test_wrong_record_version_is_none():
    bad = dict(GOOD, record_version=2)
    assert parse_record(_content(bad)) is None


def test_missing_record_version_is_none():
    bad = {k: v for k, v in GOOD.items() if k != "record_version"}
    assert parse_record(_content(bad)) is None


def test_non_dict_top_level_is_none():
    assert parse_record({RECORD_BASENAME: json.dumps([1, 2])}) is None


def test_oversized_body_is_none():
    big = dict(GOOD)
    big["technologies"] = dict(GOOD["technologies"], dependencies=["x" * 90] * 50)
    body = json.dumps(big) + " " * RECORD_MAX_BYTES
    assert parse_record({RECORD_BASENAME: body}) is None


def test_pinned_mode_requires_provider_and_model():
    ok = dict(GOOD, model_access={"mode": "pinned", "provider": "anthropic",
                                  "model": "claude-opus-5"})
    assert parse_record(_content(ok))["model_access"]["model"] == "claude-opus-5"
    bad = dict(GOOD, model_access={"mode": "pinned", "provider": None, "model": None})
    # pinned-without-model is a field-level defect: the field drops, the record survives
    rec = parse_record(_content(bad))
    assert rec is not None and rec.get("model_access") is None


def test_invalid_mode_drops_field_keeps_record():
    bad = dict(GOOD, model_access={"mode": "sometimes"})
    rec = parse_record(_content(bad))
    assert rec is not None and rec.get("model_access") is None


def test_overlong_strings_dropped_field_level():
    bad = dict(GOOD, created_by="x" * 201)
    rec = parse_record(_content(bad))
    assert rec is not None and rec.get("created_by") is None


def test_dependencies_capped_at_50():
    rec = parse_record(_content(dict(GOOD, technologies=dict(
        GOOD["technologies"], dependencies=[f"d{i}" for i in range(60)]))))
    assert len(rec["technologies"]["dependencies"]) == 50


def test_unknown_keys_ignored():
    rec = parse_record(_content(dict(GOOD, future_field={"a": 1})))
    assert rec is not None and "future_field" not in rec


def test_declared_block_shapes_facts():
    block = declared_block(_content(GOOD))
    assert block["technologies"]["language"]["value"] == "python"
    assert block["technologies"]["language"]["source"] == "declared: asset-record.json"
    assert block["model_access"]["mode"]["value"] == "byo"
    # evidence cites the file
    assert block["technologies"]["framework"]["evidence"] == [
        {"path": RECORD_BASENAME, "marker": "technologies.framework"}]


def test_declared_block_absent_when_no_record():
    assert declared_block({"README.md": "x"}) is None


def test_declared_block_omits_dropped_fields():
    bad = dict(GOOD, model_access={"mode": "sometimes"})
    block = declared_block(_content(bad))
    assert "model_access" not in block
    assert "technologies" in block
