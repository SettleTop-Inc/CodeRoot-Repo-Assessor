"""Output envelopes for the assessment (spec §Global constraints).

Fact = deterministic value (no confidence). AssessedField = LLM/uncertain value
(carries confidence). unknown = an explicit gap. `Metric` is NOT reused (it has a
required scope and no evidence).
"""
from __future__ import annotations


def fact(value, source: str, evidence: list[dict] | None = None) -> dict:
    return {"value": value, "source": source, "evidence": evidence or []}


def assessed(value, confidence: float, evidence: list[dict] | None = None) -> dict:
    return {"value": value, "confidence": round(float(confidence), 3), "evidence": evidence or []}


def unknown(reason: str) -> dict:
    return {"value": None, "known_unknown": reason}
