"""Type registry: tier constants (R6), primary selection, risk merge, repo-shape
suppressor, and TYPE_MODULES (spec §2-§3). Modules never carry confidence floats —
classify returns marker_tier and the spine stamps confidence from TIER_CONF."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from .content import _SRC_EXTS

STRONG = 0.95
WEAK = 0.6
TIER_CONF = {"strong": STRONG, "weak": WEAK}
_TIER_RANK = {"strong": 1, "weak": 0}

REGISTRY_VERSION = 9   # bump on every TYPE_MODULES/marker-semantics change; ships rearm_all_assess (§6)
# 8->9: classify_skill's SKILL.md marker became POSITIONAL (root or skills/<name>/ only).
# This IS a marker-semantics change, so unlike the 2026-07-25 dep-manifest widening it must
# be versioned: agent-host config directories (.codex/skills/, .cline/skills/,
# .claude/skills/, .qwen/skills/, internal/skills/builtin/) and evals fixtures no longer
# register as authored skills, so `content_fingerprint` legitimately moves for every
# assessed repo -- that churn is the point here, not collateral. Of the 17 repos in the
# corpus containing a SKILL.md, 12 lose the marker; the other 5 (anthropics/skills,
# CycloneDX/skills, makenotion/skills, upstash/context7, gpt-researcher) keep it because
# they have genuine top-level `skills/` collections.
# NOT bumped for the 2026-07-25 dep-manifest recall widening, deliberately. After the
# narrow/wide split (`classify_agent._all_deps` stayed root-only; only the advisory
# reader widened) there is NO marker-semantics change: the widened reach feeds
# ONLY the advisory bespoke-agent candidate probe, and `coverage_probes` is not a
# `build_payload` input. `registry_version` IS one, so a bump would move EVERY assessed
# repo's `content_fingerprint` — which is served on `AssessmentSummary`, so every
# marketplace poller would see every asset as changed for a change that alters nothing
# in their feed. Migration 0026 still re-derives everything (unconditional assess
# re-arm + acquire re-arm off `allowlist_version < 5`), so probe recall reaches
# already-assessed repos without the fingerprint churn.
FULL_TAXONOMY = ("mcp_server", "agent", "skill", "prompt", "workflow", "tool", "dataset", "model")
PRECEDENCE = ("agent", "mcp_server", "workflow", "skill", "tool", "prompt", "model", "dataset")

# Per-type identity terms (repo-name/topic vocabulary) consumed by the discriminate
# suppressor (§8.3) — types not listed here default-absent.
IDENTITY_TERMS = {"skill": {"skill", "skills", "agent-skills", "claude-skills"},
                  "prompt": {"prompt", "prompts"},
                  "mcp_server": {"mcp", "mcp-server", "mcp-servers",
                                 "modelcontextprotocol", "model-context-protocol"}}


@dataclass(frozen=True)
class TypeModule:
    name: str
    classify: Callable            # (content, *, paths=(), meta=None) -> Match | None   Match={"asset_type","marker_tier","evidence"}
    compose: Callable             # (content, *, capped=False, paths=(), meta=None) -> dict
    fingerprint_facts: Callable   # (match, composition) -> dict (sorted leaves, incl marker_tier)
    risk_signals: Callable        # (composition) -> {flag: Fact | unknown}


def _pick_primary(matches):
    """Rank by (deterministic?, tier, confidence); ties within a rank are always a tie
    (R6) -> fixed PRECEDENCE. Returns (primary, tiebroken, tie_set) — tie_set
    precedence-ordered when tiebroken.

    The leading `not promoted` term makes EVERY deterministic match outrank EVERY
    promoted one (Task 7), regardless of tier. Tier alone is not enough: a promotion is
    stamped `weak`, so against a deterministic WEAK match it tied on
    `(rank=0, conf=0.6)` and fell through to PRECEDENCE — where `agent` is index 0 and
    would seize the primary slot from a real marker, blanking
    `assessment["composition"]` (no composition is derived for a promoted type) and
    replacing the served `classification.evidence` with the single LLM marker. A
    promotion may still become primary when there is NO deterministic match at all —
    that is the bespoke-agent case this feature exists for."""
    key = lambda m: (not m.get("promoted"), _TIER_RANK[m["marker_tier"]], m["confidence"])
    best = max(matches, key=key)
    top = [m for m in matches if key(m) == key(best)]
    if len(top) == 1:
        return best, False, []
    ranked = sorted(top, key=lambda m: PRECEDENCE.index(m["asset_type"]))
    return ranked[0], True, [m["asset_type"] for m in ranked]


# -- Task 7: promotion of a citation-backed coverage-probe candidate ------------------
#
# `probes` surfaces a bespoke agent (a general LLM SDK + a hand-written loop, no
# recognized framework) as an `evidence_state="candidate"` coverage probe rather than a
# classification, and `probes._refine_bespoke_agent` asks the central gateway for a
# `file:symbol` code citation for it. This helper is the ONLY place that citation is
# allowed to reach classification, and it may only move INSIDE the envelope the
# deterministic pass already established:
#
#   * it can only add a type that ALREADY has a `candidate` probe of that same type —
#     it can never introduce a type nothing deterministic pointed at;
#   * it requires a `code_citation` that RESOLVES to a SOURCE file we hold AND names a
#     symbol in it (`_resolve_citation`). Each of those three conditions closed a real
#     hole: non-empty alone promoted a repo with one `openai` dependency line and the
#     adjective "agentic" in its README on the invented path `src/agent.py:run`;
#     existence alone then promoted `README.md:run`, i.e. the attacker-controlled
#     document naming ITSELF; and `README.md:` named no symbol at all.
#     Be precise about what this buys, because it is less than it looks: the check is
#     FILE-LEVEL. It asserts only that some path with a source extension appears in the
#     `paths` inventory. Nothing ever opens that file, nothing verifies the named symbol
#     exists, and the body need never have been acquired at all
#     (`test_citation_resolves_against_the_path_inventory_not_just_fetched_content`
#     pins that). It is not a code-level or content-level commitment, and nearly every
#     repo holds SOME source file, so it is a floor rather than a bar. What the floor
#     actually rules out is exactly the two live exploits above: a docs-only repo cannot
#     promote, and the attacker-controlled README/manifest cannot cite ITSELF into a
#     classification. That is worth having — both were reachable — but it is the whole
#     of it. `undetermined` (we didn't scan enough) and the prose-only empty-citation
#     case (the model had no code to point at) still refuse too, and every refusal is
#     reported in `known_unknowns` so a zero-promotion sweep explains itself;
#   * it stamps `PROMOTED_TIER` ("weak", TIER_CONF 0.6) — never "strong" — so a
#     deterministic strong match still outranks a promotion in `_pick_primary`;
#   * it reuses the citation `probes` ALREADY obtained. It never calls the gateway, and
#     `ReconcileModel` still has no boolean verdict field for an attacker-controlled
#     README to steer — the only thing an injected README can win here is a fabricated
#     citation, which lands at weak tier, is recorded verbatim in `promoted_types` for
#     the curator, and is explicitly labelled as LLM-derived in the match evidence.
#
# It deliberately does NOT touch the fingerprint payload: `assemble` hashes the
# DETERMINISTIC matches only, so `content_fingerprint` is byte-identical with and
# without a promotion (no REGISTRY_VERSION bump, no marketplace-wide fingerprint churn).
PROMOTED_TIER = "weak"
_PROMOTABLE_STATE = "candidate"
_MAX_CITATION = 200          # mirrors ReconcileModel.code_citation's max_length


def _resolve_citation(citation: str, known_paths) -> tuple[str, str]:
    """Resolve a `file:symbol` citation. Returns `(path, "")` on success, or
    `("", reason)` naming why it was refused.

    SCOPE, stated up front so no caller over-reads the result: this is a FILE-LEVEL
    check against the `paths` inventory. It is never symbol-level and never
    content-level — the cited file's body is never inspected here and need not even
    have been acquired. A success means "a source-extension path by that name exists in
    this snapshot, and something followed the colon", nothing stronger.

    Three things must all hold, and requiring only the first was not enough:

    1. **The path exists.** Exact membership in what we hold — no basename or fuzzy
       matching. A near-miss (wrong directory, repo-name prefix, invented path) is
       exactly the hallucination this gate catches.
    2. **The path is SOURCE** (`_SRC_EXTS`) — by EXTENSION, not by reading it.
       Requiring only (1) left the exploit narrowed but open: the attacker-controlled
       README is itself a file we hold, so `README.md:run` — the model naming the
       injected document back at us in one token — promoted. So did `package.json:run`.
       The whole point of the envelope is that a README alone cannot classify a repo;
       without this check that was aspirational, not true. Conversely, since almost
       every repo carries some source file, (2) excludes docs-only repos and
       self-citation and little else.
    3. **A non-empty tail follows the path.** `README.md:` named no symbol at all and
       still passed, contradicting this function's own contract. We check only that the
       tail is non-blank — whether it names a real symbol in that file is not, and
       cannot be, established here.

    We do NOT guess which colon separates path from symbol — real citations include
    `file.py:func`, `file.rs:Type::method` and `file.ts:120:4` — so every
    colon-delimited prefix is tried and the first that satisfies all three wins. A
    prefix that resolves but fails (2) or (3) is remembered for the refusal reason
    rather than ending the scan, so a later prefix can still succeed.

    Refusing is always the safe direction: the candidate simply stays a candidate, and
    the caller records WHY in `known_unknowns` so a zero-promotion sweep explains
    itself."""
    if not isinstance(citation, str) or not citation.strip():
        return "", "no_citation"
    if ":" not in citation:
        return "", "no_symbol"          # a bare path names nothing in the code

    non_source = no_tail = ""
    for idx, ch in enumerate(citation):
        if ch != ":":
            continue
        head = citation[:idx].strip().lstrip("./").lstrip("/")
        if not head or head not in known_paths:
            continue
        if not head.lower().endswith(_SRC_EXTS):
            non_source = non_source or head
            continue
        if not citation[idx + 1:].strip():
            no_tail = no_tail or head
            continue
        return head, ""
    if non_source:
        return "", "not_source"
    if no_tail:
        return "", "no_symbol"
    return "", "unresolved"


_REFUSAL_DETAIL = {
    "no_citation": "the LLM read returned no code citation",
    "unresolved": "citation {c!r} does not name a file in this snapshot",
    "not_source": "citation {c!r} does not name a source file in this snapshot",
    "no_symbol": "citation {c!r} names no symbol after the file path",
}


def _refusal_detail(asset_type: str, citation: str, reason: str) -> str:
    """Human-readable `known_unknowns` detail for a refused promotion.

    OBSERVABILITY (why this exists): without it, "no candidates existed" and "candidates
    existed but every citation was rejected" both render as `promoted_types == []` with
    nothing to tell them apart — and a loosely-citing local model makes the second
    outcome likely on the first live sweep. The citation is quoted VERBATIM so a curator
    can see what the model actually said."""
    body = _REFUSAL_DETAIL[reason].format(c=citation)
    return f"{asset_type} candidate not promoted: {body}"


def promote_from_probes(matches, coverage_probes, suppressed=(), *, known_paths=frozenset()):
    """Return `(promoted_matches, promoted_records, refusals)` for candidate probes.

    `promoted_matches` are match dicts (same shape the classifiers emit, plus
    `promoted: True`) to append to the kept set AFTER the fingerprint has been
    computed; `promoted_records` are the audit trail served as
    `assessment["promoted_types"]`; `refusals` are `{asset_type, citation, reason,
    detail}` for candidates the gates turned down, which the caller records as
    `known_unknowns`. Pure — no I/O, no gateway call.

    `suppressed` is the suppressor output: a type a deterministic rule DROPPED (demo/
    template shape, weak keyword-only) must not be reinstated by an LLM citation —
    that is the LLM overriding deterministic evidence, not moving inside it.

    `known_paths` is the set of files we actually hold for this subject (acquired
    content keys plus the repo path inventory). The citation must name a SOURCE file
    among them and name a symbol inside it — see `_resolve_citation`."""
    known_types = {m.name for m in TYPE_MODULES}
    already = {m["asset_type"] for m in (matches or [])}
    already |= {s.get("asset_type") for s in (suppressed or ()) if isinstance(s, dict)}
    promoted, records, refusals = [], [], []
    for probe in coverage_probes or []:
        if not isinstance(probe, dict):
            continue
        asset_type = probe.get("type")
        if asset_type not in known_types or asset_type in already:
            continue
        if probe.get("evidence_state") != _PROMOTABLE_STATE:
            continue
        # Defensive unwrapping throughout: a stored/legacy `llm_reconciliation` is
        # LLM-shaped JSON, so anything that is not the `shapes.assessed` envelope with
        # a string citation must REFUSE rather than raise.
        recon = probe.get("llm_reconciliation")
        if not isinstance(recon, dict):
            # Gateway off, unreachable, or invalid output. NOT a refusal — the feature
            # simply did not run, and the gateway is off by default, so recording one
            # here would put a "not promoted" note on essentially every candidate in
            # the corpus and drown the refusals that actually mean something.
            continue
        value = recon.get("value")
        citation = value.get("citation") if isinstance(value, dict) else None
        citation = citation.strip()[:_MAX_CITATION] if isinstance(citation, str) else ""
        _path, reason = _resolve_citation(citation, known_paths)
        if reason:
            refusals.append({"asset_type": asset_type, "citation": citation, "reason": reason,
                             "detail": _refusal_detail(asset_type, citation, reason)})
            continue
        already.add(asset_type)          # one promotion per type, first probe wins
        promoted.append({
            "asset_type": asset_type, "marker_tier": PROMOTED_TIER,
            "confidence": TIER_CONF[PROMOTED_TIER], "promoted": True,
            "evidence": [{"path": "llm",
                          "marker": f"promoted coverage-probe candidate — citation: {citation}"}],
        })
        records.append({"asset_type": asset_type, "from_evidence_state": _PROMOTABLE_STATE,
                        "citation": citation, "confidence": TIER_CONF[PROMOTED_TIER]})
    return promoted, records, refusals


def _risk_rank(flag: dict) -> int:
    if flag.get("known_unknown"):
        return 1
    return 2 if flag.get("value") else 0


def _merge_risk(per_type):
    """Three-valued per-flag lattice: fact(True) > unknown(basis) > fact(False) (§7).
    Sorted by type name first => order-independent. Evidence concatenates only
    between Fact envelopes; unknown-over-clean records who reported clean."""
    out: dict[str, dict] = {}
    clean_by: dict[str, list[str]] = {}
    for tname, flags in sorted(per_type, key=lambda p: p[0]):
        for key, flag in flags.items():
            if _risk_rank(flag) == 0:
                clean_by.setdefault(key, []).append(tname)
            cur = out.get(key)
            if cur is None or _risk_rank(flag) > _risk_rank(cur):
                out[key] = dict(flag)
            elif _risk_rank(flag) == _risk_rank(cur) == 2:
                out[key]["evidence"] = list(cur.get("evidence") or []) + list(flag.get("evidence") or [])
    for key, flag in out.items():
        if _risk_rank(flag) == 1 and clean_by.get(key):
            flag["basis"] = ("clean-over-complete reported by: " + ", ".join(sorted(clean_by[key])))
    return out


_SHAPE_SUFFIXES = ("-template", "-example", "-starter", "-demo")
_EXAMPLE_DIRS = ("examples/", "cookbook/")
_EXAMPLE_DENSITY = 0.5           # §4.3: threshold fixed here, encoded in the fixture
_SHAPE_EXEMPT = {"mcp_server"}   # R3 grandfather (§4.3); removed by the named follow-up


def repo_is_scaffold(repo_url: str, content) -> bool:
    """Demo/template shape verdict: slug ends -template/-example/-starter/-demo, OR
    the source tree is example-dense (§4.3). Extracted from
    `_apply_repo_shape_suppressor` into a reusable predicate — the coverage-probe
    detector (DP3) uses it to skip probes for a scaffold repo (expected-incomplete),
    same verdict the suppressor already uses to drop weak matches."""
    src = [p for p in content if p.lower().endswith(_SRC_EXTS)]
    dense = bool(src) and (sum(p.startswith(_EXAMPLE_DIRS) for p in src) / len(src)) >= _EXAMPLE_DENSITY
    name = repo_url.rstrip("/").rsplit("/", 1)[-1].lower()
    return name.endswith(_SHAPE_SUFFIXES) or dense


def _apply_repo_shape_suppressor(matches, repo_url, content):
    if not repo_is_scaffold(repo_url, content):
        return matches, []
    kept, suppressed = [], []
    for m in matches:
        if m["marker_tier"] == "weak" and m["asset_type"] not in _SHAPE_EXEMPT:
            suppressed.append({"asset_type": m["asset_type"],
                               "reason": "demo/template shape — weak match suppressed"})
        else:
            kept.append(m)
    return kept, suppressed


_PROSE_PATHS = {"README.md", "README.rst"}


def _is_prose_only(match) -> bool:
    """A weak match whose every evidence entry is a README/prose keyword marker —
    no manifest, dep, construction, or file marker. (mcp's 'mcp keyword' is prose;
    'server construction' / 'MCP manifest present' / 'dep ...' are NOT.)"""
    ev = match.get("evidence") or []
    if not ev:
        return False
    return all(
        (e.get("path") in _PROSE_PATHS) or ("keyword" in (e.get("marker") or "")) or ("prose" in (e.get("marker") or ""))
        for e in ev
    )


def _corroborated(asset_type, meta) -> bool:
    """Does declared identity (topics/description) confirm THIS type? Permissive on
    purpose — being generous here means we suppress LESS (safe direction: never drop
    a genuine dual-type)."""
    if not isinstance(meta, dict):
        return False
    terms = IDENTITY_TERMS.get(asset_type, set())
    if not terms:
        return False
    topics = meta.get("topics") or []
    if not isinstance(topics, list):
        topics = []
    topics_l = {str(t).lower() for t in topics}
    if terms & topics_l:
        return True
    desc = (meta.get("description") or "").lower()
    # match hyphenated terms against a spaced description too ("mcp-server" ~ "mcp server")
    return any(t in desc or t.replace("-", " ") in desc for t in terms)


def _apply_declared_identity_suppressor(matches, meta):
    """Drop a weak, prose-only match of type X when (a) some OTHER type has a strong
    match, and (b) X is not corroborated by declared identity. Returns (kept, suppressed)."""
    strong_types = {m["asset_type"] for m in matches if m["marker_tier"] == "strong"}
    kept, suppressed = [], []
    for m in matches:
        other_strong = bool(strong_types - {m["asset_type"]})
        if (m["marker_tier"] == "weak" and _is_prose_only(m)
                and other_strong and not _corroborated(m["asset_type"], meta)):
            suppressed.append({"asset_type": m["asset_type"],
                               "reason": "weak keyword-only — contains-not-ships (strong other-type present)"})
        else:
            kept.append(m)
    return kept, suppressed


# Bottom imports on purpose: the type modules import TIER constants from this module,
# which are already defined by the time these imports execute.
from . import classify_mcp, compose_mcp   # noqa: E402
from . import risk as _risk               # noqa: E402

MCP_SERVER = TypeModule(
    name=classify_mcp.NAME, classify=classify_mcp.classify,
    compose=lambda content, *, capped=False, paths=(), meta=None:
        compose_mcp.compose(content, source_coverage_capped=capped),
    fingerprint_facts=compose_mcp.fingerprint_facts, risk_signals=_risk.assess)

from . import classify_agent, compose_agent   # noqa: E402

AGENT = TypeModule(name=classify_agent.NAME, classify=classify_agent.classify,
                   compose=lambda content, *, capped=False, paths=(), meta=None:
                       compose_agent.compose(content, capped=capped),
                   fingerprint_facts=compose_agent.fingerprint_facts,
                   risk_signals=compose_agent.risk_signals)

from . import classify_skill, compose_skill   # noqa: E402

SKILL = TypeModule(name=classify_skill.NAME, classify=classify_skill.classify,
                   compose=lambda content, *, capped=False, paths=(), meta=None:
                       compose_skill.compose(content, capped=capped,
                                              paths=paths, meta=meta),
                   fingerprint_facts=compose_skill.fingerprint_facts,
                   risk_signals=compose_skill.risk_signals)

from . import classify_prompt, compose_prompt   # noqa: E402

PROMPT = TypeModule(name=classify_prompt.NAME, classify=classify_prompt.classify,
                    compose=lambda content, *, capped=False, paths=(), meta=None:
                        compose_prompt.compose(content, capped=capped,
                                                paths=paths, meta=meta),
                    fingerprint_facts=compose_prompt.fingerprint_facts,
                    risk_signals=compose_prompt.risk_signals)

TYPE_MODULES = (MCP_SERVER, AGENT, SKILL, PROMPT)
