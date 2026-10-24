"""The Quick Export PNG must be memoized, and must never go stale.

That rasterization measured 1316 ms and used to run on every rerun of the
Spectra page, because st.download_button needs its bytes at render time. The
risk in fixing it is the opposite failure: serving a PNG for a state the user
is no longer looking at.

The cache key is the plotly Figure itself, hashed by content. These tests pin
that it notices every input that changes the picture — including a module-level
palette constant, which a hand-rolled fingerprint could not see.
"""

import hashlib

import numpy as np
import pytest
import streamlit as st
from streamlit.runtime.caching.hashing import update_hash

from modules.spectra.ui.workspace import _export_png_cached
from modules.spectra.viz import fit_plot


class _Peak:
    def __init__(self, label="LA", color="#d62728", curve=None):
        self.label = label
        self.color = color
        self.component_curve = curve


class _Fit:
    success = True

    def __init__(self, peaks, total, residuals):
        self.fitted_peaks = peaks
        self.total_fit_curve = total
        self.residuals = residuals


X = np.linspace(100.0, 400.0, 600)


def _figure(color="#d62728", label="LA", residual_sign=1.0, shift=0.0):
    y = 300.0 * np.exp(-0.5 * ((X - (130.0 + shift)) / 12.0) ** 2)
    total = y * 0.99
    fit = _Fit([_Peak(label, color, y * 0.5)], total, residual_sign * (y - total))
    return fit_plot.plot_composite(
        X, y, fit, mode="Raman", title="sample.txt - Fit Results", show_components=True
    )


def _key(fig):
    """What Streamlit's cache will hash this figure to."""
    digest = hashlib.md5()
    update_hash(fig, digest, "cache_data")
    return digest.hexdigest()


class TestTheFigureIsTheKey:
    """Each of these changes the rendered picture, so each must miss."""

    def test_an_identical_figure_hits(self):
        assert _key(_figure()) == _key(_figure())

    def test_changed_data_misses(self):
        assert _key(_figure(shift=5.0)) != _key(_figure())

    def test_a_changed_peak_color_misses(self):
        assert _key(_figure(color="#00ff00")) != _key(_figure())

    def test_a_changed_peak_label_misses(self):
        """Legend text shifts the plot area's right edge, so it moves pixels."""
        assert _key(_figure(label="LA2")) != _key(_figure())

    def test_changed_residuals_miss(self):
        """Residuals are drawn on every Quick Export -- show_residuals defaults
        to True and the Spectra page never passes it. A hand-rolled key that treated
        them as derivable from Y - total_fit_curve would bet correctness on
        lmfit's sign convention, which nothing here enforces."""
        assert _key(_figure(residual_sign=-1.0)) != _key(_figure())

    def test_a_changed_palette_constant_misses(self):
        """The one hole that was actually reachable with a hand-rolled
        fingerprint: st.cache_data hashes only the decorated function's own
        source, not the palette its callees read, so restyling served the old
        PNG under an identical key until the process restarted. Hashing the
        figure closes it, because the constant is baked into the traces."""
        before = _key(_figure())

        original = fit_plot.RESIDUAL_COLOR
        fit_plot.RESIDUAL_COLOR = "#FF00FF"
        try:
            after = _key(_figure())
        finally:
            fit_plot.RESIDUAL_COLOR = original

        assert after != before


class TestTheCacheActuallyCaches:
    def test_a_repeat_call_does_not_rasterize_again(self, monkeypatch):
        calls = []
        import modules.spectra.ui.workspace as workspace_module

        def _counting_export(fig, width, height, scale):
            calls.append((width, height, scale))
            return b"png-bytes"

        monkeypatch.setattr(workspace_module, "export_figure_png", _counting_export)
        _export_png_cached.clear()

        fig = _figure()
        first = _export_png_cached(fig, width=1200, height=600, scale=2.0)
        second = _export_png_cached(_figure(), width=1200, height=600, scale=2.0)

        assert first == second == b"png-bytes"
        assert len(calls) == 1, f"rasterized {len(calls)} times, expected 1"

    def test_a_different_figure_rasterizes_again(self, monkeypatch):
        calls = []
        import modules.spectra.ui.workspace as workspace_module

        monkeypatch.setattr(
            workspace_module, "export_figure_png",
            lambda fig, width, height, scale: calls.append(1) or b"x",
        )
        _export_png_cached.clear()

        _export_png_cached(_figure(), width=1200, height=600, scale=2.0)
        _export_png_cached(_figure(shift=7.0), width=1200, height=600, scale=2.0)

        assert len(calls) == 2

    def test_the_export_size_is_part_of_the_identity(self, monkeypatch):
        calls = []
        import modules.spectra.ui.workspace as workspace_module

        monkeypatch.setattr(
            workspace_module, "export_figure_png",
            lambda fig, width, height, scale: calls.append(1) or b"x",
        )
        _export_png_cached.clear()

        fig = _figure()
        _export_png_cached(fig, width=1200, height=600, scale=2.0)
        _export_png_cached(fig, width=800, height=400, scale=1.0)

        assert len(calls) == 2

    def test_a_failing_render_is_not_cached(self, monkeypatch):
        """Streamlit does not cache exceptions, so a broken kaleido install
        re-pays the failing render every rerun. Pinned so nobody assumes the
        error path is cheap."""
        calls = []
        import modules.spectra.ui.workspace as workspace_module

        def _boom(fig, width, height, scale):
            calls.append(1)
            raise RuntimeError("kaleido missing")

        monkeypatch.setattr(workspace_module, "export_figure_png", _boom)
        _export_png_cached.clear()

        fig = _figure()
        for _ in range(3):
            with pytest.raises(RuntimeError):
                _export_png_cached(fig, width=1200, height=600, scale=2.0)

        assert len(calls) == 3


class TestCacheIsBounded:
    def test_max_entries_is_set(self):
        """The default is unbounded, and each PNG is ~165-300 KB, so every
        refit would add one forever."""
        import inspect

        import modules.spectra.ui.workspace as workspace_module

        assert "max_entries=" in inspect.getsource(workspace_module)

    def test_the_spinner_is_suppressed(self):
        """The default would render 'Running _export_png_cached(...)' inside the
        narrow Quick Export column."""
        import inspect

        import modules.spectra.ui.workspace as workspace_module

        assert "show_spinner=False" in inspect.getsource(workspace_module)


class TestCachesAreIsolatedBetweenTests:
    """The autouse fixture in conftest must actually be clearing them; the
    cache is process-wide and outlives an AppTest."""

    def test_no_entry_survives_from_another_test(self, monkeypatch):
        calls = []
        import modules.spectra.ui.workspace as workspace_module

        monkeypatch.setattr(
            workspace_module, "export_figure_png",
            lambda fig, width, height, scale: calls.append(1) or b"x",
        )

        # The identical figure was rendered by tests above in this same process.
        _export_png_cached(_figure(), width=1200, height=600, scale=2.0)

        assert len(calls) == 1, "a previous test's cached bytes leaked into this one"


def test_streamlit_cache_clear_is_available():
    """The conftest fixture depends on this API existing."""
    assert hasattr(st.cache_data, "clear")
