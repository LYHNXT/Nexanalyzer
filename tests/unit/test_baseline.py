"""Unit tests for modules.spectra.processing.baseline (baseline correction algorithms)."""

import numpy as np
import pytest

from modules.spectra.processing.baseline import (
    baseline_polynomial,
    baseline_polynomial_with_mask,
    baseline_als,
    baseline_als_with_mask,
)


class TestBaselinePolynomial:
    def test_flat_signal_gives_near_zero_corrected(self, synthetic_spectrum):
        x, y = synthetic_spectrum(n_points=200, peaks=(), baseline_offset=50.0)
        y_corr, baseline = baseline_polynomial(x, y, degree=1)

        assert baseline.shape == x.shape
        assert np.abs(y_corr).max() < 1.0

    def test_invalid_degree_raises(self):
        x = np.linspace(0, 10, 200)
        y = np.ones(200)
        with pytest.raises(ValueError):
            baseline_polynomial(x, y, degree=0)
        with pytest.raises(ValueError):
            baseline_polynomial(x, y, degree=11)

    def test_mismatched_lengths_raise(self):
        with pytest.raises(ValueError):
            baseline_polynomial(np.arange(10.0), np.arange(5.0), degree=2)

    def test_a_constant_offset_moves_the_baseline_not_the_corrected_spectrum(self, synthetic_spectrum):
        """Why the v2.1 auto-shift was removed (v5.34.0): a polynomial with a
        constant term follows any offset, negative included, by itself."""
        x, y = synthetic_spectrum(n_points=200, peaks=(), baseline_slope=0.05, baseline_offset=50.0)
        corrected, baseline = baseline_polynomial(x, y, degree=2)
        corrected_low, baseline_low = baseline_polynomial(x, y - 80.0, degree=2)

        np.testing.assert_allclose(baseline_low, baseline - 80.0, atol=1e-8)
        np.testing.assert_allclose(corrected_low, corrected, atol=1e-8)

    def test_with_mask_excludes_peak_region(self, synthetic_spectrum):
        x, y = synthetic_spectrum(
            n_points=300, x_range=(0.0, 300.0), peaks=((150.0, 500.0, 20.0),),
            baseline_offset=10.0,
        )
        y_corr, baseline = baseline_polynomial_with_mask(x, y, degree=1, exclusions=[(120.0, 180.0)])
        assert baseline.shape == x.shape
        # Outside the excluded/peak region the corrected signal should be near flat.
        far_from_peak = (x < 100.0) | (x > 200.0)
        assert np.abs(y_corr[far_from_peak]).max() < 5.0

    def test_with_mask_too_few_points_raises(self):
        x = np.linspace(0, 10, 100)
        y = np.ones(100)
        # Excluding almost everything leaves too few points for a degree-5 fit.
        with pytest.raises(ValueError):
            baseline_polynomial_with_mask(x, y, degree=5, exclusions=[(0.0, 9.9)])


class TestBaselineAls:
    def test_returns_correct_shapes(self, synthetic_spectrum):
        x, y = synthetic_spectrum(n_points=150, peaks=(), baseline_offset=20.0)
        y_corr, baseline = baseline_als(x, y, lambda_=1e4, p=0.01, max_iter=10)
        assert y_corr.shape == x.shape
        assert baseline.shape == x.shape

    def test_invalid_lambda_raises(self):
        x = np.linspace(0, 10, 100)
        y = np.ones(100)
        with pytest.raises(ValueError):
            baseline_als(x, y, lambda_=1.0)

    def test_invalid_p_raises(self):
        x = np.linspace(0, 10, 100)
        y = np.ones(100)
        with pytest.raises(ValueError):
            baseline_als(x, y, p=0.5)

    def test_too_few_points_raises(self):
        x = np.linspace(0, 10, 5)
        y = np.ones(5)
        with pytest.raises(ValueError):
            baseline_als(x, y)

    def test_negative_data_needs_no_shift(self, synthetic_spectrum):
        """ALS's smoothness penalty annihilates constants and its weights depend
        only on y - z, so z - c solves the system for y - c."""
        x, y = synthetic_spectrum(n_points=150, peaks=((75.0, 300.0, 15.0),), baseline_offset=10.0)
        corrected, baseline = baseline_als(x, y, lambda_=1e4, p=0.01)
        corrected_low, baseline_low = baseline_als(x, y - 40.0, lambda_=1e4, p=0.01)

        np.testing.assert_allclose(baseline_low, baseline - 40.0, atol=1e-6)
        np.testing.assert_allclose(corrected_low, corrected, atol=1e-6)

    def test_with_mask_runs_with_exclusions(self, synthetic_spectrum):
        x, y = synthetic_spectrum(
            n_points=150, x_range=(0.0, 150.0), peaks=((75.0, 300.0, 15.0),), baseline_offset=10.0
        )
        y_corr, baseline = baseline_als_with_mask(x, y, lambda_=1e4, p=0.01, exclusions=[(60.0, 90.0)])
        assert baseline.shape == x.shape
class TestMaskedAlsNeedsUnexcludedPoints:
    """An exclusion covering the window used to give a baseline of exactly 0,
    so the fit ran on the raw offset and nothing said so."""

    def _spectrum(self):
        from modules.spectra.processing.baseline import baseline_als_with_mask
        x = np.linspace(650, 880, 300)
        y = 500 + 0.2 * (x - 650) + 50 * np.exp(-0.5 * ((x - 760) / 10) ** 2)
        return baseline_als_with_mask, x, y

    def test_everything_excluded_is_refused(self):
        als, x, y = self._spectrum()
        with pytest.raises(ValueError, match="Not enough points"):
            als(x, y, exclusions=[(650, 880)])

    def test_a_handful_left_is_refused_too(self):
        als, x, y = self._spectrum()
        with pytest.raises(ValueError, match="Not enough points"):
            als(x, y, exclusions=[(651, 880)])

    def test_a_normal_exclusion_still_follows_the_offset(self):
        als, x, y = self._spectrum()
        corrected, baseline = als(x, y, exclusions=[(740, 780)])
        assert np.all(baseline > 400)
