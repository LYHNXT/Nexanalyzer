"""Unit tests for modules.datalog.io.threshold_store (the thresholds.json sidecar)."""

import json

import pytest

from modules.datalog.io.tag_store import SidecarUnreadable

from modules.datalog.io.threshold_store import (
    DEFAULT_TIMING_FLOOR_SEC,
    DEFAULT_TIMING_TOLERANCE_PCT,
    DEFAULT_TOLERANCE_PCT,
    THRESHOLDS_FILENAME,
    get_thresholds_path,
    load_thresholds,
    merge_overrides,
    save_thresholds,
    save_tolerances,
)


def _write_sidecar(folder, data):
    (folder / THRESHOLDS_FILENAME).write_text(json.dumps(data), encoding="utf-8")


class TestLoadThresholds:
    def test_a_folder_with_no_sidecar_gets_the_defaults(self, tmp_path):
        assert load_thresholds(str(tmp_path)) == {
            "global_default_pct": DEFAULT_TOLERANCE_PCT,
            "overrides": {},
            "timing_tolerance_pct": DEFAULT_TIMING_TOLERANCE_PCT,
            "timing_floor_sec": DEFAULT_TIMING_FLOOR_SEC,
        }

    def test_all_four_keys_are_always_present(self, tmp_path):
        # analysis.get_tolerance_pct indexes global_default_pct directly, and
        # this is what makes that safe.
        _write_sidecar(tmp_path, {"overrides": {"Heater": 2.0}})

        thresholds = load_thresholds(str(tmp_path))

        assert set(thresholds) == {
            "global_default_pct", "overrides", "timing_tolerance_pct", "timing_floor_sec"
        }

    def test_a_two_key_file_from_an_older_build_gains_the_timing_defaults(self, tmp_path):
        _write_sidecar(tmp_path, {"global_default_pct": 3.0, "overrides": {}})

        thresholds = load_thresholds(str(tmp_path))

        assert thresholds["global_default_pct"] == 3.0
        assert thresholds["timing_floor_sec"] == DEFAULT_TIMING_FLOOR_SEC

    def test_a_stored_overrides_map_replaces_the_default_wholesale(self, tmp_path):
        # A shallow merge, deliberately: per-key merging would mean an override
        # deleted in the editor came back on the next load.
        _write_sidecar(tmp_path, {"overrides": {"Heater": 2.0}})

        assert load_thresholds(str(tmp_path))["overrides"] == {"Heater": 2.0}

    def test_a_malformed_sidecar_degrades_to_the_defaults(self, tmp_path):
        (tmp_path / THRESHOLDS_FILENAME).write_text("{ not json", encoding="utf-8")

        assert load_thresholds(str(tmp_path))["global_default_pct"] == DEFAULT_TOLERANCE_PCT

    def test_the_path_is_inside_the_operators_data_folder(self, tmp_path):
        # It travels with the data, not with this installation: another machine
        # opening the folder inherits its tolerances.
        assert get_thresholds_path(str(tmp_path)) == tmp_path / "thresholds.json"


class TestSaveThresholds:
    def test_a_saved_value_round_trips(self, tmp_path):
        save_thresholds(str(tmp_path), {"global_default_pct": 3.0, "overrides": {"Heater": 2.0}})

        thresholds = load_thresholds(str(tmp_path))

        assert thresholds["global_default_pct"] == 3.0
        assert thresholds["overrides"] == {"Heater": 2.0}

    def test_saving_a_partial_dict_keeps_the_keys_it_does_not_mention(self, tmp_path):
        # The source app wrote the dict verbatim, so saving a PV/SV tolerance
        # from the Datalog view erased the Runcard check's timing settings --
        # invisibly, because load backfills defaults. Writing a partial dict is
        # safe now, and the Runcard page depends on that.
        save_thresholds(str(tmp_path), {"timing_floor_sec": 9.0, "timing_tolerance_pct": 8.0})

        save_thresholds(str(tmp_path), {"global_default_pct": 3.0, "overrides": {}})

        thresholds = load_thresholds(str(tmp_path))
        assert thresholds["timing_floor_sec"] == 9.0
        assert thresholds["timing_tolerance_pct"] == 8.0

    def test_an_override_removed_from_the_editor_is_removed_from_the_file(self, tmp_path):
        # Merging costs the ability to delete a top-level key, which nothing
        # wants, and keeps the ability to delete an override, which the
        # tolerance editor is built on: overrides is replaced wholesale.
        save_thresholds(str(tmp_path), {"overrides": {"Heater": 2.0, "MFC-1": 10.0}})

        save_thresholds(str(tmp_path), {"overrides": {"MFC-1": 10.0}})

        assert load_thresholds(str(tmp_path))["overrides"] == {"MFC-1": 10.0}

    def test_the_file_is_written_sorted_and_without_a_bom(self, tmp_path):
        save_thresholds(str(tmp_path), {"global_default_pct": 3.0})

        raw = (tmp_path / THRESHOLDS_FILENAME).read_bytes()

        assert not raw.startswith(b"\xef\xbb\xbf")
        assert list(json.loads(raw)) == sorted(json.loads(raw))

    def test_the_temp_file_of_the_atomic_write_does_not_survive(self, tmp_path):
        save_thresholds(str(tmp_path), {"global_default_pct": 3.0})

        assert [p.name for p in tmp_path.glob("*.tmp")] == []


class TestASaveNeverWipesTheFileItCouldNotRead:
    def test_a_damaged_file_is_left_untouched(self, tmp_path, monkeypatch):
        monkeypatch.setattr("modules.datalog.io.tag_store._UPDATE_READ_RETRY_S", 0)
        damaged = '{"overrides": {"MFC-1": 9.0}, "timing_tolerance_pct": 12'
        (tmp_path / THRESHOLDS_FILENAME).write_text(damaged, encoding="utf-8")

        with pytest.raises(SidecarUnreadable):
            save_thresholds(str(tmp_path), {"global_default_pct": 4.0})

        assert (tmp_path / THRESHOLDS_FILENAME).read_text(encoding="utf-8") == damaged


class TestTheToleranceEditorOnlyRewritesTheChannelsItShows:
    def test_an_override_for_a_channel_this_run_lacks_survives(self):
        stored = {"MFC-7": 10.0, "Heater": 2.0}
        assert merge_overrides(stored, ["Heater", "MFC-1"], {"Heater": 3.0}) == {
            "MFC-7": 10.0, "Heater": 3.0,
        }

    def test_a_shown_channel_set_back_to_the_global_is_cleared(self):
        assert merge_overrides({"Heater": 2.0, "MFC-7": 10.0}, ["Heater"], {}) == {"MFC-7": 10.0}

    def test_a_new_override_is_added(self):
        assert merge_overrides({}, ["MFC-1"], {"MFC-1": 8.0}) == {"MFC-1": 8.0}

    def test_stored_is_not_modified(self):
        stored = {"Heater": 2.0}
        merge_overrides(stored, ["Heater"], {})
        assert stored == {"Heater": 2.0}

    def test_save_keeps_absent_channels_and_timing_keys_on_disk(self, tmp_path):
        _write_sidecar(tmp_path, {
            "global_default_pct": 5.0,
            "overrides": {"MFC-7": 10.0, "Heater": 2.0},
            "timing_tolerance_pct": 12.0,
        })

        save_tolerances(str(tmp_path), 4.0, ["Heater", "MFC-1"], {"MFC-1": 8.0})

        on_disk = json.loads((tmp_path / THRESHOLDS_FILENAME).read_text(encoding="utf-8"))
        assert on_disk["global_default_pct"] == 4.0
        assert on_disk["overrides"] == {"MFC-7": 10.0, "MFC-1": 8.0}
        assert on_disk["timing_tolerance_pct"] == 12.0

    def test_save_into_an_empty_folder(self, tmp_path):
        save_tolerances(str(tmp_path), 5.0, ["Heater"], {"Heater": 2.0})
        assert load_thresholds(str(tmp_path))["overrides"] == {"Heater": 2.0}

    def test_save_never_wipes_a_file_it_could_not_read(self, tmp_path, monkeypatch):
        monkeypatch.setattr("modules.datalog.io.tag_store._UPDATE_READ_RETRY_S", 0)
        damaged = '{"overrides": {"MFC-7": 9.0}, "timing_tolerance_pct": 12'
        (tmp_path / THRESHOLDS_FILENAME).write_text(damaged, encoding="utf-8")

        with pytest.raises(SidecarUnreadable):
            save_tolerances(str(tmp_path), 4.0, ["Heater"], {})

        assert (tmp_path / THRESHOLDS_FILENAME).read_text(encoding="utf-8") == damaged
