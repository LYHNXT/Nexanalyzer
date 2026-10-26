"""
Integration test for the Material Presets page (pages/3_Material_Presets.py).

This page's Save button writes the committed data/materials.json, so a break
here corrupts the store every other page reads. Until v4.0.0 nothing exercised
it -- `test_pages_render` only proved the page renders -- and the page was
rewritten wholesale for the nested schema, which is exactly the change that
needs a round trip rather than a smoke test.

`get_presets_path` is redirected at the module the page's `save_presets` and
`load_store` both call it from, so the real store is never touched.
"""

import json

import pytest
from streamlit.testing.v1 import AppTest

from core.paths import PROJECT_ROOT

import modules.spectra.io.preset_store as preset_store
from modules.spectra.models.preset import (
    MaterialPreset,
    OpticalParams,
    PeakTemplate,
    TechniquePreset,
)

PAGE = str(PROJECT_ROOT / "pages/3_Material_Presets.py")


def _peak(label="E2g", center=250.0, color="#1f77b4"):
    return PeakTemplate(peak_label=label, center=center, center_tolerance=7.0,
                        width_fwhm=4.0, shape=0.3, color=color)


@pytest.fixture
def store(tmp_path, monkeypatch):
    """A throwaway store, seeded with one material that has all three blocks."""
    path = tmp_path / "materials.json"
    monkeypatch.setattr(preset_store, "get_presets_path", lambda: path)

    preset_store.save_presets({
        "WSe2": MaterialPreset(
            material_name="WSe2",
            enabled=True,
            description="WSe2 Raman / WSe2 PL peaks",
            raman=TechniquePreset(despike_threshold=8.0, baseline_algorithm="ALS",
                                  baseline_lambda=50000.0, baseline_p=0.01,
                                  x_range_enabled=True, x_min=6.0, x_max=400.0,
                                  peak_templates=[_peak(), _peak("2LA", 260.0)]),
            pl=TechniquePreset(despike_threshold=8.0,
                               peak_templates=[_peak("Exciton", 770.0)]),
            optical={"2L": OpticalParams(nsigma=4.0, minpx=3)},
        ),
    })
    return path


def _read(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


class TestTheEditorRoundTrips:
    def test_the_page_renders_an_existing_material(self, store):
        at = AppTest.from_file(PAGE, default_timeout=60).run()

        assert not at.exception, [e.value for e in at.exception]
        assert any("WSe2" in e.label for e in at.expander)

    def test_saving_without_editing_changes_nothing(self, store):
        """The strongest round-trip assertion available: open the editor, press
        Save, and the file must come back byte-for-byte."""
        before = _read(store)

        at = AppTest.from_file(PAGE, default_timeout=60).run()
        next(b for b in at.button if b.key == "edit_WSe2_save").click().run()

        assert not at.exception, [e.value for e in at.exception]
        assert _read(store) == before

    def test_peak_rows_survive_a_save(self, store):
        at = AppTest.from_file(PAGE, default_timeout=60).run()
        next(b for b in at.button if b.key == "edit_WSe2_save").click().run()

        raman = _read(store)["materials"][0]["raman"]
        assert [p["peak_label"] for p in raman["peak_templates"]] == ["E2g", "2LA"]
        assert raman["peak_templates"][0]["center_tolerance"] == 7.0

    def test_the_optical_block_survives_a_save(self, store):
        at = AppTest.from_file(PAGE, default_timeout=60).run()
        next(b for b in at.button if b.key == "edit_WSe2_save").click().run()

        assert _read(store)["materials"][0]["optical"] == {"2L": {"nsigma": 4.0, "minpx": 3}}

    def test_the_saved_file_is_still_schema_v2(self, store):
        at = AppTest.from_file(PAGE, default_timeout=60).run()
        next(b for b in at.button if b.key == "edit_WSe2_save").click().run()

        assert _read(store)["schema_version"] == preset_store.SCHEMA_VERSION


class TestBlocksDoNotBleedIntoEachOther:
    """Two `st.data_editor`s in one expander sharing a widget key is not an
    error Streamlit reports -- it silently serves one block's rows into the
    other. Namespacing the keys by block is what prevents it."""

    def test_raman_and_pl_peak_editors_have_distinct_keys(self, store):
        at = AppTest.from_file(PAGE, default_timeout=60).run()

        assert "edit_WSe2_raman_peaks" in at.session_state
        assert "edit_WSe2_pl_peaks" in at.session_state

    def test_the_two_blocks_keep_their_own_rows_after_a_save(self, store):
        at = AppTest.from_file(PAGE, default_timeout=60).run()
        next(b for b in at.button if b.key == "edit_WSe2_save").click().run()

        material = _read(store)["materials"][0]
        assert [p["peak_label"] for p in material["raman"]["peak_templates"]] == ["E2g", "2LA"]
        assert [p["peak_label"] for p in material["pl"]["peak_templates"]] == ["Exciton"]

    def test_the_two_layers_have_distinct_optical_widget_keys(self, store):
        at = AppTest.from_file(PAGE, default_timeout=60).run()

        assert "edit_WSe2_optical_1L_nsigma" in at.session_state
        assert "edit_WSe2_optical_2L_nsigma" in at.session_state


class TestEnabledIsAControlNow:
    """Until v4.0.0 the page hardcoded enabled=True on save, so nothing could
    write False and editing a hand-disabled preset silently re-enabled it."""

    def test_a_disabled_material_stays_disabled_across_a_save(self, store):
        presets = preset_store.load_presets()
        presets["WSe2"].enabled = False
        preset_store.save_presets(presets)

        at = AppTest.from_file(PAGE, default_timeout=60).run()
        next(b for b in at.button if b.key == "edit_WSe2_save").click().run()

        assert _read(store)["materials"][0]["enabled"] is False

    def test_the_checkbox_reflects_the_stored_value(self, store):
        presets = preset_store.load_presets()
        presets["WSe2"].enabled = False
        preset_store.save_presets(presets)

        at = AppTest.from_file(PAGE, default_timeout=60).run()

        assert at.session_state["edit_WSe2_enabled"] is False


class TestFitWindowsSurviveTheEditor:
    """A block's fit windows (v5.22.0) have their own table; Save must keep them."""

    def test_windows_and_their_baseline_overrides_survive_a_save(self, store):
        from modules.spectra.models.preset import FitWindow

        presets = preset_store.load_presets()
        presets["WSe2"].raman.fit_windows = [
            FitWindow(6.0, 200.0),
            FitWindow(200.0, 400.0, baseline_algorithm="Polynomial", baseline_degree=1,
                      exclusion_ranges="240-270"),
        ]
        preset_store.save_presets(presets)
        before = _read(store)

        at = AppTest.from_file(PAGE, default_timeout=60).run()
        next(b for b in at.button if b.key == "edit_WSe2_save").click().run()

        assert not at.exception, [e.value for e in at.exception]
        assert _read(store) == before
        assert len(before["materials"][0]["raman"]["fit_windows"]) == 2

    def test_energy_gaps_survive_a_save(self, store):
        from modules.spectra.models.preset import EnergyGap

        presets = preset_store.load_presets()
        presets["WSe2"].pl.peak_templates = [_peak("Exciton", 765.0), _peak("Trion", 780.0, "#9467bd")]
        presets["WSe2"].pl.energy_gaps = [EnergyGap("Exciton", "Trion", 35.0, 10.0)]
        preset_store.save_presets(presets)
        before = _read(store)

        at = AppTest.from_file(PAGE, default_timeout=60).run()
        next(b for b in at.button if b.key == "edit_WSe2_save").click().run()

        assert not at.exception, [e.value for e in at.exception]
        assert _read(store) == before
        assert before["materials"][0]["pl"]["energy_gaps"] == [
            {"upper": "Exciton", "lower": "Trion", "gap_meV": 35.0, "tolerance_meV": 10.0}]
