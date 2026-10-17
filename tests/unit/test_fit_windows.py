"""Fit windows (v5.22.0): each window its own baseline and fit, the results joined for display."""
import numpy as np
import pytest

from modules.spectra.models.preset import FitWindow, MaterialPreset, PeakTemplate, TechniquePreset
from modules.spectra.models.spectrum import ProcessingSettings, SpectrumData, SpectrumFile
from modules.spectra.processing.auto_workflow import execute_auto_workflow
from modules.spectra.processing.peak_metrics import peak_intensity


def _template(label, center, width=10.0, tol=8.0):
    return PeakTemplate(peak_label=label, center=center, center_tolerance=tol,
                        width_fwhm=width, shape=0.3, color="#123456")


def _block(windows, **kw):
    settings = dict(baseline_algorithm="ALS", baseline_lambda=1e5, baseline_p=0.01,
                    despike_threshold=100.0, max_iterations=2000)
    settings.update(kw)
    return TechniquePreset(
        peak_templates=[_template("low", 300.0), _template("mid", 1000.0, width=60.0, tol=20.0),
                        _template("high", 1600.0)],
        fit_windows=windows,
        **settings,
    )


_WINDOWS = [FitWindow(100, 600), FitWindow(600, 1300), FitWindow(1300, 2000)]


class TestTheModel:
    def test_peaks_go_to_the_window_owning_their_centre(self):
        owned = _block(_WINDOWS).window_peaks()

        assert [[t.peak_label for t in ts] for _, ts in owned] == [["low"], ["mid"], ["high"]]

    def test_a_shared_edge_belongs_to_the_window_starting_there_and_the_last_owns_its_end(self):
        x = np.array([600.0, 2000.0])
        assert not _WINDOWS[0].contains(x[0], last=False)
        assert _WINDOWS[1].contains(x[0], last=False)
        assert _WINDOWS[2].contains(x[1], last=True)
        assert not _WINDOWS[2].contains(x[1], last=False)

    def test_a_valid_block_has_no_errors(self):
        assert _block(_WINDOWS).validate() == []

    def test_overlapping_windows_are_rejected(self):
        errors = _block([FitWindow(100, 700), FitWindow(600, 2000)]).validate()
        assert any("overlaps" in e for e in errors)

    def test_a_peak_in_no_window_is_rejected(self):
        errors = _block([FitWindow(100, 600), FitWindow(600, 1300)]).validate()
        assert any("high" in e and "no fit window" in e for e in errors)

    def test_an_empty_window_is_rejected(self):
        errors = _block([*_WINDOWS[:2], FitWindow(1300, 1500), FitWindow(1500, 2000)]).validate()
        assert any("holds no peak" in e for e in errors)

    def test_a_window_baseline_override_is_validated(self):
        errors = _block([FitWindow(100, 600, baseline_lambda=5.0), *_WINDOWS[1:]]).validate()
        assert any("Fit window 1" in e and "lambda" in e for e in errors)

    def test_window_settings_inherit_the_block_until_overridden(self):
        block = _block(_WINDOWS)
        plain = _WINDOWS[0].resolved(block)
        stiff = FitWindow(600, 1300, baseline_algorithm="Polynomial", baseline_degree=1).resolved(block)

        assert (plain["baseline_algorithm"], plain["baseline_lambda"]) == ("ALS", 1e5)
        assert (stiff["baseline_algorithm"], stiff["baseline_degree"]) == ("Polynomial", 1)

    def test_windows_round_trip_through_the_store_format(self):
        block = _block([FitWindow(100, 600, baseline_lambda=1e6, exclusion_ranges="200-250"), *_WINDOWS[1:]])

        again = TechniquePreset.from_dict(block.to_dict())

        assert again.fit_windows == block.fit_windows

    def test_a_block_without_windows_saves_as_before_and_loads_with_none(self):
        data = _block([]).to_dict()

        # Omitted, not [], so pre-v5.22.0 stores and older app versions see no new key.
        assert "fit_windows" not in data
        assert TechniquePreset.from_dict(data).fit_windows == []


class TestTheWorkflow:
    def _run(self, synthetic_spectrum, windows, **kw):
        x, y = synthetic_spectrum(
            n_points=1200, peaks=((300.0, 400.0, 12.0), (1000.0, 150.0, 70.0), (1600.0, 800.0, 12.0)),
            baseline_slope=0.2, baseline_offset=500.0, noise_std=2.0,
        )
        data = SpectrumData(X=x, Y=y)
        spectrum = SpectrumFile(filename="RM_1.txt", mode="Raman", original_data=data, raw_data=data,
                                processed_data=data, processing_settings=ProcessingSettings())
        preset = MaterialPreset(material_name="T", raman=_block(windows, **kw))
        return spectrum, execute_auto_workflow(spectrum, preset)

    def test_the_windows_come_back_as_one_result_in_template_order(self, synthetic_spectrum):
        spectrum, result = self._run(synthetic_spectrum, _WINDOWS)

        assert result["success"], result["error_message"]
        fit = result["fit_result"]
        n = len(spectrum.processed_data.X)
        assert [p.label for p in fit.fitted_peaks] == ["low", "mid", "high"]
        for array in (fit.total_fit_curve, fit.residuals, spectrum.background,
                      *[p.component_curve for p in fit.fitted_peaks]):
            assert len(array) == n
        assert np.allclose(sum(p.component_curve for p in fit.fitted_peaks), fit.total_fit_curve)
        assert np.allclose(fit.residuals, spectrum.processed_data.Y - fit.total_fit_curve)
        assert fit.converged and fit.nfev > 0

    def test_each_peak_is_zero_outside_its_own_window(self, synthetic_spectrum):
        spectrum, result = self._run(synthetic_spectrum, _WINDOWS)
        x = spectrum.processed_data.X
        low = result["fit_result"].fitted_peaks[0]

        assert np.all(low.component_curve[x >= 600] == 0)

    def test_the_fitted_peaks_are_recovered(self, synthetic_spectrum):
        _spectrum, result = self._run(synthetic_spectrum, _WINDOWS)
        peaks = {p.label: p for p in result["fit_result"].fitted_peaks}

        assert peaks["low"].center == pytest.approx(300.0, abs=1.0)
        assert peaks["high"].center == pytest.approx(1600.0, abs=1.0)
        assert peak_intensity(peaks["high"]) == pytest.approx(800.0, rel=0.05)

    def test_corrected_plus_background_is_the_despiked_spectrum(self, synthetic_spectrum):
        spectrum, _result = self._run(synthetic_spectrum, _WINDOWS)

        x = spectrum.processed_data.X
        original = np.interp(x, spectrum.raw_data.X, spectrum.raw_data.Y)
        assert np.allclose(spectrum.processed_data.Y + spectrum.background, original)

    def test_points_outside_every_window_are_dropped(self, synthetic_spectrum):
        spectrum, result = self._run(synthetic_spectrum, [FitWindow(200, 600), FitWindow(600, 1300),
                                                          FitWindow(1300, 1900)])
        assert result["success"], result["error_message"]
        assert spectrum.processed_data.X.min() >= 200 and spectrum.processed_data.X.max() <= 1900

    def test_a_window_the_spectrum_does_not_reach_is_skipped(self, synthetic_spectrum):
        windows = [FitWindow(100, 600), FitWindow(600, 1300), FitWindow(2500, 2600)]
        _spectrum, result = self._run(synthetic_spectrum, windows)
        # "high" (1600) is owned by no window here, so only low and mid are fitted.
        assert result["success"], result["error_message"]
        assert [p.label for p in result["fit_result"].fitted_peaks] == ["low", "mid"]

    def test_a_spectrum_reaching_no_window_fails(self, synthetic_spectrum):
        _spectrum, result = self._run(synthetic_spectrum, [FitWindow(2500, 2600), FitWindow(2600, 2700)])
        assert not result["success"]
        assert "none of the fit windows" in result["error_message"]

    def test_a_block_without_windows_is_untouched(self, synthetic_spectrum):
        spectrum, result = self._run(synthetic_spectrum, [])

        assert result["success"]
        assert len(spectrum.processed_data.X) == 1200
