# LLM-Detection Coverage Audit — Findings & Precision Fix

**Date:** 2026-08-10
**Scope:** CodeRoot corpus assessment (Scope B) — how reliably does the Assessor detect that a repository uses an LLM?
**Question asked:** are we *missing* LLM usage (recall)?
**Answer:** no fixable recall gap. The measurable problem is the opposite — **precision**: the detector over-fires on documentation, tests, and CI config.

---

## 1. What was measured, and how

Three production detection surfaces exist, all hand-maintained allowlists:

| Surface | Matches | Marker class |
|---|---|---|
| `markers.PROVIDER_ENV_MARKERS` (~24) | provider **env-var names** (`OPENAI_API_KEY`, `ANTHROPIC_BASE_URL`, `OLLAMA_HOST`, …) | `provider_identifier` |
| `markers.PROVIDER_MODEL_ID_RE` | **model-id patterns** (`claude-*`, `gpt-*`, `gemini-*`, …) | `provider_model_id` |
| `classify_agent.GENERAL_LLM_DEPS` | LLM **SDK/dependency names** | dependency signal |

Method: run the **real `markers.scan_text`** and a deliberately-broad shape-based heuristic (env-var *shape*, OpenAI-wire endpoints, LLM imports, provider hostnames) over the **same bytes**, so any disagreement is a vocabulary gap, not a sampling artifact. Every candidate miss was adjudicated against real file content with a default verdict of *false positive*.

### Two audit passes — and why the first was wrong

**v1 (rejected)** scanned the **persisted** file bodies (`repo_content`): 4,419 files, **7.78%** of the 56,826 known tree paths. This sample is **biased toward the incumbent**: marker hits feed file-selection ranking, so a file with a *recognized* provider signal is preferentially persisted, while a file whose only signal is *unrecognized* can be cap-dropped and never persisted. The audit would have been searching a set partly chosen by the detector under test — circular.

**v2 (used)** scanned the **acquire-cache bare repos** (`/acquire-cache/*.git`): **46,170 blobs**, 10.4× the coverage, and — critically — the blobs the partial clone *fetched*, before selection ranking. This removes the circularity.

**Honest limits that remain even in v2** (both the incumbent and any string scanner share these):
- `--filter=blob:limit=1MiB` — files >1MiB were never fetched.
- 21 repos have truncated tree inventories — some paths were never enumerated.
- So every number here is a **lower bound**, just a much tighter one.

---

## 2. Recall result: no fixable gap

| | v1 (biased, 4,419) | v2 (unbiased, 46,170) |
|---|---|---|
| broad heuristic fired, detector silent | 11 candidates | **0** |
| …surviving adjudication | **0 / 11** | — |

On the unbiased 46k-blob sample, the shape-based heuristic found **nothing** the production detector missed. The v1 "11 misses" were entirely my heuristic's noise (`guidance` the English word, `haystack` a `needle/haystack` variable in Electron, `Replicate` the verb, `baseURL:` in lodash's Playwright config, `OpenAI` in a copyright header).

### The one real blind spot — shared, not fixable by vocabulary

The **Assessor's own** LLM usage (`assessor/llm/client.py`) is a fully **config-driven** `OpenAICompatibleProvider`: no hardcoded env-var name, no model literal — everything injected via `settings`. **No string scanner can see it** — not the incumbent, not the broad heuristic. The Assessor only "fired" in the audit because of *test-fixture strings* (`"OPENAI_API_KEY"`, `"claude-opus-5"` as detector test data). Strip the fixtures and its real usage is invisible.

This defeats vocabulary *and* shape-detection equally, so it is not an argument for either. It is a documented limit: **a config-driven LLM client with zero literals is undetectable by static content scanning.**

### Verdict on shape-detection: do not build it

Three independent reasons: cost confirmed (v1: 11/11 noise), zero recall benefit (v2: 0 misses), and blind to the one real gap anyway.

---

## 3. Precision result: the detector over-fires

Of **33** repos the detector fired on, adjudication (full for the 16 ≤80-hit tail, path-profile + name for the 17 head) found:

| | count |
|---|---|
| Genuine LLM use | ~21 (all 17 head repos + 4 tail) |
| **False positives** | **12 — every one in the ≤80-hit tail** |

Repo-level precision ≈ **64%**, but the false positives are **perfectly segregated by hit volume**: every repo above 80 hits is real. **Hit volume is itself a precision signal.**

### False-positive taxonomy (all 12, with the fix each needs)

| # | Class | Repos | Where it fires | Mitigation | Cost |
|---|---|---|---|---|---|
| 1 | **MCP server docs** explaining how to connect an external AI client *to* the server | playwright-mcp, github-mcp-server, terraform-mcp-server, mcp/servers | `README.md`, `docs/installation-*` | hard — README is root, not a `docs/` path; needs prose-context suppression | high |
| 2 | **Eval/test harness** that calls an LLM to *grade* the asset | mongodb-mcp-server, grafana/mcp-grafana | `tests/eval/`, `*_eval_test.go` | path exclusion | low |
| 3 | **Test fixtures / detector's own test data** | CodeRoot-Authoring-MCP, CodeRoot-Repo-Assessor | `tests/`, `test_*.py` | path exclusion | low |
| 4 | **Reference/BOM example data** ("fake llama3") | CycloneDX/skills | `references/guides/ML-BOM/` | path exclusion (docs/reference) | low |
| 5 | **Dual-use env var** (`GOOGLE_API_KEY` for Chromium, not Gemini) | electron | source + docs | vocabulary — split `GOOGLE_API_KEY` from `GEMINI_API_KEY`/`GOOGLE_GENAI_API_KEY` (the detector *already* keeps those distinct; the fix is to treat bare `GOOGLE_API_KEY` as weak/ambiguous) | low |
| 6 | **CI maintenance bot** (`claude-code-action`) | type-fest, mcp/servers | `.github/workflows/` | path exclusion | low |
| 7 | **Editor-extension recommendation** (`claude-dev`) | awslabs/mcp | `.devcontainer/devcontainer.json` | file/path exclusion | low |

**5 of 7 classes are closed by path exclusion in `markers.is_scannable`** — which already does exactly this for vendored dirs and lockfiles, by path segment. Adding `test`/`tests`/`__tests__`/`spec`/`docs`/`.github`/`.devcontainer`/`eval`/`evals`/`references` as excluded segments (plus `*_test.go`, `*_eval_test.go`, `test_*.py` basenames) is the same mechanism, same file.

Class 5 (`GOOGLE_API_KEY`) is a small vocabulary refinement. Class 1 (MCP-server README prose) is genuinely harder and lower-value — it is the one class where a real fix needs prose-context reasoning, and it may be acceptable to leave.

---

## 4. Severity — why this is quality, not correctness

Marker hits are consumed in two places, and **neither turns a precision false-positive into a wrong classification:**

- **File-selection ranking** — a false hit lifts a doc/test file into the acquired set, wasting a selection slot. Mild inefficiency.
- **The bespoke-agent probe** — `probes` consumes only `agent_run_shape`/`agent_host_config` hits and **explicitly rejects** `provider_identifier`/`provider_model_id` for candidate promotion (verified in `probes.py`). So a spurious provider hit **cannot** promote a non-agent to an agent.

The over-fire is real and cheaply fixable, but it degrades *acquisition efficiency and evidence cleanliness*, not classification correctness. That is why this is filed as a precision-quality improvement, not a bug.

---

## 5. Recommendation

1. **Close the recall investigation.** No fixable gap on the tightest sample available; the one real blind spot (config-driven, no literals) is unreachable by any static scan.
2. **Do not build shape-based detection.** Measured to add noise and no recall.
3. **Reframe the qwen/grok vocabulary task** (already spawned separately) as consistency hygiene, *not* recall: qwen usage was co-caught by env vars, and adding model names to an over-firing detector risks *more* doc-mention false positives on those names.
4. **Optional precision fix — worth a small task, not urgent:** extend `markers.is_scannable` to exclude test/doc/CI/eval path segments (closes 5 of 7 false-positive classes at their source), plus soften bare `GOOGLE_API_KEY`. Validate by re-running this audit (the scripts are reusable). Leave class 1 (MCP-server README prose) unless it proves to matter.

---

## 6. Reproducing this audit

Scripts (scratchpad, not committed): `audit_cache.py` scans the acquire-cache blobs; the slug→cache-dir map is `sha256("owner/name")` per `DirectSource`. Run inside the `assessor` container against `/acquire-cache`. The adjudication discipline — default false-positive, verify every candidate against real file content via `POST /repos/{id}/files` — is the load-bearing part; the raw regex counts are not trustworthy without it, as v1 proved.
