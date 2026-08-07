from assessor.assessment import assemble
from assessor.assessment.registry import TYPE_MODULES


from assessor.config import Settings

_S = Settings(assessor_api_token="x")


def test_all_modules_accept_paths_and_meta():
    content = {"README.md": "hello"}
    for m in TYPE_MODULES:
        m.classify(content, paths=("a.py",), meta={"topics": [], "description": None})
        # classify may return None; the call must not raise on the new kwargs


def test_build_accepts_paths_and_meta_is_derived():
    rec = assemble.build("https://github.com/o/n", {"README.md": "x"}, "sha", None,
                         bucket_b={"description": "d", "topics": ["t"], "homepage": None},
                         paths=("README.md",), settings=_S)
    assert rec["asset_type"] == "not_an_asset"   # no markers → unchanged behavior
