"""Shared pytest fixtures for the NexAnalyzer test suite."""

import numpy as np
import pytest


@pytest.fixture(autouse=True)
def _clear_streamlit_caches():
    """Empty every st.cache_data between tests.

    Streamlit's data cache is process-wide and outlives an AppTest instance —
    it survives across AppTest runs and across test files in one pytest
    process. Without this, a test asserting "the PNG download has bytes" can
    pass on a payload another test cached, even with the render path broken,
    and any "rendered only once" assertion passes for the wrong reason.
    """
    import streamlit as st

    st.cache_data.clear()
    yield
    st.cache_data.clear()


@pytest.fixture(autouse=True)
def _isolate_runcard_sidecar(tmp_path_factory, monkeypatch):
    """Redirect the Runcard page's remembered-folder sidecar to a scratch dir.

    The twin of _isolate_datalog_sidecar below, for the same reason and with
    the same reach. Kept as two fixtures rather than one loop over both modules
    so that a third page adding a sidecar has an obvious thing to copy, and so
    a failure names which sidecar escaped.
    """
    import modules.runcard.io.config_store as config_store

    scratch = tmp_path_factory.mktemp("sidecars")
    monkeypatch.setattr(
        config_store, "get_runcard_config_path", lambda: scratch / "runcard.json"
    )


@pytest.fixture(autouse=True)
def _isolate_datalog_sidecar(tmp_path_factory, monkeypatch):
    """Redirect the Datalog page's remembered-folder sidecar to a scratch dir.

    The page persists a "last used folder" the moment one is picked, so a test
    that never mentions the sidecar still rewrites the developer's own
    data/datalog.json. Worse, a test asserting "it reopens the last folder"
    would pass on a value an earlier test left behind -- the same failure mode
    _clear_streamlit_caches exists to prevent.

    Autouse and suite-wide, because the write happens deep inside the page
    rather than in the test, and nothing in the test names it. The two shared
    sidecars need no patch: runcard_tags.json and thresholds.json are written
    into the data root the caller passes, which is always tmp_path. That holds
    only while every storage entry point takes root_folder as an argument -- if
    one ever defaults to the remembered folder, it escapes this.
    """
    import modules.datalog.io.config_store as config_store

    scratch = tmp_path_factory.mktemp("sidecars")
    monkeypatch.setattr(
        config_store, "get_datalog_config_path", lambda: scratch / "datalog.json"
    )


def _pseudo_voigt(x, center, intensity, fwhm, shape=0.5):
    """Cheap pseudo-Voigt profile for building synthetic test spectra.

    Not the same math as lmfit's VoigtModel (true Voigt is a convolution),
    but close enough in shape to exercise fitting/despiking/baseline code
    against a realistic peak-on-baseline signal.
    """
    sigma = fwhm / 2.355
    gaussian = np.exp(-0.5 * ((x - center) / sigma) ** 2)
    lorentzian = 1.0 / (1.0 + ((x - center) / (fwhm / 2)) ** 2)
    return intensity * ((1 - shape) * gaussian + shape * lorentzian)


@pytest.fixture
def synthetic_spectrum():
    """Factory fixture: build an (x, y) synthetic spectrum for testing.

    Returns a callable so each test can customize peaks/baseline/noise.
    Defaults satisfy SpectrumData's >=100-point minimum.
    """
    def _make(
        n_points=300,
        x_range=(100.0, 2000.0),
        peaks=((1000.0, 1000.0, 50.0),),  # (center, intensity, fwhm) tuples
        baseline_slope=0.0,
        baseline_offset=0.0,
        noise_std=0.0,
        seed=0,
    ):
        x = np.linspace(x_range[0], x_range[1], n_points)
        y = baseline_offset + baseline_slope * (x - x_range[0])
        for center, intensity, fwhm in peaks:
            y = y + _pseudo_voigt(x, center, intensity, fwhm)
        if noise_std > 0:
            rng = np.random.default_rng(seed)
            y = y + rng.normal(0, noise_std, size=n_points)
        return x, y

    return _make


@pytest.fixture(autouse=True)
def _isolate_tool_roots(tmp_path_factory, monkeypatch):
    """Redirect data/tool_roots.json to a scratch dir.

    The same reasoning as the two sidecar fixtures above, with one more: every
    folder page draws a Tool dropdown when this file names a tool, so the
    developer's own roots would add widgets the page tests do not expect.
    """
    import core.io.tool_roots as tool_roots

    scratch = tmp_path_factory.mktemp("sidecars")
    monkeypatch.setattr(tool_roots, "get_tool_roots_path", lambda: scratch / "tool_roots.json")


@pytest.fixture(autouse=True)
def _every_shipped_preset_offered(request, monkeypatch):
    """Show every committed material in the dropdowns, whatever ships enabled.

    Which of the committed presets are offered is a deployment choice -- at
    v5.47.0 only MoS2_QC and WSe2_QC are, the rest carry ``enabled: false`` --
    and it moves whenever the lab's work does. The page tests drive real
    dropdowns and pick real materials (Silicon for the Raman-only path, WSe2
    for the one with an optical pair), so without this they would fail on a
    choice that says nothing about the code they cover.

    The shipped set itself is asserted in
    `tests/unit/test_preset_store.py::TestTheCommittedFile`, which reads the
    JSON rather than this loader and so is unaffected.

    A test about the Enabled control itself asks for the file's own flags with
    ``@pytest.mark.shipped_preset_flags``.

    `load_store` is what is replaced, not `load_presets`: half the callers
    imported that name at import time (`modules/spectra/ui/workspace.py`), and
    a page re-executed by AppTest would see the patch while a module imported
    once would not. `load_presets` resolves `load_store` in this module's
    globals on every call, so patching it reaches both.
    """
    from dataclasses import replace

    import modules.spectra.io.preset_store as preset_store

    if request.node.get_closest_marker("shipped_preset_flags"):
        return

    real = preset_store.load_store

    def _all_offered(*args, **kwargs):
        store = real(*args, **kwargs)
        store.presets = {name: (p if p.enabled else replace(p, enabled=True))
                         for name, p in store.presets.items()}
        return store

    monkeypatch.setattr(preset_store, "load_store", _all_offered)
