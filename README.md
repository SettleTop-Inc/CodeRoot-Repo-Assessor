# CodeRoot Repo Assessor

Classify a source repository as an agent, MCP server, skill or prompt — with evidence.

This is a standalone judgment service: point it at a repository and it reads the
repository's own files — manifests, entrypoints, README prose — and returns a
classification backed by named evidence, never a bare label. The same judgment
core is reachable two ways: an HTTP API, and an MCP server exposing three tools
to any MCP client. In that second sense this repo is itself both things it
classifies — an agent that assesses repositories, and the MCP server that
exposes that assessment.

It ships model-agnostic. An LLM is never required to run it: every deterministic
marker (a dependency, a manifest file, a constructed server object) is evaluated
without one. A configured OpenAI-compatible endpoint only ever *adds* advisory
fields on top of that — a business-domain guess, a citation-backed second look at
a borderline case — and every one of those is labelled as LLM-derived. Bring your
own model, or run with none at all.

## Quickstart

```bash
docker build -t coderoot-repo-assessor:dev .

docker run --rm -p 8081:8081 \
  -e ASSESSOR_API_TOKEN=changeme \
  -e LLM_PROVIDER=openai_compatible \
  -e LLM_BASE_URL=http://host.docker.internal:11434/v1 \
  -e LLM_MODEL=qwen2.5:7b \
  coderoot-repo-assessor:dev
```

The three `LLM_*` lines are optional — omit them (or leave `LLM_PROVIDER` unset,
which defaults to `none`) to run with no model at all; classification itself
does not change, only the advisory LLM-derived fields disappear in favor of an
honest `known_unknown`. `ASSESSOR_API_TOKEN` is not optional: the container
refuses to start without either a token or `-e ASSESSOR_ALLOW_ANONYMOUS=true`
set explicitly (see [Honesty](#honesty) — `/v1/acquire` clones a caller-supplied
URL using your GitHub tokens, so an unauthenticated default would be a
request-forgery primitive with credentials attached).

```bash
curl -s localhost:8081/healthz
# {"status":"ok"}

curl -s -X POST localhost:8081/v1/assess \
  -H 'content-type: application/json' \
  -H 'authorization: Bearer changeme' \
  -d '{"subject":{"repo_url":"https://github.com/octocat/Hello-World",
       "subject_key":"octocat/Hello-World","commit_sha":"","subdir":""},
       "source":"direct"}'
```

`GITHUB_TOKENS` (comma-separated PATs) is optional too but strongly recommended
— it raises the GitHub REST API rate limit (60 → 5000/hr) that the repo-object
and commit-SHA lookups run against. It has no effect on the git fetch itself:
that runs deliberately hardened against credential injection (no credential
helper, no askpass) and is always anonymous, token or not.

## MCP

Any MCP client can reach the same judgment core `build_mcp` exposes, as three
tools:

| Tool | Description |
| --- | --- |
| `assess_repository(repo_url, subject_key="", subdir="")` | Classify a source repository as an agent, MCP server, skill or prompt. Returns the asset types found, a confidence, the evidence behind each match, a composition inventory, and an explicit list of what could not be determined. |
| `acquire_repository(repo_url)` | Fetch a repository's file snapshot at its current HEAD, with the marker scan and path inventory the classifier uses. Returns the pinned commit SHA alongside the selected file bodies. |
| `assessor_version()` | Report the classification registry, selection allowlist and marker vocabulary versions. A change in any of them means previously derived records are stale and should be re-derived. |

`assess_repository` and `acquire_repository` call the same handlers as the
HTTP surface's `/v1/assess` and `/v1/acquire` and map the same typed errors
(`NotDerivable`, `RepoGone`, invalid-URL `ValueError`) to the same body
shape, so a caller sees identical failure behavior regardless of which
surface it used. The request shapes are not identical, though: incremental
re-acquire via `prior` (skip the git fetch entirely when the caller's last
known commit SHA and allowlist version still match) is HTTP-only today —
`acquire_repository` always calls with `prior=None`, so `status:"unchanged"`
is reachable over `/v1/acquire` but not yet from this MCP tool.

Run the MCP server directly with its packaged stdio entrypoint:

```bash
coderoot-repo-assessor-mcp
```

Point any MCP client (Claude Desktop, an IDE plugin, etc.) at this command
as a subprocess and it speaks MCP over that process's stdin/stdout.

## Configuration

Every setting arrives as an environment variable (`.env.example` is the
canonical list; copy it to `.env` for local, non-Docker runs — no value is
committed).

| Variable | Default | Secret | Purpose |
| --- | --- | --- | --- |
| `LLM_PROVIDER` | `none` | no | `none` or `openai_compatible`. Off by default — every classification works with no model configured. |
| `LLM_BASE_URL` | *(empty)* | no | Base URL of an OpenAI-compatible chat-completions endpoint. |
| `LLM_MODEL` | *(empty)* | no | Model name requested from that endpoint. |
| `LLM_API_KEY` | *(empty)* | yes | Credential for the LLM endpoint, if it requires one. |
| `LLM_TIMEOUT_S` | `60` | no | Per-call LLM timeout, in seconds. |
| `LLM_MAX_TOKENS` | `1024` | no | Max tokens requested per LLM call. |
| `GITHUB_TOKENS` | *(empty)* | yes | Comma-separated GitHub personal access tokens used for acquisition. |
| `ACQUIRE_CACHE_DIR` | `/acquire-cache` | no | On-disk cache directory for the bare clones acquisition makes. |
| `ACQUIRE_TIMEOUT_S` | `600` | no | Timeout for the acquisition git fetch, in seconds. |
| `BLOB_LIMIT_BYTES` | `1048576` | no | Per-blob size cutoff applied at fetch time (partial-clone filter). |
| `MAX_TREE_ENTRIES` | `200000` | no | Cap on tree entries read per repository. |
| `CODEROOT_MCP_URL` | *(empty)* | no | Unused until the CodeRoot-MCP plan lands; reserved. |
| `CODEROOT_MCP_TOKEN` | *(empty)* | yes | Unused until the CodeRoot-MCP plan lands; reserved. |
| `ASSESSOR_API_TOKEN` | *(empty)* | yes | Bearer token required on every authenticated route. |
| `ASSESSOR_ALLOW_ANONYMOUS` | `false` | no | Explicit opt-out of auth. Startup fails closed unless this or `ASSESSOR_API_TOKEN` is set. |
| `ASSESSOR_BIND_ADDR` | `127.0.0.1` | no | Listen address. The container image overrides this to `0.0.0.0` — binding all interfaces inside a container is correct; the published port is the operator's choice. |

## What it detects

Every type below is decided by deterministic markers first — a dependency, a
manifest file, a constructed object in source — never by prose alone. README
wording can add *evidence* to a match that already fired on a marker; it never
fires one by itself.

- **agent** — a dependency on a recognized agent framework (LangGraph, CrewAI,
  AutoGen/ag2, the OpenAI Agents SDK, Google ADK, pydantic-ai, smolagents, the
  Claude Agent SDK, Mastra, browser-use, LiveKit/Pipecat agents, and others) in
  a root manifest, a `langgraph.json`, an A2A `agent-card.json`/`agent.json`, or
  a CrewAI `agents.yaml` + `tasks.yaml` pair. A general-purpose LLM SDK
  (`openai`, `anthropic`, `langchain`) plus a hand-written loop is *not*
  enough on its own — that shape is surfaced separately as an advisory
  candidate for review, never as a classification, so a construct alone never
  promotes a repo to "agent".
- **mcp_server** — a Model Context Protocol server: an `@modelcontextprotocol/sdk`
  dependency, a Python `mcp` package dependency, an `mcp.json`/`server.json`
  manifest, or a `smithery.yaml` `startCommand`. Weaker: README prose declaring
  MCP, or a server-construction pattern in source — either alone is enough,
  independently. The repo's declared topics/description come in only
  afterward, to promote an existing weak match to strong; they don't trigger
  one by themselves.
- **skill** — the Anthropic Skills format: a `SKILL.md` with `name:` and
  `description:` YAML frontmatter, at the repo root or at `skills/<name>/SKILL.md`.
  Positional on purpose — an agent-host configuration directory
  (`.claude/skills/`, `.codex/skills/`, …) is evidence the repo *hosts* skills,
  not that it authors one.
- **prompt** — a repo that *is* a prompt collection, not a tool that merely
  contains one: five or more files under a `prompts/` directory or matching a
  prompt extension, outnumbering the repo's own source files, with no
  build/application manifest and no tool/framework self-description.
  Precision-first — a CLI that ships a `data/patterns/` directory of prompt
  templates is a tool, and classifies as one.

## Honesty

The service reports what it could not determine rather than guessing. Every
served field is one of three shapes: a **Fact** (deterministic, with the
evidence that produced it), an **AssessedField** (LLM-derived, carrying a
confidence and never touching classification on its own), or an explicit
**known_unknown** naming why a value isn't there.

A standalone deployment has no release metrics and no assessment history —
there is no Aveloxis integration here, so `metrics()` always returns `None`.
Assessment history and the `latest_release` field degrade to
`known_unknowns` accordingly, but `release_count` does not: with no releases
collected, it is still reported as a **Fact of `0`** rather than a
`known_unknown` — the field states "zero releases", without distinguishing
"GitHub confirmed zero" from "GitHub was never asked" (this is a known,
narrow gap in that one field, not the general behavior). License is
different: every acquisition
calls the GitHub REST API for the repo object regardless of Aveloxis, so when
GitHub has already detected a license, its `license.spdx_id` is used directly;
only when that's absent does detection fall back to matching the repository's
own `LICENSE` text, and only when neither is available does it become an
honest `known_unknown` rather than a guess.

It ships with no model. `LLM_PROVIDER` defaults to `none`, and every field an
LLM could help with — a business-domain guess, a coverage-probe reconciliation,
a citation-backed promotion of a bespoke agent that no framework marker caught —
simply does not run without one configured, and reports as absent rather than
fabricated. The operator supplies their own OpenAI-compatible endpoint to turn
those on.
