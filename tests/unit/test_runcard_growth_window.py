"""Unit tests for modules.runcard.processing.growth_window (the recipe clock and the growth window)."""

import math

import pytest

from modules.runcard.io.parser import RuncardCommand, parse_runcard
from modules.runcard.processing.growth_window import (
    ROOM_TEMP_C,
    GrowthWindow,
    build_aux_trace,
    build_profile,
    build_temp_trace,
    build_timeline,
    channel_value_at_growth_mid,
    find_growth_window,
    fixed_channel_value_at_growth_mid,
    get_species,
    species_value_at_growth_mid,
    wait_duration_seconds,
)
from tests.datalog_fixtures import RUNCARD_FULL, RUNCARD_SHORT

#: A growth run in eight rows: ramp to 900 over 10 s, hold 10 s, heater off,
#: cool. Prefixed with whatever channel commands a test is about, so that every
#: such test has a real growth window to read values at (mid = 20 s).
_GROWTH_TAIL = "Wait,Sec,10\nHeater Ramp,900,10\nWait,Sec,10\nHeater Soak,0,--\nWait,Sec,60\n"

#: The ramp to 900 is issued at t=10, the same instant the heater goes off, so
#: it never acts: the trace never leaves room temperature and there is no
#: window. Until v5.23.0 the ramp was laid down whole after heater-off and
#: `peak_temp_c` reported a 900 the heater was never driven to.
_NO_WINDOW = "MFC/PC,PC-1,400\nWait,Sec,10\nHeater Ramp,900,10\nHeater Soak,0,--\nWait,Sec,60\n"

#: No `Heater Soak`, so no cooldown and no extension to `total`: the trace ends
#: at the ramp vertex and both window edges land on it.
_NO_SOAK = "Heater Ramp,900,100\nWait,Sec,5000\nEnd,--,--\n"


def _commands(tmp_path, text, name="recipe.csv"):
    """Parse `text` the way the page does -- through a real file on disk."""
    path = tmp_path / name
    path.write_text(text, encoding="utf-8", newline="")
    return parse_runcard(str(path))


def _profile(tmp_path, text, name="recipe.csv"):
    return build_profile(_commands(tmp_path, text, name))


def _wait(unit, value):
    return RuncardCommand(index=0, name="Wait", params=(unit, value))


class TestWaitDurationSeconds:
    """The runcard puts the **unit first**: params are (unit, magnitude).

    Read the other way round, `Wait,Min,20` reconstructs as 20 seconds instead
    of 20 minutes, which on a HAD* recipe moves the growth window by most of an
    hour.
    """

    @pytest.mark.parametrize(
        "unit,value,expected",
        [
            ("Sec", "5", 5.0),
            ("Min", "5", 300.0),
            ("min", "5", 300.0),
            ("minute", "5", 300.0),  # startswith, so every spelling of minutes scales
            ("--", "5", 5.0),        # a unit cell nobody looks at must not refuse the file
            ("Hours", "5", 5.0),     # anything that is not minutes is silently seconds
        ],
    )
    def test_only_minutes_scale_and_everything_else_is_seconds(self, unit, value, expected):
        assert wait_duration_seconds(_wait(unit, value)) == expected

    def test_a_non_numeric_magnitude_raises(self):
        with pytest.raises(ValueError):
            wait_duration_seconds(_wait("Sec", "--"))

    def test_too_few_params_raises(self):
        with pytest.raises(IndexError):
            wait_duration_seconds(RuncardCommand(index=0, name="Wait", params=("Sec",)))


class TestBuildTimelineClock:
    """Exactly three commands advance the clock: `Wait`, `Check Status` and `Pulse*`."""

    def test_every_other_recognised_command_is_instantaneous(self, tmp_path):
        timeline = build_timeline(_commands(
            tmp_path,
            "Wait,Sec,10\nCheck Status,--,--\nHeater Ramp,850,60\nMFC/PC,MFC-1 Ar,20\n"
            "Stage Rot,10,--\nRTV Pressure Ctrl,100 Torr,60\nHeater Soak,0,--\n"
            "Accumulation PC1,300,20\n",
        ))

        assert timeline.total_time == 20.0

    def test_pumping_and_pumping_forward_never_advance_the_clock(self, tmp_path):
        # Deliberate, and forced: their single param is a target *pressure*
        # (`Pumping Forward,0.0011,--`) and params[1] is always `--`, so there
        # is no number in the row that could be added to the clock. The real
        # hold is however long the pump takes, which the recipe does not say.
        # In the VBBE00 datalog that is ~14 minutes of wall-clock contributing
        # 0 s here -- the reconstructed clock is idealised, not wall-clock.
        timeline = build_timeline(_commands(
            tmp_path, "Pumping,5.00E-01,--\nPumping Forward,0.0011,--\nWait,Sec,10\n",
        ))

        assert timeline.total_time == 10.0
        assert timeline.heater_ramps == []
        assert timeline.mfc_events == []
        assert timeline.pc_events == []
        assert timeline.heater_off_t is None

    def test_both_spellings_of_the_pump_command_are_recorded(self, tmp_path):
        """The HAD* family writes bare "Pumping"; the VBBE family writes "Pumping Forward".

        Knowing only the second dropped every HAD* pump-down silently, and
        that is 25 of the 39 example recipes. *Where* the recipe pumps is worth
        keeping even though *how long* is unknowable.
        """
        timeline = build_timeline(_commands(
            tmp_path,
            "Pumping,5.00E-01,--\nWait,Sec,10\nPumping Forward,0.0011,--\nWait,Sec,10\n",
        ))

        assert timeline.pump_events == [(0.0, 0.5), (10.0, 0.0011)]

    def test_a_pump_row_with_no_readable_target_is_skipped_not_fatal(self, tmp_path):
        # A hand-typed recipe can carry "--" where a number belongs, and one
        # unreadable pump row must not cost the whole file.
        timeline = build_timeline(_commands(tmp_path, "Pumping,--,--\nWait,Sec,10\n"))

        assert timeline.pump_events == []
        assert timeline.total_time == 10.0

    def test_end_and_unknown_commands_are_dropped_silently(self, tmp_path):
        # There is no `else` in the dispatch, which is what makes
        # `total_time <= 0` a usable "this file is not a recipe" test.
        timeline = build_timeline(_commands(tmp_path, "Purge Line,1,2\nWait,Sec,10\nEnd,--,--\n"))

        assert timeline.total_time == 10.0

    def test_a_wait_in_minutes_is_converted(self, tmp_path):
        assert build_timeline(_commands(tmp_path, "Wait,Min,2\n")).total_time == 120.0


class TestBuildTimelineDispatch:
    def test_a_ramp_records_its_declared_duration_without_spending_it(self, tmp_path):
        timeline = build_timeline(_commands(tmp_path, "Heater Ramp,850,120\n"))

        assert timeline.heater_ramps == [(0.0, ROOM_TEMP_C, 850.0, 120.0)]
        assert timeline.total_time == 0.0

    def test_a_ramp_starts_from_the_running_temperature_the_last_one_left(self, tmp_path):
        timeline = build_timeline(_commands(
            tmp_path, "Wait,Sec,10\nHeater Ramp,300,60\nWait,Sec,60\nHeater Ramp,850,120\n",
        ))

        assert timeline.heater_ramps == [
            (10.0, 25.0, 300.0, 60.0),
            (70.0, 300.0, 850.0, 120.0),
        ]

    def test_each_preheater_tracks_its_own_running_temperature(self, tmp_path):
        timeline = build_timeline(_commands(
            tmp_path, "P1_Heater Ramp,40,60\nP1_Heater Ramp,60,30\nP2_Heater Ramp,50,60\n",
        ))

        assert timeline.p1_ramps == [(0.0, 25.0, 40.0, 60.0), (0.0, 40.0, 60.0, 30.0)]
        assert timeline.p2_ramps == [(0.0, 25.0, 50.0, 60.0)]

    def test_only_the_first_heater_soak_marks_the_heater_off(self, tmp_path):
        # HADH00 and HADG37 each carry two (`Heater Soak,0,--` then
        # `Heater Soak,0,600`), and the 600 is not a duration the clock spends.
        timeline = build_timeline(_commands(
            tmp_path, "Wait,Sec,10\nHeater Soak,0,--\nWait,Sec,20\nHeater Soak,0,600\n",
        ))

        assert timeline.heater_off_t == 10.0
        assert timeline.total_time == 30.0

    def test_mfc_and_pc_rows_are_split_on_the_channel_prefix(self, tmp_path):
        # Routed on the literal "PC-" prefix rather than a regex, so an
        # unrecognised channel name lands in mfc_events instead of vanishing.
        timeline = build_timeline(_commands(
            tmp_path, "MFC/PC,MFC-1 Ar,20\nMFC/PC,PC-1,400\nMFC/PC,Purge Line,5\n",
        ))

        assert timeline.mfc_events == [(0.0, "MFC-1 Ar", 20.0), (0.0, "Purge Line", 5.0)]
        assert timeline.pc_events == [(0.0, "PC-1", 400.0)]

    def test_the_rtv_row_stores_its_third_column_not_its_pressure(self, tmp_path):
        # `params[0]` is only ever "100 Torr" or "1000 Torr" across the whole
        # dataset while `params[1]` ranges over {10, 60, 70, 90}, and the
        # measured tube pressure during the controlled segment is ~7.9 Torr --
        # so the stored number is neither the gauge range nor the achieved
        # pressure. Every historical figure was drawn from it regardless.
        timeline = build_timeline(_commands(tmp_path, "RTV Pressure Ctrl,100 Torr,60\n"))

        assert timeline.rtv_events == [(0.0, 60.0)]

    def test_accumulation_rows_join_the_pressure_controllers(self, tmp_path):
        # Value from params[0], unlike MFC/PC, and both spellings are accepted.
        # Nothing downstream can tell these from an `MFC/PC PC-n` row.
        timeline = build_timeline(_commands(
            tmp_path, "Accumulation PC1,300,20\nAccumulation_PC2,130,20\n",
        ))

        assert timeline.pc_events == [(0.0, "PC-1", 300.0), (0.0, "PC-2", 130.0)]

    def test_an_accumulation_row_with_no_digits_yields_a_nameless_channel(self, tmp_path):
        timeline = build_timeline(_commands(tmp_path, "Accumulation PC,7,--\n"))

        assert timeline.pc_events == [(0.0, "PC-", 7.0)]

    def test_stage_rot_records_its_first_param(self, tmp_path):
        timeline = build_timeline(_commands(tmp_path, "Stage Rot,10,--\n"))

        assert timeline.spin_events == [(0.0, 10.0)]

    def test_simultaneous_commands_share_a_timestamp_in_command_order(self, tmp_path):
        timeline = build_timeline(_commands(tmp_path, RUNCARD_FULL))

        assert timeline.mfc_events[:2] == [(10.0, "MFC-1 Ar", 20.0), (10.0, "MFC-3 O2", 0.1)]

    def test_the_full_recipe_replays_to_a_known_clock(self, tmp_path):
        timeline = build_timeline(_commands(tmp_path, RUNCARD_FULL))

        assert timeline.total_time == 490.0
        assert timeline.heater_ramps == [(10.0, 25.0, 300.0, 60.0), (70.0, 300.0, 850.0, 120.0)]
        assert timeline.heater_off_t == 310.0
        assert timeline.pc_events == [
            (10.0, "PC-1", 400.0),
            (10.0, "PC-2", 140.0),
            (70.0, "PC-1", 300.0),  # the Accumulation PC1 row, merged in
        ]
        assert timeline.rtv_events == [(10.0, 60.0)]
        assert timeline.spin_events == [(0.0, 10.0)]

    def test_a_malformed_number_raises_out_of_the_replay(self, tmp_path):
        # Which is exactly what `list_runcards` catches per file, so one bad
        # recipe costs one row rather than the whole listing.
        with pytest.raises(ValueError):
            build_timeline(_commands(tmp_path, "Heater Ramp,abc,--\nWait,Sec,10\n"))


class TestBuildTempTrace:
    def test_a_ramp_is_a_plateau_vertex_and_an_end_vertex(self, tmp_path):
        # Two vertices and the straight line between them; nothing in between.
        # Anything wanting a temperature part-way up a ramp interpolates itself.
        profile = _profile(tmp_path, "Wait,Sec,10\nHeater Ramp,300,60\nWait,Sec,60\n")

        assert profile.temp_trace == [(0.0, 25.0), (10.0, 25.0), (70.0, 300.0)]

    def test_the_plateau_vertex_is_suppressed_when_there_is_no_gap(self, tmp_path):
        profile = _profile(tmp_path, "Heater Ramp,300,60\nWait,Sec,60\n")

        assert profile.temp_trace == [(0.0, 25.0), (60.0, 300.0)]

    def test_the_cooldown_starts_one_step_after_the_heater_went_off(self, tmp_path):
        profile = _profile(tmp_path, RUNCARD_FULL)

        assert [t for t, _temp in profile.temp_trace] == [0.0, 10.0, 70.0, 190.0, 310.0, 370.0, 430.0, 490.0]

    def test_the_cooldown_decays_from_the_last_ramp_target(self, tmp_path):
        profile = _profile(tmp_path, RUNCARD_FULL)

        assert profile.temp_trace[5][1] == pytest.approx(25.0 + (850.0 - 25.0) * math.exp(-60 / 1500))

    def test_the_last_cooldown_vertex_lands_exactly_on_the_end_of_the_run(self, tmp_path):
        # 90 s of cooldown is not a whole number of 60 s steps, so the final
        # step is short rather than overrunning the run.
        profile = _profile(tmp_path, "Wait,Sec,10\nHeater Soak,0,--\nWait,Sec,90\n")

        assert [t for t, _temp in profile.temp_trace] == [0.0, 10.0, 70.0, 100.0]

    def test_no_heater_soak_leaves_the_trace_at_the_final_ramp(self, tmp_path):
        # Asymmetric with build_aux_trace on purpose: a heater with no off
        # command has no modelled decay, and holding it flat out to `total`
        # would assert one.
        profile = _profile(tmp_path, _NO_SOAK)

        assert profile.timeline.total_time == 5000.0
        assert profile.temp_trace == [(0.0, 25.0), (100.0, 900.0)]

    def test_an_empty_recipe_still_has_its_origin_vertex(self):
        profile = build_profile([])

        assert profile.temp_trace == [(0.0, ROOM_TEMP_C)]

    def test_a_soak_with_no_ramps_cools_from_room_temperature(self, tmp_path):
        # The exponential multiplies (25 - 25), so every vertex is 25.
        profile = _profile(tmp_path, "Heater Soak,0,--\nWait,Sec,120\n")

        assert profile.temp_trace == [(0.0, 25.0), (60.0, 25.0), (120.0, 25.0)]


class TestBuildAuxTrace:
    def test_a_recipe_with_no_preheater_ramps_has_no_aux_trace(self, tmp_path):
        # `[]`, not `[(0.0, 25.0)]`: an origin-only trace would put a 25 degree
        # line on the plot for a heater the recipe never addressed. Every HAD*
        # recipe in the example folder is this case.
        profile = _profile(tmp_path, RUNCARD_SHORT)

        assert profile.p1_trace == []
        assert profile.p2_trace == []
        assert profile.temp_trace != []

    def test_the_aux_trace_always_reaches_the_end_of_the_run(self, tmp_path):
        profile = _profile(tmp_path, RUNCARD_FULL)

        assert profile.p1_trace == [(0.0, 25.0), (10.0, 25.0), (70.0, 40.0), (490.0, 40.0)]

    def test_the_preheaters_get_no_cooldown_even_though_the_heater_went_off(self, tmp_path):
        profile = _profile(tmp_path, RUNCARD_FULL)

        assert profile.timeline.heater_off_t == 310.0
        assert profile.p2_trace[-1] == (490.0, 50.0)

    def test_the_two_builders_disagree_about_an_empty_ramp_list(self):
        # The asymmetry is the whole difference between the two: a preheater
        # nobody addressed has no line to draw, while the main heater's origin
        # vertex is what every later lookup falls back to.
        assert build_aux_trace([], 500.0) == []
        assert build_temp_trace([], None, 500.0) == [(0.0, ROOM_TEMP_C)]


class TestFindGrowthWindow:
    def test_the_edges_snap_to_trace_vertices(self, tmp_path):
        # 190 is where the second ramp completes (70 + 120), not where the
        # rising ramp crossed peak - 5; 310 is the heater-off plateau vertex.
        profile = _profile(tmp_path, RUNCARD_FULL)

        assert profile.growth == GrowthWindow(start_s=190.0, end_s=310.0, peak_temp_c=850.0)
        assert profile.growth.mid_s == 250.0
        assert profile.growth.duration_min == 2.0

    def test_a_vertex_exactly_at_heater_off_is_inside_the_window(self):
        window = find_growth_window([(0.0, 25.0), (10.0, 850.0), (20.0, 850.0), (30.0, 700.0)], 20.0)

        assert (window.start_s, window.end_s) == (10.0, 20.0)

    def test_a_vertex_near_the_peak_is_not_in_the_window(self):
        """The defect this guards: growth used to begin within 5 degrees of peak.

        A ramp passing through peak - 5 on its way up is still heating, and on
        a slow final ramp that is minutes of climb counted as growth. The
        reference renderer records the band as a previously-fixed bug; this
        port reintroduced it by carrying an older ancestor forward.
        """
        trace = [(0.0, 25.0), (10.0, 845.0), (20.0, 850.0), (30.0, 850.0), (40.0, 700.0)]

        window = find_growth_window(trace, None)

        assert window.start_s == 20.0

    def test_a_commanded_ramp_down_ends_growth_before_the_heater_goes_off(self):
        """Growth does not run through a deliberate step to a lower temperature.

        The end is the earlier of heater-off and the start of a ramp-down --
        and it is the ramp-down's *first* vertex, the last instant still at
        peak, not the vertex where it arrives somewhere cooler.
        """
        trace = [(0.0, 25.0), (10.0, 850.0), (20.0, 850.0), (35.0, 600.0), (50.0, 600.0)]

        window = find_growth_window(trace, heater_off_t=50.0)

        assert (window.start_s, window.end_s) == (10.0, 20.0)

    def test_a_run_that_never_heats_has_no_window(self):
        # Without the ambient floor, a recipe that never turns the heater on
        # reports its own room temperature as a "peak" and the whole run as
        # growth.
        window = find_growth_window([(0.0, 25.0), (100.0, 25.0)], None)

        assert (window.start_s, window.end_s) == (None, None)

    def test_a_final_ramp_after_the_heater_went_off_has_no_window(self, tmp_path):
        # Changed in v5.23.0: this pinned peak_temp_c == 900, the target of a
        # ramp issued as the heater went off. That ramp never acts, so the
        # peak is room temperature -- and peak_temp_c is still populated, a
        # max() over a trace that always has a vertex, never evidence of a window.
        profile = _profile(tmp_path, _NO_WINDOW)

        assert profile.growth == GrowthWindow(start_s=None, end_s=None, peak_temp_c=ROOM_TEMP_C)
        assert profile.growth.mid_s is None

    def test_a_recipe_with_no_heater_soak_gets_a_zero_length_window(self, tmp_path):
        profile = _profile(tmp_path, _NO_SOAK)

        assert profile.growth == GrowthWindow(start_s=100.0, end_s=100.0, peak_temp_c=900.0)
        assert profile.growth.mid_s == 100.0

    def test_a_heater_off_at_zero_cuts_the_window_like_any_other(self, tmp_path):
        """A heater off at t=0 is off, not absent.

        This used to be a latent bug held deliberately: the cutoff was a
        truthiness test where `build_temp_trace` used `is not None`, so
        `heater_off_t == 0.0` disabled the cutoff entirely and this recipe
        reported a window starting at 100 s -- after the heater had gone off.
        The exact-peak rule tests `is not None` throughout, so the window now
        ends at 0, which is before it could start, and there is none.
        """
        profile = _profile(tmp_path, "Heater Soak,0,--\nHeater Ramp,900,100\nWait,Sec,600\n")

        assert profile.timeline.heater_off_t == 0.0
        assert profile.growth.start_s is None

    def test_an_empty_recipe_has_no_window_at_all(self):
        # The lone origin vertex is its own peak, but 25 C never clears the
        # ambient floor -- so a recipe that never heats reports no growth
        # rather than "growth at room temperature for the whole run".
        profile = build_profile([])

        assert profile.growth == GrowthWindow(start_s=None, end_s=None, peak_temp_c=25.0)


class TestGrowthWindowProperties:
    def test_the_midpoint_is_the_arithmetic_mean(self):
        assert GrowthWindow(start_s=190.0, end_s=310.0, peak_temp_c=850.0).mid_s == 250.0

    @pytest.mark.parametrize("start,end", [(None, 310.0), (190.0, None), (None, None)])
    def test_a_missing_edge_leaves_no_midpoint(self, start, end):
        assert GrowthWindow(start_s=start, end_s=end, peak_temp_c=850.0).mid_s is None

    def test_duration_is_zero_rather_than_none_with_no_window(self):
        # Which is why callers must test start_s or mid_s for existence: a
        # genuine zero-length window reads identically here.
        no_window = GrowthWindow(start_s=None, end_s=None, peak_temp_c=850.0)
        zero_length = GrowthWindow(start_s=100.0, end_s=100.0, peak_temp_c=850.0)

        assert no_window.duration_min == 0.0
        assert zero_length.duration_min == 0.0


class TestGetSpecies:
    @pytest.mark.parametrize(
        "channel,expected",
        [
            ("MFC-1 Ar", "Ar"),
            ("MFC-8 H2Se", "H2Se"),
            ("PC-1", "PC-1"),          # single token: the controllers carry no gas
            ("MFC-3 O2 high", "high"),  # the *last* token, not the second
            ("", ""),
            (" ", " "),                 # " ".split() is [], so the input comes back
        ],
    )
    def test_the_gas_a_channel_name_names(self, channel, expected):
        assert get_species(channel) == expected


class TestSpeciesValueAtGrowthMid:
    @pytest.mark.parametrize(
        "species,expected",
        [("H2Se", 3.5), ("Ar", 20.0), ("O2", 0.1), ("H2", None), ("Xx", None)],
    )
    def test_the_setpoint_holding_at_the_midpoint(self, tmp_path, species, expected):
        profile = _profile(tmp_path, RUNCARD_FULL)

        assert species_value_at_growth_mid(profile, species) == expected

    def test_the_earliest_touched_channel_is_the_one_reported(self, tmp_path):
        # VBBE00 runs Ar on MFC-1, 2, 4 and 5; a species lookup reports the
        # first line touched and says nothing about the carrier flows on the
        # others. Summing them would invent a flow nobody set.
        profile = _profile(tmp_path, "MFC/PC,MFC-4 Ar,170\nMFC/PC,MFC-1 Ar,20\n" + _GROWTH_TAIL)

        assert species_value_at_growth_mid(profile, "Ar") == 170.0

    def test_an_idle_purge_flow_does_not_count_as_flowing(self, tmp_path):
        # Real runcards idle a channel at exactly 0.01 sccm and the floor is
        # strict, so the idle value is reported as nothing rather than as a gas.
        profile = _profile(tmp_path, "MFC/PC,MFC-3 O2,0.01\n" + _GROWTH_TAIL)

        assert species_value_at_growth_mid(profile, "O2") is None

    def test_no_growth_window_answers_for_no_species(self, tmp_path):
        profile = _profile(tmp_path, "MFC/PC,MFC-8 H2Se,3.5\n" + _NO_WINDOW)

        assert species_value_at_growth_mid(profile, "H2Se") is None


class TestChannelValueAtGrowthMid:
    def test_a_named_channel_answers_for_itself_not_for_its_species(self, tmp_path):
        profile = _profile(tmp_path, "MFC/PC,MFC-4 Ar,170\nMFC/PC,MFC-1 Ar,20\n" + _GROWTH_TAIL)

        assert channel_value_at_growth_mid(profile, "MFC-1 Ar") == 20.0

    def test_a_setpoint_holds_until_the_next_command_for_that_channel(self, tmp_path):
        # PC-1 is set to 400 at t=10 and to 300 at t=70 by an Accumulation row;
        # the midpoint is 250, so the answer is the second one.
        profile = _profile(tmp_path, RUNCARD_FULL)

        assert channel_value_at_growth_mid(profile, "PC-1") == 300.0

    def test_each_side_of_the_prefix_keeps_its_own_floor(self, tmp_path):
        # The same 0.01 is an idle purge on a gas line and a real setpoint on a
        # controller, which is why the two floors are not one constant.
        profile = _profile(tmp_path, "MFC/PC,MFC-3 O2,0.01\nMFC/PC,PC-2,0.01\n" + _GROWTH_TAIL)

        assert channel_value_at_growth_mid(profile, "MFC-3 O2") is None
        assert channel_value_at_growth_mid(profile, "PC-2") == 0.01

    def test_a_channel_the_recipe_never_mentions_is_none(self, tmp_path):
        profile = _profile(tmp_path, RUNCARD_FULL)

        assert channel_value_at_growth_mid(profile, "MFC-7 H2") is None


class TestFixedChannelValueAtGrowthMid:
    @pytest.mark.parametrize(
        "channel_id,expected",
        [
            ("Heater", 850.0),
            ("P1", 40.0),
            ("P2", 50.0),
            ("PC-1", 300.0),
            ("PC-2", 140.0),
            ("RTV", 60.0),
            ("Spin", 10.0),
        ],
    )
    def test_the_seven_non_gas_channels(self, tmp_path, channel_id, expected):
        profile = _profile(tmp_path, RUNCARD_FULL)

        assert fixed_channel_value_at_growth_mid(profile, channel_id) == expected

    def test_heater_is_the_run_wide_peak_and_not_a_sample(self, tmp_path):
        # "Heater" answers with the run's peak, which is the number a grower
        # quotes for the run, rather than whatever the reconstructed trace
        # happens to read at the window's midpoint. Here the ramp-down has
        # already begun by the midpoint, so a sampled value would be lower.
        profile = _profile(
            tmp_path,
            "Heater Ramp,900,100\nWait,Sec,100\nWait,Sec,100\n"
            "Heater Ramp,600,100\nWait,Sec,100\nHeater Soak,0,--\nWait,Sec,600\n",
        )
        sampled = [temp for t, temp in profile.temp_trace if t <= profile.growth.mid_s][-1]

        assert fixed_channel_value_at_growth_mid(profile, "Heater") == 900.0
        assert profile.growth.start_s is not None
        assert sampled == 900.0

    def test_a_preheater_at_zero_is_a_reading_not_an_absence(self, tmp_path):
        # P1/P2 have no floor, unlike PC/RTV/Spin: those three are off when
        # they read zero, a preheater at 0 degrees is a measurement.
        profile = _profile(
            tmp_path,
            "P1_Heater Ramp,0,10\nWait,Sec,20\nHeater Ramp,900,10\nWait,Sec,10\n"
            "Heater Soak,0,--\nWait,Sec,60\n",
        )

        assert fixed_channel_value_at_growth_mid(profile, "P1") == 0.0

    @pytest.mark.parametrize("channel_id", ["PC-1", "RTV", "Spin"])
    def test_a_zero_on_the_controllers_reads_as_off(self, tmp_path, channel_id):
        profile = _profile(
            tmp_path,
            "MFC/PC,PC-1,0\nRTV Pressure Ctrl,100 Torr,0\nStage Rot,0,--\n" + _GROWTH_TAIL,
        )

        assert fixed_channel_value_at_growth_mid(profile, channel_id) is None

    def test_a_channel_a_recipe_never_set_is_none(self, tmp_path):
        profile = _profile(tmp_path, RUNCARD_SHORT)

        assert fixed_channel_value_at_growth_mid(profile, "P1") is None
        assert fixed_channel_value_at_growth_mid(profile, "RTV") is None

    def test_an_unknown_channel_id_raises(self, tmp_path):
        # A typo in a channel id is a programming error; a silent None would
        # show as a cell indistinguishable from a channel nobody used.
        profile = _profile(tmp_path, RUNCARD_FULL)

        with pytest.raises(ValueError, match="Unknown fixed channel"):
            fixed_channel_value_at_growth_mid(profile, "PC-3")

    def test_an_unknown_channel_id_stays_silent_when_there_is_no_window(self, tmp_path):
        # The `mid is None` gate runs before the dispatch, so the raise cannot
        # be relied on to catch a typo on every file. Reproduced as-is.
        profile = _profile(tmp_path, _NO_WINDOW)

        assert fixed_channel_value_at_growth_mid(profile, "PC-3") is None


class TestPulseAdvancesTheClock:
    """`Pulse H2Se,N,half` is the growth step of every recipe since mid-2025.

    The vendored reference ignores it, which put the growth window of 44 real
    recipes at a single instant: heater-off landed on the same second the
    peak was reached, and the page read "Growth (0 min @ 890 C)" on a
    13-minute growth.
    """

    def test_a_pulse_costs_count_times_on_plus_off_plus_switching(self, tmp_path):
        from modules.runcard.processing.growth_window import PULSE_SWITCH_OVERHEAD_SEC

        timeline = build_timeline(_commands(tmp_path, "Pulse H2Se,72,5\nWait,Sec,10\n"))

        assert timeline.total_time == pytest.approx(72 * (10 + PULSE_SWITCH_OVERHEAD_SEC) + 10)
        assert timeline.mfc_events == []   # the controller toggles the gas, not MFC/PC rows

    @pytest.mark.parametrize("row", ["Pulse,6,30", "Pulse O2,6,30"])
    def test_every_pulse_spelling_is_on_the_clock(self, tmp_path, row):
        from modules.runcard.processing.growth_window import PULSE_SWITCH_OVERHEAD_SEC

        assert build_timeline(_commands(tmp_path, row + "\n")).total_time == pytest.approx(
            6 * (60 + PULSE_SWITCH_OVERHEAD_SEC)
        )

    def test_a_malformed_pulse_row_contributes_nothing(self, tmp_path):
        assert build_timeline(_commands(tmp_path, "Pulse H2Se,--,--\nWait,Sec,10\n")).total_time == 10.0

    def test_the_growth_window_spans_the_pulsed_growth(self, tmp_path):
        from modules.runcard.processing.growth_window import PULSE_SWITCH_OVERHEAD_SEC

        # Ramp to 890 over 60 s, grow by pulsing, heater off: the window is
        # the pulse block, not the instant the ramp finished.
        profile = _profile(
            tmp_path,
            "Heater Ramp,890,60\nWait,Sec,60\nPulse H2Se,72,5\nHeater Soak,0,--\nWait,Sec,60\n",
        )

        assert profile.growth.start_s == 60.0
        assert profile.growth.end_s == pytest.approx(60 + 72 * (10 + PULSE_SWITCH_OVERHEAD_SEC))
        assert profile.growth.duration_min == pytest.approx(72 * (10 + PULSE_SWITCH_OVERHEAD_SEC) / 60)


def _cooled(from_c, seconds):
    """The synthesized cooldown, `seconds` after heater-off, from `from_c`."""
    return ROOM_TEMP_C + (from_c - ROOM_TEMP_C) * math.exp(-seconds / 1500)


class TestNothingAfterHeaterOffReachesTheTrace:
    """A ramp the heater was given after it went off never acted (v5.23.0).

    Every ramp used to be laid down whole and the cooldown started from the
    last one's target, so 34 HA1N01 cards with a `Heater Ramp` after the soak
    drew a trace that ran backwards in time, and a VB1P01 card cut part-way
    up a ramp to 850 reported an 850 peak it never reached.
    """

    def test_a_ramp_issued_after_heater_off_is_ignored(self, tmp_path):
        profile = _profile(
            tmp_path,
            "Heater Ramp,900,100\nWait,Sec,100\nHeater Soak,0,--\nWait,Sec,60\n"
            "Heater Ramp,500,30\nWait,Sec,60\n",
        )

        trace = profile.temp_trace
        assert [t for t, _temp in trace] == [0.0, 100.0, 160.0, 220.0]
        assert trace[2][1] == pytest.approx(_cooled(900.0, 60))
        assert trace[3][1] == pytest.approx(_cooled(900.0, 120))

    def test_the_trace_never_runs_backwards_after_heater_off(self, tmp_path):
        # The NSHA1N012506035 shape before the soak setpoint was understood:
        # heater off, then a ramp issued and waited out.
        profile = _profile(
            tmp_path,
            "Heater Ramp,900,100\nWait,Sec,100\nHeater Soak,0,--\nWait,Sec,900\n"
            "Heater Ramp,900,600\nWait,Sec,600\n",
        )

        times = [t for t, _temp in profile.temp_trace]
        assert times == sorted(times)
        assert max(temp for t, temp in profile.temp_trace if t > 100.0) < 900.0

    def test_a_ramp_still_running_at_heater_off_is_cut_at_the_temperature_reached(self, tmp_path):
        # 200 -> 850 over 200 s from t=100, heater cut at t=200: half way, 525.
        profile = _profile(
            tmp_path,
            "Heater Ramp,200,100\nWait,Sec,100\nHeater Ramp,850,200\nWait,Sec,100\n"
            "Heater Soak,0,--\nWait,Sec,60\n",
        )

        assert profile.temp_trace[:3] == [(0.0, 25.0), (100.0, 200.0), (200.0, 525.0)]
        assert profile.temp_trace[3][1] == pytest.approx(_cooled(525.0, 60))
        assert profile.growth.peak_temp_c == 525.0

    def test_a_heater_cut_mid_ramp_has_a_zero_length_window_at_heater_off(self, tmp_path):
        # The peak is the instant the heater was cut, so the window is that
        # instant -- the edges the reference draws, at the temperature reached.
        profile = _profile(
            tmp_path,
            "Heater Ramp,850,200\nWait,Sec,100\nHeater Soak,0,--\nWait,Sec,60\n",
        )

        assert profile.growth == GrowthWindow(start_s=100.0, end_s=100.0, peak_temp_c=437.5)

    def test_build_temp_trace_cuts_a_running_ramp_directly(self):
        ramps = [(0.0, 25.0, 825.0, 100.0), (150.0, 825.0, 25.0, 50.0)]

        trace = build_temp_trace(ramps, 25.0, 25.0)

        assert trace == [(0.0, 25.0), (25.0, 225.0)]

    def test_a_ramp_completing_exactly_at_heater_off_is_whole(self):
        trace = build_temp_trace([(0.0, 25.0, 825.0, 100.0)], 100.0, 160.0)

        assert trace[:2] == [(0.0, 25.0), (100.0, 825.0)]
        assert trace[2][1] == pytest.approx(_cooled(825.0, 60))


class TestANonzeroHeaterSoakIsASetpointStep:
    """`Heater Soak,600,--` steps the heater to 600 and holds; only a zero soak is off.

    The HA1P01 datalogs show the SV going to 600 and staying there, and the
    `NSHA1N012506029`-`036` cards then ramp back to 900 for a second growth
    before a final `Heater Soak,0`. The vendored reference reads every first
    soak as heater-off; this is a deliberate departure (docs/reference/README.md).
    """

    def test_a_nonzero_soak_is_a_zero_duration_step_and_not_heater_off(self, tmp_path):
        timeline = build_timeline(_commands(
            tmp_path, "Heater Ramp,900,100\nWait,Sec,100\nHeater Soak,600,--\nWait,Sec,50\n",
        ))

        assert timeline.heater_ramps == [(0.0, 25.0, 900.0, 100.0), (100.0, 900.0, 600.0, 0.0)]
        assert timeline.heater_off_t is None

    @pytest.mark.parametrize("row", ["Heater Soak,0,--", "Heater Soak,0,600", "Heater Soak,--,--"])
    def test_a_zero_or_unreadable_soak_is_heater_off(self, tmp_path, row):
        timeline = build_timeline(_commands(tmp_path, f"Wait,Sec,10\n{row}\nWait,Sec,10\n"))

        assert timeline.heater_off_t == 10.0
        assert timeline.heater_ramps == []

    def test_the_next_ramp_starts_from_the_soak_setpoint(self, tmp_path):
        timeline = build_timeline(_commands(
            tmp_path, "Heater Soak,600,--\nWait,Sec,10\nHeater Ramp,900,60\n",
        ))

        assert timeline.heater_ramps[-1] == (10.0, 600.0, 900.0, 60.0)

    def test_a_two_step_recipe_grows_in_the_first_stretch_at_the_peak(self, tmp_path):
        # Ramp to 900, grow 200 s, step to 600 and hold 150 s, ramp back to 900
        # over 60 s, hold 200 s, heater off. The window is the first plateau,
        # ended by the step down -- the documented "whichever comes first of
        # heater-off and a commanded ramp-down".
        profile = _profile(
            tmp_path,
            "Heater Ramp,900,100\nWait,Sec,100\nWait,Sec,200\nHeater Soak,600,--\nWait,Sec,150\n"
            "Heater Ramp,900,60\nWait,Sec,60\nWait,Sec,200\nHeater Soak,0,--\nWait,Sec,60\n",
        )

        assert profile.timeline.heater_off_t == 710.0
        assert profile.temp_trace[:7] == [
            (0.0, 25.0), (100.0, 900.0), (300.0, 900.0), (300.0, 600.0),
            (450.0, 600.0), (510.0, 900.0), (710.0, 900.0),
        ]
        assert profile.temp_trace[7][1] == pytest.approx(_cooled(900.0, 60))
        assert profile.growth == GrowthWindow(start_s=100.0, end_s=300.0, peak_temp_c=900.0)
