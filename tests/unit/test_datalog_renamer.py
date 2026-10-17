"""Unit tests for modules.datalog.io.renamer (renaming runs to <timestamp>[~tag].csv)."""

import json
from pathlib import Path

import pandas as pd
import pytest

from modules.datalog.io import tag_store
from modules.datalog.io.renamer import (
    RenameCollision,
    RenamePlan,
    SweepResult,
    TagOrphaned,
    apply_rename,
    execute_sweep,
    plan_renames,
    plan_single_rename,
    sanitize_tag_for_filename,
    target_filename,
)
from modules.datalog.io.scanner import get_run_metadata
from modules.datalog.io.tag_store import RUNCARD_TAGS_FILENAME, get_runcard_tag, set_runcard_tag
from tests.datalog_fixtures import write_datalog

_START = pd.Timestamp("2026-08-06 17:33:12")


def _names(folder):
    return sorted(p.name for p in folder.iterdir())


def _sidecar(folder):
    return json.loads((folder / RUNCARD_TAGS_FILENAME).read_text(encoding="utf-8"))


class TestSanitizeTagForFilename:
    @pytest.mark.parametrize("tag,expected", [
        ("VBBE00", "VBBE00"),
        ("VB/BE:00", "VB-BE-00"),      # path separators must not turn a rename into a move
        ("VB~BE00", "VB-BE00"),        # the delimiter itself, or the round trip is ambiguous
        ("VBBE00  ", "VBBE00"),        # Windows silently drops these, so the file
        ("VBBE00..", "VBBE00"),        # would not match the name this planned
        ('a"b<c>d|e*f?g', "a-b-c-d-e-f-g"),
    ])
    def test_illegal_characters_become_hyphens(self, tag, expected):
        assert sanitize_tag_for_filename(tag) == expected

    def test_the_stored_tag_is_not_sanitized_only_the_filename_is(self, tmp_path):
        # Search and the run list show the tag the operator typed; mangling
        # that would mean the tag on screen is not the one they entered.
        path = write_datalog(tmp_path, name="raw_dump.csv")

        set_runcard_tag(str(tmp_path), path, "VB/BE00")

        assert get_runcard_tag(str(tmp_path), path) == "VB/BE00"


class TestTargetFilename:
    def test_the_stamp_uses_the_filename_convention_not_the_csvs(self):
        # Hyphens and an underscore, so a file browser sorts the folder
        # chronologically. The CSV's own stamps use slashes.
        assert target_filename(_START, "VBBE00", ".csv") == "2026-08-06_173312~VBBE00.csv"

    def test_an_empty_tag_leaves_no_trailing_delimiter(self):
        assert target_filename(_START, "", ".csv") == "2026-08-06_173312.csv"

    def test_a_tag_of_nothing_windows_would_keep_leaves_no_trailing_delimiter(self):
        # Illegal characters become hyphens and survive; only the leading and
        # trailing spaces and dots are dropped, so this is the tag that can
        # sanitize away to nothing.
        assert target_filename(_START, " . ", ".csv") == "2026-08-06_173312.csv"

    def test_the_original_suffix_is_kept(self):
        # The planner is fed whatever discover_csv_files found, and a file that
        # arrived as .CSV should not change extension as a side effect of being
        # tagged.
        assert target_filename(_START, "VBBE00", ".CSV") == "2026-08-06_173312~VBBE00.CSV"


class TestPlanning:
    def test_a_file_already_correctly_named_will_not_change(self, tmp_path):
        # This is the UI's "is there anything to do?" test, which turns the
        # rename button into a caption.
        path = write_datalog(tmp_path)

        plan = plan_single_rename(str(tmp_path), get_run_metadata(path))

        assert plan.will_change is False
        assert plan.new_path == path

    def test_an_untagged_file_is_planned_to_the_bare_stamp(self, tmp_path):
        path = write_datalog(tmp_path, name="raw_dump.csv")

        plan = plan_single_rename(str(tmp_path), get_run_metadata(path))

        assert plan.new_path.endswith("2026-08-06_173312.csv")
        assert plan.will_change is True

    def test_the_tag_is_resolved_from_disk(self, tmp_path):
        path = write_datalog(tmp_path, name="raw_dump.csv")
        set_runcard_tag(str(tmp_path), path, "VBBE00")

        plan = plan_single_rename(str(tmp_path), get_run_metadata(path))

        assert plan.new_path.endswith("2026-08-06_173312~VBBE00.csv")

    def test_planning_writes_nothing(self, tmp_path):
        path = write_datalog(tmp_path, name="raw_dump.csv")

        plan_single_rename(str(tmp_path), get_run_metadata(path))

        assert _names(tmp_path) == ["raw_dump.csv"]

    def test_a_sweep_recomputes_every_name_from_the_current_tag(self, tmp_path):
        # So a file named under a tag that has since been edited gets fixed,
        # not just the ones that were never renamed.
        path = write_datalog(tmp_path, name="2026-08-06_173312~OLD.csv")
        set_runcard_tag(str(tmp_path), path, "NEW")

        plans = plan_renames(str(tmp_path))

        assert [p.new_path.endswith("2026-08-06_173312~NEW.csv") for p in plans] == [True]

    def test_a_collision_is_forecast_before_anything_is_touched(self, tmp_path):
        write_datalog(tmp_path, name="raw_dump.csv")
        (tmp_path / "2026-08-06_173312.csv").write_text("already here")

        plans = plan_renames(str(tmp_path))

        colliding = [p for p in plans if p.path.endswith("raw_dump.csv")]
        assert colliding[0].collision is True

    def test_two_runs_that_will_collide_with_each_other_are_not_forecast(self, tmp_path):
        # The flag is a point-in-time exists() check, so it cannot see a
        # collision the sweep is about to create. It forecasts; apply_rename
        # decides.
        write_datalog(tmp_path, name="a.csv")
        write_datalog(tmp_path, name="b.csv")

        plans = plan_renames(str(tmp_path))

        assert [p.collision for p in plans] == [False, False]

    def test_a_hidden_stale_copy_is_not_planned(self, tmp_path):
        # The copier's byte-identical `06173312.csv` beside the renamed run is
        # hidden by `duplicates`. Renaming it to `2026-08-06_173312.csv` would
        # give both files their own canonical name, so neither would be stale
        # any more: the hidden copy would reappear and cleanup could never
        # remove it.
        write_datalog(tmp_path, name="2026-08-06_173312~VBBE00.csv")
        write_datalog(tmp_path, name="06173312.csv")

        plans = plan_renames(str(tmp_path))

        assert [Path(p.path).name for p in plans] == ["2026-08-06_173312~VBBE00.csv"]
        assert not any(p.will_change for p in plans)

    def test_a_sweep_leaves_the_stale_copy_for_cleanup(self, tmp_path):
        from modules.datalog.io.duplicates import find_duplicates
        from modules.datalog.io.scanner import scan_folder

        write_datalog(tmp_path, name="2026-08-06_173312~VBBE00.csv")
        write_datalog(tmp_path, name="06173312.csv")

        execute_sweep(str(tmp_path), [p for p in plan_renames(str(tmp_path)) if p.will_change])

        assert _names(tmp_path) == ["06173312.csv", "2026-08-06_173312~VBBE00.csv"]
        assert [Path(p.path).name for p in find_duplicates(str(tmp_path), scan_folder(str(tmp_path)))] == [
            "06173312.csv"
        ]

    def test_a_near_duplicate_is_still_planned(self, tmp_path):
        # Different bytes are not hidden, so they are not exempt either.
        from tests.datalog_fixtures import DATALOG_SHORT, drop_rows

        write_datalog(tmp_path, name="2026-08-06_173312~VBBE00.csv")
        write_datalog(tmp_path, text=drop_rows(DATALOG_SHORT, "2026/08/06 17:33:40", 3),
                      name="06173312.csv")

        names = sorted(Path(p.path).name for p in plan_renames(str(tmp_path)))

        assert names == ["06173312.csv", "2026-08-06_173312~VBBE00.csv"]


class TestApplyRename:
    def test_the_file_moves_and_its_tag_entry_follows(self, tmp_path):
        # One operation, not two: a rename that succeeded without the re-key
        # leaves a tagged run reading as untagged.
        path = write_datalog(tmp_path, name="raw_dump.csv")
        set_runcard_tag(str(tmp_path), path, "VBBE00")
        plan = plan_single_rename(str(tmp_path), get_run_metadata(path))

        apply_rename(str(tmp_path), plan)

        assert "2026-08-06_173312~VBBE00.csv" in _names(tmp_path)
        assert _sidecar(tmp_path) == {"2026-08-06_173312~VBBE00.csv": "VBBE00"}

    def test_renaming_an_untagged_run_writes_no_sidecar(self, tmp_path):
        path = write_datalog(tmp_path, name="raw_dump.csv")
        plan = plan_single_rename(str(tmp_path), get_run_metadata(path))

        apply_rename(str(tmp_path), plan)

        assert _names(tmp_path) == ["2026-08-06_173312.csv"]

    def test_a_target_taken_since_the_preview_raises_rather_than_overwriting(self, tmp_path):
        # Path.rename replaces the destination silently on POSIX, so trusting
        # the stale plan.collision flag there destroys a run that appeared
        # between preview and confirm.
        path = write_datalog(tmp_path, name="raw_dump.csv")
        plan = plan_single_rename(str(tmp_path), get_run_metadata(path))
        (tmp_path / "2026-08-06_173312.csv").write_text("appeared after the preview")

        with pytest.raises(RenameCollision):
            apply_rename(str(tmp_path), plan)

        assert (tmp_path / "2026-08-06_173312.csv").read_text() == "appeared after the preview"
        assert (tmp_path / "raw_dump.csv").exists()

    def test_the_collision_message_names_the_file_not_two_absolute_paths(self, tmp_path):
        # It is rendered beside the filename in the skipped list.
        path = write_datalog(tmp_path, name="raw_dump.csv")
        plan = plan_single_rename(str(tmp_path), get_run_metadata(path))
        (tmp_path / "2026-08-06_173312.csv").write_text("x")

        with pytest.raises(RenameCollision) as excinfo:
            apply_rename(str(tmp_path), plan)

        assert str(excinfo.value) == 'A file named "2026-08-06_173312.csv" already exists.'

    def test_a_plan_built_against_another_folder_raises_and_leaves_the_file_alone(self, tmp_path):
        # The tag store is asked for a key it cannot compute, and the rename is
        # rolled back rather than left standing. ui.datalog_state still drops a
        # pending plan when the folder changes -- a plan that reaches here is
        # already a bug -- but the bug no longer re-shuffles a folder the
        # operator is not looking at.
        old_root = tmp_path / "old"
        new_root = tmp_path / "new"
        new_root.mkdir()
        old_root.mkdir()
        path = write_datalog(old_root, name="raw_dump.csv")
        plan = plan_single_rename(str(old_root), get_run_metadata(path))

        with pytest.raises(ValueError):
            apply_rename(str(new_root), plan)

        assert _names(old_root) == ["raw_dump.csv"]


class TestExecuteSweep:
    def test_one_locked_file_does_not_abort_the_other_four_hundred(self, tmp_path):
        write_datalog(tmp_path, name="a.csv")
        write_datalog(tmp_path, name="b.csv")
        plans = plan_renames(str(tmp_path))

        result = execute_sweep(str(tmp_path), plans)

        assert len(result.renamed) == 1
        assert len(result.skipped) == 1
        assert _names(tmp_path) == ["2026-08-06_173312.csv", "b.csv"]

    def test_a_skipped_file_records_why(self, tmp_path):
        # A sweep that says "1 skipped" without saying why is a sweep the
        # operator has to run again to learn anything.
        write_datalog(tmp_path, name="a.csv")
        write_datalog(tmp_path, name="b.csv")
        plans = plan_renames(str(tmp_path))

        result = execute_sweep(str(tmp_path), plans)

        skipped_path, reason = result.skipped[0]
        assert skipped_path.endswith("b.csv")
        assert "already exists" in reason

    def test_renamed_pairs_are_old_then_new(self, tmp_path):
        path = write_datalog(tmp_path, name="raw_dump.csv")
        plans = plan_renames(str(tmp_path))

        result = execute_sweep(str(tmp_path), plans)

        assert result.renamed[0][0] == path
        assert result.renamed[0][1].endswith("2026-08-06_173312.csv")

    def test_a_plan_pointing_at_a_file_that_is_gone_is_skipped(self, tmp_path):
        plan = RenamePlan(
            path=str(tmp_path / "archived.csv"),
            new_path=str(tmp_path / "2026-08-06_173312.csv"),
            will_change=True,
            collision=False,
        )

        result = execute_sweep(str(tmp_path), [plan])

        assert result.renamed == []
        assert len(result.skipped) == 1


def _sidecar_write_fails(monkeypatch, message="another process has the file open"):
    """Make the tags sidecar unwritable the way Windows does it.

    `tag_store._write_json` writes a temp file and `Path.replace`s it over the
    real one. A read-only share, or datalog_monitor holding runcard_tags.json
    open, makes that replace raise PermissionError -- which used to land after
    the file had already been renamed.
    """
    def refuse(path, data):
        raise PermissionError(message)

    monkeypatch.setattr(tag_store, "_write_json", refuse)


def _rollback_to_fails(monkeypatch, original_name):
    """Break only the rename that would put `original_name` back.

    Targeting the undo by name rather than by call count keeps the fixture
    independent of the order `scan_folder` happens to yield files in.
    """
    original = Path.rename

    def flaky(self, target):
        if Path(target).name == original_name:
            raise PermissionError("the file is open in another process")
        return original(self, target)

    monkeypatch.setattr(Path, "rename", flaky)


class TestTheRenameAndTheRekeyAreOnePairOrNeither:
    """A failed re-key used to leave the file renamed and the tag behind.

    The rename landed first and the sidecar write second, so a read-only share
    -- or datalog_monitor holding runcard_tags.json open on Windows -- left the
    run sitting under its new name with its tag filed under the old key, and
    the sweep reported it as "skipped", which was false in both directions.
    """

    def test_a_failed_rekey_puts_the_file_back(self, tmp_path, monkeypatch):
        path = write_datalog(tmp_path, name="raw_dump.csv")
        set_runcard_tag(str(tmp_path), path, "VBBE00")
        plan = plan_single_rename(str(tmp_path), get_run_metadata(path))
        _sidecar_write_fails(monkeypatch)

        with pytest.raises(PermissionError):
            apply_rename(str(tmp_path), plan)

        assert (tmp_path / "raw_dump.csv").exists()
        assert not (tmp_path / "2026-08-06_173312~VBBE00.csv").exists()

    def test_re_running_the_sweep_afterwards_finishes_the_job(self, tmp_path, monkeypatch):
        # The point of the rollback. Left half-applied, the file is already at
        # the new name with its tag stranded under the old key, so the next
        # sweep plans it from an empty tag and renames it to the bare stamp --
        # losing the runcard the operator had entered.
        path = write_datalog(tmp_path, name="raw_dump.csv")
        set_runcard_tag(str(tmp_path), path, "VBBE00")
        _sidecar_write_fails(monkeypatch)
        execute_sweep(str(tmp_path), plan_renames(str(tmp_path)))

        monkeypatch.undo()
        execute_sweep(str(tmp_path), plan_renames(str(tmp_path)))

        assert "2026-08-06_173312~VBBE00.csv" in _names(tmp_path)
        assert _sidecar(tmp_path) == {"2026-08-06_173312~VBBE00.csv": "VBBE00"}

    def test_a_rolled_back_file_is_reported_as_skipped_which_is_now_true(
        self, tmp_path, monkeypatch
    ):
        path = write_datalog(tmp_path, name="raw_dump.csv")
        set_runcard_tag(str(tmp_path), path, "VBBE00")
        plans = plan_renames(str(tmp_path))
        _sidecar_write_fails(monkeypatch)

        result = execute_sweep(str(tmp_path), plans)

        assert result.renamed == []
        assert result.tag_orphaned == []
        assert len(result.skipped) == 1
        assert "another process has the file open" in result.skipped[0][1]
        # "Skipped" has to mean the folder is as it was, or the word is a lie.
        assert _names(tmp_path) == sorted(["raw_dump.csv", RUNCARD_TAGS_FILENAME])


class TestWhenTheRollbackAlsoFails:
    """The one half-applied outcome, reported as itself rather than as "skipped".

    A skipped file is untouched and re-sweeping fixes it. An orphaned one is on
    disk under its new name with its tag under the old key, so re-sweeping will
    not find it -- calling the two the same thing tells the operator to do the
    one thing that cannot work.
    """

    def test_it_raises_tag_orphaned_naming_both_names(self, tmp_path, monkeypatch):
        path = write_datalog(tmp_path, name="raw_dump.csv")
        set_runcard_tag(str(tmp_path), path, "VBBE00")
        plan = plan_single_rename(str(tmp_path), get_run_metadata(path))
        _sidecar_write_fails(monkeypatch)
        _rollback_to_fails(monkeypatch, "raw_dump.csv")

        with pytest.raises(TagOrphaned) as excinfo:
            apply_rename(str(tmp_path), plan)

        assert "raw_dump.csv" in str(excinfo.value)
        assert "2026-08-06_173312~VBBE00.csv" in str(excinfo.value)

    def test_the_sweep_files_it_under_tag_orphaned_not_skipped(self, tmp_path, monkeypatch):
        path = write_datalog(tmp_path, name="raw_dump.csv")
        set_runcard_tag(str(tmp_path), path, "VBBE00")
        plans = plan_renames(str(tmp_path))
        _sidecar_write_fails(monkeypatch)
        _rollback_to_fails(monkeypatch, "raw_dump.csv")

        result = execute_sweep(str(tmp_path), plans)

        assert result.renamed == []
        assert result.skipped == []
        assert len(result.tag_orphaned) == 1
        old, new, reason = result.tag_orphaned[0]
        assert old.endswith("raw_dump.csv")
        assert new.endswith("2026-08-06_173312~VBBE00.csv")
        assert "another process has the file open" in reason

    def test_one_orphan_does_not_abort_the_rest_of_the_sweep(self, tmp_path, monkeypatch):
        # Same contract as a skipped file: four hundred runs must not stop
        # because one of them ended up half-applied. The second run rolls back
        # cleanly and is skipped; only the first is orphaned.
        orphan = write_datalog(tmp_path, name="raw_dump.csv")
        other = write_datalog(tmp_path, name="other_dump.csv")
        set_runcard_tag(str(tmp_path), orphan, "VBBE00")
        set_runcard_tag(str(tmp_path), other, "VBBE01")
        plans = plan_renames(str(tmp_path))
        _sidecar_write_fails(monkeypatch)
        _rollback_to_fails(monkeypatch, "raw_dump.csv")

        result = execute_sweep(str(tmp_path), plans)

        assert len(plans) == 2
        assert [Path(p).name for p, _new, _why in result.tag_orphaned] == ["raw_dump.csv"]
        assert [Path(p).name for p, _why in result.skipped] == ["other_dump.csv"]
        assert (tmp_path / "other_dump.csv").exists()


class TestSweepResultShape:
    def test_tag_orphaned_defaults_to_empty(self):
        # So a reader of the first two buckets that predates the third
        # under-reports rather than failing to construct one at all.
        assert SweepResult(renamed=[], skipped=[]).tag_orphaned == []
