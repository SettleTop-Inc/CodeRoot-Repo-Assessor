"""Purpose / domain extraction. description is a Fact (repo-declared, unverified);
intended_use/category/business_domain/out_of_scope are LLM AssessedFields (or
known_unknown when the LLM is off). business_domain is enum-constrained."""
from __future__ import annotations

import json
from typing import Literal

from pydantic import BaseModel, Field

from ..llm.client import complete_json_detailed
from .shapes import assessed, fact, unknown

_DOMAINS = ("Software & IT", "Sales & CRM", "Marketing & Content", "Customer Support",
            "Finance & Accounting", "HR & Recruiting", "Legal & Compliance",
            "Operations & Productivity", "Data & Analytics", "Knowledge & Research")

_Domain = Literal[
    "Software & IT", "Sales & CRM", "Marketing & Content", "Customer Support",
    "Finance & Accounting", "HR & Recruiting", "Legal & Compliance",
    "Operations & Productivity", "Data & Analytics", "Knowledge & Research"]


class PurposeOut(BaseModel):
    intended_use: str = Field(max_length=500)
    category: str = Field(max_length=100)
    business_domain: _Domain
    out_of_scope_use: str = Field(max_length=500)


_SYSTEM = (
    "You classify an open-source agentic asset (an MCP server, AI agent, or similar) from its repository files. "
    "Return JSON with: intended_use (one sentence), category (short slug), "
    f"business_domain (exactly one of: {', '.join(_DOMAINS)}), out_of_scope_use (one sentence). "
    "Base it ONLY on the provided content; if unsure, be conservative.")

_LLM_CONF = 0.5


_PROMPT_PATHS = ("README.md", "README.rst", "package.json", "pyproject.toml", "server.json", "mcp.json", "langgraph.json", "agent.json")
_BADGE = ("[![", "<!--", "![")


def _readme_oneliner(content: dict[str, str]) -> str | None:
    for line in content.get("README.md", "").splitlines():
        s = line.strip().lstrip("# ").strip()
        if s and not s.startswith(_BADGE):
            return s
    return None


def _description(content: dict[str, str], repo_description: str | None = None) -> dict:
    if repo_description:
        return fact(repo_description, "repo object (github description)", [{"path": "repo", "marker": "description"}])
    pkg = content.get("package.json", "")
    if pkg:
        try:
            parsed = json.loads(pkg)
        except ValueError:
            parsed = None
        desc = parsed.get("description") if isinstance(parsed, dict) else None
        if desc:
            return fact(desc, "repo-declared, unverified", [{"path": "package.json", "marker": "description"}])
    line = _readme_oneliner(content)
    if line:
        return fact(line, "repo-declared, unverified", [{"path": "README.md", "marker": "first line"}])
    return unknown("no description declared")


_REASON_GAP = {"off": "llm unavailable or output invalid",
               "timeout": "llm timed out",
               "unreachable": "llm unreachable",
               "http": "llm returned an error",
               "invalid": "llm output failed validation"}
_SALVAGE_EV = [{"path": "llm", "marker": "partial salvage (schema-invalid response)"}]


def _salvage(partial: dict) -> dict:
    """Keep individually-valid fields when full-model validation failed on one field."""
    def keep_str(key: str, maxlen: int) -> dict:
        v = partial.get(key)
        if isinstance(v, str) and 0 < len(v) <= maxlen:
            return assessed(v, _LLM_CONF, _SALVAGE_EV)
        return unknown("llm output failed validation")

    dom = partial.get("business_domain")
    domain = (assessed(dom, _LLM_CONF, _SALVAGE_EV) if dom in _DOMAINS
              else unknown("llm output failed validation"))
    return {"intended_use": keep_str("intended_use", 500),
            "category": keep_str("category", 100),
            "business_domain": domain,
            "out_of_scope_use": keep_str("out_of_scope_use", 500)}


def extract(content: dict[str, str], *, cache=None, settings=None, provider=None, repo_description=None) -> dict:
    out = {"description": _description(content, repo_description)}
    # Feed the LLM only the purpose-relevant subset (README + manifests), NOT the whole
    # source subtree — keeps the prompt grounded and avoids large-context timeouts.
    joined = "\n\n".join(f"### {p}\n{content[p][:4000]}" for p in _PROMPT_PATHS if p in content)
    res = complete_json_detailed(_SYSTEM, joined, PurposeOut, provider=provider, cache=cache, settings=settings)
    data, partial, reason = res["data"], res["partial"], res["reason"]
    if data is not None:
        ev = [{"path": "llm", "marker": "assessed from content"}]
        out["intended_use"] = assessed(data["intended_use"], _LLM_CONF, ev)
        out["category"] = assessed(data["category"], _LLM_CONF, ev)
        out["business_domain"] = assessed(data["business_domain"], _LLM_CONF, ev)
        out["out_of_scope_use"] = assessed(data["out_of_scope_use"], _LLM_CONF, ev)
    elif partial is not None:
        out.update(_salvage(partial))
    else:
        gap = unknown(_REASON_GAP.get(reason, "llm unavailable or output invalid"))
        out.update(intended_use=gap, category=gap, business_domain=gap, out_of_scope_use=gap)
    return out
