"""Budget-neutral record selection (spec §7 channel 4): the record is always
selected, and its presence changes NO other file's selection."""
from assessor.assessment.content import select_source_paths
from assessor.assessment.record import RECORD_BASENAME, RECORD_MAX_BYTES


def _candidates_exhausting_budgets() -> dict:
    # 130 source files of 16KiB each: >120-file cap AND >1600KiB byte cap.
    c = {f"src/m{i:03}.py": (f"sha{i}", 16 * 1024) for i in range(130)}
    c["package.json"] = ("shaP", 200)          # dep manifest
    c["skills/a/SKILL.md"] = ("shaS", 300)     # newtype manifest
    return c


def test_record_always_selected_even_when_budgets_exhausted():
    c = _candidates_exhausting_budgets()
    c[RECORD_BASENAME] = ("shaR", 2_000)
    selected, capped = select_source_paths(c)
    assert RECORD_BASENAME in selected
    assert capped is True                      # capped by the SOURCE files, as before


def test_record_presence_changes_no_other_selection():
    c = _candidates_exhausting_budgets()
    base, base_capped = select_source_paths(dict(c))
    c[RECORD_BASENAME] = ("shaR", 2_000)
    with_rec, rec_capped = select_source_paths(c)
    assert [p for p in with_rec if p != RECORD_BASENAME] == base
    assert rec_capped == base_capped


def test_subdir_records_selected_up_to_cap():
    c = {"README.md": ("s0", 10)}
    for i in range(25):
        c[f"pkg{i}/{RECORD_BASENAME}"] = (f"s{i}", 500)
    selected, capped = select_source_paths(c)
    picked = [p for p in selected if p.endswith(RECORD_BASENAME)]
    assert len(picked) == 20                   # _RECORD_MAX_FILES
    assert capped is False                     # record overflow NEVER claims source truncation


def test_oversized_record_not_selected():
    c = {"README.md": ("s0", 10), RECORD_BASENAME: ("sR", RECORD_MAX_BYTES + 1)}
    selected, capped = select_source_paths(c)
    assert RECORD_BASENAME not in selected
    assert capped is False


def test_record_never_sets_capped_flag():
    c = {RECORD_BASENAME: ("sR", 2_000)}
    selected, capped = select_source_paths(c)
    assert selected == [RECORD_BASENAME] and capped is False
