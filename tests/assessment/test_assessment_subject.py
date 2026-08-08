import pytest

from assessor.assessment.subject import (
    normalize_subdir, asset_id, scoped_source_url, filter_content_to_subdir,
    filter_paths_to_subdir)


def test_normalize_strips_and_defaults():
    assert normalize_subdir("/packages/x/") == "packages/x"
    assert normalize_subdir(None) == "" and normalize_subdir("") == "" and normalize_subdir(".") == ""


def test_normalize_rejects_traversal():
    for bad in ("../x", "a/../../etc", "/abs/../..", "C:/x"):
        with pytest.raises(ValueError):
            normalize_subdir(bad)


def test_asset_id_deterministic_and_distinct():
    r = "11111111-1111-1111-1111-111111111111"
    assert asset_id(r, "") == asset_id(r, "")
    assert asset_id(r, "a") != asset_id(r, "b") != asset_id(r, "")
    assert len(asset_id(r, "a")) == 32


def test_scoped_source_url():
    assert scoped_source_url("github.com", "o", "n", "abc", "pkg/x") == "https://github.com/o/n/tree/abc/pkg/x"
    assert scoped_source_url("github.com", "o", "n", "abc", "") == "https://github.com/o/n"
    # no commit → cannot pin a subdir; fall back to plain repo url (never a malformed //)
    assert scoped_source_url("github.com", "o", "n", "", "pkg/x") == "https://github.com/o/n"
    assert scoped_source_url("github.com", "o", "n", None, "pkg/x") == "https://github.com/o/n"


def test_filter_reroots_subtree():
    content = {"README.md": "root", "skills/pdf/SKILL.md": "---\nname: pdf\ndescription: d\n---",
               "src/app.py": "x"}
    f = filter_content_to_subdir(content, "skills/pdf")
    assert f == {"SKILL.md": "---\nname: pdf\ndescription: d\n---"}   # re-rooted, subtree only
    assert filter_content_to_subdir(content, "") == content          # whole-repo unchanged


def test_filter_paths_to_subdir_reroots():
    assert filter_paths_to_subdir(("skills/pdf/SKILL.md", "src/x.py"), "skills/pdf") == ("SKILL.md",)


def test_filter_content_to_subdir_reroots_nested_subtree():
    # subtree deeper than one level: filtering on "skills" should re-root skills/pdf/SKILL.md -> pdf/SKILL.md
    content = {"README.md": "root", "skills/pdf/SKILL.md": "---\nname: pdf\ndescription: d\n---",
               "src/app.py": "x"}
    f = filter_content_to_subdir(content, "skills")
    assert f == {"pdf/SKILL.md": "---\nname: pdf\ndescription: d\n---"}


def test_normalize_rejects_backslash_traversal_and_normalizes_windows_path():
    # backslash-delimited traversal must be caught (\\ normalized to / then ..-guarded)
    for bad in ("a\\..\\b", "..\\..\\etc"):
        with pytest.raises(ValueError):
            normalize_subdir(bad)
    # a Windows-typed (non-traversal) path is normalized to forward slashes, not rejected
    assert normalize_subdir("packages\\x") == "packages/x"


def test_filter_sibling_prefix_boundary_no_collision():
    # subdir "foo" must NOT capture sibling "foobar/..." (the "+ '/'" boundary)
    content = {"foo/a.py": "1", "foobar/b.py": "2", "foo": "self"}
    assert filter_content_to_subdir(content, "foo") == {"a.py": "1", "foo": "self"}
    assert filter_paths_to_subdir(("foo/a.py", "foobar/b.py"), "foo") == ("a.py",)


def test_dot_segment_collapses_to_the_same_subject_as_no_dot():
    # A bare '.' segment used to survive normalization, so "pkg/./a" and
    # "pkg/a" normalized to two DIFFERENT strings and therefore minted two
    # different asset_ids for what is really the same directory. Collapsing
    # '.' the same way '' already is closes that alias.
    assert normalize_subdir("pkg/./a") == normalize_subdir("pkg/a") == "pkg/a"
    r = "11111111-1111-1111-1111-111111111111"
    assert asset_id(r, normalize_subdir("pkg/./a")) == asset_id(r, normalize_subdir("pkg/a"))


def test_dot_segment_no_longer_derives_a_verdict_from_zero_files():
    # Before the fix, a subdir normalized from "pkg/./a" stayed "pkg/./a" and
    # filter_content_to_subdir's exact-prefix match against real content keyed
    # by "pkg/a/..." matched nothing, silently returning {} -- an
    # assessable-looking but empty content set. After the collapse, the same
    # content dict actually matches.
    content = {"pkg/a/SKILL.md": "---\nname: a\n---", "README.md": "root"}
    subdir = normalize_subdir("pkg/./a")
    assert filter_content_to_subdir(content, subdir) == {"SKILL.md": "---\nname: a\n---"}
