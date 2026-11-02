"""Unit tests for modules.datalog.processing.runcard_match (which runcard produced a run).

Every datalog here is built by `recipe_datalog` from a list of steps, and every
card is `MATCH_RUNCARD` with at most one line changed -- so each test's input
differs from the exact replay by the one thing the test is about.
"""

import io

import pandas as pd
import pytest

from modules.datalog.processing import runcard_match as rm
from tests.datalog_fixtures import MATCH_RUNCARD, MATCH_STEPS, recipe_datalog

_START = pd.Timestamp("2026-08-06 17:33:12")


def _steps(text):
    """`run_steps` on CSV text, parsed the way `io.runcard_index.read_run_steps` parses a file."""
    df = pd.read_csv(io.StringIO(text))
    df["Time"] = pd.to_datetime(df["Time"], format=rm.TIME_FORMAT)
    for col in df.columns:
        if col not in ("Time", "Program", "651C Gauge", "Auto Action"):
            df[col] = pd.to_numeric(df[col], errors="coerce")
    return rm.run_steps(df)


def _commands(text):
    rows = []
    for line in text.splitlines():
        parts = [p.strip() for p in line.split(",")] + ["--", "--"]
        if parts[0] in ("--", ""):
            continue
        if parts[0] == "End":
            break
        rows.append((parts[0], parts[1], parts[2]))
    return tuple(rows)


def _saved(delta):
    """An mtime `delta` before the run's start, as a local-time epoch like `os.stat` gives."""
    return (_START - delta).to_pydatetime().timestamp()


def _card(text=MATCH_RUNCARD, name="HAXA01", saved=pd.Timedelta(hours=12), common=False):
    return rm.Runcard(name=name, path=f"{name}.csv", mtime=_saved(saved),
                      commands=_commands(text), common=common)


def _edit(old, new, text=MATCH_RUNCARD):
    assert old in text
    return text.replace(old, new, 1)


def _replace_step(index, step, steps=MATCH_STEPS):
    steps = list(steps)
    steps[index] = step
    return steps


class TestAnExactReplayCostsNothing:
    def test_the_card_that_ran_explains_its_log(self):
        assert rm.content_cost(_steps(recipe_datalog()), _card()) == 0

    def test_pumping_time_is_not_on_the_clock(self):
        # Pumping lasts as long as the chamber takes; the card cannot say how
        # long, so a run that pumped for ten minutes is the same recipe.
        slow = _replace_step(0, ("Pumping", 600, {}))

        assert rm.content_cost(_steps(recipe_datalog(slow)), _card()) == 0

    def test_the_log_is_cut_at_end(self):
        # After End the operator vents by hand; none of it is the recipe.
        vent = list(MATCH_STEPS) + [("End", 60, {"MFC-4": 500, "MFC-6": 500})]

        assert rm.content_cost(_steps(recipe_datalog(vent)), _card()) == 0


class TestADifferenceCostsWhereItIs:
    def test_a_changed_flow_is_one_value_edit(self):
        card = _card(_edit("MFC/PC,MFC-8 H2Se,4", "MFC/PC,MFC-8 H2Se,5"))
        run = _steps(recipe_datalog())

        cost = rm.content_cost(run, card)

        assert 0 < cost <= 0.7
        assert any("MFC-8: log 4, card 5" in d for d in rm.differences(run, card))

    def test_an_operator_changed_first_wait_costs_one_step_not_the_rest_of_the_run(self):
        # The case that retired the timeline comparison: 60 s vs 300 s at the
        # start shifted every later step, so the right card looked wrong everywhere.
        run = _steps(recipe_datalog(_replace_step(2, ("Wait", 300, {}))))

        cost = rm.content_cost(run, _card())

        assert 0 < cost <= 0.7
        assert rm.differences(run, _card()) == ["0:00 wait: log 300 s, card 60 s"]

    def test_a_missing_step_costs_more_than_a_changed_value(self):
        # A step present on one side only is a different recipe; a changed
        # value on a shared step is an edit of the same one. Edited cleaning
        # recipes are told apart by exactly this.
        run = _steps(recipe_datalog())
        edited = _card(_edit("MFC/PC,MFC-8 H2Se,1", "MFC/PC,MFC-8 H2Se,2"))
        missing = _card(_edit("MFC/PC,MFC-8 H2Se,1\n", ""))

        assert rm.content_cost(run, missing) > rm.content_cost(run, edited)


class TestWhatTheLogCannotShow:
    def test_a_0_01_setpoint_on_a_coarse_mfc_reads_as_zero(self):
        # MFC-1 never logs 0.01: below its resolution. Only MFC-3 and MFC-8 do.
        card = _card(_edit("MFC/PC,MFC-1 Ar,50", "MFC/PC,MFC-1 Ar,0.01"))
        run = _steps(recipe_datalog(_replace_step(1, ("MFC/PC", 1, {"MFC-8": 0.01, "MFC-3": 0.01}))))

        assert rm.content_cost(run, card) == 0

    def test_the_last_runs_vent_flow_switched_off_by_hand_is_not_a_step(self):
        steps = [("Pumping", 5, {}), ("Wait", 1, {}), ("Pumping", 25, {"MFC-4": 0, "MFC-6": 0})] + MATCH_STEPS[1:]
        run = _steps(recipe_datalog(steps, initial={"MFC-4": 500, "MFC-6": 500}))

        assert rm.content_cost(run, _card()) == pytest.approx(0, abs=0.5)
        assert not any("MFC-4" in d for d in rm.differences(run, _card()))

    def test_an_aborted_run_is_not_charged_for_steps_it_never_reached(self):
        aborted = MATCH_STEPS[:5]           # stopped in the 120 s hold, no End
        run = _steps(recipe_datalog(aborted))

        assert not run.ended
        assert rm.content_cost(run, _card()) == 0

    def test_an_aborted_run_is_not_charged_for_the_hold_it_was_stopped_in(self):
        # Stopped 40 s into the 120 s hold. The log's last block lines up with
        # a *middle* card block, and its wait is short only because of the
        # abort. Before v5.23.0 the forgiveness keyed on the card's last block
        # too, so this cost 0.538 with "wait: log 39 s, card 120 s".
        run = _steps(recipe_datalog(MATCH_STEPS[:4] + [("Wait", 40, {})]))

        assert not run.ended
        assert rm.content_cost(run, _card()) == 0
        assert rm.differences(run, _card()) == []

    def test_an_aborted_run_whose_last_hold_overran_the_card_is_still_charged(self):
        # The forgiveness is for a wait cut *short*; a hold 200 s longer than
        # the card's is a different recipe whether or not the run then ended.
        run = _steps(recipe_datalog(MATCH_STEPS[:4] + [("Wait", 320, {})]))

        assert not run.ended
        assert rm.content_cost(run, _card()) > 0
        assert any("wait: log" in d for d in rm.differences(run, _card()))

    def test_a_short_last_hold_on_a_run_that_ended_is_still_a_difference(self):
        # Only an aborted run is forgiven; a completed run with a short final
        # hold ran a different card.
        steps = _replace_step(8, ("Wait", 40, {}))
        run = _steps(recipe_datalog(steps))

        assert run.ended
        assert rm.content_cost(run, _card()) > 0


class TestSuggestRuncards:
    def test_a_growth_card_goes_to_one_run_and_a_common_recipe_to_any_number(self):
        a = ("a.csv", _steps(recipe_datalog()))
        b = ("b.csv", _steps(recipe_datalog(start="2026-08-06 20:00:00")))
        cards = [_card(name="HAXA01"), _card(name="HAXA02", saved=pd.Timedelta(hours=11))]

        out = rm.suggest_runcards([a, b], cards)

        assert {out["a.csv"].tag, out["b.csv"].tag} == {"HAXA01", "HAXA02"}

    def test_identical_cards_are_given_out_in_run_order(self):
        early = ("early.csv", _steps(recipe_datalog()))
        late = ("late.csv", _steps(recipe_datalog(start="2026-08-07 09:00:00")))
        # Saved in the opposite order to their numbers, so only the order rule
        # can put HAXA01 on the earlier run.
        cards = [_card(name="HAXA01", saved=pd.Timedelta(hours=1)),
                 _card(name="HAXA02", saved=pd.Timedelta(hours=20))]

        out = rm.suggest_runcards([late, early], cards)

        assert out["early.csv"].tag == "HAXA01"
        assert out["late.csv"].tag == "HAXA02"

    def test_a_later_run_never_takes_an_earlier_number_however_well_it_fits(self):
        # HADJ, 2026-10-31: the card of the later run had been re-saved just
        # before the earlier one, and content alone read the pair backwards.
        # Card numbers have never gone backwards in time on this tool, so the
        # order the runs went in decides, not the fit.
        edited = _edit("MFC/PC,MFC-8 H2Se,4", "MFC/PC,MFC-8 H2Se,5")
        early = ("early.csv", _steps(recipe_datalog(_replace_step(5, ("MFC/PC", 1, {"MFC-6": 50, "MFC-8": 5})))))
        late = ("late.csv", _steps(recipe_datalog(start="2026-08-07 09:00:00")))
        cards = [_card(name="HAXA01", saved=pd.Timedelta(hours=2)),
                 _card(edited, name="HAXA02", saved=pd.Timedelta(hours=30))]

        out = rm.suggest_runcards([early, late], cards)

        assert out["early.csv"].tag == "HAXA01"
        assert out["late.csv"].tag == "HAXA02"

    def test_a_card_already_tagged_on_another_run_is_taken(self):
        run = ("x.csv", _steps(recipe_datalog()))
        cards = [_card(name="HAXA01"), _card(name="HAXA02")]

        out = rm.suggest_runcards([run], cards, used={"HAXA01": _START - pd.Timedelta(hours=3)})

        assert out["x.csv"].tag == "HAXA02"

    def test_a_card_tagged_only_on_a_failed_attempt_is_free_for_its_rerun(self):
        # HADF22--fail at 14:15, HADF22 at 16:10: the rerun used the same card.
        run = ("rerun.csv", _steps(recipe_datalog()))
        cards = [_card(name="HAXA01"), _card(name="HAXA02")]

        out = rm.suggest_runcards([run], cards, used={"HAXA01_FAIL": _START - pd.Timedelta(hours=2)})

        assert out["rerun.csv"].tag == "HAXA01"

    def test_a_card_saved_as_the_run_started_wins_a_tie(self):
        run = ("x.csv", _steps(recipe_datalog()))
        cards = [_card(name="HAXA05", saved=pd.Timedelta(days=1)),
                 _card(name="HAXB07", saved=pd.Timedelta(seconds=8))]

        out = rm.suggest_runcards([run], cards)

        assert out["x.csv"].tag == "HAXB07"
        assert "card saved as the run started" in out["x.csv"].notes

    def test_a_number_between_the_tagged_runs_around_it_is_preferred(self):
        run = ("x.csv", _steps(recipe_datalog()))
        cards = [_card(name="HAXA02"), _card(name="HAXA04")]
        used = {"HAXA01": _START - pd.Timedelta(hours=3), "HAXA03": _START + pd.Timedelta(hours=3)}

        out = rm.suggest_runcards([run], cards, used=used)

        assert out["x.csv"].tag == "HAXA02"

    def test_an_aborted_attempt_shares_the_card_of_its_rerun_and_is_marked_failed(self):
        attempt = ("attempt.csv", _steps(recipe_datalog(MATCH_STEPS[:5])))
        rerun = ("rerun.csv", _steps(recipe_datalog(start="2026-08-06 17:50:00")))
        cards = [_card(name="HAXA01"), _card(name="HAXA02")]

        out = rm.suggest_runcards([attempt, rerun], cards)

        assert out["attempt.csv"].tag == out["rerun.csv"].tag + "_FAIL"
        assert out["attempt.csv"].confidence != "high"

    def test_an_edited_cleaning_is_named_from_a_tagged_log_of_its_era(self):
        # The cleaning file was edited after the run: today's CLEANING-2 is off
        # by one value, while a tagged "Cleaning-3" log from the same week ran
        # exactly these steps.
        run = ("c.csv", _steps(recipe_datalog()))
        reference = ("Cleaning-3", _steps(recipe_datalog(start="2026-08-05 10:00:00")))
        current = _card(_edit("MFC/PC,MFC-8 H2Se,4", "MFC/PC,MFC-8 H2Se,9"), name="CLEANING-2", common=True)

        out = rm.suggest_runcards([run], [current], references=[reference])

        assert out["c.csv"].tag == "CLEANING-3"
        assert out["c.csv"].confidence == "high"

    def test_cards_saved_outside_the_window_are_not_candidates(self):
        run = ("x.csv", _steps(recipe_datalog()))
        stale = _card(name="HAXA01", saved=pd.Timedelta(days=40))

        assert rm.suggest_runcards([run], [stale]) == {}


class TestNames:
    @pytest.mark.parametrize("tag,expected", [
        ("HADF22--fail", "HADF22"), ("HADF05_FAIL", "HADF05"), (" hadh71 ", "HADH71"), ("CLEANING", "CLEANING"),
    ])
    def test_base_tag_drops_the_failure_suffix(self, tag, expected):
        assert rm.base_tag(tag) == expected

    @pytest.mark.parametrize("tag,expected", [
        ("cleanin-4", "CLEANING-4"), ("Cleaning-2", "CLEANING-2"), ("CLEANING-5", "CLEANING-5"),
        ("Cleaning", None), ("CLEANING_STOP", None),
    ])
    def test_cleaning_name_reads_the_number_or_nothing(self, tag, expected):
        assert rm.cleaning_name(tag) == expected

    @pytest.mark.parametrize("name,expected", [
        ("HADH75", ("HADH", 75)), ("NSHA1P012509001", ("SER2509", 1)),
        ("NSHA1P0125010003_H2OPC100", ("SER25010", 3)), ("CLEANING-4", (None, None)),
    ])
    def test_series_of(self, name, expected):
        assert rm.series_of(name) == expected

    def test_an_explorer_copy_is_recognised(self):
        assert rm.is_copy_name("HADH84 - 複製")
        assert not rm.is_copy_name("HADH84")


class TestSubResolutionSetpointsFollowTheLog:
    """Which MFCs log a 0.01 sccm setpoint is read off each log, not fixed.

    From 2026-10 HA1P01 logs MFC-8's 0.01 as 0 (it logged it through
    September). With MFC-8 hard-coded as a channel that shows 0.01, every
    October card's two MFC-8 0.01 steps cost 1.0 each: a flat 2.0 on every
    growth run, which is the "medium" ceiling.
    """

    def _october_log(self):
        # The same recipe, logged by a controller that writes MFC-8's 0.01 as 0.
        return _steps(recipe_datalog(_replace_step(
            1, ("MFC/PC", 1, {"MFC-8": 0, "MFC-1": 50, "MFC-3": 0.01}),
        )))

    def test_a_card_0_01_on_an_mfc_this_log_never_shows_below_resolution_costs_nothing(self):
        run = self._october_log()

        assert "MFC-8" in run.coarse and "MFC-3" not in run.coarse
        assert rm.content_cost(run, _card()) == 0
        assert not any("MFC-8" in d for d in rm.differences(run, _card()))

    def test_an_mfc_the_log_does_show_at_0_01_still_holds_the_card_to_it(self):
        run = _steps(recipe_datalog())   # MFC-8 logs its 0.01, as through September

        assert "MFC-8" not in run.coarse
        card = _card(_edit("MFC/PC,MFC-8 H2Se,0.01", "MFC/PC,MFC-8 H2Se,0"))
        assert rm.content_cost(run, card) > 0

    def test_a_caller_without_a_log_gets_the_fixed_list(self):
        run = _steps(recipe_datalog())
        with_default, _ = rm.card_blocks(_card().commands, run.initial)
        with_log, _ = rm.card_blocks(_card().commands, run.initial, run.coarse)

        assert with_default == with_log   # the September log agrees with the old list


class TestAHeaterSoakIsASetpointStep:
    """`Heater Soak,800` steps the heater SV to 800 (growth_window reads it so
    since v5.21.0). The matcher dropped the command, so every CLEANING-5 run
    cost 1.4 against its own card: "heater SV: log 800, card 900"."""

    _CARD = _edit("MFC/PC,MFC-8 H2Se,4\n", "MFC/PC,MFC-8 H2Se,4\nHeater Soak,800,--\n")

    def _log(self):
        steps = list(MATCH_STEPS)
        steps.insert(6, ("Heater Soak", 1, {}))
        lines = recipe_datalog(steps).splitlines()
        header = lines[0].split(",")
        sv, program = header.index("Heater SV"), header.index("Program")
        out, soaked = [lines[0]], False
        for line in lines[1:]:
            row = line.split(",")
            soaked = soaked or row[program] == "Heater Soak"
            if soaked:
                row[sv] = "800"
            out.append(",".join(row))
        return "\n".join(out) + "\n"

    def test_the_card_with_the_soak_explains_its_log(self):
        assert rm.content_cost(_steps(self._log()), _card(self._CARD)) == 0

    def test_a_card_without_the_soak_does_not(self):
        assert rm.content_cost(_steps(self._log()), _card()) > 0

    def test_a_soak_to_zero_is_heater_off_and_moves_no_setpoint(self):
        card = _edit("MFC/PC,MFC-8 H2Se,4\n", "MFC/PC,MFC-8 H2Se,4\nHeater Soak,0,--\n")
        assert rm.content_cost(_steps(recipe_datalog()), _card(card)) == 0
