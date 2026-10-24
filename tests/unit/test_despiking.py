"""Unit tests for modules.spectra.processing.despiking (cosmic-ray spike removal)."""

import numpy as np
import pytest

from modules.spectra.models.preset import TechniquePreset
from modules.spectra.models.spectrum import DESPIKE_THRESHOLD_MAX, ProcessingSettings
from modules.spectra.processing.despiking import (
    remove_spikes,
)


class TestRemoveSpikes:
    def test_detects_and_replaces_spike(self):
        y = np.array([100., 102., 101., 99., 500., 98., 101., 100., 102., 99.])
        y_clean, mask = remove_spikes(y, threshold=6.0)

        assert mask.tolist() == [False, False, False, False, True, False, False, False, False, False]
        assert y_clean[4] == pytest.approx(100.0)
        # Non-spike points are untouched.
        np.testing.assert_array_equal(np.delete(y_clean, 4), np.delete(y, 4))

    def test_short_array_is_a_noop(self):
        """Arrays under 10 points skip detection entirely (despiking.py:83-85)."""
        y = np.array([100., 102., 500., 98., 101.])
        y_clean, mask = remove_spikes(y, threshold=6.0)

        assert not mask.any()
        np.testing.assert_array_equal(y_clean, y)

    def test_constant_signal_has_no_spikes(self):
        y = np.full(20, 100.0)
        y_clean, mask = remove_spikes(y, threshold=6.0)

        assert not mask.any()
        np.testing.assert_array_equal(y_clean, y)

    def test_threshold_out_of_range_raises(self):
        y = np.arange(20, dtype=float)
        with pytest.raises(ValueError):
            remove_spikes(y, threshold=2.0)
        with pytest.raises(ValueError):
            remove_spikes(y, threshold=DESPIKE_THRESHOLD_MAX + 1)

    def test_threshold_above_a_real_peak_leaves_it_alone(self):
        """The Z-score is global, so a strong real peak scores like a spike.

        A threshold above the peak's own score must leave it untouched -- the
        reason the ceiling went from 30 to 500 (v5.10.5).
        """
        x = np.linspace(100.0, 400.0, 600)
        rng = np.random.default_rng(0)
        y = 100.0 + 2000.0 * np.exp(-0.5 * ((x - 250.0) / 2.0) ** 2) + rng.normal(0, 5, x.size)

        _, low = remove_spikes(y, threshold=30.0)
        y_clean, high = remove_spikes(y, threshold=DESPIKE_THRESHOLD_MAX)

        assert low.any()            # the peak top is "despiked" at 30
        assert not high.any()
        np.testing.assert_array_equal(y_clean, y)

    def test_even_window_size_raises(self):
        y = np.arange(20, dtype=float)
        with pytest.raises(ValueError):
            remove_spikes(y, threshold=6.0, window_size=4)


def _despike_errors(threshold):
    """A bare block also lacks peak templates; look only at the despike check."""
    errors = TechniquePreset(despike_threshold=threshold).validate()
    return [e for e in errors if "despike_threshold" in e]


class TestThresholdCeilingAgrees:
    """The despiker, the processing settings and the preset share one range."""

    def test_the_ceiling_is_accepted_everywhere(self):
        assert not _despike_errors(DESPIKE_THRESHOLD_MAX)
        ProcessingSettings(despike_threshold=DESPIKE_THRESHOLD_MAX)
        remove_spikes(np.arange(20, dtype=float), threshold=DESPIKE_THRESHOLD_MAX)

    def test_above_the_ceiling_is_rejected_everywhere(self):
        above = DESPIKE_THRESHOLD_MAX + 1
        assert _despike_errors(above)
        with pytest.raises(ValueError):
            ProcessingSettings(despike_threshold=above)
        with pytest.raises(ValueError):
            remove_spikes(np.arange(20, dtype=float), threshold=above)


class TestPLDespikerScoresSteps:
    """v5.41.0: PL is despiked by its steps (remove_spikes_local). The
    whole-spectrum score flagged the PL band itself and left 20 % of a
    3-pixel ray."""

    @staticmethod
    def _band(n=1650, seed=0):
        rng = np.random.default_rng(seed)
        x = np.linspace(600, 900, n)
        y = 500 + 5000 * np.exp(-0.5 * ((x - 762) / 13) ** 2) + rng.normal(0, 8, n)
        return x, y

    def test_the_band_is_left_alone(self):
        from modules.spectra.processing.despiking import remove_spikes_local
        _x, y = self._band()
        clean, mask = remove_spikes_local(y, threshold=10.0)
        assert mask.sum() <= 20
        assert clean.max() == y.max()

    def test_the_old_score_did_flag_the_band(self):
        """The regression this replaces, kept visible."""
        _x, y = self._band()
        _clean, mask = remove_spikes(y, threshold=8.0)
        assert mask.sum() > 100

    def test_one_and_three_pixel_rays_are_removed(self):
        from modules.spectra.processing.despiking import remove_spikes_local
        _x, y = self._band()
        z = y.copy()
        z[300] += 4000
        z[1200:1203] += [1600, 4000, 1600]
        clean, _mask = remove_spikes_local(z, threshold=10.0)
        assert abs(clean[300] - y[300]) < 50
        assert np.max(np.abs(clean[1200:1203] - y[1200:1203])) < 50

    def test_the_threshold_range_is_enforced(self):
        from modules.spectra.processing.despiking import remove_spikes_local
        with pytest.raises(ValueError):
            remove_spikes_local(np.ones(50), threshold=1.0)
