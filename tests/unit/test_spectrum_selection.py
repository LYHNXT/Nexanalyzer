"""Unit tests for modules.spectra.processing.spectrum_selection (outliers out, then 25 a point)."""

from types import SimpleNamespace

import numpy as np

from modules.spectra.models.peak import FitResult, FittedPeak
from modules.spectra.processing.spectrum_selection import (
    SPECTRA_PER_POINT,
    main_peak_labels,
    select_spectra_per_point,
)


def _peak(label, center, intensity, width_fwhm):
    curve = np.zeros(10)
    curve[5] = intensity
    return FittedPeak(label=label, center=center, center_stderr=0.1, area=intensity * 7.0,
                      area_stderr=1.0, width_fwhm=width_fwhm, width_stderr=0.1,
                      shape=0.3, component_curve=curve)


def _spectrum(center=383.0, intensity=1000.0, fwhm=4.5, weak_fwhm=30.0, r_squared=0.99):
    fit = FitResult(success=True,
                    fitted_peaks=[_peak("A1g", center + 21.0, intensity, fwhm + 1.5),
                                  _peak("E2g", center, intensity * 0.7, fwhm),
                                  _peak("2LA", center + 70.0, intensity * 0.1, weak_fwhm)],
                    total_fit_curve=np.zeros(10), residuals=np.zeros(10),
                    chi_squared=1.0, r_squared=r_squared, convergence_time=0.1)
    return SimpleNamespace(fit_result=fit)


def _map(point, n, rng, **overrides):
    """n ordinary spectra at `point`, small scatter on every quantity."""
    return [(point, _spectrum(center=383.0 + rng.normal(0, 0.1), intensity=1000 + rng.normal(0, 20),
                              fwhm=4.5 + rng.normal(0, 0.05), weak_fwhm=30 + rng.normal(0, 5), **overrides))
            for _ in range(n)]


class TestMainPeaks:
    def test_strongest_and_those_within_half_of_it(self):
        assert main_peak_labels([_spectrum().fit_result]) == ("A1g", "E2g")

    def test_the_laser_line_is_never_a_main_peak(self):
        fit = _spectrum().fit_result
        fit.fitted_peaks.append(_peak("center", 0.3, 50000.0, 2.0))

        assert main_peak_labels([fit]) == ("A1g", "E2g")


class TestSelection:
    def test_a_36_spectrum_point_is_cut_to_25(self):
        rng = np.random.default_rng(0)
        pairs = _map(1, 36, rng) + _map(2, 36, rng)

        kept, summary = select_spectra_per_point(pairs)

        assert [sum(1 for p, _ in kept if p == point) for point in (1, 2)] == [SPECTRA_PER_POINT] * 2
        assert summary.fitted == 72 and summary.selected == 50 and summary.points_reduced == 2
        assert summary.outliers + summary.unused == 22

    def test_outliers_go_before_the_draw(self):
        rng = np.random.default_rng(1)
        pairs = _map(1, 33, rng)
        pairs += [(1, _spectrum(center=390.0)), (1, _spectrum(fwhm=9.0)), (1, _spectrum(r_squared=0.2))]
        bad = {id(s) for _, s in pairs[-3:]}

        kept, summary = select_spectra_per_point(pairs)

        assert not bad & {id(s) for _, s in kept}
        assert summary.outliers >= 3

    def test_a_weak_peak_out_of_its_fence_does_not_drop_the_spectrum(self):
        rng = np.random.default_rng(2)
        pairs = _map(1, 35, rng) + [(1, _spectrum(weak_fwhm=300.0))]

        _kept, summary = select_spectra_per_point(pairs)

        assert summary.outliers == 0

    def test_the_draw_is_reproducible_and_keeps_order(self):
        rng = np.random.default_rng(3)
        pairs = _map(4, 36, rng)

        first, _ = select_spectra_per_point(pairs)
        second, _ = select_spectra_per_point(pairs)

        assert [id(s) for _, s in first] == [id(s) for _, s in second]
        order = {id(s): i for i, (_, s) in enumerate(pairs)}
        assert [order[id(s)] for _, s in first] == sorted(order[id(s)] for _, s in first)

    def test_a_point_at_or_under_25_is_untouched(self):
        rng = np.random.default_rng(4)
        pairs = _map(1, 24, rng) + [(1, _spectrum(center=390.0, r_squared=0.2))] + _map(2, 1, rng)

        kept, summary = select_spectra_per_point(pairs)

        assert kept == pairs
        assert summary.points_reduced == 0 and summary.outliers == 0

    def test_fewer_than_25_survivors_are_all_kept(self):
        rng = np.random.default_rng(5)
        pairs = _map(1, 20, rng) + [(1, _spectrum(r_squared=0.1)) for _ in range(10)]

        kept, summary = select_spectra_per_point(pairs)

        # The ten failed fits, plus whatever the fences catch in the scatter.
        assert summary.outliers >= 10 and summary.unused == 0
        assert len(kept) == 30 - summary.outliers < SPECTRA_PER_POINT
        assert all(s.fit_result.r_squared > 0.5 for _, s in kept)
