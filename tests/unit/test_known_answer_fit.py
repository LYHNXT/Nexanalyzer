"""Known-answer tests for the Voigt fitter: a true Voigt in, its parameters out.

tests/unit/test_fitting.py checks that fits converge and reach an R²; nothing
there asserts that the *numbers* come back. These build the spectrum from
lmfit's own `voigt` lineshape -- the fitter's model -- so centre, Voigt FWHM
(`fitting.voigt_fwhm`), intensity (the analytic height) and area are known
exactly, and a fit that "runs fine but returns the wrong number" is caught.
Tolerances are the portable ones from tests/golden/README.md.
"""

import numpy as np
import pytest
from lmfit.lineshapes import voigt

from modules.spectra.models.peak import PeakDefinition
from modules.spectra.processing.fitting import fit_voigt_peaks, voigt_fwhm
from modules.spectra.processing.peak_metrics import peak_intensity

CENTER_TOL = 0.1       # cm-1 / nm
FWHM_TOL = 0.05
RELATIVE_TOL = 0.01    # intensity and area


def _height(area, sigma, gamma):
    return float(voigt(np.array([0.0]), area, 0.0, sigma, gamma)[0])


def _spectrum(x, peaks):
    """`peaks`: (center, sigma, gamma, area) tuples, summed on a flat zero baseline."""
    y = np.zeros_like(x)
    for center, sigma, gamma, area in peaks:
        y += voigt(x, area, center, sigma, gamma)
    return y


def _peak(result, label):
    return next(p for p in result.fitted_peaks if p.label == label)


class TestASingleVoigtComesBackAsItself:
    # WSe2 E2g+A1g-like: narrow (Voigt FWHM ~4.3 cm-1) on a 0.9 cm-1 grid, the
    # hard case for a sampled maximum. The centre sits on a grid point, so the
    # sampled maximum equals the analytic height; off-grid centres are
    # REVIEW.md 2026-11-04 #3 (tests/unit/test_review_regressions.py).
    X = 100.0 + 0.9 * np.arange(334)
    CENTER, SIGMA, GAMMA, AREA = float(X[167]), 1.0, 1.5, 5000.0

    @pytest.fixture
    def fitted(self):
        y = _spectrum(self.X, [(self.CENTER, self.SIGMA, self.GAMMA, self.AREA)])
        peak = PeakDefinition(center=self.CENTER - 1.5, intensity=1.0, width_fwhm=5.0,
                              label="E2g+A1g", center_tolerance=5.0)
        result = fit_voigt_peaks(self.X, y, [peak], mode="Raman")
        assert result.success and result.converged
        return _peak(result, "E2g+A1g")

    def test_the_centre_is_recovered(self, fitted):
        assert fitted.center == pytest.approx(self.CENTER, abs=CENTER_TOL)

    def test_the_width_is_the_voigt_fwhm_not_the_gaussian_part(self, fitted):
        # 2.355 * sigma alone would be 2.35 here; the Voigt width is 4.34.
        assert fitted.width_fwhm == pytest.approx(voigt_fwhm(self.SIGMA, self.GAMMA), abs=FWHM_TOL)
        assert fitted.width_fwhm > 2.355 * self.SIGMA * 1.5

    def test_intensity_is_the_height_and_area_is_the_integral(self, fitted):
        # Two quantities, two names (CLAUDE.md): area = height x FWHM x k, with
        # k from 1.064 (pure Gaussian) to pi/2 (pure Lorentzian).
        assert peak_intensity(fitted) == pytest.approx(_height(self.AREA, self.SIGMA, self.GAMMA),
                                                       rel=RELATIVE_TOL)
        assert fitted.area == pytest.approx(self.AREA, rel=RELATIVE_TOL)
        k = fitted.area / (peak_intensity(fitted) * fitted.width_fwhm)
        assert 1.064 <= k <= np.pi / 2

    def test_a_peak_the_data_support_is_not_on_a_limit(self, fitted):
        assert not fitted.on_limit


class TestTwoPeaksOfDifferentWidthsAreSeparated:
    """A narrow strong peak and a broad weak one 20 cm-1 apart -- the shape of
    WSe2's E2g+A1g beside its LA hump, where intensity and area ratios differ
    ~4x and a width mix-up would move every ratio."""

    X = 100.0 + 0.9 * np.arange(334)
    NARROW = (float(X[167]), 1.0, 1.5, 5000.0)      # ~250.3 cm-1, FWHM 4.3
    BROAD = (float(X[145]), 6.0, 6.0, 4000.0)       # ~230.5 cm-1, FWHM ~21

    @pytest.fixture
    def result(self):
        y = _spectrum(self.X, [self.NARROW, self.BROAD])
        peaks = [
            PeakDefinition(center=251.0, intensity=1.0, width_fwhm=5.0, label="E2g+A1g", center_tolerance=5.0),
            PeakDefinition(center=228.0, intensity=1.0, width_fwhm=20.0, label="LA", center_tolerance=10.0),
        ]
        result = fit_voigt_peaks(self.X, y, peaks, mode="Raman")
        assert result.success and result.converged
        return result

    @pytest.mark.parametrize("label, truth", [("E2g+A1g", NARROW), ("LA", BROAD)])
    def test_each_peak_recovers_its_own_parameters(self, result, label, truth):
        center, sigma, gamma, area = truth
        peak = _peak(result, label)
        assert peak.center == pytest.approx(center, abs=CENTER_TOL)
        assert peak.width_fwhm == pytest.approx(voigt_fwhm(sigma, gamma), abs=FWHM_TOL)
        assert peak_intensity(peak) == pytest.approx(_height(area, sigma, gamma), rel=RELATIVE_TOL)
        assert peak.area == pytest.approx(area, rel=RELATIVE_TOL)

    def test_the_intensity_ratio_is_not_the_area_ratio(self, result):
        """The two ratios differ by the width ratio (~5x here), times at most the
        Gaussian/Lorentzian shape factor (1.064 .. pi/2) of each peak."""
        la, main = _peak(result, "LA"), _peak(result, "E2g+A1g")
        intensity_ratio = peak_intensity(la) / peak_intensity(main)
        area_ratio = la.area / main.area
        width_ratio = la.width_fwhm / main.width_fwhm
        assert area_ratio > 2.0 * intensity_ratio
        assert 1.064 / (np.pi / 2) <= (area_ratio / intensity_ratio) / width_ratio <= (np.pi / 2) / 1.064


class TestAPlExcitonInNanometres:
    X = 700.0 + 0.5 * np.arange(241)
    CENTER, SIGMA, GAMMA, AREA = float(X[110]), 6.0, 4.0, 20000.0

    def test_centre_width_and_height_come_back(self):
        y = _spectrum(self.X, [(self.CENTER, self.SIGMA, self.GAMMA, self.AREA)])
        peak = PeakDefinition(center=757.0, intensity=1.0, width_fwhm=20.0, label="Exciton", center_tolerance=15.0)
        result = fit_voigt_peaks(self.X, y, [peak], mode="PL")
        assert result.success and result.converged
        fitted = _peak(result, "Exciton")
        assert fitted.center == pytest.approx(self.CENTER, abs=CENTER_TOL)
        assert fitted.width_fwhm == pytest.approx(voigt_fwhm(self.SIGMA, self.GAMMA), abs=FWHM_TOL)
        assert peak_intensity(fitted) == pytest.approx(_height(self.AREA, self.SIGMA, self.GAMMA), rel=RELATIVE_TOL)


class TestInvariances:
    X = 100.0 + 0.9 * np.arange(334)
    TRUTH = (float(X[167]), 1.0, 1.5, 5000.0)

    def _fit(self, x, y):
        peak = PeakDefinition(center=251.0, intensity=1.0, width_fwhm=5.0, label="p", center_tolerance=5.0)
        result = fit_voigt_peaks(x, y, [peak], mode="Raman")
        assert result.success
        return _peak(result, "p")

    def test_a_descending_axis_gives_the_same_fit(self):
        """Some exports run high-to-low; nothing sorts X. (A randomly ordered
        file is a different matter: the fitter reads the grid spacing from
        neighbouring points and refuses one whose spacing looks like 100.)"""
        y = _spectrum(self.X, [self.TRUTH])
        ascending, descending = self._fit(self.X, y), self._fit(self.X[::-1], y[::-1])
        assert descending.center == pytest.approx(ascending.center, abs=1e-6)
        assert descending.width_fwhm == pytest.approx(ascending.width_fwhm, abs=1e-6)
        assert descending.area == pytest.approx(ascending.area, rel=1e-6)

    @pytest.mark.parametrize("factor", [0.01, 7.0, 350.0])
    def test_scaling_the_counts_scales_intensity_and_area_only(self, factor):
        y = _spectrum(self.X, [self.TRUTH])
        base, scaled = self._fit(self.X, y), self._fit(self.X, y * factor)
        assert scaled.center == pytest.approx(base.center, abs=CENTER_TOL)
        assert scaled.width_fwhm == pytest.approx(base.width_fwhm, abs=FWHM_TOL)
        assert peak_intensity(scaled) == pytest.approx(factor * peak_intensity(base), rel=RELATIVE_TOL)
        assert scaled.area == pytest.approx(factor * base.area, rel=RELATIVE_TOL)
