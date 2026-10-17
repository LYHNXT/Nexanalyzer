"""Zero-line calibration (v5.24.0): the laser line is put at 0 cm-1 before anything else."""
import numpy as np
import pytest

from modules.spectra.models.preset import MaterialPreset, PeakTemplate, TechniquePreset
from modules.spectra.models.spectrum import ProcessingSettings, SpectrumData, SpectrumFile
from modules.spectra.processing.auto_workflow import execute_auto_workflow
from modules.spectra.processing.zero_line import laser_line_offset


def _spectrum(line_at=0.4, notch_edge=True, line_height=2000.0, peak_at=250.4, seed=0):
    rng = np.random.default_rng(seed)
    x = np.arange(-100.0, 400.0, 0.87)
    y = 100.0 + rng.normal(0, 2.0, x.size)
    if line_height:
        y += line_height * np.exp(-0.5 * ((x - line_at) / 1.0) ** 2)
    if notch_edge:                      # the notch filter's edge: a broad shoulder a few cm-1 up
        y += 900.0 * np.exp(-0.5 * ((x - 5.5) / 1.5) ** 2)
    y += 300.0 * np.exp(-0.5 * ((x - peak_at) / 2.0) ** 2)
    return x, y


class TestLaserLineOffset:
    def test_the_line_is_found_to_sub_point_precision(self):
        x, y = _spectrum(line_at=0.4, notch_edge=False)
        assert laser_line_offset(x, y) == pytest.approx(0.4, abs=0.15)

    def test_the_notch_edge_a_few_cm1_up_is_not_taken_for_the_line(self):
        x, y = _spectrum(line_at=0.3, notch_edge=True)
        assert laser_line_offset(x, y) == pytest.approx(0.3, abs=0.2)

    def test_no_clear_line_gives_none(self):
        x, y = _spectrum(line_height=0.0, notch_edge=False)
        assert laser_line_offset(x, y) is None

    def test_a_spectrum_that_does_not_reach_zero_gives_none(self):
        x = np.arange(100.0, 400.0, 0.87)
        assert laser_line_offset(x, np.ones_like(x)) is None


class TestTheWorkflowShiftsTheAxis:
    def _run(self, calibrate, line_at=0.8):
        x, y = _spectrum(line_at=line_at, notch_edge=False)
        data = SpectrumData(X=x, Y=y)
        spectrum = SpectrumFile(filename="RM_1.txt", mode="Raman", original_data=data, raw_data=data,
                                processed_data=data, processing_settings=ProcessingSettings())
        block = TechniquePreset(
            x_range_enabled=True, x_min=200.0, x_max=300.0, despike_threshold=100.0,
            baseline_algorithm="ALS", baseline_lambda=1e5, baseline_p=0.01,
            peak_templates=[PeakTemplate("P", 250.0, 5.0, 4.0, 0.3, "#123456")],
            zero_line_calibration=calibrate,
        )
        result = execute_auto_workflow(spectrum, MaterialPreset(material_name="T", raman=block))
        assert result["success"], result["error_message"]
        return spectrum, result["fit_result"].fitted_peaks[0]

    def test_the_fitted_peak_moves_by_the_offset_and_the_offset_is_recorded(self):
        off_spec, off_peak = self._run(False)
        on_spec, on_peak = self._run(True)

        assert off_spec.zero_offset is None
        assert on_spec.zero_offset == pytest.approx(0.8, abs=0.15)
        assert on_peak.center == pytest.approx(off_peak.center - on_spec.zero_offset, abs=0.05)

    def test_the_recorded_data_is_never_shifted_so_a_rerun_cannot_shift_twice(self):
        spectrum, _ = self._run(True)
        first = spectrum.zero_offset
        x_before = spectrum.original_data.X.copy()
        execute_auto_workflow(spectrum, MaterialPreset(material_name="T", raman=TechniquePreset(
            x_range_enabled=True, x_min=200.0, x_max=300.0, despike_threshold=100.0,
            baseline_algorithm="ALS", baseline_lambda=1e5, baseline_p=0.01,
            peak_templates=[PeakTemplate("P", 250.0, 5.0, 4.0, 0.3, "#123456")],
            zero_line_calibration=True)))

        assert np.array_equal(spectrum.original_data.X, x_before)
        assert spectrum.zero_offset == pytest.approx(first)

    def test_the_setting_round_trips_and_is_omitted_when_off(self):
        on = TechniquePreset(zero_line_calibration=True, peak_templates=[PeakTemplate("P", 1.0, 1.0, 1.0, 0.3, "#123456")])
        off = TechniquePreset(peak_templates=[PeakTemplate("P", 1.0, 1.0, 1.0, 0.3, "#123456")])

        assert TechniquePreset.from_dict(on.to_dict()).zero_line_calibration is True
        assert "zero_line_calibration" not in off.to_dict()
