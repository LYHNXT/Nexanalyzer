"""Unit tests for modules.splittable: reading, filtering, linking and charting a split table."""

import pandas as pd
import pytest

from core.io.tool_layout import ToolLayout
from modules.splittable.io.workbook import OTHER_GROUP, column_groups, read_split_table
from modules.splittable.processing import links
from modules.splittable.processing.table import (
    NO_SERIES,
    filter_rows,
    is_growth,
    non_growth_kind,
    not_discriminable,
    numeric_columns,
    series_of,
    std_column,
)
from modules.splittable.viz.charts import (
    NON_GROWTH_COLORS,
    X_PROCESS_START,
    build_scatter_figure,
    build_trend_figure,
)


def _table():
    return pd.DataFrame({
        "WaferID": ["HADH90", "CLEANING-4", "HADI51", "HADI52", "VBCA02"],
        "Month": ["202609", "202610", "202610", "202610", "202610"],
        "Run_Type": ["growth", "cleaning", "growth", "growth (stopped)", "growth"],
        "Process_Start": pd.to_datetime(
            ["2026-09-30 10:00", "2026-10-13 21:20", "2026-10-13 16:48", None, "2026-10-14 09:00"]),
        "OM_Reference_pct": [91.0, None, 95.5, None, 80.0],
        "OM_Reference_Std": [1.0, None, 4.6, None, 2.0],
        "OM_Discriminable": [1.0, None, 0.0, None, 1.0],
        "T_growth_C": [900, 950, 900, 905, 880],
    })


class TestReading:
    def test_a_onedrive_workbook_is_read_and_tidied(self, tmp_path):
        path = tmp_path / "HA_SplitTable_from_runcards.xlsx"
        data = pd.DataFrame({
            "WaferID": ["A01", "A02"], "Month": [202610.0, 202609.0],
            "Dist_cm": [1, "1-2"], "Extra": [1.0, 2.0],
        })
        design = pd.DataFrame({"Column": ["WaferID", "Month", "Dist_cm"],
                               "Group": ["identity", "identity", "process"]})
        with pd.ExcelWriter(path) as xl:
            data.to_excel(xl, sheet_name="HA_SplitTable_gen", index=False)
            design.to_excel(xl, sheet_name="Column_Design", index=False)

        table = read_split_table(path)

        assert table.sheet == "HA_SplitTable_gen"
        # VB writes Month as 202610.0; both tools must filter on the same text.
        assert list(table.data["Month"]) == ["202610", "202609"]
        # A mixed-type column becomes text, or st.dataframe refuses it.
        assert list(table.data["Dist_cm"]) == ["1", "1-2"]
        assert table.groups == {"identity": ["WaferID", "Month"], "process": ["Dist_cm"],
                                OTHER_GROUP: ["Extra"]}

    def test_without_a_design_sheet_every_column_is_other(self):
        assert column_groups(["A", "B"], None) == {OTHER_GROUP: ["A", "B"]}


class TestFiltering:
    def test_rows_keep_their_workbook_position(self):
        rows = filter_rows(_table(), months=["202610"])
        assert list(rows.index) == [1, 2, 3, 4]

    def test_every_filter_must_hold(self):
        rows = filter_rows(_table(), months=["202610"], run_types=["growth"], series=["HADI"])
        assert list(rows["WaferID"]) == ["HADI51"]

    def test_a_search_matches_any_of_several_terms(self):
        rows = filter_rows(_table(), query="hadh, vbca")
        assert list(rows["WaferID"]) == ["HADH90", "VBCA02"]

    def test_series_is_the_declared_one_else_the_wafer_prefix(self):
        data = _table()
        data["Series"] = [None, None, None, None, "VBCA-old"]
        assert list(series_of(data)) == ["HADH", NO_SERIES, "HADI", "HADI", "VBCA-old"]

    def test_growth_includes_stopped_and_untyped_runs(self):
        data = _table()
        data.loc[0, "Run_Type"] = None
        assert list(is_growth(data)) == [True, False, True, True, True]

    @pytest.mark.parametrize("run_type, kind", [
        ("cleaning (stopped)", "cleaning"), ("O2-annealing", "anneal"),
        ("anneal", "anneal"), ("conditioning (no H2Se)", "other"), ("unidentified", "other"),
    ])
    def test_non_growth_kinds(self, run_type, kind):
        assert non_growth_kind(run_type) == kind


class TestColumnReadings:
    @pytest.mark.parametrize("column, std", [
        ("OM_Reference_pct", "OM_Reference_Std"),       # HA and VB OM
        ("E2g_A1g_Center_Mean", "E2g_A1g_Center_Std"),  # HA Raman
        ("E2g_A1g_FWHM", "E2g_A1g_FWHM_std"),           # VB Raman
        ("Defect_Ratio_Median", None),                  # a MAD is not a std
    ])
    def test_std_column(self, column, std):
        columns = ["OM_Reference_pct", "OM_Reference_Std", "E2g_A1g_Center_Mean",
                   "E2g_A1g_Center_Std", "E2g_A1g_FWHM", "E2g_A1g_FWHM_std",
                   "Defect_Ratio_Median", "Defect_Ratio_MAD"]
        assert std_column(columns, column) == std

    def test_only_om_columns_are_flagged_not_discriminable(self):
        data = _table()
        assert list(not_discriminable(data, "OM_Reference_pct")) == [False, False, True, False, False]
        assert not not_discriminable(data, "T_growth_C").any()

    def test_numeric_columns_skip_empty_ones(self):
        data = _table()
        data["Empty"] = float("nan")
        assert "Empty" not in numeric_columns(data)
        assert "T_growth_C" in numeric_columns(data)


class TestTrendFigure:
    def test_one_column_is_one_trace_per_series_and_cleanings_are_lines(self):
        fig = build_trend_figure(_table(), ["OM_Reference_pct"])

        points = [t for t in fig.data if t.mode == "lines+markers"]
        assert sorted(t.name for t in points) == ["HADH", "HADI", "VBCA"]
        assert len(fig.layout.shapes) == 1
        assert fig.layout.shapes[0].line.color == NON_GROWTH_COLORS["cleaning"]

    def test_hollow_markers_and_error_bars(self):
        fig = build_trend_figure(_table(), ["OM_Reference_pct"])
        hadi = next(t for t in fig.data if t.name == "HADI")
        # HADI52 has no value; HADI51 is the one point, not discriminable.
        assert list(hadi.marker.symbol) == ["circle-open"]
        assert list(hadi.error_y.array) == [4.6]

    def test_several_columns_are_one_trace_each(self):
        fig = build_trend_figure(_table(), ["OM_Reference_pct", "T_growth_C"], mark_non_growth=False)
        assert [t.name for t in fig.data] == ["OM_Reference_pct", "T_growth_C"]
        assert not fig.layout.shapes

    def test_process_start_axis_drops_rows_without_a_time(self):
        fig = build_trend_figure(_table(), ["T_growth_C"], X_PROCESS_START)
        assert sum(len(t.x) for t in fig.data if t.mode == "lines+markers") == 3

    def test_scatter_keeps_rows_with_both_values(self):
        fig = build_scatter_figure(_table(), "T_growth_C", "OM_Reference_pct")
        assert sum(len(t.x) for t in fig.data) == 3


@pytest.fixture
def tool(tmp_path):
    """A miniature HA1P01 with one wafer's three artifacts and a renamed datalog."""
    root = tmp_path / "HA1P01"
    (root / "OPTICALS" / "202610" / "HADI51").mkdir(parents=True)
    for month in ("202609", "202610"):
        cards = root / "RUNCARD" / "lcy" / "NSHA1P01" / month
        cards.mkdir(parents=True)
        (cards / "HADI51.csv").write_text("x", encoding="utf-8")
    logs = root / "DATALOG" / "2026_10"
    logs.mkdir(parents=True)
    (logs / "13184401~HADI51.csv").write_text("x", encoding="utf-8")
    spec = {
        "optical": {"subfolder": "OPTICALS", "patterns": ["OPTICALS/*/{wafer}"]},
        "runcard": {"subfolder": "RUNCARD/lcy/NSHA1P01",
                    "patterns": ["RUNCARD/lcy/NSHA1P01/*/{wafer}.csv"]},
        "datalog": {"subfolder": "DATALOG", "patterns": ["DATALOG/*/*~{wafer}.csv"]},
        "splittable": {"file": "SPLITTABLE/HA_SplitTable_from_runcards.xlsx"},
    }
    return ToolLayout(name="HA1P01", root=root, spec=spec)


class TestLinks:
    def test_the_workbook_is_declared_per_tool(self, tool):
        assert tool.splittable_file == tool.root / "SPLITTABLE" / "HA_SplitTable_from_runcards.xlsx"

    def test_a_renamed_datalog_is_found_beside_its_recorded_name(self, tool):
        row = pd.Series({"WaferID": "HADI51", "Month": "202610", "Datalog": "2026_10/13184401.csv"})
        found = links.resolve_row(tool, row)
        assert found.datalog == tool.root / "DATALOG" / "2026_10" / "13184401~HADI51.csv"
        assert found.optical == tool.root / "OPTICALS" / "202610" / "HADI51"

    def test_the_rows_month_picks_between_two_runcards(self, tool):
        row = pd.Series({"WaferID": "HADI51", "Month": "202609"})
        found = links.resolve_row(tool, row)
        assert found.runcard == tool.root / "RUNCARD" / "lcy" / "NSHA1P01" / "202609" / "HADI51.csv"

    def test_an_untraceable_row_is_not_linked(self, tool):
        found = links.resolve_row(tool, pd.Series({"WaferID": "UNIDENTIFIED", "Datalog": None}))
        assert found == links.RowLinks()
