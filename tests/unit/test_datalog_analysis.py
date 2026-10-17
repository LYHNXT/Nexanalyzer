"""Unit tests for modules.datalog.processing.analysis (run statistics and tolerance violations)."""

import numpy as np
import pandas as pd
import pytest

from modules.datalog.io.scanner import detect_pv_sv_pairs, load_run
from modules.datalog.processing.analysis import (
    compute_all_violations,
    compute_deviation_pct,
    SV_FLOOR,
    compute_summary_stats,
    evaluable_rows,
    find_final_plateau_start,
    find_violation_mask,
    find_violation_segments,
    get_tolerance_pct,
    stale_lead_in_mask,
)
from tests.datalog_fixtures import write_datalog


def _pair_frame(sv, pv=None):
    """A one-pair frame at 1 Hz, for checking arithmetic without a CSV."""
    timestamps = pd.date_range("2026-08-06 17:33:12", periods=len(sv), freq="1s")
    return pd.DataFrame({
        "Time": timestamps,
        "X PV": sv if pv is None else pv,
        "X SV": sv,
    })


def _segments_for(df, tolerance_pct):
    """Every violation segment of the one pair in `df`."""
    deviation = compute_deviation_pct(df, "X PV", "X SV")
    mask = find_violation_mask(deviation, tolerance_pct)
    return find_violation_segments(df, "X", mask, deviation)


class TestGetTolerancePct:
    def test_the_global_default_answers_for_a_channel_with_no_override(self):
        thresholds = {"global_default_pct": 5.0, "overrides": {"Heater": 2.0}}

        assert get_tolerance_pct(thresholds, "MFC-1") == 5.0

    def test_an_override_replaces_the_global_rather_than_adjusting_it(self):
        thresholds = {"global_default_pct": 5.0, "overrides": {"Heater": 2.0}}

        assert get_tolerance_pct(thresholds, "Heater") == 2.0

    def test_the_override_key_is_the_pair_name_not_a_column_name(self):
        # Saved overrides are keyed on the pair name, so a column renamed in the
        # controller's log orphans the override rather than half-matching it.
        thresholds = {"global_default_pct": 5.0, "overrides": {"Heater PV": 2.0}}

        assert get_tolerance_pct(thresholds, "Heater") == 5.0

    def test_a_dict_with_no_overrides_key_still_resolves(self):
        # A thresholds.json written before per-channel overrides existed.
        assert get_tolerance_pct({"global_default_pct": 5.0}, "Heater") == 5.0

    def test_a_dict_with_no_global_default_is_a_programming_error(self):
        # Deliberately asymmetric with the line above: load_thresholds always
        # supplies this key, so a dict missing it was hand-built wrongly and
        # should say so instead of inventing a tolerance.
        with pytest.raises(KeyError):
            get_tolerance_pct({"overrides": {}}, "Heater")


class TestComputeSummaryStats:
    def test_rows_follow_the_order_the_caller_asked_for(self, tmp_path):
        df = load_run(write_datalog(tmp_path))

        stats = compute_summary_stats(df, ["Tube Pressure", "Heater PV"])

        assert stats["Channel"].tolist() == ["Tube Pressure", "Heater PV"]
        assert list(stats.columns) == ["Channel", "Min", "Max", "Avg"]

    def test_min_max_and_mean_come_from_the_same_population(self, tmp_path):
        df = load_run(write_datalog(tmp_path))

        stats = compute_summary_stats(df, ["Heater PV"])

        assert stats.loc[0, "Min"] == 88
        assert stats.loc[0, "Max"] == 850
        assert stats.loc[0, "Avg"] == pytest.approx(344.611111, abs=1e-6)

    def test_a_channel_this_run_does_not_carry_contributes_no_row(self, tmp_path):
        df = load_run(write_datalog(tmp_path))

        stats = compute_summary_stats(df, ["Heater PV", "MFC-7 PV"])

        assert stats["Channel"].tolist() == ["Heater PV"]

    def test_an_all_nan_channel_contributes_no_row_rather_than_a_row_of_nans(self):
        # A row of NaNs under a channel heading reads as a measurement that came
        # back zero; no row reads as what it is.
        df = _pair_frame([np.nan] * 4)

        stats = compute_summary_stats(df, ["X SV"])

        assert stats.empty

    def test_no_qualifying_channel_returns_a_frame_with_no_columns_at_all(self):
        # Callers have to test .empty before reaching for ["Channel"]; this is
        # why.
        stats = compute_summary_stats(_pair_frame([1.0, 2.0]), ["Nothing"])

        assert stats.empty
        assert list(stats.columns) == []


class TestComputeDeviationPct:
    def test_deviation_is_signed_relative_to_the_setpoint(self):
        df = _pair_frame([100.0, 100.0], pv=[105.0, 95.0])

        deviation = compute_deviation_pct(df, "X PV", "X SV")

        assert deviation.tolist() == [5.0, -5.0]

    def test_an_idle_channel_reports_nan_not_a_hundred_percent_excursion(self):
        # Every MFC logs SV = 0.000 for most of a run. Without this guard the
        # violations table is nothing but idle channels.
        df = _pair_frame([0.0, 0.0], pv=[0.0, 0.3])

        deviation = compute_deviation_pct(df, "X PV", "X SV")

        assert deviation.isna().all()

    def test_a_nan_setpoint_lands_in_the_same_branch_as_a_zero_one(self):
        df = _pair_frame([np.nan], pv=[42.0])

        assert compute_deviation_pct(df, "X PV", "X SV").isna().all()

    def test_a_negative_setpoint_still_produces_a_deviation(self):
        # The guard is on the absolute value, so a negative setpoint is a real
        # setpoint rather than an idle channel.
        df = _pair_frame([-10.0], pv=[-11.0])

        assert compute_deviation_pct(df, "X PV", "X SV").tolist() == [10.0]

    def test_the_result_keeps_the_frames_index_gaps_and_all(self, tmp_path):
        # _build_segment slices this series by label, so it has to be the same
        # index the frame left the scanner with.
        df = load_run(write_datalog(tmp_path)).drop(index=[3, 4])

        deviation = compute_deviation_pct(df, "MFC-1 PV", "MFC-1 SV")

        assert deviation.index.equals(df.index)


class TestFindViolationMask:
    def test_a_deviation_exactly_at_the_tolerance_is_in_tolerance(self):
        # A channel held at precisely its stated limit is not a finding.
        mask = find_violation_mask(pd.Series([5.0, -5.0, 5.0001]), 5.0)

        assert mask.tolist() == [False, False, True]

    def test_a_blanked_row_never_flags(self):
        mask = find_violation_mask(pd.Series([np.nan]), 0.0)

        assert mask.tolist() == [False]
        assert mask.dtype == bool


class TestFindViolationSegments:
    def test_a_single_sample_violation_reports_zero_duration(self):
        # Duration is the span between the first and last violating samples,
        # not sample count times the log period. At 1 Hz a five-sample
        # violation reads 4.0 s, and one sample reads 0.0.
        df = _pair_frame([10.0] * 3, pv=[10.0, 20.0, 10.0])

        segments = _segments_for(df, 5.0)

        assert len(segments) == 1
        assert segments[0]["Duration (s)"] == 0.0
        assert segments[0]["Start"] == segments[0]["End"]

    def test_a_segment_ends_at_the_last_flagged_row_not_the_row_that_closed_it(self):
        df = _pair_frame([10.0] * 5, pv=[10.0, 20.0, 20.0, 10.0, 10.0])

        segments = _segments_for(df, 5.0)

        assert segments[0]["End"] == pd.Timestamp("2026-08-06 17:33:14")
        assert segments[0]["Duration (s)"] == 1.0

    def test_a_violation_still_open_when_the_log_ends_is_reported(self):
        # The run whose heater never came back into band is exactly the one
        # worth reporting.
        df = _pair_frame([10.0] * 4, pv=[10.0, 10.0, 20.0, 20.0])

        segments = _segments_for(df, 5.0)

        assert len(segments) == 1
        assert segments[0]["End"] == pd.Timestamp("2026-08-06 17:33:15")

    def test_two_excursions_separated_by_one_clean_sample_stay_two_segments(self):
        df = _pair_frame([10.0] * 5, pv=[20.0, 20.0, 10.0, 20.0, 20.0])

        segments = _segments_for(df, 5.0)

        assert [s["Duration (s)"] for s in segments] == [1.0, 1.0]

    def test_max_deviation_is_a_magnitude_and_loses_the_sign(self):
        df = _pair_frame([10.0] * 3, pv=[10.0, 4.0, 10.0])

        segments = _segments_for(df, 5.0)

        assert segments[0]["Max Deviation (%)"] == pytest.approx(60.0)

    def test_a_clean_channel_produces_no_segments(self):
        df = _pair_frame([10.0] * 4)

        assert _segments_for(df, 5.0) == []


class TestComputeAllViolations:
    def test_every_pairs_segments_pool_into_one_chronological_table(self, tmp_path):
        df = load_run(write_datalog(tmp_path))
        pairs = detect_pv_sv_pairs(list(df.columns))

        violations, _masks = compute_all_violations(df, pairs, {"global_default_pct": 5.0})

        # The heater's 910 lead-in is the previous run's, so the fixture's
        # only finding is MFC-1's ramp (see the stale lead-in tests below).
        assert violations["Channel"].tolist() == ["MFC-1"]
        assert violations["Start"].tolist() == [pd.Timestamp("2026-08-06 17:33:20")]
        assert violations["Duration (s)"].tolist() == [2.0]

    def test_pairs_pool_in_start_order_across_channels(self):
        timestamps = pd.date_range("2026-08-06 17:33:12", periods=6, freq="1s")
        df = pd.DataFrame({
            "Time": timestamps,
            "A PV": [10, 10, 10, 10, 20, 20], "A SV": [10.0] * 6,
            "B PV": [10, 20, 10, 10, 10, 10], "B SV": [10.0] * 6,
        })

        violations, _masks = compute_all_violations(
            df, [("A", "A PV", "A SV"), ("B", "B PV", "B SV")], {"global_default_pct": 5.0}
        )

        assert violations["Channel"].tolist() == ["B", "A"]

    def test_the_heaters_inherited_setpoint_is_not_a_violation(self, tmp_path):
        # Heater PV sits at ~88 against the previous run's leftover SV of 910
        # until the recipe commands its own ramp. Before v5.23.0 that was
        # reported as 90.3 % out for 18 s; on HADI49 the same lead-in was
        # 844 s at 96.6 % -- the largest row in an 87-row table, and nothing
        # this run commanded.
        df = load_run(write_datalog(tmp_path))
        pairs = detect_pv_sv_pairs(list(df.columns))

        violations, masks = compute_all_violations(df, pairs, {"global_default_pct": 5.0})

        assert "Heater" not in set(violations["Channel"])
        _mask, deviation = masks["Heater"]
        assert deviation.iloc[:19].isna().all()      # the 910 block
        assert deviation.iloc[19:].notna().all()     # from the first command on

    def test_the_idle_ends_of_a_flow_channel_are_not_flagged(self, tmp_path):
        # MFC-1 sits at SV 0 for rows 1-8 and 30-36. Only the ramp against a
        # live setpoint is a finding.
        df = load_run(write_datalog(tmp_path))
        pairs = detect_pv_sv_pairs(list(df.columns))

        violations, _masks = compute_all_violations(df, pairs, {"global_default_pct": 5.0})

        mfc = violations[violations["Channel"] == "MFC-1"]
        assert len(mfc) == 1
        assert mfc.iloc[0]["Max Deviation (%)"] == pytest.approx(75.0)

    def test_a_clean_pair_still_gets_a_mask(self, tmp_path):
        # The chart needs the deviation series to draw a clean channel as clean.
        df = load_run(write_datalog(tmp_path))
        pairs = detect_pv_sv_pairs(list(df.columns))

        _violations, masks = compute_all_violations(df, pairs, {"global_default_pct": 5.0})

        assert sorted(masks) == ["Heater", "MFC-1", "P1"]
        mask, deviation = masks["P1"]
        assert not mask.any()
        assert len(deviation) == len(df)

    def test_a_pair_whose_columns_are_missing_is_skipped_whole(self, tmp_path):
        df = load_run(write_datalog(tmp_path))

        violations, masks = compute_all_violations(
            df, [("MFC-7", "MFC-7 PV", "MFC-7 SV")], {"global_default_pct": 5.0}
        )

        assert masks == {}
        assert violations.empty

    def test_no_violations_anywhere_returns_a_frame_with_no_columns(self, tmp_path):
        # Zero columns, not four empty ones -- which is why the sort is guarded.
        df = load_run(write_datalog(tmp_path))

        violations, _masks = compute_all_violations(
            df, detect_pv_sv_pairs(list(df.columns)), {"global_default_pct": 1000.0}
        )

        assert violations.empty
        assert list(violations.columns) == []

    def test_the_tolerance_resolver_can_be_injected(self, tmp_path):
        # The seam that keeps this module free of the threshold sidecar: a stub
        # resolver is all a test needs.
        df = load_run(write_datalog(tmp_path))
        pairs = detect_pv_sv_pairs(list(df.columns))

        violations, _masks = compute_all_violations(
            df, pairs, {}, lambda thresholds, pair_name: 0.1
        )

        assert set(violations["Channel"]) == {"Heater", "MFC-1", "P1"}


class TestFindFinalPlateauStart:
    def test_alignment_lands_on_the_real_ramp_not_the_stale_leading_block(self, tmp_path):
        # The fixture reproduces the quirk the docstring records: Heater SV
        # reads 910 from row 0 -- a leftover from whatever ran before -- then
        # drops and ramps to this run's real 850 target, which never reaches
        # back up to 910. A naive "last rising edge into the global max" scans
        # for 910, finds only the row-0 artifact, and returns the run's start
        # time: a plausible-looking answer that is the exact degenerate
        # alignment this function exists to avoid.
        df = load_run(write_datalog(tmp_path))

        assert df["Heater SV"].max() == 910
        assert find_final_plateau_start(df, "Heater SV") == pd.Timestamp("2026-08-06 17:33:40")

    def test_the_rising_edge_scan_still_runs_over_the_full_column(self):
        # The leading block is excluded from the *target search* only. Here the
        # stale value is below the real plateau, so the answer is the same
        # either way -- the point is that excluding it from the scan too would
        # move it.
        series = [100.0, 100.0, 20.0, 50.0, 200.0, 200.0]

        df = _pair_frame(series)

        assert find_final_plateau_start(df, "X SV") == pd.Timestamp("2026-08-06 17:33:16")

    def test_a_setpoint_that_never_moves_aligns_on_the_first_row(self):
        df = _pair_frame([850.0] * 4)

        assert find_final_plateau_start(df, "X SV") == pd.Timestamp("2026-08-06 17:33:12")

    def test_a_run_that_returns_to_the_plateau_aligns_on_the_last_arrival(self):
        df = _pair_frame([0.0, 850.0, 20.0, 850.0, 850.0])

        assert find_final_plateau_start(df, "X SV") == pd.Timestamp("2026-08-06 17:33:15")

    def test_an_empty_column_has_no_alignment_point(self):
        df = pd.DataFrame({"Time": pd.to_datetime([]), "X SV": pd.Series([], dtype=float)})

        assert find_final_plateau_start(df, "X SV") is None

    def test_an_all_nan_column_has_no_alignment_point(self):
        # The page falls back to wall-clock start and names the runs that fell
        # back, because a silent fallback looks like a real misalignment.
        df = _pair_frame([np.nan] * 4)

        assert find_final_plateau_start(df, "X SV") is None


class TestStaleLeadIn:
    def test_the_leading_block_is_masked_when_the_setpoint_later_changes(self):
        sv = pd.Series([900.0, 900.0, 900.0, 31.0, 32.0, 900.0])
        assert stale_lead_in_mask(sv).tolist() == [True, True, True, False, False, False]

    def test_a_setpoint_that_never_changes_is_not_masked(self):
        # Nothing says where an inherited value stops, so it is the run's own.
        sv = pd.Series([600.0] * 5)
        assert not stale_lead_in_mask(sv).any()

    def test_a_gapped_index_is_masked_by_position(self):
        # The scanner's dropna leaves gaps; the mask must keep df's labels.
        sv = pd.Series([500.0, 500.0, 140.0, 140.0], index=[0, 2, 3, 7])
        mask = stale_lead_in_mask(sv)
        assert mask.index.tolist() == [0, 2, 3, 7]
        assert mask.tolist() == [True, True, False, False]

    def test_an_empty_series_masks_nothing(self):
        assert stale_lead_in_mask(pd.Series([], dtype=float)).empty

    def test_the_plateau_finder_reads_the_same_lead_in(self, tmp_path):
        # One definition of "where this run's commands begin": the alignment
        # target search starts exactly where the violation mask stops.
        df = load_run(write_datalog(tmp_path))
        first_evaluated = df.index[~stale_lead_in_mask(df["Heater SV"]).to_numpy()][0]
        assert df.loc[first_evaluated, "Heater SV"] == 100
        assert find_final_plateau_start(df, "Heater SV") == pd.Timestamp("2026-08-06 17:33:40")


class TestSubResolutionFloor:
    def _mfc(self, sv, pv):
        timestamps = pd.date_range("2026-08-06 17:33:12", periods=len(sv), freq="1s")
        return pd.DataFrame({"Time": timestamps, "MFC-3 PV": pv, "MFC-3 SV": sv})

    def test_a_sub_resolution_mfc_setpoint_is_not_judged(self):
        # HADI49: MFC-3 at a card's 0.01 sccm reads 0.06-0.10, which is 930 %
        # and is the meter's floor, not a fault.
        df = self._mfc([0.01] * 4, [0.066, 0.07, 0.103, 0.06])

        violations, masks = compute_all_violations(
            df, [("MFC-3", "MFC-3 PV", "MFC-3 SV")], {"global_default_pct": 5.0}
        )

        assert violations.empty
        assert masks["MFC-3"][1].isna().all()

    def test_a_real_setpoint_on_the_same_channel_still_is(self):
        df = self._mfc([1.0] * 4, [1.0, 0.5, 1.0, 1.0])

        violations, _masks = compute_all_violations(
            df, [("MFC-3", "MFC-3 PV", "MFC-3 SV")], {"global_default_pct": 5.0}
        )

        assert violations["Max Deviation (%)"].tolist() == [pytest.approx(50.0)]

    def test_the_floor_itself_is_evaluated(self):
        # Constant setpoints, so the lead-in rule stays out of it.
        assert evaluable_rows(self._mfc([0.05] * 2, [0.05] * 2), "MFC-3", "MFC-3 SV").all()
        assert not evaluable_rows(self._mfc([0.049] * 2, [0.049] * 2), "MFC-3", "MFC-3 SV").any()

    def test_mfc_floor_matches_the_runcard_matchers_sub_resolution_threshold(self):
        from modules.datalog.processing import runcard_match

        assert all(SV_FLOOR[f"MFC-{i}"] == runcard_match._SUB_RESOLUTION_SCCM for i in range(1, 9))

    def test_pressure_controllers_have_a_floor_and_heaters_do_not(self):
        assert SV_FLOOR["P1"] == SV_FLOOR["P2"] == SV_FLOOR["P3"] == 1.0
        assert not {"Heater", "P1_H", "P2_H", "P3_H"} & set(SV_FLOOR)
