from assessor.handlers import acquire_handler


class _Source:
    def __init__(self, status):
        self.status, self.seen_prior = status, "unset"

    def acquire(self, repo_url, *, prior):
        self.seen_prior = prior
        return {"status": self.status, "snapshot": None, "commit_sha": "abc123",
                "metadata": {"description": "d"}, "allowlist_version": 7}

    def snapshot(self, subject): raise AssertionError("not called")
    def metrics(self, subject): return None
    def prior_assessment(self, subject): return None


def test_prior_is_passed_through_untouched():
    src = _Source("unchanged")
    prior = {"commit_sha": "abc123", "allowlist_version": 7}
    acquire_handler(src, "https://github.com/o/n", prior)
    assert src.seen_prior == prior


def test_none_prior_is_passed_through_as_none():
    src = _Source("acquired")
    acquire_handler(src, "https://github.com/o/n", None)
    assert src.seen_prior is None


def test_unchanged_result_carries_metadata():
    r = acquire_handler(_Source("unchanged"), "https://github.com/o/n",
                        {"commit_sha": "abc123", "allowlist_version": 7})
    assert r["status"] == "unchanged" and r["metadata"]["description"] == "d"
