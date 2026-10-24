"""The Spectra page's "this fit predates the preset you have selected" notice.

Until v5.4.0 the page compared nothing: the QC Report had fingerprinted presets
since v5.0.0 and told the operator to re-run, while the Spectra page left a fit
on screen beside settings it was not made with and said nothing. `fit_stale`
existed but covered despike and baseline only, and nothing rendered it.

These exercise the two helpers directly. Whether the warning is *drawn* is one
`st.warning` call in `render_run_setup`; what it decides is the part worth
pinning.
"""

import streamlit as st

from modules.spectra.models.preset import (
    MaterialPreset,
    PeakTemplate,
    TechniquePreset,
)
from modules.spectra.ui.workspace import (
    _FIT_PRESET_KEY,
    _remember_fit_preset,
    _warn_if_preset_moved,
)


class _Spectrum:
    """Just the three attributes the helpers read."""

    def __init__(self, filename="RM_a.txt", mode="Raman", fit_done=True):
        self.filename = filename
        self.mode = mode
        self.fit_done = fit_done


def _preset(name="MoS2", center=383.0, despike=8.0):
    return MaterialPreset(
        material_name=name,
        enabled=True,
        raman=TechniquePreset(
            x_range_enabled=False, x_min=None, x_max=None,
            despike_threshold=despike,
            baseline_algorithm="ALS", baseline_degree=None,
            baseline_lambda=50000.0, baseline_p=0.01,
            peak_templates=[
                PeakTemplate(peak_label="E2g", center=center, center_tolerance=5.0,
                             width_fwhm=10.0, shape=0.3, color="#d62728"),
            ],
        ),
    )


def _warnings(spectrum, preset):
    """`_warn_if_preset_moved`'s output, as the messages it would draw."""
    drawn = []
    original = st.warning
    st.warning = lambda message, **kwargs: drawn.append(message)
    try:
        _warn_if_preset_moved(spectrum, preset)
    finally:
        st.warning = original
    return drawn


def setup_function():
    st.session_state[_FIT_PRESET_KEY] = {}


class TestNothingToSay:
    def test_an_unchanged_preset_is_quiet(self):
        spectrum, preset = _Spectrum(), _preset()
        _remember_fit_preset(spectrum, preset)

        assert _warnings(spectrum, _preset()) == []

    def test_a_file_with_no_fit_is_quiet(self):
        spectrum = _Spectrum(fit_done=False)
        _remember_fit_preset(spectrum, _preset())

        assert _warnings(spectrum, _preset(center=390.0)) == []

    def test_a_fit_this_session_never_saw_is_quiet(self):
        """Fitted by hand, or before the recording existed. There is nothing to
        compare against, and guessing would cry wolf on every load."""
        assert _warnings(_Spectrum(), _preset()) == []

    def test_an_edit_to_the_other_technique_is_quiet(self):
        """The fingerprint is per technique block, so a PL edit must not
        invalidate a Raman fit."""
        spectrum, preset = _Spectrum(mode="Raman"), _preset()
        _remember_fit_preset(spectrum, preset)

        with_pl = _preset()
        with_pl.pl = TechniquePreset(
            x_range_enabled=False, x_min=None, x_max=None, despike_threshold=6.0,
            baseline_algorithm="ALS", baseline_degree=None,
            baseline_lambda=10000.0, baseline_p=0.01,
            peak_templates=[
                PeakTemplate(peak_label="Exciton", center=770.0, center_tolerance=10.0,
                             width_fwhm=35.0, shape=0.5, color="#1f77b4"),
            ],
        )

        assert _warnings(spectrum, with_pl) == []

    def test_each_file_is_tracked_separately(self):
        first, second = _Spectrum("RM_a.txt"), _Spectrum("RM_b.txt")
        _remember_fit_preset(first, _preset())

        assert _warnings(second, _preset()) == []


class TestSayingSo:
    def test_an_edited_peak_centre_is_reported(self):
        spectrum = _Spectrum()
        _remember_fit_preset(spectrum, _preset(center=383.0))

        drawn = _warnings(spectrum, _preset(center=390.0))

        assert len(drawn) == 1
        assert "earlier settings" in drawn[0]
        assert "MoS2" in drawn[0]

    def test_an_edited_despike_threshold_is_reported(self):
        spectrum = _Spectrum()
        _remember_fit_preset(spectrum, _preset(despike=8.0))

        assert len(_warnings(spectrum, _preset(despike=3.0))) == 1

    def test_switching_material_names_both(self):
        spectrum = _Spectrum()
        _remember_fit_preset(spectrum, _preset("MoS2"))

        drawn = _warnings(spectrum, _preset("WSe2"))

        assert len(drawn) == 1
        assert "MoS2" in drawn[0] and "WSe2" in drawn[0]

    def test_re_running_clears_it(self):
        spectrum = _Spectrum()
        _remember_fit_preset(spectrum, _preset(center=383.0))
        edited = _preset(center=390.0)
        assert _warnings(spectrum, edited) != []

        _remember_fit_preset(spectrum, edited)

        assert _warnings(spectrum, edited) == []
