"""Unit tests for modules.spectra.io.preset_store (JSON-backed material preset storage)."""

import pytest

from modules.spectra.io.preset_store import load_presets, save_presets
from modules.spectra.models.preset import (
    EnergyGap,
    FitWindow,
    MaterialPreset,
    PeakTemplate,
    TechniquePreset,
    parse_exclusion_ranges,
)


def _make_block():
    return TechniquePreset(
        x_range_enabled=False,
        x_min=None,
        x_max=None,
        despike_threshold=6.0,
        baseline_algorithm="Polynomial",
        baseline_degree=5,
        baseline_lambda=None,
        baseline_p=None,
        peak_templates=[
            PeakTemplate(peak_label="Si", center=520.0, center_tolerance=3.0,
                         width_fwhm=8.0, shape=0.2, color="#2ca02c")
        ],
    )


def _make_preset(material_name="Silicon", mode="Raman"):
    blocks = {"raman": None, "pl": None}
    blocks["raman" if mode == "Raman" else "pl"] = _make_block()
    return MaterialPreset(material_name=material_name, enabled=True, **blocks)


class TestLoadSaveRoundTrip:
    def test_round_trip_preserves_data(self, tmp_path):
        path = tmp_path / "materials.json"
        presets = {"Silicon": _make_preset()}

        save_presets(presets, path=path)
        loaded = load_presets(path=path)

        assert set(loaded.keys()) == {"Silicon"}
        block = loaded["Silicon"].block_for("Raman")
        assert block.despike_threshold == 6.0
        assert block.baseline_degree == 5
        assert len(block.peak_templates) == 1
        assert block.peak_templates[0].peak_label == "Si"
        assert loaded["Silicon"].block_for("PL") is None

    def test_missing_file_returns_empty_dict(self, tmp_path):
        assert load_presets(path=tmp_path / "does_not_exist.json") == {}

    def test_multiple_presets_sorted_on_save(self, tmp_path):
        path = tmp_path / "materials.json"
        presets = {
            "WSe2": _make_preset("WSe2", "Raman"),
            "MoS2": _make_preset("MoS2", "Raman"),
        }

        save_presets(presets, path=path)
        loaded = load_presets(path=path)

        assert set(loaded.keys()) == {"WSe2", "MoS2"}


class TestPeakTemplateHasNoIntensity:
    def test_intensity_field_removed(self):
        # v2.11.0: the initial-guess field was dropped from PeakTemplate since the fitter
        # auto-estimates it from data and never consults the preset value.
        template = PeakTemplate(peak_label="Si", center=520.0, center_tolerance=3.0,
                                 width_fwhm=8.0, shape=0.2, color="#2ca02c")
        assert not hasattr(template, "amplitude")
        assert not hasattr(template, "intensity")

    def test_to_peak_definition_still_produces_valid_intensity(self):
        template = PeakTemplate(peak_label="Si", center=520.0, center_tolerance=3.0,
                                 width_fwhm=8.0, shape=0.2, color="#2ca02c")
        peak_def = template.to_peak_definition(
            mode="Raman", x_range=(100.0, 1000.0), y_max=1000.0, spectral_resolution=1.0
        )
        assert peak_def.intensity > 0


class TestParseExclusionRanges:
    def test_parses_multiple_ranges(self):
        assert parse_exclusion_ranges("1200-1400; 2600-2800") == [(1200.0, 1400.0), (2600.0, 2800.0)]

    def test_none_or_empty_returns_empty_list(self):
        assert parse_exclusion_ranges(None) == []
        assert parse_exclusion_ranges("") == []

    def test_invalid_range_raises(self):
        with pytest.raises(ValueError):
            parse_exclusion_ranges("not-a-range-format-1")


class TestDominantPeak:
    """v5.26.0: `dominant_peak` round-trips, is omitted when unset, and must name a peak."""

    def test_round_trips_and_is_omitted_when_unset(self):
        block = _make_block()
        assert "dominant_peak" not in block.to_dict()
        block.dominant_peak = "Si"
        assert TechniquePreset.from_dict(block.to_dict()).dominant_peak == "Si"

    def test_must_name_one_of_the_blocks_peaks(self):
        block = _make_block()
        block.dominant_peak = "Si"
        assert block.validate() == []
        block.dominant_peak = "A0"
        assert any("dominant_peak" in e for e in block.validate())

    def test_committed_mos2_qc_holds_a0_dominant(self):
        assert load_presets()["MoS2_QC"].pl.dominant_peak == "A0"


class TestEnergyGaps:
    """v5.46.0: `energy_gaps` round-trips, is omitted when empty, and is validated."""

    @staticmethod
    def _pl_block(gaps, upper=765.0, lower=780.0):
        return TechniquePreset(
            baseline_algorithm="None (Skip)",
            peak_templates=[
                PeakTemplate(peak_label="Exciton", center=upper, center_tolerance=15.0,
                             width_fwhm=30.0, shape=0.3, color="#d62728"),
                PeakTemplate(peak_label="Trion", center=lower, center_tolerance=15.0,
                             width_fwhm=40.0, shape=0.3, color="#9467bd"),
            ],
            energy_gaps=gaps,
        )

    def test_round_trips_and_is_omitted_when_empty(self):
        assert "energy_gaps" not in self._pl_block([]).to_dict()
        block = self._pl_block([EnergyGap("Exciton", "Trion", 35.0, 10.0)])
        assert TechniquePreset.from_dict(block.to_dict()).energy_gaps == block.energy_gaps
        assert block.validate() == []

    @pytest.mark.parametrize("gap, needle", [
        (EnergyGap("Exciton", "nope", 35.0, 10.0), "not one of this block's peak labels"),
        (EnergyGap("Trion", "Trion", 35.0, 10.0), "two different peaks"),
        (EnergyGap("Exciton", "Trion", 0.0, 0.0), "gap_meV must be > 0"),
        (EnergyGap("Exciton", "Trion", 35.0, -1.0), "tolerance_meV must be in"),
        (EnergyGap("Exciton", "Trion", 35.0, 35.0), "tolerance_meV must be in"),
        (EnergyGap("Exciton", "Trion", 20.0, 5.0), "outside 20 +- 5 meV"),  # templates are 31 meV apart
    ])
    def test_rejects(self, gap, needle):
        assert any(needle in e for e in self._pl_block([gap]).validate())

    def test_a_peak_cannot_be_the_lower_of_two_gaps(self):
        gaps = [EnergyGap("Exciton", "Trion", 35.0, 10.0), EnergyGap("Exciton", "Trion", 30.0, 10.0)]
        assert any("already the lower peak" in e for e in self._pl_block(gaps).validate())

    def test_a_gap_cannot_span_two_fit_windows(self):
        block = self._pl_block([EnergyGap("Exciton", "Trion", 35.0, 10.0)])
        block.fit_windows = [FitWindow(700.0, 772.0), FitWindow(772.0, 850.0)]
        assert any("different fit windows" in e for e in block.validate())

    def test_a_raman_block_cannot_hold_gaps(self):
        preset = MaterialPreset(material_name="X", enabled=True, raman=self._pl_block(
            [EnergyGap("Exciton", "Trion", 35.0, 10.0)]))
        assert any("PL only" in e for e in preset.validate())

    def test_committed_qc_presets_hold_the_agreed_gaps(self):
        presets = load_presets()
        assert presets["WSe2_QC"].pl.energy_gaps == [EnergyGap("Exciton", "Trion", 35.0, 10.0)]
        assert presets["MoS2_QC"].pl.energy_gaps == [EnergyGap("A0", "A-", 20.0, 10.0)]
        assert presets["WSe2_QC"].validate() == [] and presets["MoS2_QC"].validate() == []
