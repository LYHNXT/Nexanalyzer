"""Tests pinned to REVIEW.md (2026-11-04) findings.

Each `xfail(strict=True)` states the behaviour the review asks for and fails
today for the reason its row documents. When the fix lands the test XPASSes,
strict turns that into a failure, and the marker is removed in the same PR --
so a finding cannot be fixed without its test, or forgotten while it stands.
The fixes to #1, #3 and #4 move numbers: version bump, CHANGELOG, golden
regeneration (docs/Development_Process.md section 2 and 6).

The tests that are not marked pass today: they pin behaviour the review
checked and found right, so it stays right.
"""

import numpy as np
import pytest
from lmfit.lineshapes import voigt

from modules.spectra.models.peak import PeakDefinition
from modules.spectra.processing import parser
from modules.spectra.processing.despiking import remove_spikes
from modules.spectra.processing.fitting import fit_voigt_peaks
from modules.spectra.processing.peak_metrics import (
    peak_intensity,
    ratio_values_by_point,
    raw_peak_stats,
)
from modules.spectra.processing.sample_scanner import scan_sample_folder


def _three_column_file(tmp_path, first_row):
    rows = [f"{100 + i:.1f}\t{10 + i:.1f}\t{20 + i:.1f}" for i in range(120)]
    rows[0] = first_row
    path = tmp_path / "RM_1.txt"
    path.write_text("\n".join(rows), encoding="utf-8")
    return str(path)


class TestFinding1RamanDespikerRemovesAWholeRay:
    """REVIEW.md #1: `remove_spikes` (the Raman path, auto_workflow.py:211)
    replaces a flagged pixel by the median of its 4 neighbours, flagged ones
    included. One or two flagged neighbours are outvoted; from three pixels
    wide the median is itself on the ray and the ray survives cleaning."""

    def _ray(self, width):
        rng = np.random.default_rng(0)
        y = 100.0 + rng.normal(0.0, 2.0, 400)
        y[200:200 + width] += 2000.0
        return y

    @pytest.mark.parametrize("width", [1, 2])
    def test_a_ray_up_to_two_pixels_wide_is_removed(self, width):
        cleaned, mask = remove_spikes(self._ray(width), threshold=100.0)
        assert mask[200:200 + width].all()
        np.testing.assert_allclose(cleaned[200:200 + width], 100.0, atol=10.0)

    @pytest.mark.parametrize("width", [3, 4, 5])
    def test_a_ray_three_to_five_pixels_wide_is_removed_to_the_noise(self, width):
        """Fixed in v5.54.0; until then a 3-px ray kept +1000 of its +2000. A
        flagged run wider than the window (5) is treated as a real peak."""
        cleaned, mask = remove_spikes(self._ray(width), threshold=100.0)
        assert mask[200:200 + width].all()
        np.testing.assert_allclose(cleaned[200:200 + width], 100.0, atol=10.0)


class TestFinding2OneBadCellMustNotLoseASpectrum:
    """REVIEW.md #2: parser.py:222-233 swallows a column's error; only "no
    usable columns" is ever raised."""

    def test_a_complete_three_column_file_gives_two_spectra(self, tmp_path):
        path = _three_column_file(tmp_path, "100.0\t10.0\t20.0")
        assert len(parser.parse_spectrum_multi(path)) == 2

    @pytest.mark.xfail(strict=True, reason="REVIEW.md #2: the column with the gap is dropped without a word")
    @pytest.mark.parametrize("first_row", ["100.0\t10.0\t", "100.0\t\t20.0"], ids=["trailing gap", "middle gap"])
    def test_a_gap_in_one_column_is_reported_not_swallowed(self, tmp_path, first_row):
        path = _three_column_file(tmp_path, first_row)
        try:
            spectra = parser.parse_spectrum_multi(path)
        except parser.SpectrumFileError as exc:
            assert "column" in str(exc).lower()
        else:
            assert len(spectra) == 2, "fewer spectra than Y columns, and no error"

    @pytest.mark.xfail(strict=True, reason="REVIEW.md #2: float() accepts nan, inf and 1_000")
    @pytest.mark.parametrize("line", ["nan\tnan", "inf\t1.0", "1_000\t2_000"])
    def test_non_data_float_literals_are_not_a_numeric_row(self, line):
        assert not parser._is_numeric_row(line, "\t")

    def test_a_decimal_comma_file_is_refused_not_misread(self, tmp_path):
        """'1,5;2,3' must not parse as X=1, Y=5 or similar."""
        rows = [f"{100 + i},{i % 10}\t{10 + i},5" for i in range(120)]
        path = tmp_path / "RM_1.txt"
        path.write_text("\n".join(rows), encoding="utf-8")
        with pytest.raises(parser.SpectrumFileError):
            parser.parse_spectrum_multi(str(path))


class TestFinding3IntensityIsTheHeightNotTheTallestSample:
    """REVIEW.md #3: peak_intensity is max(component_curve); a narrow peak
    whose centre falls between grid points is under-read by up to 2.4 % at
    FWHM 5 on a 0.9 cm-1 grid, and the error moves with the grid phase."""

    SIGMA, GAMMA, AREA = 1.0, 1.5, 5000.0
    HEIGHT = float(voigt(np.array([0.0]), AREA, 0.0, SIGMA, GAMMA)[0])

    def _fit_at(self, phase):
        x = 100.0 + 0.9 * np.arange(334) + phase
        center = 250.3                       # on the grid at phase 0, mid-way at 0.45
        y = voigt(x, self.AREA, center, self.SIGMA, self.GAMMA)
        peak = PeakDefinition(center=251.0, intensity=1.0, width_fwhm=5.0, label="p", center_tolerance=5.0)
        result = fit_voigt_peaks(x, y, [peak], mode="Raman")
        assert result.success
        return result.fitted_peaks[0]

    def test_on_the_grid_the_sampled_maximum_is_the_height(self):
        assert peak_intensity(self._fit_at(0.0)) == pytest.approx(self.HEIGHT, rel=0.002)

    @pytest.mark.xfail(strict=True, reason="REVIEW.md #3: sampled maximum under-reads an off-grid centre by ~2 %")
    def test_between_grid_points_the_intensity_is_still_the_height(self):
        assert peak_intensity(self._fit_at(0.45)) == pytest.approx(self.HEIGHT, rel=0.005)


class TestFinding4RawFwhm:
    """REVIEW.md #4: raw_peak_stats measures first-to-last sample above half
    maximum, with no interpolation."""

    @pytest.mark.xfail(strict=True, reason="REVIEW.md #4b: width is quantised to the sample spacing")
    def test_the_half_maximum_crossing_is_interpolated(self):
        x = np.arange(600.0, 800.0, 1.0)
        y = 100.0 * np.exp(-4.0 * np.log(2.0) * ((x - 760.3) / 39.0) ** 2)
        assert raw_peak_stats(x, y).fwhm == pytest.approx(39.0, abs=0.2)

    @pytest.mark.xfail(strict=True, reason="REVIEW.md #4a (needs decision): a second band above half max is spanned")
    def test_a_second_band_above_half_maximum_does_not_widen_the_main_one(self):
        x = np.arange(600.0, 800.0, 0.5)
        g = lambda c, w: np.exp(-4.0 * np.log(2.0) * ((x - c) / w) ** 2)  # noqa: E731
        y = 100.0 * g(760.0, 30.0) + 60.0 * g(700.0, 20.0)
        assert raw_peak_stats(x, y).fwhm == pytest.approx(30.0, abs=1.0)

    def test_one_band_measures_its_own_width(self):
        x = np.arange(600.0, 800.0, 0.5)
        y = 100.0 * np.exp(-4.0 * np.log(2.0) * ((x - 760.0) / 30.0) ** 2)
        assert raw_peak_stats(x, y).fwhm == pytest.approx(30.0, abs=0.5)


class TestFinding6DuplicatePointsAreNotSilent:
    """REVIEW.md #6: two files resolving to one point overwrite each other."""

    @pytest.mark.xfail(strict=True, reason="REVIEW.md #6: the losing file is neither used nor listed")
    def test_the_second_file_for_a_point_is_listed_as_ignored(self, tmp_path):
        (tmp_path / "RM_1.txt").write_text("x")
        (tmp_path / "Raman_1.txt").write_text("x")
        scan = scan_sample_folder(str(tmp_path))
        assert len(scan.raman_files) == 1
        assert any(name in scan.ignored_files for name in ("RM_1.txt", "Raman_1.txt"))


class TestRatioSelectionEdges:
    """Checked and right in the review; pinned so it stays so."""

    def test_a_zero_or_nan_denominator_yields_no_ratio(self):
        spectra = [
            (1, {"LA": (10.0, True), "E2g+A1g": (0.0, True)}),
            (1, {"LA": (10.0, True), "E2g+A1g": (float("nan"), True)}),
            (2, {"LA": (10.0, True), "E2g+A1g": (100.0, True)}),
        ]
        out = ratio_values_by_point(spectra, "LA", "E2g+A1g")
        assert 1 not in out
        np.testing.assert_allclose(out[2], [0.1])

    def test_an_unmeasured_peak_is_left_out_unless_the_pair_is_formed_from_every_spectrum(self):
        spectra = [(1, {"C": (10.0, False), "LB": (20.0, True)}),
                   (1, {"LA": (10.0, False), "E2g+A1g": (100.0, True)})]
        assert ratio_values_by_point(spectra, "C", "LB") == {}
        np.testing.assert_allclose(ratio_values_by_point(spectra, "LA", "E2g+A1g")[1], [0.1])
