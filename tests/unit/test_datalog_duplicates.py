"""Unit tests for modules.datalog.io.duplicates (runs stored twice on disk)."""

import json
from pathlib import Path

import pytest

from modules.datalog.io.duplicates import (
    CleanupResult,
    DuplicatePlan,
    digest_of,
    find_duplicates,
    remove_duplicate,
    remove_duplicates,
    send_to_recycle_bin,
    stale_copies,
    without_duplicates,
)
from modules.datalog.io.scanner import scan_folder
from modules.datalog.io.tag_store import RUNCARD_TAGS_FILENAME, get_runcard_tag
from tests.datalog_fixtures import DATALOG_SHORT, drop_rows, write_datalog

#: The canonical name for DATALOG_SHORT tagged VBBE00 -- what `renamer` writes,
#: and therefore the only name in a group that can be the keeper.
_CANONICAL = "2026-08-06_173312~VBBE00.csv"

#: The recorder's own name for the same run: the name the copier keeps putting
#: back, and the one every test here expects to be hidden or removed.
_RAW = "06173312.csv"


def _names(folder):
    return sorted(p.name for p in folder.iterdir())


def _write_pair(folder, raw_name=_RAW, canonical_name=_CANONICAL, text=DATALOG_SHORT):
    """The situation this whole module exists for: one run, two identical files."""
    write_datalog(folder, text=text, name=canonical_name)
    write_datalog(folder, text=text, name=raw_name)
    return str(folder / raw_name), str(folder / canonical_name)


class TestStaleCopies:
    def test_raw_copy_of_a_canonically_named_run_is_stale(self, tmp_path):
        raw, canonical = _write_pair(tmp_path)
        assert stale_copies(str(tmp_path), scan_folder(str(tmp_path))) == {raw: canonical}

    def test_lone_run_is_never_stale(self, tmp_path):
        write_datalog(tmp_path, name=_CANONICAL)
        assert stale_copies(str(tmp_path), scan_folder(str(tmp_path))) == {}

    def test_group_with_no_canonical_name_is_left_alone(self, tmp_path):
        # Two identical copies, neither named as `renamer` would name it. There
        # is no basis for picking a survivor, so nothing is a candidate.
        write_datalog(tmp_path, name="06173312.csv")
        write_datalog(tmp_path, name="06173312b.csv")
        assert stale_copies(str(tmp_path), scan_folder(str(tmp_path))) == {}

    def test_same_run_with_different_bytes_is_left_alone(self, tmp_path):
        # A half-copied file: same start, but truncated. Removing it would
        # destroy the only evidence the copy went wrong, so it stays -- and
        # stays visible, which is the point of not hiding it either.
        write_datalog(tmp_path, name=_CANONICAL)
        write_datalog(tmp_path, text=drop_rows(DATALOG_SHORT, "2026/08/06 17:33:40", 3), name=_RAW)
        assert stale_copies(str(tmp_path), scan_folder(str(tmp_path))) == {}

    def test_identical_copies_in_different_folders_are_left_alone(self, tmp_path):
        # Two month folders holding the same run is an archiving decision. This
        # module must not resolve one by deleting somebody's second copy.
        (tmp_path / "2026_08").mkdir()
        (tmp_path / "archive").mkdir()
        write_datalog(tmp_path / "2026_08", name=_CANONICAL)
        write_datalog(tmp_path / "archive", name=_CANONICAL)
        assert stale_copies(str(tmp_path), scan_folder(str(tmp_path))) == {}

    def test_untagged_canonical_name_still_keeps(self, tmp_path):
        # A run with no tag is canonically `<timestamp>.csv`, with no delimiter
        # left behind -- that is a keeper too, not only the tagged form.
        write_datalog(tmp_path, name="2026-08-06_173312.csv")
        write_datalog(tmp_path, name=_RAW)
        assert stale_copies(str(tmp_path), scan_folder(str(tmp_path))) == {
            str(tmp_path / _RAW): str(tmp_path / "2026-08-06_173312.csv")
        }

    def test_stored_tag_decides_the_canonical_name(self, tmp_path):
        # The keeper test goes through `get_runcard_tag`, so a tag stored in the
        # sidecar -- not derivable from the filename -- names the survivor.
        write_datalog(tmp_path, name="2026-08-06_173312~VBBE99.csv")
        write_datalog(tmp_path, name=_RAW)
        (tmp_path / RUNCARD_TAGS_FILENAME).write_text(
            json.dumps({"2026-08-06_173312~VBBE99.csv": "VBBE99"}), encoding="utf-8"
        )
        assert stale_copies(str(tmp_path), scan_folder(str(tmp_path))) == {
            str(tmp_path / _RAW): str(tmp_path / "2026-08-06_173312~VBBE99.csv")
        }

    def test_a_tag_edit_can_unmake_a_keeper(self, tmp_path):
        # The keeper is whatever matches the tag *now*. Retag the renamed file
        # and its own name stops being canonical, so the group loses its single
        # keeper and nothing is removed -- a bulk rename is what fixes it.
        write_datalog(tmp_path, name=_CANONICAL)
        write_datalog(tmp_path, name=_RAW)
        (tmp_path / RUNCARD_TAGS_FILENAME).write_text(
            json.dumps({_CANONICAL: "SOMETHINGELSE"}), encoding="utf-8"
        )
        assert stale_copies(str(tmp_path), scan_folder(str(tmp_path))) == {}


class TestWithoutDuplicates:
    def test_hides_the_stale_copy_and_keeps_the_renamed_one(self, tmp_path):
        _, canonical = _write_pair(tmp_path)
        shown = without_duplicates(str(tmp_path), scan_folder(str(tmp_path)))
        assert [m.path for m in shown] == [canonical]

    def test_hiding_touches_nothing_on_disk(self, tmp_path):
        # The whole reason hiding is the primary answer: it cannot lose a race
        # with the copier, because it does not race anything.
        _write_pair(tmp_path)
        without_duplicates(str(tmp_path), scan_folder(str(tmp_path)))
        assert _names(tmp_path) == sorted([_RAW, _CANONICAL])

    def test_preserves_scan_order(self, tmp_path):
        write_datalog(tmp_path, name=_CANONICAL)
        write_datalog(tmp_path, name=_RAW)
        write_datalog(tmp_path, text=DATALOG_SHORT.replace("17:33", "18:33"),
                      name="2026-08-06_183312~VBBE01.csv")
        scanned = scan_folder(str(tmp_path))
        shown = without_duplicates(str(tmp_path), scanned)
        assert [m.path for m in shown] == [m.path for m in scanned if m.path != str(tmp_path / _RAW)]


class TestFindDuplicates:
    def test_agrees_with_what_the_list_hides(self, tmp_path):
        # The invariant the page depends on: the preview an operator confirms
        # names exactly the files the table stopped showing them.
        _write_pair(tmp_path)
        scanned = scan_folder(str(tmp_path))
        hidden = {m.path for m in scanned} - {m.path for m in without_duplicates(str(tmp_path), scanned)}
        assert {p.path for p in find_duplicates(str(tmp_path), scanned)} == hidden

    def test_plan_names_both_files(self, tmp_path):
        raw, canonical = _write_pair(tmp_path)
        assert find_duplicates(str(tmp_path), scan_folder(str(tmp_path))) == [
            DuplicatePlan(path=raw, keeper=canonical)
        ]

    def test_sorted_by_path(self, tmp_path):
        write_datalog(tmp_path, name=_CANONICAL)
        write_datalog(tmp_path, name="b_copy.csv")
        write_datalog(tmp_path, name="a_copy.csv")
        plans = find_duplicates(str(tmp_path), scan_folder(str(tmp_path)))
        assert [p.path for p in plans] == sorted(p.path for p in plans)


class TestDigestOf:
    def test_identical_files_share_a_digest(self, tmp_path):
        raw, canonical = _write_pair(tmp_path)
        assert digest_of(raw) == digest_of(canonical)

    def test_differing_files_do_not(self, tmp_path):
        a = write_datalog(tmp_path, name="a.csv")
        b = write_datalog(tmp_path, text=drop_rows(DATALOG_SHORT, "2026/08/06 17:33:40", 2),
                          name="b.csv")
        assert digest_of(a) != digest_of(b)

    def test_cache_key_includes_size(self, tmp_path):
        """A rewrite inside one mtime tick must not serve the previous digest.

        `_digest` spells its cache parameters without a leading underscore
        precisely so Streamlit keys on them; an ``_mtime``/``_size`` spelling
        would key on the path alone and pass every other test in this file
        while quietly hashing stale content forever.
        """
        path = write_datalog(tmp_path, name="a.csv")
        before = digest_of(path)
        Path(path).write_text(drop_rows(DATALOG_SHORT, "2026/08/06 17:33:40", 4), encoding="utf-8",
                              newline="")
        assert digest_of(path) != before


class TestRemoveDuplicate:
    def test_removes_the_stale_copy_only(self, tmp_path):
        raw, canonical = _write_pair(tmp_path)
        remove_duplicate(str(tmp_path), DuplicatePlan(path=raw, keeper=canonical))
        assert _names(tmp_path) == [_CANONICAL]

    def test_drops_the_stale_copys_sidecar_entry(self, tmp_path):
        raw, canonical = _write_pair(tmp_path)
        (tmp_path / RUNCARD_TAGS_FILENAME).write_text(
            json.dumps({_RAW: "VBBE00", _CANONICAL: "VBBE00"}), encoding="utf-8"
        )
        remove_duplicate(str(tmp_path), DuplicatePlan(path=raw, keeper=canonical))
        stored = json.loads((tmp_path / RUNCARD_TAGS_FILENAME).read_text(encoding="utf-8"))
        assert stored == {_CANONICAL: "VBBE00"}

    def test_keepers_tag_survives(self, tmp_path):
        raw, canonical = _write_pair(tmp_path)
        remove_duplicate(str(tmp_path), DuplicatePlan(path=raw, keeper=canonical))
        assert get_runcard_tag(str(tmp_path), canonical) == "VBBE00"

    def test_untagged_folder_gains_no_sidecar(self, tmp_path):
        # Removing a file must not leave a `runcard_tags.json` behind in a
        # folder that never had one -- `clear_runcard_tag` over
        # `set_runcard_tag(..., "")` is exactly this.
        raw, canonical = _write_pair(tmp_path)
        remove_duplicate(str(tmp_path), DuplicatePlan(path=raw, keeper=canonical))
        assert not (tmp_path / RUNCARD_TAGS_FILENAME).exists()

    def test_missing_file_raises_and_leaves_the_sidecar_alone(self, tmp_path):
        # The sidecar is cleared *after* the removal, so a failed removal must
        # not have untagged anything.
        _, canonical = _write_pair(tmp_path)
        (tmp_path / RUNCARD_TAGS_FILENAME).write_text(
            json.dumps({_RAW: "VBBE00"}), encoding="utf-8"
        )
        with pytest.raises(OSError):
            remove_duplicate(str(tmp_path), DuplicatePlan(path=str(tmp_path / "gone.csv"),
                                                          keeper=canonical))
        stored = json.loads((tmp_path / RUNCARD_TAGS_FILENAME).read_text(encoding="utf-8"))
        assert stored == {_RAW: "VBBE00"}


    def test_an_unreadable_sidecar_still_counts_the_file_as_removed(self, tmp_path, monkeypatch):
        # The file is already in the Recycle Bin when the tag is cleared, so a
        # sidecar that cannot be read must neither be overwritten nor turn the
        # removal into a "skipped" row.
        monkeypatch.setattr("modules.datalog.io.tag_store._UPDATE_READ_RETRY_S", 0)
        raw, canonical = _write_pair(tmp_path)
        damaged = '{"' + _RAW + '": "VBB'
        (tmp_path / RUNCARD_TAGS_FILENAME).write_text(damaged, encoding="utf-8")
        plan = DuplicatePlan(path=raw, keeper=canonical)

        assert remove_duplicates(str(tmp_path), [plan]) == CleanupResult(removed=[plan], skipped=[])
        assert not Path(raw).exists()
        assert (tmp_path / RUNCARD_TAGS_FILENAME).read_text(encoding="utf-8") == damaged

class TestRemoveDuplicates:
    def test_reports_what_it_removed(self, tmp_path):
        raw, canonical = _write_pair(tmp_path)
        plan = DuplicatePlan(path=raw, keeper=canonical)
        assert remove_duplicates(str(tmp_path), [plan]) == CleanupResult(removed=[plan], skipped=[])

    def test_one_failure_does_not_abort_the_rest(self, tmp_path):
        raw, canonical = _write_pair(tmp_path)
        missing = DuplicatePlan(path=str(tmp_path / "gone.csv"), keeper=canonical)
        good = DuplicatePlan(path=raw, keeper=canonical)
        result = remove_duplicates(str(tmp_path), [missing, good])
        assert result.removed == [good]
        assert [p for p, _ in result.skipped] == [missing.path]
        assert _names(tmp_path) == [_CANONICAL]

    def test_skip_reason_is_the_exceptions_own_text(self, tmp_path):
        _, canonical = _write_pair(tmp_path)
        missing = str(tmp_path / "gone.csv")
        result = remove_duplicates(str(tmp_path), [DuplicatePlan(path=missing, keeper=canonical)])
        assert result.skipped[0][1] == f"Not a file: {missing}"

    def test_empty_plan_list_is_a_no_op(self, tmp_path):
        _write_pair(tmp_path)
        assert remove_duplicates(str(tmp_path), []) == CleanupResult(removed=[], skipped=[])
        assert _names(tmp_path) == sorted([_RAW, _CANONICAL])

    def test_cleanup_then_rescan_finds_nothing(self, tmp_path):
        _write_pair(tmp_path)
        root = str(tmp_path)
        remove_duplicates(root, find_duplicates(root, scan_folder(root)))
        assert find_duplicates(root, scan_folder(root)) == []


class TestSendToRecycleBin:
    def test_directory_is_refused(self, tmp_path):
        # Guards the one input that would turn a file removal into a tree
        # removal if the shell were handed it.
        (tmp_path / "sub").mkdir()
        with pytest.raises(OSError, match="Not a file"):
            send_to_recycle_bin(str(tmp_path / "sub"))

    def test_missing_path_is_refused(self, tmp_path):
        with pytest.raises(OSError, match="Not a file"):
            send_to_recycle_bin(str(tmp_path / "gone.csv"))
