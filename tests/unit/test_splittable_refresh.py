"""Unit tests for the split-table refresh: QC summary, QC choice, W source, in-place write."""

import os

import pandas as pd
import pytest
from openpyxl import Workbook, load_workbook
from openpyxl.styles import Font, PatternFill

from core.version import __version__
from modules.spectra.io.results_excel import TechniqueResults, build_sample_results_xlsx
from modules.splittable.io import qc_summary, table_writer
from modules.splittable.processing import vb_build
from modules.splittable.processing.datalog_runs import run_tag
from modules.splittable.processing.qc_select import SOURCE_LEGACY, SOURCE_REPORT, select_qc
from tests.unit.test_results_excel import _peak_stat, _spectrum


# ----------------------------------------------------------------- QC reports
def _report(optical, folder, sample, material, date="2026-11-08", layer="1L", fwhm=5.0):
    path = optical / folder / f"{sample}_QC_Report.xlsx"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(build_sample_results_xlsx(
        sample_name=sample, material_name=material, report_date=date, layer=layer,
        techniques=[TechniqueResults(
            "Raman", [(1, _spectrum())], [_peak_stat(label="E2g+A1g", fwhm_mean=fwhm)],
            ratios=[("LA / E2g+A1g", (0.09, 0.01, 5))])],
    ))
    return path


class TestReadQcReport:
    def test_a_report_reads_back_in_the_summary_column_names(self, tmp_path):
        path = _report(tmp_path, "202611/VBCA40", "VBCA40", "WSe2_QC", fwhm=5.1)
        record = qc_summary.read_qc_report(path, tmp_path)

        assert record["Wafer_ID"] == "VBCA40"
        assert record["Folder"] == "202611/VBCA40"
        assert record["Month"] == 202611
        assert record["Fit_Mode"] == "WSe2_QC"
        assert record["Layer"] == "1L"
        assert record["NexAnalyzer_version"] == __version__
        assert record["Raman_E2g+A1g_FWHM"] == pytest.approx(5.1)
        assert record["Raman_LA/E2g+A1g_median"] == pytest.approx(0.09)
        assert record["Raman_n_spectra"] == 1

    def test_the_raw_pl_row_reaches_the_split_table_columns(self, tmp_path):
        path = tmp_path / "202611" / "VBCA41" / "VBCA41_QC_Report.xlsx"
        path.parent.mkdir(parents=True)
        path.write_bytes(build_sample_results_xlsx(
            sample_name="VBCA41", material_name="WSe2_QC", report_date="2026-11-08",
            techniques=[TechniqueResults(
                "PL", [(1, _spectrum())], [_peak_stat(label="Raw", fwhm_mean=42.0)])],
        ))
        record = qc_summary.read_qc_report(path, tmp_path)

        assert record["PL_Raw_FWHM"] == pytest.approx(42.0)
        for column in ("PL_Raw_Center", "PL_Raw_FWHM", "PL_Raw_Intensity"):
            assert column in vb_build.COLUMNS
            assert vb_build.QC_MAP[column] in record

    def test_the_wafer_is_the_folder_not_the_sample_name(self, tmp_path):
        """OPTICAL/202610/VBCA00 holds VBBG00's report: the same wafer, renamed."""
        path = _report(tmp_path, "202610/VBCA00", "VBBG00", "WSe2")
        assert qc_summary.read_qc_report(path, tmp_path)["Wafer_ID"] == "VBCA00"


class TestCollectReports:
    def test_a_report_regenerated_with_the_same_preset_replaces_its_row(self, tmp_path):
        path = _report(tmp_path, "202611/VBCA40", "VBCA40", "WSe2_QC", fwhm=5.0)
        first = qc_summary.collect_reports(tmp_path, None).summary.reports
        _report(tmp_path, "202611/VBCA40", "VBCA40", "WSe2_QC", fwhm=4.0)
        os.utime(path, (1e9, 2e9))
        second = qc_summary.collect_reports(tmp_path, first)

        assert len(second.summary.reports) == 1
        assert second.updated
        assert second.summary.reports["Raman_E2g+A1g_FWHM"].iloc[0] == pytest.approx(4.0)

    def test_a_report_regenerated_with_another_preset_keeps_the_old_row(self, tmp_path):
        path = _report(tmp_path, "202611/VBCA40", "VBCA40", "WSe2")
        first = qc_summary.collect_reports(tmp_path, None).summary.reports
        _report(tmp_path, "202611/VBCA40", "VBCA40", "WSe2_QC")
        os.utime(path, (1e9, 2e9))
        reports = qc_summary.collect_reports(tmp_path, first).summary.reports

        assert sorted(reports["Fit_Mode"]) == ["WSe2", "WSe2_QC"]
        assert dict(zip(reports["Fit_Mode"], reports["Current"])) == {"WSe2": False, "WSe2_QC": True}

    def test_an_unchanged_report_is_not_reread(self, tmp_path):
        _report(tmp_path, "202611/VBCA40", "VBCA40", "WSe2_QC")
        first = qc_summary.collect_reports(tmp_path, None).summary.reports

        def refuse(*_a):
            raise AssertionError("reread")
        again = qc_summary.collect_reports(tmp_path, first, reader=refuse)
        assert not again.changed and len(again.summary.reports) == 1

    def test_a_wafer_folder_without_a_report_is_listed_missing(self, tmp_path):
        _report(tmp_path, "202611/VBCA40", "VBCA40", "WSe2_QC")
        (tmp_path / "202611" / "VBCA41").mkdir()
        reports = qc_summary.collect_reports(tmp_path, None).summary.reports
        assert list(qc_summary.missing_reports(tmp_path, reports)["Wafer_ID"]) == ["VBCA41"]


# ----------------------------------------------------------------- QC choice
def _rows(*records):
    return pd.DataFrame(list(records))


class TestSelectQc:
    def test_a_qc_preset_beats_a_newer_report_with_another(self):
        reports = _rows({"Wafer_ID": "A", "Fit_Mode": "WSe2_QC", "QC_Report_Date": "2026-11-01"},
                        {"Wafer_ID": "A", "Fit_Mode": "WSe2", "QC_Report_Date": "2026-11-05"})
        assert select_qc(reports, None)["A"].record["Fit_Mode"] == "WSe2_QC"

    def test_of_two_qc_reports_the_newer_wins(self):
        reports = _rows({"Wafer_ID": "A", "Fit_Mode": "WSe2_QC", "QC_Report_Date": "2026-11-01", "v": 1},
                        {"Wafer_ID": "A", "Fit_Mode": "MoS2_QC", "QC_Report_Date": "2026-11-03", "v": 2})
        assert select_qc(reports, None)["A"].record["v"] == 2

    def test_a_dated_qc_report_beats_qc_legacy(self):
        reports = _rows({"Wafer_ID": "A", "Fit_Mode": "WSe2_QC", "QC_Report_Date": "2026-11-01"})
        legacy = _rows({"Wafer_ID": "A", "Fit_Mode": "3_WSe2_QC"})
        assert select_qc(reports, legacy)["A"].source == SOURCE_REPORT

    def test_qc_legacy_beats_a_report_with_another_preset(self):
        reports = _rows({"Wafer_ID": "A", "Fit_Mode": "WSe2_truncate", "QC_Report_Date": "2026-11-01"})
        legacy = _rows({"Wafer_ID": "A", "Fit_Mode": "3_WSe2_QC"})
        choice = select_qc(reports, legacy)["A"]
        assert (choice.source, choice.candidates) == (SOURCE_LEGACY, 2)

    def test_a_block_the_chosen_row_lacks_is_filled_whole_from_the_next(self):
        """VBCA00: legacy 3_WSe2_QC wins, but only its WSe2 report has PL Raw."""
        reports = _rows({"Wafer_ID": "A", "Fit_Mode": "WSe2", "QC_Report_Date": "2026-10-21",
                         "Report_File": "A_QC_Report.xlsx", "PL_Raw_FWHM": 45.0,
                         "PL_Raw_Center": 770.0, "PL_Exciton_FWHM": 30.0})
        legacy = _rows({"Wafer_ID": "A", "Fit_Mode": "3_WSe2_QC", "PL_Exciton_FWHM": 28.0})
        choice = select_qc(reports, legacy)["A"]

        assert choice.source == SOURCE_LEGACY
        assert choice.record["Fit_Mode"] == "3_WSe2_QC"
        assert choice.record["PL_Exciton_FWHM"] == 28.0       # the chosen row's own block
        assert (choice.record["PL_Raw_FWHM"], choice.record["PL_Raw_Center"]) == (45.0, 770.0)
        assert choice.fills == ("PL_Raw from A_QC_Report.xlsx (WSe2, 2026-10-21)",)

    def test_a_block_is_never_assembled_from_two_rows(self):
        reports = _rows({"Wafer_ID": "A", "Fit_Mode": "WSe2_QC", "QC_Report_Date": "2026-11-01",
                         "PL_Raw_FWHM": 45.0},
                        {"Wafer_ID": "A", "Fit_Mode": "WSe2", "QC_Report_Date": "2026-10-01",
                         "PL_Raw_FWHM": 50.0, "PL_Raw_Center": 770.0})
        choice = select_qc(reports, None)["A"]
        assert choice.record["PL_Raw_FWHM"] == 45.0
        assert "PL_Raw_Center" not in choice.record and choice.fills == ()

    def test_any_report_beats_non_qc_legacy(self):
        reports = _rows({"Wafer_ID": "A", "Fit_Mode": "WSe2", "QC_Report_Date": "2026-09-19"})
        legacy = _rows({"Wafer_ID": "A", "Fit_Mode": "1_WSe2_-5-500_all"})
        assert select_qc(reports, legacy)["A"].source == SOURCE_REPORT

    def test_legacy_alone_is_used(self):
        legacy = _rows({"Wafer_ID": "nsva1p012504002", "Fit_Mode": "1_WSe2_-5-500_all"})
        assert "VA1P012504002" in select_qc(None, legacy, key=vb_build.canonical)


# ----------------------------------------------------------------- W source
RULES = {"VB": {"VBA": "WO3", "VBB": "W(CO)6", "VBC": "W foil"},
         "VA": {"VA1P0125": "W(CO)6"}}


class TestWSource:
    def test_milestone_first(self):
        assert vb_build.w_source_for("VBCA27", {"W_source": "W foil"}, RULES, {}) == (
            "W foil", "Milestone", None)

    def test_the_run_id_rule_where_milestone_is_blank(self):
        assert vb_build.w_source_for("VBCA27", {"W_source": None}, RULES, {"VBCA": "WO3"}) == (
            "W foil", "run ID VBC", None)

    def test_series_majority_when_no_rule_matches(self):
        assert vb_build.w_source_for("VBXA01", None, RULES, {"VBXA": "WO3"}) == (
            "WO3", "series majority", None)

    def test_a_milestone_value_the_rule_disagrees_with_is_named(self):
        _, _, check = vb_build.w_source_for("VBCA27", {"W_source": "WO3"}, RULES, {})
        assert check == "Milestone WO3 vs run ID VBC W foil"

    def test_milestone_na_reads_as_blank(self):
        assert vb_build.w_source_of("NA") is None
        assert vb_build.w_source_of("140") == "W(CO)6"


class TestRunTag:
    def test_filename_tag_then_json(self):
        assert run_tag("2026_08/2026-08-02_101010~VBAC02.csv", {}) == "VBAC02"
        assert run_tag("2026_08/12345678.csv", {"2026_08/12345678.csv": "VBAC03"}) == "VBAC03"
        assert run_tag("2026_08/12345678.csv", {}) is None


# ----------------------------------------------------------------- write
def _table(overrides=None):
    rows = [
        {"WaferID": "VBCA01", "Run_Type": "growth", "Process_Start": pd.Timestamp("2026-11-01 08:00"),
         "Runcard_Saved": None, "P_torr": 2.0, "Purpose": "base"},
        {"WaferID": "CLEANING-4", "Run_Type": "cleaning", "Process_Start": pd.Timestamp("2026-11-02 08:00"),
         "Runcard_Saved": None, "P_torr": 5.0, "Purpose": None},
    ]
    for (i, col), value in (overrides or {}).items():
        rows[i][col] = value
    return pd.DataFrame(rows)


class TestMerge:
    def test_the_first_refresh_lists_what_it_changed(self):
        table, changes, report = table_writer.merge(
            _table(), _table({(0, "P_torr"): 3.0}), None, vb_build.sort_rows)
        assert table["P_torr"].iloc[0] == 2.0
        assert list(changes["Column"]) == ["P_torr"]
        assert report.first_refresh_changes == 1

    def test_a_hand_edit_is_kept_and_flagged(self):
        last = _table()
        visible = _table({(0, "Purpose"): "edited by hand"})
        today = _table({(0, "Purpose"): "base v2"})
        table, _, report = table_writer.merge(today, visible, last, vb_build.sort_rows)

        assert table["Purpose"].iloc[0] == "edited by hand"
        assert table["Hand_Edits"].iloc[0] == "kept: Purpose (now base v2)"
        assert report.hand_edits

    def test_an_untouched_cell_takes_the_new_value(self):
        table, _, _ = table_writer.merge(_table({(1, "P_torr"): 6.0}), _table(), _table(),
                                         vb_build.sort_rows)
        assert table["P_torr"].iloc[1] == 6.0

    def test_a_press_with_nothing_new_changes_nothing(self):
        sheet = _table()
        sheet["Hand_Edits"] = None
        _, _, report = table_writer.merge(_table(), sheet, _table(), vb_build.sort_rows)
        assert report.unchanged

        _, _, report = table_writer.merge(_table({(1, "P_torr"): 6.0}), sheet, _table(),
                                          vb_build.sort_rows)
        assert (report.unchanged, report.cells_changed) == (False, 1)

    def test_a_new_column_is_a_change(self):
        sheet = _table()
        sheet["Hand_Edits"] = None
        today = _table()
        today["PL_Raw_FWHM"] = [45.0, None]
        _, _, report = table_writer.merge(today, sheet, _table(), vb_build.sort_rows)
        assert not report.unchanged

    def test_a_hand_added_column_and_row_are_carried(self):
        visible = _table()
        visible["My_note"] = ["keep me", None]
        extra = pd.DataFrame([{"WaferID": "VBCA99", "Run_Type": "growth",
                               "Process_Start": pd.Timestamp("2026-11-03")}])
        visible = pd.concat([visible, extra], ignore_index=True)
        table, _, _ = table_writer.merge(_table(), visible, _table(), vb_build.sort_rows)

        assert table["My_note"].iloc[0] == "keep me"
        assert table.loc[table["WaferID"] == "VBCA99", "Hand_Edits"].iloc[0] == "row added by hand"

    def test_rows_follow_process_start_then_card_save_time(self):
        rows = pd.DataFrame([
            {"WaferID": "B", "Process_Start": None, "Runcard_Saved": pd.Timestamp("2026-11-01 12:00")},
            {"WaferID": "A", "Process_Start": pd.Timestamp("2026-11-02"), "Runcard_Saved": None},
            {"WaferID": "C", "Process_Start": pd.Timestamp("2026-10-30"), "Runcard_Saved": None},
            {"WaferID": "D", "Process_Start": None, "Runcard_Saved": None},
        ])
        assert list(vb_build.sort_rows(rows)["WaferID"]) == ["C", "B", "A", "D"]


class TestWriteTable:
    def _workbook(self, path):
        wb = Workbook()
        ws = wb.active
        ws.title = vb_build.SHEET
        ws.append(["WaferID", "P_torr"])
        ws.append(["VBCA01", 2.0])
        ws["A1"].fill = PatternFill("solid", fgColor="FFD9D9D9")
        ws["A1"].font = Font(bold=True)
        wb.create_sheet("My_sheet")["A1"] = "mine"
        wb.save(path)

    def test_keeps_header_style_and_other_sheets_and_backs_up(self, tmp_path):
        path = tmp_path / "VB_SplitTable_from_runcards.xlsx"
        self._workbook(path)
        table = _table()
        backup = table_writer.write_table(path, vb_build.SHEET, table, table,
                                          {"P_torr": "process"}, [("Notes", pd.DataFrame({"a": [1]}))])
        wb = load_workbook(path)

        assert wb[vb_build.SHEET]["A1"].fill.fgColor.rgb == "FFD9D9D9"
        assert wb["My_sheet"]["A1"].value == "mine"
        assert wb[table_writer.COMPUTED_SHEET].sheet_state == "hidden"
        assert backup is not None and backup.parent.name == "_backups" and backup.exists()

    def test_refuses_a_workbook_open_in_excel(self, tmp_path):
        path = tmp_path / "VB_SplitTable_from_runcards.xlsx"
        self._workbook(path)
        (tmp_path / ("~$" + path.name)).write_bytes(b"")
        with pytest.raises(table_writer.TableBusy):
            table_writer.check_writable(path)

    def test_one_refresh_at_a_time(self, tmp_path):
        path = tmp_path / "t.xlsx"
        with table_writer.refresh_lock(path):
            with pytest.raises(table_writer.TableBusy):
                with table_writer.refresh_lock(path):
                    pass
        with table_writer.refresh_lock(path):
            pass


class TestReadSheets:
    def test_na_text_stays_text(self, tmp_path):
        """Milestone writes "NA" in Coat and Result; pandas' default reads it as
        missing, and a carried cell would be written back empty."""
        path = tmp_path / "t.xlsx"
        pd.DataFrame({"WaferID": ["VBCA01"], "Coat": ["NA"], "Remark": [None]}).to_excel(
            path, sheet_name=vb_build.SHEET, index=False)
        visible, computed = table_writer.read_sheets(path, vb_build.SHEET)
        assert visible["Coat"].iloc[0] == "NA"
        assert pd.isna(visible["Remark"].iloc[0])
        assert computed is None
