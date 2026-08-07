"""Regenerate tests/fixtures/parity-sample/*.json.

Unlike scripts/export_corpus.py, this DOES run in this repo's own venv — it
imports only `assessor` and calls `assess_handler` directly against small,
hand-written synthetic snapshots, then records whatever this service itself
produced as `expected`. That is the whole point of these fixtures: they
exercise the parity harness's plumbing (tests/test_parity.py), not parity
with CodeRoot, since both sides of every comparison come from this service.

Run this whenever a change could move `content_fingerprint` for the synthetic
cases below — most notably a REGISTRY_VERSION bump (assessor/assessment/
registry.py), a marker/composition change, or an edit to the synthetic file
contents in this script — and the fixtures' `expected` values need to be
re-derived rather than hand-edited. `git diff tests/fixtures/parity-sample/`
after running this should show only the values a real behaviour change
actually moved.

Usage (from the repo root, with this repo's own venv):
    .venv/Scripts/python.exe scripts/gen_parity_fixtures.py
"""
from __future__ import annotations

import json
from pathlib import Path

from assessor.config import Settings
from assessor.handlers import assess_handler
from assessor.ports.cache import NullCache

_S = Settings(assessor_api_token="x", llm_provider="none")
_OUT = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "parity-sample"


class _Src:
    def __init__(self, snap, metrics=None):
        self._snap, self._metrics = snap, metrics

    def acquire(self, repo_url, *, prior): raise AssertionError("not called")
    def snapshot(self, subject): return self._snap
    def metrics(self, subject): return self._metrics
    def prior_assessment(self, subject): return None


# Each case's "snapshot"/"subject"/"metrics" are exactly the shape
# scripts/export_corpus.py writes and tests/test_parity.py reads — see that
# script's docstring for the authoritative key list.
CASES = {
    "demo__mcp-server": {
        "subject": {"repo_url": "https://github.com/demo/mcp-server",
                    "subject_key": "fixture-1", "commit_sha": "aaa111", "subdir": ""},
        "snapshot": {
            "commit_sha": "aaa111",
            "metadata": {"description": "A demo MCP server", "homepage": None,
                         "topics": ["mcp"], "license_spdx": "MIT"},
            "tree_paths": ["server.py", "README.md"],
            "tree_capped": False,
            "marker_hits": [],
            "files": {
                "server.py": (
                    "from mcp.server.fastmcp import FastMCP\n"
                    "mcp = FastMCP('demo')\n"
                    "@mcp.tool()\n"
                    "def add(a: int, b: int) -> int:\n"
                    "    return a + b\n"),
                "README.md": "# demo mcp server\n\nA tiny MCP server used for parity fixtures.\n",
            },
            "source_coverage_capped": False,
            "allowlist_version": 7,
        },
        "metrics": {"license": "MIT", "releases": []},
    },
    "demo__plain-repo": {
        "subject": {"repo_url": "https://github.com/demo/plain-repo",
                    "subject_key": "fixture-2", "commit_sha": "bbb222", "subdir": ""},
        "snapshot": {
            "commit_sha": "bbb222",
            "metadata": {"description": "Just some utility scripts", "homepage": None,
                         "topics": [], "license_spdx": None},
            "tree_paths": ["main.py", "README.md"],
            "tree_capped": False,
            "marker_hits": [],
            "files": {
                "main.py": ("def main():\n    print('hello world')\n\n"
                            "if __name__ == '__main__':\n    main()\n"),
                "README.md": "# plain repo\n\nNothing special here.\n",
            },
            "source_coverage_capped": False,
            "allowlist_version": 7,
        },
        "metrics": None,
    },
    "demo__mcp-node": {
        "subject": {"repo_url": "https://github.com/demo/mcp-node",
                    "subject_key": "fixture-3", "commit_sha": "ccc333", "subdir": ""},
        "snapshot": {
            "commit_sha": "ccc333",
            "metadata": {"description": None, "homepage": "https://example.com",
                         "topics": ["mcp", "server"], "license_spdx": None},
            "tree_paths": ["index.ts", "package.json"],
            "tree_capped": False,
            "marker_hits": [],
            "files": {
                "index.ts": (
                    "import { McpServer } from '@modelcontextprotocol/sdk/server/mcp.js';\n"
                    "const server = new McpServer({ name: 'demo', version: '1.0.0' });\n"
                    "server.tool('add', { a: 'number', b: 'number' }, "
                    "async ({a, b}) => ({a: a+b}));\n"),
                "package.json": '{"name": "mcp-node", "version": "1.0.0"}\n',
            },
            "source_coverage_capped": False,
            "allowlist_version": 7,
        },
        "metrics": None,
    },
}


def main() -> None:
    _OUT.mkdir(parents=True, exist_ok=True)
    for name, case in CASES.items():
        subject = case["subject"]
        snap = dict(case["snapshot"])
        snap["tree_paths"] = tuple(snap["tree_paths"])
        snap["marker_hits"] = tuple(snap["marker_hits"])
        record = assess_handler(_Src(snap, case["metrics"]), NullCache(), _S, subject)
        payload = {
            "subject": subject,
            "snapshot": case["snapshot"],
            "metrics": case["metrics"],
            "expected": {"asset_types": record["asset_types"],
                        "content_fingerprint": record["content_fingerprint"],
                        # No LLM is available here (llm_provider="none"), so no
                        # promotion can ever occur to record — always empty.
                        "promoted_types": []},
        }
        (_OUT / f"{name}.json").write_text(json.dumps(payload, indent=2) + "\n",
                                           encoding="utf-8")
        print(name, "->", record["asset_types"], record["content_fingerprint"])


if __name__ == "__main__":
    main()
