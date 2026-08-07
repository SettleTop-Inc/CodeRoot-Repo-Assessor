# tests/test_ports_mcp_source.py
import pytest
from assessor.errors import NotDerivable
from assessor.ports.source import Source
from assessor.ports.mcp_source import McpSource

_SUBJECT = {"repo_url": "https://github.com/o/n", "subject_key": "rid-1",
            "commit_sha": "abc", "subdir": ""}


class _Tools:
    def __init__(self, *, missing=(), files=None):
        self._missing, self._files = list(missing), files if files is not None else {"a.py": "x"}
        self.calls = []
        self.requested_paths = None

    def get_subject(self, repo_id, subdir=""):
        self.calls.append("get_subject")
        # tree_paths is the full inventory; content_paths is the much smaller set
        # acquisition actually stored bodies for. Deliberately unequal in size —
        # a fixture where they match cannot catch a "requested the whole tree"
        # regression (see Task 7 brief).
        return {"commit_sha": "abc", "description": "d", "homepage": None,
                "topics": ["mcp"], "license_spdx": "MIT",
                "tree_paths": ["a.py", "big/one.bin", "docs/x.md"],
                "content_paths": ["a.py"],
                "tree_capped": False, "marker_hits": [], "source_coverage_capped": False,
                "allowlist_version": 7}

    def read_files(self, repo_id, commit_sha, paths):
        self.calls.append("read_files")
        self.requested_paths = list(paths)
        return {"files": self._files, "missing": self._missing}

    def get_metrics(self, repo_id):
        return {"license": "Apache-2.0", "releases": [{"tag": "v1"}]}

    def get_prior_assessment(self, repo_id, subdir=""):
        return {"content_fingerprint": "fp", "asset_types": ["mcp_server"]}


def test_satisfies_the_source_protocol():
    assert isinstance(McpSource(_Tools()), Source)


def test_snapshot_assembles_metadata_and_bodies():
    s = McpSource(_Tools()).snapshot(_SUBJECT)
    assert s["commit_sha"] == "abc"
    assert s["files"] == {"a.py": "x"}
    assert s["metadata"]["license_spdx"] == "MIT"
    # Full tree inventory is kept in full, distinct from the smaller content set
    # that read_files was actually asked for (see
    # test_snapshot_requests_only_the_paths_that_have_stored_bodies below).
    assert s["tree_paths"] == ("a.py", "big/one.bin", "docs/x.md")


def test_a_missing_blob_is_not_derivable_not_a_short_file_set():
    """CodeRoot re-arms acquire when a blob is gone. Silently deriving from a short
    set would produce a record that reads as 'we looked and found nothing'."""
    with pytest.raises(NotDerivable):
        McpSource(_Tools(missing=["gone.py"])).snapshot(_SUBJECT)


def test_metrics_and_prior_come_from_their_own_tools():
    src = McpSource(_Tools())
    assert src.metrics(_SUBJECT)["license"] == "Apache-2.0"
    assert src.prior_assessment(_SUBJECT)["content_fingerprint"] == "fp"


def test_acquire_is_refused_this_source_never_touches_github():
    with pytest.raises(NotImplementedError):
        McpSource(_Tools()).acquire("https://github.com/o/n", prior=None)


def test_snapshot_requests_only_the_paths_that_have_stored_bodies():
    """The full tree inventory is far larger than the stored body set — measured on the
    live corpus, 8144 vs 133 for one repo. Requesting bodies for the whole inventory
    would put thousands of paths in `missing` and raise NotDerivable on every
    repository. The double above reproduces that asymmetry deliberately."""
    t = _Tools()
    snap = McpSource(t).snapshot(_SUBJECT)
    assert t.requested_paths == ["a.py"]            # content_paths, not tree_paths
    assert snap["tree_paths"] == ("a.py", "big/one.bin", "docs/x.md")  # full inventory kept
    assert t.calls == ["get_subject", "read_files"]
