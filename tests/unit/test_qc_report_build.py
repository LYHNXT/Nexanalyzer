"""modules.qc_report.processing.build: the QC Report pipeline without the page.

Until v5.35.0 this pipeline lived inside pages/2_QC_Report.py and could only
be exercised through AppTest. These call it directly on a synthetic 3x3
sample (Silicon Raman + OM frames, the integration tests' fixtures) and check
what it hands back, what it warns about, and that saving writes the set in
reading order.
"""

import pytest

from core.report.progress import build as build_progress
from modules.qc_report.processing.build import (
    GRID_POINTS,
    REPORT_FIGURES,
    QcReportInputs,
    build_qc_report,
    grid_points_only,
    save_report,
    threshold_noise_warning,
)
from modules.spectra.io.preset_store import load_presets
from modules.spectra.processing.sample_scanner import scan_sample_folder
from tests.integration.test_qc_report_flow import _write_multi_spectrum_raman, _write_om_frame


def _sample(tmp_path, points=range(1, 10), images=True):
    for point in points:
        _write_multi_spectrum_raman(tmp_path / f"RM_{point}.txt", seed=point)
        if images:
            _write_om_frame(tmp_path / f"50x_{point}.png", seed=100 + point)
    return scan_sample_folder(str(tmp_path))


def _inputs(scan, **overrides):
    preset = load_presets()["Silicon"]
    om_paths = dict(scan.image_files.get("50x", {}))
    kw = dict(scan=scan, material="Silicon", magnification="50x", reference_layer="2L",
              om_paths=om_paths, optical=preset.optical_for("2L") if om_paths else None,
              raman_preset=preset, report_date="2026-10-24")
    kw.update(overrides)
    return QcReportInputs(**kw)


def _progress(inputs):
    return build_progress(lambda *_: None, has_raman=inputs.raman_preset is not None,
                          has_pl=inputs.pl_preset is not None, has_optical=bool(inputs.om_paths),
                          has_segmentation=inputs.optical is not None,
                          has_domain_size=inputs.measure_domain_size)


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    scan = _sample(tmp_path_factory.mktemp("sample"))
    inputs = _inputs(scan)
    warnings = []
    return build_qc_report(inputs, _progress(inputs), warn=warnings.append), warnings


class TestBuildQcReport:
    def test_every_figure_a_raman_and_om_sample_has_is_built(self, built):
        result, _ = built
        for key in ("summary_png", "om_png", "om_diagnostic_png", "raman_grid_png", "raman_stats_png"):
            assert result[key].startswith(b"\x89PNG"), key
        assert result["xlsx_bytes"][:2] == b"PK"

    def test_techniques_without_data_produce_nothing(self, built):
        result, _ = built
        for key in ("pl_grid_png", "pl_stats_png", "domain_grid_png"):
            assert not result.get(key)

    def test_the_counts_the_page_reports_are_returned(self, built):
        result, _ = built
        assert result["raman_fitted"] > 0
        assert 0 <= result["raman_dropped"] < result["raman_fitted"]
        assert result["raman_unconverged"] >= 0 and result["raman_errors"] == []
        assert result["report_date"] == "2026-10-24"
        assert len(result["frames"]) == 9

    def test_every_returned_key_is_one_the_page_state_resets(self, built):
        """The page stores the result with state.update(); a key outside the
        artifact tuples would outlive the run that produced it."""
        from modules.optical.ui.qc_report_state import (
            _COMPOSITE_ARTIFACTS, _OPTICAL_ARTIFACTS, _PL_ARTIFACTS, _RAMAN_ARTIFACTS)
        result, _ = built
        derived = set(_OPTICAL_ARTIFACTS + _RAMAN_ARTIFACTS + _PL_ARTIFACTS + _COMPOSITE_ARTIFACTS)
        assert set(result) <= derived

    def test_a_clean_sample_warns_about_nothing(self, built):
        _, warnings = built
        assert warnings == []

    def test_a_frame_that_cannot_be_read_is_warned_about_and_dropped(self, tmp_path):
        scan = _sample(tmp_path)
        (tmp_path / "50x_5.png").write_bytes(b"not an image")
        inputs = _inputs(scan, raman_preset=None)
        warnings = []
        result = build_qc_report(inputs, _progress(inputs), warn=warnings.append)
        assert sorted(f.point for f in result["frames"]) == [1, 2, 3, 4, 6, 7, 8, 9]
        assert any("point 5" in w for w in warnings)

    def test_the_batch_runner_can_be_replaced(self, tmp_path):
        """The scripts pass a process-pool batch; whatever is passed is what runs."""
        from modules.spectra.processing.sample_batch import run_sample_batch
        scan = _sample(tmp_path, images=False)
        inputs = _inputs(scan)
        calls = []

        def counting(*args, **kwargs):
            calls.append(1)
            return run_sample_batch(*args, **kwargs)

        build_qc_report(inputs, _progress(inputs), run_batch=counting)
        assert calls == [1]


class TestSaveReport:
    def test_files_are_numbered_in_reading_order_beside_the_workbook(self, built, tmp_path):
        result, _ = built
        saved = save_report(result, str(tmp_path / "S_QC_Report"))
        names = [p.split("\\")[-1].split("/")[-1] for p in saved]
        assert names == ["S_QC_Report_1_Summary.png", "S_QC_Report_2_OM.png",
                         "S_QC_Report_3_OM_diagnostic.png", "S_QC_Report_5_Raman.png",
                         "S_QC_Report_6_Raman_stats.png", "S_QC_Report.xlsx"]

    def test_an_empty_report_writes_nothing(self, tmp_path):
        assert save_report({}, str(tmp_path / "S")) == []
        assert list(tmp_path.iterdir()) == []

    def test_the_figure_list_has_eight_numbered_entries(self):
        assert [n for n, _, _ in REPORT_FIGURES] == list(range(1, 9))


class TestGridPointsOnly:
    def test_a_tenth_point_is_dropped_everywhere_and_named(self, tmp_path):
        scan = _sample(tmp_path, points=range(1, 11))
        kept, dropped = grid_points_only(scan)
        assert dropped == [10]
        assert max(kept.raman_files) == GRID_POINTS
        assert max(kept.image_files["50x"]) == GRID_POINTS

    def test_a_nine_point_scan_is_returned_as_is(self, tmp_path):
        scan = _sample(tmp_path, images=False)
        assert grid_points_only(scan) == (scan, [])

    def test_no_scan_is_no_scan(self):
        assert grid_points_only(None) == (None, [])


class TestThresholdNoiseWarning:
    class _Frame:
        def __init__(self, point, sigma_noise, mode=100.0):
            self.point, self.sigma_noise, self.mode = point, sigma_noise, mode

    def test_frames_whose_cut_is_inside_their_noise_are_named(self):
        frames = [self._Frame(1, 0.5), self._Frame(2, 3.0)]  # noise 0.5 % and 3 %
        message = threshold_noise_warning(frames, {"abs_threshold": (4.0, 4.0)})
        assert "P2 (1.3x)" in message and "P1" not in message

    def test_no_fixed_pair_means_nothing_to_check(self):
        assert threshold_noise_warning([self._Frame(1, 9.0)], {}) is None

    def test_every_frame_clearing_it_is_silent(self):
        assert threshold_noise_warning([self._Frame(1, 0.5)], {"abs_threshold": 4.0}) is None
