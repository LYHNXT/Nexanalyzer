"""Unit tests for modules.optical.processing.layer_suggest (1L/2L suggestion)."""

import numpy as np
import pytest
from PIL import Image

from modules.optical.processing import contrast
from modules.optical.processing.layer_suggest import (
    CALIBRATIONS,
    CONFIDENCE_FLOOR,
    FrameFeatures,
    frame_features,
    frame_score,
    suggest_from_features,
    suggest_layer,
)


def _frame(blue_green, red_green, band_area=0.0, level=200.0, seed=0, size=(240, 320)):
    """A uniform film of the given colour, with `band_area` of it 10 % darker --
    where monolayer sits inside a bilayer film."""
    rng = np.random.default_rng(seed)
    h, w = size
    green = np.full((h, w), level) + rng.normal(0, 1.0, (h, w))
    if band_area:
        side = int(round(np.sqrt(band_area * h * w)))
        top, left = h // 2 - side // 2, w // 2 - side // 2
        green[top:top + side, left:left + side] *= 0.90
    rgb = np.stack([green * red_green, green, green * blue_green], axis=-1)
    return np.clip(rgb, 0, 255).round()


# Colours at the calibration's class medians (50x): HA bilayer, VB monolayer.
_BILAYER = dict(blue_green=1.041, red_green=0.919, band_area=0.03)
_MONOLAYER = dict(blue_green=0.978, red_green=0.965)


class TestFrameFeatures:
    def test_ratios_are_read_at_the_film_mode(self):
        f = frame_features(_frame(1.04, 0.92))

        assert f.blue_green == pytest.approx(1.04, abs=0.005)
        assert f.red_green == pytest.approx(0.92, abs=0.005)

    def test_ratios_ignore_exposure(self):
        """The camera auto-exposes, which is why levels are not a feature."""
        bright = frame_features(_frame(1.04, 0.92, level=220))
        dim = frame_features(_frame(1.04, 0.92, level=150))

        assert bright.blue_green == pytest.approx(dim.blue_green, abs=0.005)
        assert bright.red_green == pytest.approx(dim.red_green, abs=0.005)

    def test_band_counts_the_area_ten_percent_darker(self):
        """Only the film's valid interior counts, so the share is of that."""
        a = _frame(1.0, 1.0, band_area=0.04)
        _, valid, _ = contrast.make_mask(a)
        expected = 100.0 * 0.04 * a.shape[0] * a.shape[1] / valid.sum()

        assert frame_features(a).band_pct == pytest.approx(expected, rel=0.15)

    def test_a_clean_film_has_an_empty_band(self):
        assert frame_features(_frame(1.0, 1.0)).band_pct < 0.05


class TestCalibration:
    """Lock the direction of the WSe2 calibration, not its exact weights."""

    def test_bilayer_colour_scores_2L(self):
        f = frame_features(_frame(**_BILAYER))
        assert frame_score(f, CALIBRATIONS["WSe2"]) > 0

    def test_monolayer_colour_scores_1L(self):
        f = frame_features(_frame(**_MONOLAYER))
        assert frame_score(f, CALIBRATIONS["WSe2"]) < 0

    def test_bluer_means_more_bilayer(self):
        cal = CALIBRATIONS["WSe2"]
        assert (frame_score(FrameFeatures(1.03, 0.95, 0.1), cal)
                > frame_score(FrameFeatures(0.99, 0.95, 0.1), cal))

    def test_more_monolayer_band_means_more_bilayer(self):
        cal = CALIBRATIONS["WSe2"]
        assert (frame_score(FrameFeatures(1.0, 0.95, 3.0), cal)
                > frame_score(FrameFeatures(1.0, 0.95, 0.0), cal))


class TestSuggest:
    def test_confident_bilayer(self):
        s = suggest_from_features([frame_features(_frame(**_BILAYER, seed=i)) for i in range(3)], "WSe2")

        assert s.layer == "2L" and s.likely == "2L"
        assert s.confidence >= CONFIDENCE_FLOOR
        assert s.n_frames == 3

    def test_confident_monolayer(self):
        s = suggest_from_features([frame_features(_frame(**_MONOLAYER, seed=i)) for i in range(3)], "WSe2")

        assert s.layer == "1L"
        assert s.confidence >= CONFIDENCE_FLOOR

    def test_unsure_names_no_layer_but_still_leans(self):
        """A score near zero must not pre-fill anything."""
        cal = CALIBRATIONS["WSe2"]
        rg, band = 0.95, 0.0
        bg = -(cal.intercept + cal.red_green * rg + cal.log_band * np.log10(band + 0.01)) / cal.blue_green
        s = suggest_from_features([FrameFeatures(bg + 0.001, rg, band)], "WSe2")

        assert s.layer is None
        assert s.likely in ("1L", "2L")
        assert s.confidence < CONFIDENCE_FLOOR

    def test_the_median_frame_decides(self):
        """One odd frame among nine does not flip the wafer."""
        frames = [FrameFeatures(1.04, 0.92, 2.0)] * 8 + [FrameFeatures(0.95, 1.0, 0.0)]
        assert suggest_from_features(frames, "WSe2").layer == "2L"

    def test_uncalibrated_material_has_no_opinion(self):
        assert suggest_from_features([FrameFeatures(1.04, 0.92, 2.0)], "MoS2") is None
        assert suggest_from_features([FrameFeatures(1.04, 0.92, 2.0)], None) is None

    def test_no_frames_has_no_opinion(self):
        assert suggest_from_features([], "WSe2") is None

    def test_suggest_layer_reads_files(self, tmp_path):
        paths = []
        for i in range(3):
            p = tmp_path / f"50x_{i + 1}.png"
            Image.fromarray(_frame(**_BILAYER, seed=i).astype(np.uint8)).save(p)
            paths.append(p)

        assert suggest_layer(paths, "WSe2").layer == "2L"

    def test_suggest_layer_does_not_read_files_for_an_uncalibrated_material(self, tmp_path):
        assert suggest_layer([tmp_path / "missing.png"], "Silicon") is None


class TestCopiedPresetsAndNoise:
    """v5.22.0: a copied preset keeps its parent's calibration; noisy frames name no layer."""

    @pytest.mark.parametrize("name", ["WSe2_QC", "WSe2 extended", "WSe2_truncate"])
    def test_a_copied_preset_uses_its_parents_calibration(self, name):
        from modules.optical.processing.layer_suggest import calibration_for

        assert calibration_for(name) is CALIBRATIONS["WSe2"]
        s = suggest_from_features([frame_features(_frame(**_BILAYER, seed=i)) for i in range(3)], name)
        assert s.layer == "2L"

    @pytest.mark.parametrize("name", ["MoS2", "MoS2_QC", "", None])
    def test_an_uncalibrated_family_still_has_no_opinion(self, name):
        from modules.optical.processing.layer_suggest import calibration_for

        assert calibration_for(name) is None

    def test_noisy_frames_name_no_layer_however_confident(self):
        from modules.optical.processing.layer_suggest import NOISE_GATE_PCT

        noisy = [FrameFeatures(1.04, 0.92, 2.0, noise_pct=NOISE_GATE_PCT + 1.0)] * 3
        s = suggest_from_features(noisy, "WSe2")

        assert s.too_noisy and s.layer is None
        assert s.likely == "2L" and s.confidence >= CONFIDENCE_FLOOR

    def test_frame_features_measure_the_noise(self):
        quiet = frame_features(_frame(1.0, 1.0, level=200.0))
        assert 0.0 < quiet.noise_pct < 3.0
