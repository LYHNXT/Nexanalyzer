"""Unit tests for modules.datalog.io.runcard_index (the files runcard matching reads)."""

import os
from pathlib import Path

import pytest

from modules.datalog.io.runcard_index import (
    default_runcard_folder,
    read_run_steps,
    read_runcards,
    recipe_folder,
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


class TestTheRecipePoolComesFromTheToolsPage:
    """`recipe_folder` turns the Tools page's runcard subfolder -- which answers
    "where is one wafer's card" -- into the pool matching needs, which is that
    plus the shared recipes. The two tools set up today need opposite answers."""

    def _card(self, folder: Path, name: str) -> None:
        folder.mkdir(parents=True, exist_ok=True)
        (folder / f"{name}.csv").write_text(MATCH_RUNCARD, encoding="utf-8")

    def test_the_layout_folder_is_the_pool_when_it_holds_the_shared_recipes(self, tmp_path):
        # HA1P01: CLEANING-1..5 loose in RUNCARD/lcy/NSHA1P01, wafer cards in
        # month folders under it. Widening would add another tool's cards.
        layout = tmp_path / "RUNCARD" / "lcy" / "NSHA1P01"
        for name in ("CLEANING-1", "CLEANING-2", "O2-annealing"):
            self._card(layout, name)
        self._card(layout / "202610", "HADI40")
        self._card(tmp_path / "RUNCARD" / "lcy" / "NSHA1N01", "HBDA01")

        assert recipe_folder(tmp_path / "DATALOG", layout) == str(layout)

    def test_the_parent_is_the_pool_when_the_layout_folder_is_all_wafer_cards(self, tmp_path):
        # VB1P01: 1192 wafer cards loose in RUNCARD/LYH and the shared recipes
        # a level up. Taking LYH as the pool marks every wafer card reusable.
        layout = tmp_path / "RUNCARD" / "LYH"
        for name in ("VBCA24", "VBCA25", "VBCA26"):
            self._card(layout, name)
        self._card(tmp_path / "RUNCARD", "Cleaning-1")

        assert recipe_folder(tmp_path / "DATALOG", layout) == str(tmp_path / "RUNCARD")

    def test_the_widening_is_what_decides_which_cards_count_as_shared(self, tmp_path):
        # The point of the rule, not a restatement of it: `read_runcards` calls
        # a file loose at the pool's root reusable, so the wrong pool makes
        # every wafer card reusable and card exclusivity stops meaning anything.
        layout = tmp_path / "RUNCARD" / "LYH"
        for name in ("VBCA24", "VBCA25", "VBCA26"):
            self._card(layout, name)
        self._card(tmp_path / "RUNCARD", "Cleaning-1")

        wrong = [c.name for c in read_runcards(layout) if c.common]
        right = [c.name for c in read_runcards(recipe_folder(tmp_path / "DATALOG", layout))
                 if c.common]

        assert sorted(wrong) == ["VBCA24", "VBCA25", "VBCA26"]
        assert right == ["Cleaning-1"]

    def test_a_layout_folder_of_only_subfolders_is_kept_rather_than_widened(self, tmp_path):
        # Nothing loose says the shared recipes are above, and the parent may
        # be the site's folder rather than this tool's.
        layout = tmp_path / "RUNCARD" / "LYH"
        self._card(layout / "202610", "VBCA26")
        self._card(tmp_path / "RUNCARD", "Cleaning-1")

        assert recipe_folder(tmp_path / "DATALOG", layout) == str(layout)

    def test_a_tool_with_no_layout_still_gets_the_guess(self, tmp_path):
        (tmp_path / "DATALOG").mkdir()
        (tmp_path / "RUNCARD").mkdir()

        assert recipe_folder(tmp_path / "DATALOG", None) == str(tmp_path / "RUNCARD")

    def test_a_layout_folder_that_is_not_on_this_pc_falls_back_to_the_guess(self, tmp_path):
        (tmp_path / "DATALOG").mkdir()
        (tmp_path / "RUNCARD").mkdir()

        assert (recipe_folder(tmp_path / "DATALOG", tmp_path / "nowhere")
                == str(tmp_path / "RUNCARD"))

    def test_an_explorer_copy_is_not_counted_when_deciding(self, tmp_path):
        # `HADH84 - 複製.csv` is never a card anyone ran, and read_runcards
        # drops it -- so it must not tip the loose-file count either.
        layout = tmp_path / "RUNCARD" / "lcy" / "NSHA1P01"
        self._card(layout, "CLEANING-1")
        self._card(layout, "HADI40 - 複製")
        self._card(layout / "202610", "HADI41")

        assert recipe_folder(tmp_path / "DATALOG", layout) == str(layout)
