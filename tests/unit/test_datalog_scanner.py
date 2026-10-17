"""Unit tests for modules.datalog.io.scanner (datalog CSV discovery and parsing)."""

import pandas as pd
import pytest

from modules.datalog.io import scanner
from modules.datalog.io.scanner import (
    detect_pv_sv_pairs,
    discover_csv_files,
    get_plain_numeric_channels,
    get_run_metadata,
    load_run,
    scan_folder,
)
from tests.datalog_fixtures import DATALOG_SHORT, rewrite_cell, write_datalog

_HEADER = DATALOG_SHORT.splitlines()[0]


def _rows(text):
    """The data lines of a datalog, without the header."""
    return text.splitlines()[1:]


def _rebuild(rows):
    """A datalog built from `rows`, under the standard header."""
    return "\n".join([_HEADER] + rows) + "\n"


def _shift_to_next_day(text):
    """The same run logged a day later, for ordering tests."""
    return text.replace("2026/08/06 ", "2026/08/07 ")


def _names(paths):
    """Just the filenames, so an assertion does not carry tmp_path."""
    return [p.replace("\\", "/").rsplit("/", 1)[-1] for p in paths]


class TestLoadRun:
    def test_engineering_notation_reads_as_a_plain_float(self, tmp_path):
        # The controller writes pressures as "19.45E-3" / "1.46E+0", which is
        # already a valid float literal -- a regex "fix" would be the bug here.
        df = load_run(write_datalog(tmp_path))

        assert df["Tube Pressure"].iloc[0] == pytest.approx(0.01945)
        assert df["Tube Pressure"].iloc[8] == pytest.approx(1.46)
        assert df["Tube Pressure"].iloc[24] == pytest.approx(0.05374)

    def test_the_gauge_range_label_survives_as_text(self, tmp_path):
        # "1000 Torr" is which range the pressure beside it was read on, not a
        # pressure. Coercing it would replace the only record of that with NaN.
        df = load_run(write_datalog(tmp_path))

        assert df["651C Gauge"].iloc[0] == "1000 Torr"
        assert list(df["651C Gauge"].unique()) == ["1000 Torr", "100 Torr"]
        assert df["651C Gauge"].isna().sum() == 0

    def test_operator_initials_and_recipe_step_stay_categorical(self, tmp_path):
        df = load_run(write_datalog(tmp_path))

        assert df["Auto Action"].iloc[0] == "LYH"
        assert df["Program"].iloc[0] == "Stage Rot"
        assert df["Program"].iloc[-1] == "End"

    def test_time_becomes_datetime_and_every_surviving_row_has_one(self, tmp_path):
        df = load_run(write_datalog(tmp_path))

        assert pd.api.types.is_datetime64_any_dtype(df["Time"])
        assert df["Time"].isna().sum() == 0
        assert df.shape == (36, 12)

    def test_an_unparseable_timestamp_drops_its_row_and_leaves_an_index_gap(self, tmp_path):
        # The gap is the point: the dropna does not reset the index, and
        # analysis._build_segment slices that index by label. Monotonic with
        # holes is correct; renumbered is not.
        text = rewrite_cell(DATALOG_SHORT, "2026/08/06 17:33:16", "Time", "not-a-time")

        df = load_run(write_datalog(tmp_path, text, name="bad_stamp.csv"))

        assert len(df) == 35
        assert list(df.index[:6]) == [0, 1, 2, 3, 5, 6]
        assert df.index.is_monotonic_increasing

    def test_a_row_with_too_many_fields_is_skipped_not_raised(self, tmp_path):
        # A datalog copied while the controller was mid-write really does carry
        # these. Skipping costs one sample; raising costs the whole run.
        rows = _rows(DATALOG_SHORT)
        rows[4] = rows[4] + ",999,999"

        df = load_run(write_datalog(tmp_path, _rebuild(rows), name="ragged.csv"))

        assert len(df) == 35
        assert pd.Timestamp("2026-08-06 17:33:16") not in set(df["Time"])

    def test_a_row_with_too_few_fields_is_kept_with_nan_columns(self, tmp_path):
        # Not symmetrical with the case above, and worth stating: the python
        # engine pads a short row rather than calling it bad, so a half-written
        # final line survives carrying NaNs instead of disappearing.
        rows = _rows(DATALOG_SHORT)
        rows[4] = "2026/08/06 17:33:16,LYH,Wait"

        df = load_run(write_datalog(tmp_path, _rebuild(rows), name="short_row.csv"))

        assert len(df) == 36
        assert pd.isna(df.loc[4, "Heater PV"])

    def test_a_junk_numeric_cell_costs_one_sample_not_the_file(self, tmp_path):
        text = rewrite_cell(DATALOG_SHORT, "2026/08/06 17:33:16", "Heater PV", "----")

        df = load_run(write_datalog(tmp_path, text, name="junk_cell.csv"))

        assert len(df) == 36
        assert pd.isna(df.loc[4, "Heater PV"])
        assert df.loc[5, "Heater PV"] == 90

    def test_a_column_this_build_has_never_seen_is_treated_as_numeric(self, tmp_path):
        # Numeric coercion is a pure exclusion rule, so the controller gaining a
        # channel does not require editing scanner.py.
        text = "Time,Program,Widget Torque\n2026/08/06 17:33:12,Wait,4.5\n"

        df = load_run(write_datalog(tmp_path, text, name="future_column.csv"))

        assert df["Widget Torque"].iloc[0] == pytest.approx(4.5)

    def test_a_stray_non_utf8_byte_costs_a_character_not_the_run(self, tmp_path):
        # The lister and the runcard reader both replace bad bytes, so a file
        # like this was listed and then could not be opened.
        raw = DATALOG_SHORT.encode("utf-8").replace(b",LYH,Wait,", b",LY\xe9,Wait,", 1)
        path = tmp_path / "latin1_byte.csv"
        path.write_bytes(raw)

        df = load_run(str(path))

        assert len(df) == 36
        assert "\ufffd" in "".join(df["Auto Action"].astype(str))

    def test_a_bom_does_not_corrupt_the_time_header(self, tmp_path):
        path = tmp_path / "bom.csv"
        path.write_bytes(b"\xef\xbb\xbf" + DATALOG_SHORT.encode("utf-8"))

        df = load_run(str(path))

        assert df.columns[0] == "Time"
        assert df["Time"].iloc[0] == pd.Timestamp("2026-08-06 17:33:12")


class TestGetRunMetadata:
    def test_reads_the_four_list_view_scalars(self, tmp_path):
        path = write_datalog(tmp_path)

        meta = get_run_metadata(path)

        assert meta.path == path
        assert meta.start_time == pd.Timestamp("2026-08-06 17:33:12")
        assert meta.end_time == pd.Timestamp("2026-08-06 17:33:47")
        assert meta.row_count == 36
        assert meta.first_program == "Stage Rot"

    def test_blank_lines_are_not_rows(self, tmp_path):
        text = DATALOG_SHORT.replace("2026/08/06 17:33:14", "\n2026/08/06 17:33:14", 1)

        meta = get_run_metadata(write_datalog(tmp_path, text, name="blank_line.csv"))

        assert meta.row_count == 36

    def test_a_half_written_final_line_falls_back_to_the_start_time(self, tmp_path):
        # The normal state of the run currently being logged: the controller is
        # part-way through writing the line when the page reads it.
        text = DATALOG_SHORT + "2026/08"

        meta = get_run_metadata(write_datalog(tmp_path, text, name="mid_write.csv"))

        assert meta.end_time == meta.start_time
        assert meta.row_count == 37

    def test_an_empty_final_row_falls_back_to_the_start_time_not_nat(self, tmp_path):
        # `pd.to_datetime("")` is NaT without raising, and NaT is truthy, so it
        # used to survive the `or start_time` fallback -- and the page then
        # crashed on NaT.total_seconds() and NaT.strftime().
        text = DATALOG_SHORT + ",,,,,,,,,,,\n"

        meta = get_run_metadata(write_datalog(tmp_path, text, name="empty_last_row.csv"))

        assert meta.end_time == meta.start_time
        assert not pd.isna(meta.end_time)
        int((meta.end_time - meta.start_time).total_seconds())   # what the page does
        meta.end_time.strftime("%Y-%m-%d %H:%M:%S")

    def test_an_empty_first_time_field_rejects_the_file_like_any_bad_stamp(self, tmp_path):
        rows = _rows(DATALOG_SHORT)
        rows[0] = "," + rows[0].split(",", 1)[1]

        meta = get_run_metadata(write_datalog(tmp_path, _rebuild(rows), name="empty_first.csv"))

        assert meta is None

    def test_scan_folder_survives_an_empty_final_row(self, tmp_path):
        write_datalog(tmp_path, DATALOG_SHORT + ",,,,,,,,,,,\n", name="empty_last_row.csv")
        write_datalog(tmp_path, _shift_to_next_day(DATALOG_SHORT), name="next_day.csv")

        runs = scan_folder(str(tmp_path))

        assert not any(pd.isna(r.end_time) or pd.isna(r.start_time) for r in runs)
        assert _names([r.path for r in runs]) == ["next_day.csv", "empty_last_row.csv"]

    @pytest.mark.parametrize("name,text", [
        ("empty_file", ""),
        ("header_only", "Time,Auto Action,Program\n"),
        ("no_time_column", "A,B,C\n1,2,3\n"),
        ("unparseable_first_stamp", "Time,Program\nnot-a-time,Wait\n"),
    ])
    def test_a_file_that_is_not_a_datalog_is_rejected(self, tmp_path, name, text):
        # Rejected, not raised: a data folder holds other CSVs, and a run list
        # is not the place to argue about them.
        path = tmp_path / f"{name}.csv"
        path.write_text(text, encoding="utf-8")

        assert get_run_metadata(str(path)) is None


class TestDiscoverCsvFiles:
    def test_a_folder_that_is_not_there_returns_an_empty_list(self, tmp_path):
        # The page reopens on the folder it was last shown, and that folder is
        # regularly an unplugged drive or a share that has not reconnected.
        assert discover_csv_files(str(tmp_path / "never_existed")) == []

    def test_a_file_where_a_folder_was_expected_returns_an_empty_list(self, tmp_path):
        path = tmp_path / "not_a_folder.txt"
        path.write_text("x")

        assert discover_csv_files(str(path)) == []

    def test_finds_csvs_at_every_depth_in_path_order(self, tmp_path):
        nested = tmp_path / "2026-08" / "tool-b"
        nested.mkdir(parents=True)
        (tmp_path / "b.csv").write_text("x")
        (tmp_path / "a.csv").write_text("x")
        (nested / "c.csv").write_text("x")

        found = discover_csv_files(str(tmp_path))

        # Sorted as whole path strings, so the subfolder sorts before the files
        # beside it -- deterministic, which is all the scan order has to be.
        assert _names(found) == ["c.csv", "a.csv", "b.csv"]

    def test_a_file_whose_extension_lost_its_dot_is_not_found(self, tmp_path):
        # The real "HADH01csv" in the sample folder, a dot lost in a copy.
        (tmp_path / "HADH01csv").write_text("x")

        assert discover_csv_files(str(tmp_path)) == []


class TestScanFolder:
    def test_newest_run_first(self, tmp_path):
        write_datalog(tmp_path, name="a_older.csv")
        write_datalog(tmp_path, _shift_to_next_day(DATALOG_SHORT), name="b_newer.csv")

        runs = scan_folder(str(tmp_path))

        # Display order, not scan order: lexicographic path order would have put
        # the older run first.
        assert _names(r.path for r in runs) == ["b_newer.csv", "a_older.csv"]

    def test_a_csv_that_is_not_a_datalog_is_dropped_silently(self, tmp_path):
        write_datalog(tmp_path)
        (tmp_path / "shopping_list.csv").write_text("eggs,milk\n1,2\n", encoding="utf-8")

        runs = scan_folder(str(tmp_path))

        assert len(runs) == 1

    def test_a_run_archived_mid_scan_costs_one_row_not_the_page(self, tmp_path, monkeypatch):
        # The controller writes into this tree while the page is watching it, so
        # a file really can vanish between the directory walk and its stat.
        kept = write_datalog(tmp_path, name="kept.csv")
        vanished = write_datalog(tmp_path, name="vanished.csv")
        real_signature = scanner._file_signature

        def _signature(path):
            if path == vanished:
                raise OSError("file vanished between the walk and the stat")
            return real_signature(path)

        monkeypatch.setattr(scanner, "_file_signature", _signature)

        runs = scan_folder(str(tmp_path))

        assert [r.path for r in runs] == [kept]


class TestDetectPvSvPairs:
    def test_pairs_come_back_in_header_order_not_alphabetical(self):
        columns = _HEADER.split(",")

        pairs = detect_pv_sv_pairs(columns)

        assert pairs == [
            ("Heater", "Heater PV", "Heater SV"),
            ("MFC-1", "MFC-1 PV", "MFC-1 SV"),
            ("P1", "P1 PV", "P1 SV"),
        ]

    def test_a_pv_with_no_sv_is_not_a_pair(self):
        assert detect_pv_sv_pairs(["Time", "Heater PV"]) == []

    def test_an_sv_with_no_pv_is_not_a_pair(self):
        assert detect_pv_sv_pairs(["Time", "Heater SV"]) == []

    def test_a_longer_prefix_does_not_swallow_a_shorter_one(self):
        # P1 and P1_H are both real pairs on this tool. A substring rule would
        # collapse them; the suffix-anchored one keeps them independent.
        columns = ["P1_H PV", "P1_H SV", "P1 PV", "P1 SV"]

        pairs = detect_pv_sv_pairs(columns)

        assert pairs == [("P1_H", "P1_H PV", "P1_H SV"), ("P1", "P1 PV", "P1 SV")]

    @pytest.mark.parametrize("pv_column", [
        "PV",          # no prefix, so no space before the suffix
        "Heater pv",   # the match is case-sensitive
        "HeaterPV",    # the space is part of the suffix
        "PV Total",    # the suffix is anchored at the end
    ])
    def test_the_suffix_rule_is_exact(self, pv_column):
        sv_column = pv_column.replace("PV", "SV").replace("pv", "sv")

        assert detect_pv_sv_pairs([pv_column, sv_column]) == []


class TestGetPlainNumericChannels:
    def test_everything_no_pair_claimed_in_header_order(self):
        columns = _HEADER.split(",")
        pairs = detect_pv_sv_pairs(columns)

        plain = get_plain_numeric_channels(columns, pairs)

        assert plain == ["Tube Pressure", "Stage Rot"]

    def test_time_and_the_categoricals_are_never_plain_channels(self):
        columns = ["Time", "Auto Action", "Program", "651C Gauge", "Stage Pos"]

        assert get_plain_numeric_channels(columns, []) == ["Stage Pos"]

    def test_a_pair_left_out_of_the_list_falls_back_to_a_plain_channel(self):
        # The caller decides what counts as consumed -- the degree of freedom
        # that lets one half of a pair be plotted on its own.
        columns = ["Time", "Heater PV", "Heater SV"]

        plain = get_plain_numeric_channels(columns, [])

        assert plain == ["Heater PV", "Heater SV"]
