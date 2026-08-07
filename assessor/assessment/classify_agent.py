"""Deterministic agent classification (spec §4.1). Marker sets are VERSIONED DATA —
verify every dep name against the live registry (PyPI/npm) when editing. Rules:
uses-an-LLM != is-an-agent (R3): a general LLM dep or a construct alone never matches."""
from __future__ import annotations

import json
import re

from .compose_mcp import _BLOCK_COMMENT, _LINE_COMMENT, _entrypoints

NAME = "agent"

# -- versioned marker data (§4.1) --------------------------------------------------
AGENT_DEPS = frozenset({
    # py (PyPI-verified names)
    "langgraph", "crewai", "pyautogen", "ag2", "autogen-agentchat", "autogen-core",
    "openai-agents", "google-adk", "pydantic-ai", "smolagents", "claude-agent-sdk",
    "browser-use", "livekit-agents", "pipecat-ai",
    # js
    "@openai/agents", "@mastra/core", "@anthropic-ai/claude-agent-sdk",
})
AGENT_DEP_PREFIXES = ("llama-index-agent-",)
GENERAL_LLM_DEPS = frozenset({"langchain", "llama-index", "openai", "anthropic", "@anthropic-ai/sdk"})
GENERAL_LLM_PREFIXES = ("@langchain/", "langchain-", "llama-index-")
CONSTRUCTS = ("create_react_agent(", "AgentExecutor(", "StateGraph(", "Crew(", "ClaudeSDKClient",
              "initialize_agent(", "ReActAgent(", "FunctionAgent(", "AgentWorkflow(",
              "FunctionCallingAgent(")
PROSE = ("ai agent", "autonomous agent", "agentic", "react agent", "tool-calling agent")

_REQ_LINE = re.compile(r"^\s*([A-Za-z0-9_.\-]+)")
_PYPROJECT_DEP = re.compile(r'["\']([A-Za-z0-9_.\-]+)')
_CARGO_KEY = re.compile(r'^["\']?([A-Za-z0-9_.\-]+)["\']?\s*=')
_CSPROJ_REF = re.compile(r'<PackageReference\b[^>]*\bInclude\s*=\s*["\']([^"\']+)["\']', re.I)
_POM_DEP = re.compile(r"<dependency\b[^>]*>(.*?)</dependency>", re.S | re.I)
_POM_ARTIFACT = re.compile(r"<artifactId>\s*([^<]+?)\s*</artifactId>", re.I)
_POM_GROUP = re.compile(r"<groupId>\s*([^<]+?)\s*</groupId>", re.I)
_GRADLE_DEP = re.compile(
    r"\b(?:implementation|api|compileOnly|compileOnlyApi|runtimeOnly|classpath|"
    r"annotationProcessor|kapt|ksp|developmentOnly|testImplementation|testRuntimeOnly)"
    r"\s*[( ]\s*['\"]([^'\"]+)['\"]")
_GEMFILE_GEM = re.compile(r"""^\s*gem\s+["']([^"']+)["']""")
_CARGO_DEP_TABLES = ("dependencies", "dev-dependencies", "build-dependencies")


def _norm(name: str) -> str:
    return name.lower().replace("_", "-")


# =====================================================================================
# CLASSIFICATION dependency reach — DO NOT WIDEN.
# =====================================================================================
# `_all_deps` is the NARROW, root-only reader. It is the ONLY dep source allowed to
# reach real classification / composition / the content fingerprint, and it has three
# consumers, all classification-bearing:
#   1. `classify` below            — an AGENT_DEPS hit is a STRONG `agent` match, and
#                                    PRECEDENCE puts `agent` ahead of `mcp_server`.
#   2. `compose_agent._framework`  — feeds `compose_agent.fingerprint_facts`.
#   3. `compose_mcp._external_delegation`.
# `acquire` now fetches dependency manifests from SUBDIRECTORIES and from non-Python/JS
# ecosystems (content.py `_DEP_MANIFESTS`). Letting those reach the consumers above
# would let an unrelated subpackage's manifest flip a repo's primary type — e.g. a
# `crewai` pin in one of awslabs/mcp's 63 non-root manifests would seize `primary_type`
# from `mcp_server`. That is auto-classifying bespoke agents by the back door, which the
# owner explicitly rejected. So this function stays EXACTLY as it was before the
# widening: root `package.json` / `requirements.txt` / `requirements-dev.txt` /
# `pyproject.toml` only.
#
# If you need the widened reach, use `all_deps_wide` below — and read its docstring
# first: it must NEVER be wired into any of the three consumers above.
def _all_deps(content: dict[str, str]) -> set[str]:
    deps: set[str] = set()
    try:
        pkg = json.loads(content.get("package.json", "") or "{}")
    except ValueError:
        pkg = {}
    if isinstance(pkg, dict):
        for key in ("dependencies", "devDependencies"):
            d = pkg.get(key)
            if isinstance(d, dict):
                deps.update(k.lower() for k in d)
    for fname in ("requirements.txt", "requirements-dev.txt"):
        for line in content.get(fname, "").splitlines():
            line = line.split("#", 1)[0].strip()
            m = _REQ_LINE.match(line)
            if m:
                deps.add(_norm(m.group(1)))
    py, in_deps, in_poetry = content.get("pyproject.toml", ""), False, False
    for line in py.splitlines():
        s = line.strip()
        if s.startswith("["):
            in_poetry = s == "[tool.poetry.dependencies]"
            in_deps = False
            continue
        if s.startswith("dependencies") and "[" in s:
            in_deps = True
        if in_deps:
            deps.update(_norm(x) for x in _PYPROJECT_DEP.findall(s))
            if s.endswith("]"):
                in_deps = False
        elif in_poetry and "=" in s:
            deps.add(_norm(s.split("=", 1)[0].strip()))
    return deps


# =====================================================================================
# ADVISORY-ONLY dependency reach (`all_deps_wide`) — never reaches classification.
# =====================================================================================
# -- per-manifest dependency parsers, keyed on BASENAME so a manifest acquired from a
# subdirectory or a non-Python/JS ecosystem is parsed too (acquire now fetches those —
# see docs/superpowers/plans/2026-07-25-agent-candidate-egress-and-recall.md). The
# package.json / requirements*.txt / pyproject.toml bodies mirror the narrow `_all_deps`
# logic above; their semantics (including `k.lower()` for npm scoped names vs `_norm`
# elsewhere) must not drift from it — `test_assessment_all_deps_ecosystems` pins the two
# to agree on root manifests. Every parser is total: malformed input yields no deps.

def _deps_package_json(text: str) -> set[str]:
    deps: set[str] = set()
    try:
        pkg = json.loads(text or "{}")
    except ValueError:
        pkg = {}
    if isinstance(pkg, dict):
        for key in ("dependencies", "devDependencies"):
            d = pkg.get(key)
            if isinstance(d, dict):
                deps.update(k.lower() for k in d)
    return deps


def _deps_requirements(text: str) -> set[str]:
    deps: set[str] = set()
    for line in text.splitlines():
        line = line.split("#", 1)[0].strip()
        m = _REQ_LINE.match(line)
        if m:
            deps.add(_norm(m.group(1)))
    return deps


def _deps_pyproject(text: str) -> set[str]:
    deps: set[str] = set()
    in_deps = in_poetry = False
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("["):
            in_poetry = s == "[tool.poetry.dependencies]"
            in_deps = False
            continue
        if s.startswith("dependencies") and "[" in s:
            in_deps = True
        if in_deps:
            deps.update(_norm(x) for x in _PYPROJECT_DEP.findall(s))
            if s.endswith("]"):
                in_deps = False
        elif in_poetry and "=" in s:
            deps.add(_norm(s.split("=", 1)[0].strip()))
    return deps


def _deps_go_mod(text: str) -> set[str]:
    """`require (...)` blocks and single-line `require x v1`. Module paths are only
    lowercased, never `_norm`ed: `_` is legal in a module path and hyphenating it
    would name a different module."""
    deps: set[str] = set()
    in_block = False

    def _add(mod: str) -> None:
        deps.add(mod)
        # ALSO emit the last non-version path segment. Measured 2026-08-06: crush declares
        # `github.com/openai/openai-go/v3`, `google.golang.org/genai` and
        # `github.com/charmbracelet/anthropic-sdk-go` -- full paths that can never equal a
        # bare vocabulary name, which is why a Go agent could not reach the candidate probe.
        # The full path is still emitted; this only ADDS a name.
        parts = [p for p in mod.split("/") if not re.fullmatch(r"v\d+", p)]
        if len(parts) > 1:
            deps.add(parts[-1])

    for raw in text.splitlines():
        s = raw.split("//", 1)[0].strip()
        if not s:
            continue
        if in_block:
            if s.startswith(")"):
                in_block = False
            else:
                _add(s.split()[0].strip('"').lower())
        elif s.startswith("require"):
            rest = s[len("require"):].strip()
            if rest.startswith("("):
                in_block = True
            elif rest:
                _add(rest.split()[0].strip('"').lower())
    return deps


def _deps_cargo(text: str) -> set[str]:
    """Keys under any `…dependencies` table (`[dependencies]`, `[dev-dependencies]`,
    `[workspace.dependencies]`, `[target.'cfg(x)'.dependencies]`), both `k = "v"` and
    `k = { … }`, plus the per-crate `[dependencies.<crate>]` form. Brace depth is tracked
    so a wrapped inline table's continuation lines (`features = [...]`) are not read as
    crate names."""
    deps: set[str] = set()
    in_deps, depth = False, 0
    for raw in text.splitlines():
        s = raw.split("#", 1)[0].strip()
        if depth <= 0 and s.startswith("["):
            parts = [p.strip('"\'') for p in s.strip("[] ").split(".")]
            in_deps = parts[-1] in _CARGO_DEP_TABLES
            if not in_deps and len(parts) >= 2 and parts[-2] in _CARGO_DEP_TABLES:
                deps.add(_norm(parts[-1]))           # [dependencies.<crate>]
            depth = 0
            continue
        if in_deps and depth <= 0:
            m = _CARGO_KEY.match(s)
            if m:
                deps.add(_norm(m.group(1)))
        depth = max(0, depth + s.count("{") - s.count("}"))
    return deps


def _deps_csproj(text: str) -> set[str]:
    return {_norm(m) for m in _CSPROJ_REF.findall(text)}


def _deps_pom(text: str) -> set[str]:
    """artifactIds inside `<dependency>` blocks only — a bare artifactId scan would
    also pick up the POM's own coordinates and every build plugin."""
    deps: set[str] = set()
    for block in _POM_DEP.findall(text):
        art = _POM_ARTIFACT.search(block)
        if not art:
            continue
        deps.add(_norm(art.group(1)))
        grp = _POM_GROUP.search(block)
        if grp:
            deps.add(_norm(f"{grp.group(1)}:{art.group(1)}"))
    return deps


def _deps_gradle(text: str) -> set[str]:
    deps: set[str] = set()
    for coord in _GRADLE_DEP.findall(text):
        parts = coord.split(":")
        if len(parts) >= 2:
            deps.add(_norm(parts[1]))
            deps.add(_norm(f"{parts[0]}:{parts[1]}"))
        elif parts[0]:
            deps.add(_norm(parts[0]))
    return deps


def _deps_gemfile(text: str) -> set[str]:
    return {_norm(m.group(1)) for m in
            filter(None, (_GEMFILE_GEM.match(line) for line in text.splitlines()))}


def _deps_composer(text: str) -> set[str]:
    deps: set[str] = set()
    try:
        doc = json.loads(text or "{}")
    except ValueError:
        doc = {}
    if isinstance(doc, dict):
        for key in ("require", "require-dev"):
            d = doc.get(key)
            if isinstance(d, dict):
                deps.update(k.lower() for k in d)
    return deps


_DEP_PARSERS = {
    "package.json": _deps_package_json,
    "requirements.txt": _deps_requirements,
    "requirements-dev.txt": _deps_requirements,
    "pyproject.toml": _deps_pyproject,
    "go.mod": _deps_go_mod,
    "Cargo.toml": _deps_cargo,
    "pom.xml": _deps_pom,
    "build.gradle": _deps_gradle,
    "build.gradle.kts": _deps_gradle,
    "Gemfile": _deps_gemfile,
    "composer.json": _deps_composer,
}
_DEP_PARSER_SUFFIXES = ((".csproj", _deps_csproj),)   # C# project files are named per-project


def all_deps_wide(content: dict[str, str]) -> set[str]:
    """ADVISORY-ONLY dependency reach: EVERY acquired manifest, at ANY depth, in ANY
    supported ecosystem.

    ⚠ ITS ONLY LEGAL CONSUMER IS `probes._bespoke_agent_candidate` (gate A of the
    bespoke-agent CANDIDATE probe, which is curator-review output and never enters
    `asset_types`, `primary_type`, risk, or the content fingerprint).

    DO NOT wire this into `classify` below, `compose_agent._framework`, or
    `compose_mcp._external_delegation` — use the narrow `_all_deps` there. Doing so
    would let an unrelated subdirectory's manifest change a repo's real classification
    (the rejected "auto-classify bespoke agents" behavior arriving indirectly); see the
    banner above `_all_deps`.
    """
    deps: set[str] = set()
    for path, text in (content or {}).items():
        base = path.rsplit("/", 1)[-1]
        parser = _DEP_PARSERS.get(base)
        if parser is None:
            parser = next((p for suf, p in _DEP_PARSER_SUFFIXES if base.endswith(suf)), None)
        if parser is None or not isinstance(text, str):
            continue
        try:
            deps.update(parser(text))
        except Exception:                            # a manifest must never fail assessment
            continue
    return deps


def _dirname(path: str) -> str:
    return path.rsplit("/", 1)[0] if "/" in path else ""


def _crewai_manifests(content) -> str | None:
    """agents.yaml + tasks.yaml as SIBLINGS with CrewAI shape (role:/goal:) — §4.1."""
    by_dir: dict[str, set[str]] = {}
    for p in content:
        base = p.rsplit("/", 1)[-1]
        if base in ("agents.yaml", "tasks.yaml"):
            by_dir.setdefault(_dirname(p), set()).add(base)
    for d, bases in by_dir.items():
        if bases == {"agents.yaml", "tasks.yaml"}:
            agents = content[(d + "/" if d else "") + "agents.yaml"]
            if "role:" in agents and "goal:" in agents:
                return (d + "/" if d else "") + "agents.yaml"
    return None


def _agent_card(content) -> str | None:
    for p in (".well-known/agent-card.json", ".well-known/agent.json"):
        if p in content:
            return p
    if "agent.json" in content:                      # bare top-level: AgentCard shape gate
        try:
            card = json.loads(content["agent.json"])
        except ValueError:
            return None
        if isinstance(card, dict) and card.get("name") and \
                any(k in card for k in ("url", "capabilities", "skills")):
            return "agent.json"
    return None


def classify(content: dict[str, str], *, paths=(), meta=None):
    ev: list[dict] = []
    deps = _all_deps(content)
    strong = False
    for d in sorted(deps):
        if d in AGENT_DEPS or d.startswith(AGENT_DEP_PREFIXES):
            ev.append({"path": "deps", "marker": f"agent-native dep {d}"})
            strong = True
    if "langgraph.json" in content:
        ev.append({"path": "langgraph.json", "marker": "langgraph manifest"})
        strong = True
    card = _agent_card(content)
    if card:
        ev.append({"path": card, "marker": "A2A agent card"})
        strong = True
    crew = _crewai_manifests(content)
    if crew:
        ev.append({"path": crew, "marker": "crewai agents+tasks manifests"})
        strong = True

    weak = False
    if not strong:
        general = any(d in GENERAL_LLM_DEPS or d.startswith(GENERAL_LLM_PREFIXES) for d in deps)
        if general:
            src = "\n".join(_LINE_COMMENT.sub("", _BLOCK_COMMENT.sub("", t))
                            for t in _entrypoints(content).values())
            for c in CONSTRUCTS:
                if c in src:
                    ev.append({"path": "entrypoint", "marker": f"agent construct {c.rstrip('(')}"})
                    weak = True
                    break
    if not (strong or weak):
        return None
    blob = (content.get("README.md", "") + content.get("README.rst", "")).lower()
    for phrase in PROSE:                              # evidence-only — never affects tier (§4.1)
        if phrase in blob:
            ev.append({"path": "README.md", "marker": f"prose: {phrase}"})
            break
    return {"asset_type": NAME, "marker_tier": "strong" if strong else "weak", "evidence": ev}
