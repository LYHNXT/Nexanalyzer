"""Unit tests for modules.datalog.io.runcard_index (the files runcard matching reads)."""

import os
from pathlib import Path

import pytest

from modules.datalog.io.runcard_index import (
    default_runcard_folder,
    read_run_steps,
    read_runcards,
    runcard_signature,
)
from tests.datalog_fixtures import DATALOG_SHORT, MATCH_RUNCARD, recipe_datalog


def _tool(tmp_path):
    """HA1P01's layout: DATALOG beside RUNCARD/lcy, with a month folder under lcy."""
    (tmp_path / "DATALOG").mkdir()
    lcy = tmp_path / "RUNCARD" / "lcy"
    (lcy / "NSHA1P01" / "202609").mkdir(parents=True)
    return tmp_path / "DATALOG", lcy


class TestDefaultRuncardFolder:
    def test_the_lcy_folder_beside_datalog_is_found(self, tmp_path):
        datalog, lcy = _tool(tmp_path)

        assert default_runcard_folder(datalog) == str(lcy)

    def test_a_plain_runcard_folder_is_the_fallback(self, tmp_path):
        (tmp_path / "DATALOG").mkdir()
        (tmp_path / "RUNCARD").mkdir()

        assert default_runcard_folder(tmp_path / "DATALOG") == str(tmp_path / "RUNCARD")

    def test_no_recipe_folder_is_none_not_a_guess(self, tmp_path):
        (tmp_path / "DATALOG").mkdir()

        assert default_runcard_folder(tmp_path / "DATALOG") is None


class TestReadRuncards:
    def test_top_level_files_are_common_and_month_folder_cards_are_not(self, tmp_path):
        _datalog, lcy = _tool(tmp_path)
        (lcy / "CLEANING-4.csv").write_text(MATCH_RUNCARD, encoding="utf-8")
        (lcy / "NSHA1P01" / "202609" / "HADH75.csv").write_text(MATCH_RUNCARD, encoding="utf-8")

        cards = {c.name: c for c in read_runcards(lcy)}

        assert cards["CLEANING-4"].common
        assert not cards["HADH75"].common

    def test_an_old_cleaning_version_inside_a_month_folder_is_common_too(self, tmp_path):
        # lcy/NSHA1N01/NSHA1N012508/CLEANING-2.csv is how a recipe that was
        # later edited in place survives; it can have run many times.
        _datalog, lcy = _tool(tmp_path)
        (lcy / "NSHA1P01" / "202609" / "Cleaning.csv").write_text(MATCH_RUNCARD, encoding="utf-8")

        assert read_runcards(lcy)[0].common

    def test_explorer_copies_and_non_recipes_are_skipped(self, tmp_path):
        _datalog, lcy = _tool(tmp_path)
        month = lcy / "NSHA1P01" / "202609"
        (month / "HADH84 - 複製.csv").write_text(MATCH_RUNCARD, encoding="utf-8")
        (month / "export.csv").write_text(DATALOG_SHORT, encoding="utf-8")   # a datalog dropped in

        assert read_runcards(lcy) == []

    def test_commands_stop_before_end(self, tmp_path):
        _datalog, lcy = _tool(tmp_path)
        (lcy / "CLEANING-4.csv").write_text(MATCH_RUNCARD, encoding="utf-8")

        card = read_runcards(lcy)[0]

        assert card.commands[0] == ("Pumping", "5.00E-01", "--")
        assert all(name != "End" for name, _a, _b in card.commands)

    def test_a_missing_folder_is_empty(self, tmp_path):
        assert read_runcards(tmp_path / "nope") == []
        assert runcard_signature(tmp_path / "nope") == ()


class TestRuncardSignature:
    def test_editing_a_card_moves_the_signature(self, tmp_path):
        _datalog, lcy = _tool(tmp_path)
        card = lcy / "CLEANING-4.csv"
        card.write_text(MATCH_RUNCARD, encoding="utf-8")
        before = runcard_signature(lcy)

        card.write_text(MATCH_RUNCARD.replace("H2Se,4", "H2Se,5"), encoding="utf-8")
        # Same length, and two writes inside one test can share a timestamp
        # tick; a real edit is seconds later, which this stands in for.
        later = before[0][1] + 10
        os.utime(card, (later, later))

        assert runcard_signature(lcy) != before


class TestReadRunSteps:
    def test_a_datalog_file_reduces_to_blocks(self, tmp_path):
        path = tmp_path / "2026-08-06_173312.csv"
        path.write_text(recipe_datalog(), encoding="utf-8")

        steps = read_run_steps(path)

        assert steps.ended
        assert len(steps.blocks) == 4
        assert steps.total == pytest.approx(480, abs=2)

    def test_a_file_that_is_not_a_datalog_is_none(self, tmp_path):
        path = tmp_path / "notes.csv"
        path.write_text("a,b\n1,2\n", encoding="utf-8")

        assert read_run_steps(path) is None

    def test_a_missing_file_is_none(self, tmp_path):
        assert read_run_steps(Path(tmp_path) / "gone.csv") is None


class TestAMalformedCardIsSkipped:
    def test_a_csv_error_skips_the_card_not_the_folder(self, tmp_path):
        """`csv.Error` is not a ValueError: an unclosed quote running past the
        field limit used to raise out of `read_runcards` and break Detect."""
        _datalog, lcy = _tool(tmp_path)
        (lcy / "CLEANING-4.csv").write_text(MATCH_RUNCARD, encoding="utf-8")
        (lcy / "BROKEN.csv").write_text('Wait,"' + "x" * 200_000 + "\n", encoding="utf-8")

        names = {c.name for c in read_runcards(lcy)}

        assert "CLEANING-4" in names
        assert "BROKEN" not in names
