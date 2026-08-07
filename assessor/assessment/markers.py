"""Versioned marker vocabulary + a pure scan (spec §4 Stage 1).

Marker sets are VERSIONED DATA, same discipline as `classify_agent.AGENT_DEPS`: verify
every identifier against the vendor's own documentation when editing, and bump
MARKER_VOCAB_VERSION. A vocabulary MISS is a silent under-report -- the worst failure shape
for this feature -- so prefer adding a near-duplicate over leaving a gap.

This module is pure: no git, no I/O, no network. `scan_text` takes one (path, text) pair so
it can be driven equally by the acquire-time blob walk and by unit tests.
"""
from __future__ import annotations

import re

MARKER_VOCAB_VERSION = 3

# -- provider identifiers: declared CONFIGURATION surfaces, never inferred usage ----------
# An env var name is the project stating what it accepts. Reading it is declaration; a call
# graph would be inference, which the spec forbids (D6).
PROVIDER_ENV_MARKERS = (
    "ANTHROPIC_API_KEY", "ANTHROPIC_BASE_URL", "ANTHROPIC_AUTH_TOKEN",
    "OPENAI_API_KEY", "OPENAI_BASE_URL", "OPENAI_API_BASE", "AZURE_OPENAI_API_KEY",
    "GEMINI_API_KEY", "GOOGLE_API_KEY", "GOOGLE_GENAI_API_KEY",
    "MISTRAL_API_KEY", "COHERE_API_KEY", "GROQ_API_KEY", "DEEPSEEK_API_KEY",
    "TOGETHER_API_KEY", "REPLICATE_API_TOKEN", "FIREWORKS_API_KEY", "XAI_API_KEY",
    "OPENROUTER_API_KEY", "OLLAMA_HOST", "LM_STUDIO_BASE_URL", "VLLM_BASE_URL",
    "AWS_BEDROCK_REGION", "HUGGINGFACEHUB_API_TOKEN",
)

# Model-id families. Matched as substrings of a quoted/assigned literal, so a prose mention
# of "GPT-4o" in a README does NOT hit here -- prose is handled at a lower tier elsewhere.
# Case-sensitive (model ids in code/config are conventionally lowercase) and guarded by a
# negative lookbehind so a family can't start mid-identifier (e.g. "photo1-2.png",
# "combo1-variant" must NOT match the o[1-4]- family).
# ONE HIT PER LINE: `scan_text` applies this with `.search`, not `.finditer`, so a line naming
# several model ids yields a single hit. That is deliberate for the only consumer today --
# path-level selection ranking, where the line is evidence that the FILE is interesting and a
# second id on it adds nothing. It will UNDER-REPORT for the spec's Stage 2, which counts
# per-provider evidence off the persisted `marker_hits`; switch to `.finditer` there.
PROVIDER_MODEL_ID_RE = re.compile(
    r"(?<![A-Za-z0-9_-])(claude-[a-z0-9.\-]+|gpt-[a-z0-9][a-z0-9.\-]*|o[1-4]-[a-z0-9.\-]+"
    r"|gemini-[a-z0-9.\-]+|mistral-[a-z0-9.\-]+|deepseek-[a-z0-9.\-]+"
    r"|llama-?[0-9][a-z0-9.\-]*)")

# -- agent-host configuration directories -------------------------------------------------
# A repo containing these HOSTS agents/skills. Evidence of agent-ness, and (see Task 1) the
# reason SKILL.md must be positional rather than basename-anywhere.
AGENT_HOST_DIRS = (".claude/", ".codex/", ".cline/", ".agents/", ".qwen/", ".crush/",
                   ".cursor/", ".windsurf/", ".github/copilot/")

# -- agent run-shape, v3: FILE-LEVEL CO-OCCURRENCE -----------------------------------------
# Advisory evidence for the bespoke-agent probe; it never reaches `classify` (spec: agent
# classification precision is a deliberate owner decision, not something this scan may
# override).
#
# v2 matched bare tokens and deliberately excluded `tool_calls`/`function_call` because a
# one-shot chat completion reading `response.choices[0].message.tool_calls` is not an agent.
# That was right about the token and wrong about the outcome: measured against the acquired
# source of the 13 curated agents (2026-08-06), v2 fired on 3 of 13 while `tool_calls` alone
# was present in 9 -- selection stopped being steered toward agent code at all.
#
# v3 keeps the precision argument and recovers the recall by requiring CO-OCCURRENCE: an
# LLM-interaction marker only counts alongside a loop/iteration bound or an agent identity.
# A one-shot call has neither.
#
# These four sets are MEASURED against real repos, exactly like PROVIDER_ENV_MARKERS: do not
# add an entry because it "looks like it belongs", and do not drop one without re-measuring.
AGENT_FRAMEWORK_CONSTRUCTS = (
    "create_react_agent(", "AgentExecutor(", "StateGraph(", "Crew(", "ClaudeSDKClient",
    "initialize_agent(", "ReActAgent(", "FunctionAgent(", "AgentWorkflow(",
    "FunctionCallingAgent(", "def run_agent(",
)
LLM_INTERACTION_MARKERS = (
    "tool_calls", "toolCalls", "function_call", "functionCall",
    "chat.completions", "messages.create", "generate_content", "ChatCompletion",
    "system_prompt", "SYSTEM_PROMPT", "systemPrompt", "chat_completions",
)
AGENT_LOOP_MARKERS = (
    "while True:", "while not done:", "max_iterations", "max_steps", "max_turns",
    "maxIterations", "maxSteps", "maxTurns", "MaxSteps", "MaxTurns",
    "def step(", "def run_step(", "for step in",
    # Rust and Go infinite-loop forms. The rest of this set is Python/JS-shaped, so a Rust
    # or Go agent's loop never counted: adding these two gains openai/codex via
    # `codex-rs/ext/memories/src/tools/mod.rs` (measured, 2026-08-06). The `while <ident> {`
    # form was tested and REJECTED -- it matched Fosowl/agenticSeek's `sources/memory.py`,
    # a Python file, so it was keying on brace syntax rather than on a loop.
    "loop {", "for {",
)
# Cross-language agent identity: Python/TS `class FooAgent`, Go `type FooAgent struct`,
# Rust `struct FooAgent`. Naming a type *Agent is the project declaring the shape, which is
# why it is allowed to substitute for a loop bound -- but only alongside an interaction.
# Leading \b matters: without it the `class` alternative matches INSIDE another word, so a
# comment like `# subclass BaseAgent` scores an identity marker off prose.
_AGENT_IDENTITY_RE = re.compile(
    r"\b(?:class|struct|type)\s+[A-Za-z0-9_]*Agent\b")

_VENDORED = ("node_modules", "vendor", "third_party", "thirdparty", ".venv",
             "site-packages", "dist", "build", "target")
_LOCKFILES = ("package-lock.json", "yarn.lock", "pnpm-lock.yaml", "poetry.lock",
              "Cargo.lock", "go.sum", "composer.lock", "Gemfile.lock", "uv.lock")
_BINARY_EXTS = (".png", ".jpg", ".jpeg", ".gif", ".ico", ".svg", ".pdf", ".zip", ".gz",
                ".tar", ".whl", ".so", ".dylib", ".dll", ".exe", ".wasm", ".bin",
                ".woff", ".woff2", ".ttf", ".mp4", ".mp3")

_MAX_LINE = 200


def is_scannable(path: str) -> bool:
    """False for vendored trees, lockfiles and binaries. Lockfiles are excluded because a
    transitive pin is not a declaration by this project; vendored trees because they are
    someone else's declarations. Vendored-dir matching is by PATH SEGMENT, not substring, so
    `src/rebuild/agent.py` (segment "rebuild") stays scannable while
    `packages/build/index.ts` (segment "build") does not."""
    low = path.lower()
    segments = low.split("/")
    if any(seg in _VENDORED for seg in segments):
        return False
    if low.rsplit("/", 1)[-1] in _LOCKFILES:
        return False
    return not low.endswith(_BINARY_EXTS)


def _hit(marker: str, marker_class: str, path: str, line_no: int, line: str) -> dict:
    return {"marker": marker, "marker_class": marker_class, "path": path,
            "line_no": line_no, "line": line.strip()[:_MAX_LINE]}


def scan_text(path: str, text: str) -> list[dict]:
    """All marker hits in one file. Pure and total: never raises on odd input."""
    hits: list[dict] = []
    low_path = path.lower()
    for d in AGENT_HOST_DIRS:
        if low_path.startswith(d) or ("/" + d) in low_path:
            # The directory itself is the evidence -- there is no source line to cite.
            # line/line_no are placeholders (path, 1) only to satisfy the hit shape; a
            # downstream UI must not render this as if it were quoted code.
            hits.append(_hit(d.rstrip("/"), "agent_host_config", path, 1, path))
            break
    # First line carrying each run-shape group: (line_no, line, matched marker).
    first_framework: tuple[int, str, str] | None = None
    first_interaction: tuple[int, str, str] | None = None
    first_loop: tuple[int, str, str] | None = None
    first_identity: tuple[int, str, str] | None = None
    for line_no, line in enumerate(text.splitlines(), start=1):
        # Collect every occurrence SPAN of every provider-env marker on this line. A check
        # on marker NAMES alone (e.g. "is OPENAI_API_KEY a substring of AZURE_OPENAI_API_KEY")
        # over-suppresses: it can't tell a genuinely separate OPENAI_API_KEY=x declaration
        # elsewhere on the same line from the OPENAI_API_KEY text embedded inside
        # AZURE_OPENAI_API_KEY. Spans fix that by only suppressing a match that is actually
        # contained within a different, longer marker's match at that position.
        spans: list[tuple[int, int, str]] = []
        for m in PROVIDER_ENV_MARKERS:
            start = 0
            while True:
                idx = line.find(m, start)
                if idx == -1:
                    break
                spans.append((idx, idx + len(m), m))
                start = idx + 1
        emitted_env: list[str] = []
        seen_env: set[str] = set()
        for s1, e1, m1 in spans:
            suppressed = any(m2 != m1 and len(m2) > len(m1) and s2 <= s1 and e1 <= e2
                              for s2, e2, m2 in spans)
            if not suppressed and m1 not in seen_env:
                emitted_env.append(m1)
                seen_env.add(m1)
        for m in emitted_env:
            hits.append(_hit(m, "provider_identifier", path, line_no, line))
        mid = PROVIDER_MODEL_ID_RE.search(line)
        if mid:
            hits.append(_hit(mid.group(1), "provider_model_id", path, line_no, line))
        # Run-shape evidence is a FILE-level decision (see the v3 note above), so the line
        # loop only RECORDS the first line carrying each group; the rule is applied once,
        # after the file has been read, and emits at most one hit.
        if first_framework is None:
            for c in AGENT_FRAMEWORK_CONSTRUCTS:
                if c in line:
                    first_framework = (line_no, line, c.rstrip("("))
                    break
        if first_interaction is None:
            for im in LLM_INTERACTION_MARKERS:
                if im in line:
                    first_interaction = (line_no, line, im)
                    break
        if first_loop is None:
            for c in AGENT_LOOP_MARKERS:
                if c in line:
                    first_loop = (line_no, line, c.rstrip("("))
                    break
        if first_identity is None:
            idm = _AGENT_IDENTITY_RE.search(line)
            if idm:
                first_identity = (line_no, line, " ".join(idm.group(0).split()))

    shape = _decide_run_shape(first_framework, first_interaction, first_loop, first_identity)
    if shape is not None:
        anchor_no, anchor_line, marker = shape
        hits.append(_hit(marker, "agent_run_shape", path, anchor_no, anchor_line))
    return hits


def _decide_run_shape(framework, interaction, loop, identity):
    """The v3 rule, applied once per file: framework standalone, OR interaction+loop, OR
    identity+interaction. Returns `(line_no, line, marker)` for the FIRST line that
    evidenced the match, or None. Pure; takes only what the line walk recorded."""
    if framework is not None:
        return framework
    for pair in ((interaction, loop), (identity, interaction)):
        a, b = pair
        if a is not None and b is not None:
            first, second = sorted(pair, key=lambda e: e[0])
            # The marker names BOTH halves: the whole point of v3 is that neither token is
            # evidence on its own, so a hit citing only one would misreport its own basis.
            return (first[0], first[1], f"{first[2]}+{second[2]}")
    return None
