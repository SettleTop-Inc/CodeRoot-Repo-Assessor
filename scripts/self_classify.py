"""Self-classification check (Task 12 verification): does this repo's own detector
recognize it as `agent` and `mcp_server`?

Runs the REAL acquisition and classification path -- `GitContentFetcher` +
`assess_handler` -- against this repository's own working tree, not a hand-built
file list. Two departures from a normal `/v1/assess` call, both required because
`https://github.com/SettleTop-Inc/CodeRoot-Repo-Assessor` is still an empty
repository (this branch has never been pushed):

  1. Content comes from a LOCAL git remote, not GitHub. `GitContentFetcher`'s
     `protocol.file.allow=never` hardening blocks a plain local-path remote even
     with `allowed_hosts=None` (verified in
     `tests/assessment/test_assessment_git_fetch.py`'s `git_daemon` fixture
     docstring), so this script bare-clones the working tree and serves it over
     a throwaway `git daemon` (git://) -- the same pattern that fixture uses for
     real-git integration tests.
  2. The commit pinned is local `git rev-parse HEAD`, not
     `content.resolve_head` (which would call the GitHub API for a repo that
     does not exist there yet). Repo metadata that normally comes from GitHub's
     repo object (description/topics/license) is therefore genuinely
     unavailable here -- represented as honestly empty, never fabricated.

Everything downstream of the fetch -- manifest/source selection, the marker
scan, `assess_handler`, `assemble.build` -- is the unmodified production code
path. This script hands GitContentFetcher's real output to that path; it never
assembles a file list itself.

Usage: .venv/Scripts/python.exe scripts/self_classify.py
"""
from __future__ import annotations

import hashlib
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from assessor.assessment import content as content_mod          # noqa: E402
from assessor.assessment.git_fetch import GitContentFetcher      # noqa: E402
from assessor.config import Settings                              # noqa: E402
from assessor.handlers import assess_handler                      # noqa: E402
from assessor.ports.cache import NullCache                        # noqa: E402

# The real target URL -- even though it currently resolves to an empty GitHub
# repo. Only the CONTENT transport is substituted below; the subject identity
# a real deployment would use stays intact.
REPO_URL = "https://github.com/SettleTop-Inc/CodeRoot-Repo-Assessor"
OWNER, NAME = "SettleTop-Inc", "CodeRoot-Repo-Assessor"


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait_for_port(port: int, timeout: float = 10.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                return
        except OSError:
            time.sleep(0.05)
    raise RuntimeError(f"git daemon did not start listening on {port} in time")


class LocalGitSource:
    """A `Source` that reads from the local git daemon below instead of GitHub --
    the same `GitContentFetcher.fetch` call `DirectSource.acquire` makes, the same
    Snapshot shape, only the transport and the metadata origin differ."""

    def __init__(self, fetcher: GitContentFetcher, clone_url: str, repo_key: str) -> None:
        self.fetcher, self.clone_url, self.repo_key = fetcher, clone_url, repo_key

    def snapshot(self, subject):
        sha = subject["commit_sha"]
        files, paths, capped, hits = self.fetcher.fetch(self.clone_url, self.repo_key, sha)
        return {
            "commit_sha": sha,
            # Honest empty: no GitHub API call was made (the repo does not exist
            # there yet), so description/topics/license are genuinely unavailable
            # here -- not fabricated to help classification along.
            "metadata": {"description": None, "homepage": None, "topics": [], "license_spdx": None},
            "tree_paths": tuple(paths), "tree_capped": capped,
            "marker_hits": tuple(hits), "files": files,
            "source_coverage_capped": capped,
            "allowlist_version": content_mod.ALLOWLIST_VERSION,
        }

    def metrics(self, subject):
        return None  # real DirectSource.metrics() also always returns None standalone

    def acquire(self, repo_url, *, prior=None):
        raise NotImplementedError("verification script only exercises assess_handler")

    def prior_assessment(self, subject):
        return None


def main() -> None:
    head_sha = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "rev-parse", "HEAD"],
        check=True, capture_output=True, text=True,
    ).stdout.strip()
    print(f"local HEAD: {head_sha}")

    with tempfile.TemporaryDirectory(prefix="self_classify_") as tmp:
        tmp_path = Path(tmp)
        base = tmp_path / "daemon-base"
        bare = base / "selfrepo.git"
        base.mkdir(parents=True)
        subprocess.run(["git", "clone", "-q", "--bare", str(REPO_ROOT), str(bare)],
                       check=True, capture_output=True)
        # Required for GitContentFetcher's `--filter=blob:limit=` fetch to be
        # honored over ANY local-machine transport -- without it git silently
        # ignores the filter and fetches every blob regardless of size.
        subprocess.run(["git", "-C", str(bare), "config", "uploadpack.allowfilter", "true"],
                       check=True)

        port = _free_port()
        daemon = subprocess.Popen(
            ["git", "daemon", "--reuseaddr", "--listen=127.0.0.1", f"--port={port}",
             f"--base-path={base}", "--export-all"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        try:
            _wait_for_port(port)
            clone_url = f"git://127.0.0.1:{port}/selfrepo.git"
            repo_key = hashlib.sha256(f"{OWNER}/{NAME}".encode()).hexdigest()
            fetcher = GitContentFetcher(tmp_path / "cache", allowed_hosts=None)
            source = LocalGitSource(fetcher, clone_url, repo_key)

            # llm_provider defaults to "none" -- matches this repo's own
            # .env.example default and honestly reflects "no model configured".
            settings = Settings(assessor_allow_anonymous=True)
            cache = NullCache()
            subject = {"repo_url": REPO_URL, "subject_key": "self",
                      "commit_sha": head_sha, "subdir": ""}

            result = assess_handler(source, cache, settings, subject)
        finally:
            daemon.terminate()
            try:
                daemon.wait(timeout=5)
            except subprocess.TimeoutExpired:
                daemon.kill()

    print(f"asset_types: {result['asset_types']}")
    print(f"asset_type (primary): {result['asset_type']}")
    print(f"classification_confidence: {result['classification_confidence']}")
    print(f"llm_used: {result['llm_used']}")
    print()
    print("matches:")
    for m in result["assessment"]["classification"]["matches"]:
        print(f"  - {m['asset_type']} ({m['marker_tier']}, promoted={m.get('promoted', False)})")
        for e in m["evidence"]:
            print(f"      {e}")
    print()
    print("suppressed:")
    for s in result["assessment"]["classification"]["suppressed"]:
        print(f"  - {s}")
    print()
    print("coverage_probes:")
    for p in result["assessment"]["coverage_probes"]:
        print(f"  - {p['type']}: {p['evidence_state']} -- {p['probe']}")
    print()
    print("known_unknowns:")
    for k in result["assessment"]["known_unknowns"]:
        print(f"  - [{k['asset_type']}/{k['code']}] {k['detail']}")


if __name__ == "__main__":
    main()
