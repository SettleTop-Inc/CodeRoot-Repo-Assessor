"""agent composition (spec §5): framework + models + consumed tool-BOM. Mirrors
compose_mcp discipline: comment-strip, honesty flags, caps. No LLM calls."""
from __future__ import annotations

import re

from .classify_agent import AGENT_DEPS, AGENT_DEP_PREFIXES, CONSTRUCTS, _all_deps
from .compose_mcp import _BLOCK_COMMENT, _LINE_COMMENT, _entrypoints
from .risk import _signals
from .shapes import assessed, fact, unknown

_INV_CAP = 1000
# versioned model-id family data (§5)
_MODEL_FAMILIES = re.compile(
    r"^(gpt-[\w.\-]+|o[134](-[\w.\-]+)?|claude-[\w.\-]+|gemini-[\w.\-]+|llama-?[\w.\-]+"
    r"|mistral-[\w.\-]+|deepseek-[\w.\-]+|qwen[\w.\-]*|grok-[\w.\-]+)$", re.I)
_MODEL_LIT = re.compile(r"""\bmodel\s*[:=]\s*["']([^"']+)["']""")
_MODEL_DYN = re.compile(r"""\bmodel\s*[:=]\s*[A-Za-z_][\w.]*""")
_FN_TOOL_DECO = re.compile(r"@(?:\w+\.)?(?:function_)?tool\b")
_PY_DEF = re.compile(r"^\s*(?:async\s+)?def\s+([A-Za-z0-9_]+)", re.M)
_TOOLS_LIST = re.compile(r"\btools\s*=\s*\[([^\]]*)\]")
_IDENT = re.compile(r"[A-Za-z_]\w*")
_MCP_CONN = ("mcpServers", "StdioServerParameters(", "MCPServerStdio(", "mcp_servers=")
_DYN_LOADERS = ("load_tools", ".get_tools(", "bind_tools(", "toolkit.get_tools()")


def _strip(text: str) -> str:
    return _LINE_COMMENT.sub("", _BLOCK_COMMENT.sub("", text))


def _framework(content) -> dict:
    deps = _all_deps(content)
    for d in sorted(deps):
        if d in AGENT_DEPS or d.startswith(AGENT_DEP_PREFIXES):
            return fact(d, "dependency manifest", [{"path": "deps", "marker": d}])
    if "langgraph.json" in content:
        return fact("langgraph", "manifest", [{"path": "langgraph.json", "marker": "manifest"}])
    for d in sorted(deps):
        if d.startswith(("langchain", "@langchain/", "llama-index")):
            return fact(d, "dependency manifest (general)", [{"path": "deps", "marker": d}])
    return unknown("no agent framework dependency declared")


def _strip_provider(mid: str) -> str:
    mid = mid.rsplit("/", 1)[-1]                      # openai/gpt-4o, models/gemini-x
    if mid.count(".") and mid.split(".", 1)[0] in ("anthropic", "meta", "mistral", "amazon"):
        mid = mid.split(".", 1)[1]                    # bedrock anthropic.claude-…
    return mid


def _models(entry) -> dict:
    lits, dyn = [], False
    for path, raw in entry.items():
        src = _strip(raw)
        lits += [(m, path) for m in _MODEL_LIT.findall(src)]
        dyn = dyn or (_MODEL_DYN.search(src) is not None)
    if not lits:
        return (unknown("model selected at runtime/config") if dyn
                else unknown("no model literals at assignment sites"))
    if len(lits) > 8:
        return unknown("model selected at runtime/config (many literals)")
    seen = sorted({_strip_provider(m) for m, _ in lits})
    recognized = [m for m in seen if _MODEL_FAMILIES.match(m)]
    ev = [{"path": p, "marker": "model assignment"} for _, p in lits[:1]]
    if len(recognized) == len(seen):
        return fact(seen, "code assignment sites", ev)
    # any unrecognized literal degrades the WHOLE field — never drop it silently
    return assessed(seen, 0.5, ev + [{"path": "models", "marker": "unrecognized model id family"}])


def _tools_consumed(entry):
    tools, seen = [], set()

    def add(name, path):
        if name and name not in seen:
            seen.add(name)
            tools.append({"name": name, "evidence": {"path": path, "marker": "tool wiring"}})

    mcp_conn = dynamic = False
    for path, raw in entry.items():
        src = _strip(raw)
        mcp_conn = mcp_conn or any(m in src for m in _MCP_CONN)
        dynamic = dynamic or any(d in src for d in _DYN_LOADERS)
        for m in _FN_TOOL_DECO.finditer(src):
            d = _PY_DEF.search(src, m.end())
            if d:
                add(d.group(1), path)
        for m in _TOOLS_LIST.finditer(src):
            for ident in _IDENT.findall(m.group(1)):
                add(ident, path)
    # sort the FULL candidate set before truncating — which names survive the
    # cap must not depend on file-iteration/dict order
    tools = sorted(tools, key=lambda t: t["name"])
    inv_capped = len(tools) > _INV_CAP
    tools = tools[:_INV_CAP]
    return tools, mcp_conn, dynamic, inv_capped


def compose(content: dict[str, str], *, capped: bool = False,
            paths=(), meta=None) -> dict:
    entry = _entrypoints(content)
    src_all = "\n".join(_strip(t) for t in entry.values())
    tools, mcp_conn, dynamic, inv_capped = _tools_consumed(entry)
    if mcp_conn:
        complete, reason = False, "consumes external MCP-server tools (defined out-of-repo)"
    elif dynamic:
        complete, reason = False, "tools loaded dynamically"
    elif capped:
        complete, reason = False, "source coverage capped/truncated"
    elif inv_capped:
        complete, reason = False, f"inventory capped at {_INV_CAP}"
    elif not tools:
        complete, reason = False, "no tool wiring statically found"
    else:
        complete, reason = True, None
    constructs = sum(src_all.count(c) for c in CONSTRUCTS)
    return {"framework": _framework(content), "models": _models(entry),
            "tools_consumed": tools, "tools_complete": complete,
            "tools_incomplete_reason": reason,
            "agents_multiple": bool(capped or constructs > 1)}


def fingerprint_facts(match: dict, composition: dict) -> dict:
    """R4. models stays [] until a manifest-declared source exists — literal-scraped
    ids never enter the fingerprint (§5)."""
    fw = composition["framework"].get("value")
    return {"marker_tier": match["marker_tier"], "framework": fw, "models": [],
            "tool_names": sorted(t["name"] for t in composition["tools_consumed"])}


def risk_signals(composition: dict) -> dict:
    names = " ".join(t["name"] for t in composition.get("tools_consumed", [])).lower()
    complete = bool(composition.get("tools_complete"))
    basis = composition.get("tools_incomplete_reason") or "consumed-tool inventory incomplete"
    models = composition.get("models", {})
    has_model = bool(models.get("value")) and not models.get("known_unknown")
    secrets = has_model or any(s in names for s in ("token", "key", "secret"))
    return _signals(names, complete, basis, secrets)
