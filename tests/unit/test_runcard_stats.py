"""Unit tests for modules.runcard.processing.stats (the numbers and rows behind the runcard chart)."""

import pytest

from modules.runcard.io.parser import parse_runcard
from modules.runcard.processing.growth_window import (
    GrowthWindow,
    RuncardProfile,
    Timeline,
    build_profile,
)
from modules.runcard.processing.stats import (
    BAR_ROW_PX,
    CANVAS_BASE_PX,
    MFC_BAR_FLOOR,
    build_bar_rows,
    canvas_height,
    gantt_row_slots,
    gas_events,
    growth_mid_values,
    interp_temp,
    profile_stats,
)
from tests.datalog_fixtures import RUNCARD_FULL, RUNCARD_SHORT

#: See test_runcard_growth_window: a run with a real window (mid = 20 s).
_GROWTH_TAIL = "Wait,Sec,10\nHeater Ramp,900,10\nWait,Sec,10\nHeater Soak,0,--\nWait,Sec,60\n"

#: The final ramp completes after the heater went off, so there is no window --
#: and every value read at the midpoint has to say so.
_NO_WINDOW = "MFC/PC,PC-1,400\nWait,Sec,10\nHeater Ramp,900,10\nHeater Soak,0,--\nWait,Sec,60\n"


def _profile(tmp_path, text, name="recipe.csv"):
    path = tmp_path / name
    path.write_text(text, encoding="utf-8", newline="")
    return build_profile(parse_runcard(str(path)))


def _row(rows, label):
    return next(row for row in rows if row.label == label)


class TestInterpTemp:
    def test_it_interpolates_between_vertices(self):
        # A ramp is two vertices and the line between them, so a marker part-way
        # up one has to be placed on that line or it sits visibly off it.
        assert interp_temp([(0.0, 25.0), (10.0, 25.0), (20.0, 825.0)], 15.0) == 425.0

    @pytest.mark.parametrize("t,expected", [(-5.0, 25.0), (0.0, 25.0), (99.0, 825.0)])
    def test_it_clamps_outside_the_traces_own_span(self, t, expected):
        # The right-hand clamp is the one that matters: a recipe with no Heater
        # Soak has a trace that stops at its final ramp while the run continues.
        assert interp_temp([(0.0, 25.0), (20.0, 825.0)], t) == expected

    def test_a_zero_length_segment_does_not_divide_by_zero(self):
        # Two vertices share a timestamp whenever simultaneous commands produce
        # one, which is normal in these recipes.
        assert interp_temp([(5.0, 1.0), (5.0, 2.0), (9.0, 3.0)], 5.0) == 1.0

    def test_an_empty_trace_is_zero(self):
        assert interp_temp([], 5.0) == 0.0


class TestBuildBarRows:
    def test_rows_are_stacked_gases_then_the_valve_then_pressure_then_the_stage(self, tmp_path):
        # Gases lead because they are what the markers above the temperature
        # trace refer to; the stage is last because it is usually one segment
        # across the whole run.
        profile = _profile(tmp_path, RUNCARD_FULL)

        rows = build_bar_rows(profile.timeline)

        assert [row.label for row in rows] == [
            "MFC-1 Ar", "MFC-3 O2", "MFC-8 H2Se", "RTV P", "PC-1", "PC-2", "Spin",
            "Inner tube (MFC1+3+4)", "Outer tube (MFC6+7+8)",
        ]
        assert [row.kind for row in rows] == [
            "gas", "gas", "gas", "rtv", "pc", "pc", "spin", "tube", "tube",
        ]

    def test_a_segment_runs_until_that_channels_next_command(self, tmp_path):
        # PC-1 is set to 400 at t=10 and to 300 at t=70 by an Accumulation row;
        # the last segment runs to the end of the run, because a setpoint is
        # never cancelled by the recipe, only replaced.
        profile = _profile(tmp_path, RUNCARD_FULL)

        segments = _row(build_bar_rows(profile.timeline), "PC-1").segments

        assert [(s.start_s, s.end_s, s.value) for s in segments] == [
            (10.0, 70.0, 400.0),
            (70.0, 490.0, 300.0),
        ]

    def test_a_channel_commanded_only_below_the_floor_keeps_an_empty_row(self, tmp_path):
        # Dropping the row would make "commanded closed" and "never mentioned"
        # look identical, and the operator needs to see that the recipe
        # addressed the line and left it shut.
        profile = _profile(tmp_path, f"MFC/PC,MFC-2 Ar,{MFC_BAR_FLOOR}\nWait,Sec,10\n")

        rows = build_bar_rows(profile.timeline)

        assert [row.label for row in rows][0] == "MFC-2 Ar"
        assert rows[0].segments == ()

    def test_the_bar_floor_is_strict(self, tmp_path):
        profile = _profile(tmp_path, "MFC/PC,MFC-7 H2,0.06\nWait,Sec,10\n")

        assert len(build_bar_rows(profile.timeline)[0].segments) == 1

    def test_the_valve_and_the_stage_are_dropped_when_the_recipe_never_set_them(self, tmp_path):
        # There is one throttle valve and one stage, so an empty row would say
        # "this reactor has a stage" rather than anything about this recipe.
        profile = _profile(tmp_path, RUNCARD_SHORT)

        labels = [row.label for row in build_bar_rows(profile.timeline)]

        assert labels[:3] == ["MFC-1 Ar", "PC-1", "Spin"]
        assert "RTV P" not in labels

    def test_the_unit_travels_on_the_row(self, tmp_path):
        # The RTV row is in Torr like the other pressure rows: its number is
        # params[1] of RTV Pressure Ctrl, the commanded chamber-pressure
        # setpoint, where params[0] is the gauge range.
        profile = _profile(tmp_path, RUNCARD_FULL)
        rows = build_bar_rows(profile.timeline)

        assert _row(rows, "MFC-1 Ar").unit == "sccm"
        assert _row(rows, "PC-1").unit == "Torr"
        assert _row(rows, "Spin").unit == "rpm"
        assert _row(rows, "RTV P").unit == "Torr"

    def test_the_valve_row_is_labelled_rtv_p_in_torr(self, tmp_path):
        # params[1] of RTV Pressure Ctrl is the chamber-pressure setpoint the
        # recipe commands, in Torr -- params[0] is the gauge range. This app
        # briefly drew the row as "RTV" with no unit, on the reasoning that the
        # number matched neither the range nor the measured pressure; a setpoint
        # is simply allowed to differ from what the chamber settles at.
        profile = _profile(tmp_path, RUNCARD_FULL)

        assert "RTV P" in [row.label for row in build_bar_rows(profile.timeline)]


class TestGasEvents:
    def test_a_valve_crossing_the_floor_in_either_direction_is_marked(self, tmp_path):
        # RUNCARD_FULL's reactive gas opens at 190 and closes at 310, which are
        # this recipe's growth-window edges -- so both are suppressed and the
        # one marker left is the O2 pulse before the run heats. See
        # test_markers_inside_the_growth_window_are_suppressed.
        profile = _profile(tmp_path, RUNCARD_FULL)

        events = gas_events(profile)

        assert [(e.t_s, e.species, e.direction) for e in events] == [(10.0, "O2", "on")]

    def test_markers_inside_the_growth_window_are_suppressed(self, tmp_path):
        """The band is already shaded and captioned with its own chemistry.

        A marker there repeats the caption, and the reactive gases all switch
        within seconds of the window's edges -- which is exactly where labels
        pile on top of each other.
        """
        profile = _profile(tmp_path, RUNCARD_FULL)

        assert profile.growth.start_s == 190.0
        assert all(
            not (190.0 - 30 < e.t_s < 310.0 + 30) for e in gas_events(profile)
        )

    def test_the_state_machine_still_runs_through_a_suppressed_command(self, tmp_path):
        # Suppressing means "emit no marker", never "ignore the command": a
        # valve opened inside the window and closed well outside it still
        # reports the close, which it could not do if the open had been skipped.
        profile = _profile(
            tmp_path,
            "Wait,Sec,10\nHeater Ramp,900,10\nWait,Sec,10\nMFC/PC,MFC-8 H2Se,3\n"
            "Heater Soak,0,--\nWait,Sec,600\nMFC/PC,MFC-8 H2Se,0\nWait,Sec,10\n",
        )

        events = gas_events(profile)

        assert [(e.t_s, e.direction) for e in events] == [(620.0, "off")]

    def test_the_temperature_is_interpolated_along_the_ramp(self, tmp_path):
        # A held lookup would place this marker at 25 degrees, on the plateau
        # below the line it is annotating. The valve moves at t=20, mid-ramp
        # and well clear of the window at 120.
        profile = _profile(
            tmp_path,
            "Wait,Sec,10\nHeater Ramp,900,110\nWait,Sec,10\nMFC/PC,MFC-8 H2Se,3\n"
            "Wait,Sec,100\nHeater Soak,0,--\nWait,Sec,60\n",
        )

        events = gas_events(profile)

        assert [(e.t_s, round(e.temp_c, 1)) for e in events] == [(20.0, 104.5)]

    def test_the_carrier_gases_are_never_marked(self, tmp_path):
        # Ar and N2 are open for most of a run on several lines at once, so
        # marking their transitions buries the reactive-gas markers.
        profile = _profile(
            tmp_path,
            "MFC/PC,MFC-1 Ar,20\nMFC/PC,MFC-5 N2,5\nWait,Sec,10\n"
            "MFC/PC,MFC-1 Ar,0\nMFC/PC,MFC-5 N2,0\nWait,Sec,10\n",
        )

        assert gas_events(profile) == []

    def test_a_command_at_time_zero_sets_the_state_without_marking(self, tmp_path):
        # A recipe opens its carrier lines in its first block; marking those
        # stacks a pile of labels at the left edge before the run has started.
        # The state still moves, so the later close is marked -- placed well
        # after the window here so the close is not suppressed with it.
        profile = _profile(
            tmp_path,
            "MFC/PC,MFC-8 H2Se,3\nWait,Sec,10\nHeater Ramp,900,10\nWait,Sec,10\n"
            "Heater Soak,0,--\nWait,Sec,600\nMFC/PC,MFC-8 H2Se,0\nWait,Sec,10\n",
        )

        events = gas_events(profile)

        assert [(e.t_s, e.direction) for e in events] == [(620.0, "off")]


class TestGrowthMidValues:
    def test_every_channel_holding_at_the_midpoint(self, tmp_path):
        profile = _profile(tmp_path, RUNCARD_FULL)

        values = growth_mid_values(profile)

        assert values.mid_s == 250.0
        assert values.peak_temp_c == 850.0
        assert values.mfc == (("MFC-1 Ar", 20.0), ("MFC-3 O2", 0.1), ("MFC-8 H2Se", 3.5))
        assert values.pc == (("PC-1", 300.0), ("PC-2", 140.0))
        assert (values.rtv, values.spin, values.p1, values.p2) == (60.0, 10.0, 40.0, 50.0)

    def test_a_channel_below_its_floor_is_left_out_entirely(self, tmp_path):
        # The summary lists what was running and says nothing about what was
        # shut, so an idling line is absent rather than present at 0.01.
        profile = _profile(tmp_path, "MFC/PC,MFC-3 O2,0.01\nMFC/PC,MFC-8 H2Se,3.5\n" + _GROWTH_TAIL)

        values = growth_mid_values(profile)

        assert values.mfc == (("MFC-8 H2Se", 3.5),)

    def test_no_growth_window_empties_everything_but_the_peak(self, tmp_path):
        profile = _profile(tmp_path, _NO_WINDOW)

        values = growth_mid_values(profile)

        assert values.mid_s is None
        assert values.peak_temp_c == 900.0
        assert values.mfc == ()
        assert values.pc == ()
        assert (values.rtv, values.spin, values.p1, values.p2) == (None, None, None, None)


class TestProfileStats:
    def test_every_key_for_a_full_recipe(self, tmp_path):
        profile = _profile(tmp_path, RUNCARD_FULL)

        stats = profile_stats(profile)

        assert set(stats) == {
            "total_min", "peak_T", "gw_start_min", "gw_end_min", "gw_dur", "p1_T", "p2_T", "canvas_h",
        }
        assert stats["total_min"] == pytest.approx(490 / 60)
        assert stats["peak_T"] == 850.0
        assert stats["gw_start_min"] == pytest.approx(190 / 60)
        assert stats["gw_end_min"] == pytest.approx(310 / 60)
        assert stats["gw_dur"] == 2.0
        assert stats["p1_T"] == 40.0
        assert stats["p2_T"] == 50.0
        # Seven commanded rows and the two tube sums.
        assert stats["canvas_h"] == CANVAS_BASE_PX + BAR_ROW_PX * 9

    def test_the_clock_is_the_recipes_own_and_not_wall_clock(self, tmp_path):
        # RUNCARD_FULL holds two Pumping Forward commands, which contribute
        # nothing here; in the VBBE00 datalog they occupy ~14 real minutes.
        profile = _profile(tmp_path, RUNCARD_FULL)

        assert profile_stats(profile)["total_min"] == pytest.approx(profile.timeline.total_time / 60)

    def test_the_window_keys_are_none_when_there_is_no_window(self, tmp_path):
        # The `is not None` guards the ancestor wrote as truthiness tests: a
        # window starting at exactly t=0 got a drawn band and a printed
        # duration beside an empty "Growth window" metric.
        profile = _profile(tmp_path, _NO_WINDOW)

        stats = profile_stats(profile)

        assert stats["gw_start_min"] is None
        assert stats["gw_end_min"] is None
        assert stats["peak_T"] == 900.0

    def test_a_window_starting_at_time_zero_still_reports_its_edges(self):
        """The guard the ancestor wrote as `if gw_start` rather than `is not None`.

        A window whose start is 0.0 is a real window, and truthiness reported
        it as absent beside a band that was drawn anyway. The profile is built
        by hand because no recipe can now produce one: every reconstructed
        trace starts at room temperature, and the ambient floor means a peak
        at t=0 is not a peak. The guard still has to be right.
        """
        timeline = Timeline(
            total_time=60.0, heater_ramps=[], p1_ramps=[], p2_ramps=[],
            mfc_events=[], pc_events=[], rtv_events=[], spin_events=[],
            pump_events=[], heater_off_t=None,
        )
        profile = RuncardProfile(
            timeline=timeline,
            temp_trace=[(0.0, 900.0), (60.0, 900.0)],
            p1_trace=[], p2_trace=[],
            growth=GrowthWindow(start_s=0.0, end_s=0.0, peak_temp_c=900.0),
        )

        stats = profile_stats(profile)

        assert stats["gw_start_min"] == 0.0
        assert stats["gw_end_min"] == 0.0

    def test_the_duration_is_zero_rather_than_none_with_no_window(self, tmp_path):
        # It comes straight off GrowthWindow.duration_min, so gw_start_min is
        # what a caller reads to decide whether to show it at all.
        profile = _profile(tmp_path, _NO_WINDOW)

        assert profile_stats(profile)["gw_dur"] == 0.0

    def test_the_preheater_temperatures_are_end_of_run_values(self, tmp_path):
        # An aux trace holds its last setpoint out to `total`, so this is its
        # last vertex and not a midpoint sample.
        profile = _profile(tmp_path, RUNCARD_FULL)

        assert profile_stats(profile)["p1_T"] == profile.p1_trace[-1][1]

    def test_a_recipe_with_no_preheaters_has_no_preheater_temperatures(self, tmp_path):
        profile = _profile(tmp_path, RUNCARD_SHORT)

        stats = profile_stats(profile)

        assert stats["p1_T"] is None
        assert stats["p2_T"] is None

    def test_the_canvas_grows_one_row_at_a_time(self, tmp_path):
        # Height and the bar region are both derived from BAR_ROW_PX, or the
        # rows crowd as a recipe adds channels.
        # Three commanded rows and the two tube sums.
        five_rows = _profile(tmp_path, RUNCARD_SHORT, "short.csv")

        assert profile_stats(five_rows)["canvas_h"] == CANVAS_BASE_PX + BAR_ROW_PX * 5

    def test_a_rowless_recipe_still_reports_one_rows_worth_of_canvas(self, tmp_path):
        # build_profile_figure reserves a row for its "no commands in this
        # recipe" note, so such a figure is 530 px tall. canvas_h reported 500,
        # and the page hands that number to the layout and to the PNG exporter
        # -- so the export was sized 30 px short of the figure it exported.
        no_rows = _profile(tmp_path, "Heater Ramp,900,10\nWait,Sec,10\n", "bare.csv")

        assert profile_stats(no_rows)["canvas_h"] == CANVAS_BASE_PX + BAR_ROW_PX

    def test_the_keys_the_page_formats_without_a_none_check(self, tmp_path):
        # pages/5_Runcard.py formats total_min, peak_T and canvas_h straight
        # into an f-string; the window keys and the preheaters it guards. The
        # worst case for the three unguarded ones is a recipe with no growth
        # window, no completed ramp and no preheater: peak_T falls back to room
        # temperature rather than to None, which is why the metric prints.
        stats = profile_stats(_profile(tmp_path, _NO_WINDOW))

        assert stats["total_min"] is not None
        assert stats["peak_T"] == 900.0
        assert stats["canvas_h"] is not None
        assert (stats["gw_start_min"], stats["p1_T"], stats["p2_T"]) == (None, None, None)

    def test_a_recipe_with_no_heater_command_at_all_still_has_a_peak(self, tmp_path):
        # peak_T is documented as never None and never below 25, because the
        # origin vertex is always there. The page's metric has no fallback for
        # a None and would raise formatting one.
        stats = profile_stats(_profile(tmp_path, "MFC/PC,MFC-1 Ar,20\nWait,Sec,30\n"))

        assert stats["peak_T"] == 25.0


class TestCanvasHeight:
    def test_one_row_of_height_per_row(self):
        assert canvas_height([]) == CANVAS_BASE_PX + BAR_ROW_PX
        assert canvas_height([object()] * 4) == CANVAS_BASE_PX + BAR_ROW_PX * 4

    def test_the_slot_count_never_falls_below_one(self):
        # The figure builder and canvas_h both read this rather than each
        # applying their own floor, which is how they came to disagree.
        assert gantt_row_slots([]) == 1
        assert gantt_row_slots([object(), object()]) == 2


class TestTubeRows:
    #: MFC-1 and MFC-3 feed the inner tube in every series; MFC-2 and MFC-5 are
    #: the ones the series disagree on.
    _RECIPE = (
        "MFC/PC,MFC-1 Ar,100\n"
        "MFC/PC,MFC-2 Ar,30\n"
        "MFC/PC,MFC-5 Ar,20\n"
        "MFC/PC,MFC-6 Ar,40\n"
        "Wait,Sec,60\n"
        "MFC/PC,MFC-3 O2,5\n"
        "Wait,Sec,60\n"
        "MFC/PC,MFC-3 O2,0\n"
        "Wait,Sec,60\n"
    )

    def _tubes(self, tmp_path, run_id):
        profile = _profile(tmp_path, self._RECIPE)
        rows = build_bar_rows(profile.timeline, run_id)
        return [
            (row.label, [(s.start_s, s.end_s, s.value) for s in row.segments])
            for row in rows if row.kind == "tube"
        ]

    def test_ha_sums_mfc_1_3_4_and_6_7_8(self, tmp_path):
        assert self._tubes(tmp_path, "HADI44") == [
            ("Inner tube (MFC1+3+4)", [(0.0, 60.0, 100.0), (60.0, 120.0, 105.0), (120.0, 180.0, 100.0)]),
            ("Outer tube (MFC6+7+8)", [(0.0, 180.0, 40.0)]),
        ]

    @pytest.mark.parametrize("run_id", ["VBCA08", "VABD14", "vbbe00"])
    def test_va_and_vb_sum_mfc_1_to_4_and_5_to_8(self, tmp_path, run_id):
        assert self._tubes(tmp_path, run_id) == [
            ("Inner tube (MFC1+2+3+4)", [(0.0, 60.0, 130.0), (60.0, 120.0, 135.0), (120.0, 180.0, 130.0)]),
            ("Outer tube (MFC5+6+7+8)", [(0.0, 180.0, 60.0)]),
        ]

    def test_an_unknown_series_falls_back_to_ha(self, tmp_path):
        assert [label for label, _ in self._tubes(tmp_path, "NX123")] == [
            "Inner tube (MFC1+3+4)", "Outer tube (MFC6+7+8)",
        ]

    def test_equal_neighbouring_totals_merge_and_zero_stretches_are_blank(self, tmp_path):
        # Two lines swapping 10 sccm at t=60 leave the sum unchanged, so that is
        # one bar; after both close, the row is blank rather than reading "0".
        profile = _profile(tmp_path, (
            "MFC/PC,MFC-1 Ar,10\nWait,Sec,60\n"
            "MFC/PC,MFC-1 Ar,0\nMFC/PC,MFC-4 H2,10\nWait,Sec,60\n"
            "MFC/PC,MFC-4 H2,0\nWait,Sec,60\n"
        ))
        inner = build_bar_rows(profile.timeline, "HA")[-2]

        assert [(s.start_s, s.end_s, s.value) for s in inner.segments] == [(0.0, 120.0, 10.0)]

    def test_the_row_count_does_not_depend_on_the_run_id(self, tmp_path):
        # profile_stats sizes the figure without knowing the run id.
        profile = _profile(tmp_path, self._RECIPE)

        assert len(build_bar_rows(profile.timeline, "VB")) == len(build_bar_rows(profile.timeline, "HA"))

    def test_a_recipe_with_no_gas_lines_gets_no_tube_rows(self, tmp_path):
        profile = _profile(tmp_path, "MFC/PC,PC-1,400\nWait,Sec,10\n")

        assert [row.label for row in build_bar_rows(profile.timeline)] == ["PC-1"]
