"""
Fit one spectrum file the way the app does and record every number it reports.

Used by ``tests/golden/run.py`` (the validated golden set, with tolerances).
``tests/unit/test_golden_fit.py`` keeps its own copy of the record logic: it
is a locked record, and its comparison is the one CI and the hooks run. The
customer edition (NexAnalyzer_TSMC) shares this module with its compiled
self-test, which is what ``tripwire_drift`` is for there.

Every value here is what a report shows: intensity is the fitted maximum
(``peak_metrics.peak_intensity_and_stderr``), area the integral, FWHM the
Voigt FWHM.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np

from ..models.preset import MaterialPreset
from ..models.spectrum import ProcessingSettings, SpectrumFile
from .auto_workflow import execute_auto_workflow
from .parser import detect_mode_from_filename, parse_spectrum
from .peak_metrics import peak_intensity_and_stderr, peak_is_measured

#: Same machine, same libraries: the fit is bit-for-bit deterministic. The
#: slack is for a numpy/scipy/lmfit upgrade reordering floating-point sums.
TRIPWIRE_RTOL = 1e-5

#: Another machine: the last digits differ, and the golden RM_1 amplifies them
#: (a 1-ulp change in the Voigt function moves its chi-squared ~1e-4 and its
#: unmeasured C/LB peaks ~3 %, and decides whether its stderrs exist at all).
#: A portable comparison checks the fit at PORTABLE_FIT_RTOL and only measured
#: peaks' PORTABLE_PEAK_KEYS at PORTABLE_PEAK_RTOL -- the same rule as
#: tests/unit/test_golden_fit.py.
PORTABLE_FIT_RTOL = 1e-3
PORTABLE_PEAK_RTOL = 1e-2
PORTABLE_PEAK_KEYS = ("center", "intensity", "area", "fwhm", "measured")
PORTABLE_ENV = "NEXANALYZER_GOLDEN_PORTABLE"

PEAK_KEYS = ("center", "center_stderr", "intensity", "intensity_stderr",
             "area", "area_stderr", "fwhm", "fwhm_stderr", "shape")


def fit_record(path, preset: MaterialPreset, mode: Optional[str] = None) -> dict:
    """Parse, x-range, despike, baseline and Voigt-fit `path` with `preset`.

    `mode` defaults to what the filename says (``RM_*`` / ``PL_*``). Raises
    RuntimeError with the workflow's own message when the fit does not succeed.
    """
    path = Path(path)
    data = parse_spectrum(str(path))
    spectrum = SpectrumFile(
        filename=path.name,
        mode=mode or detect_mode_from_filename(path.name),
        original_data=data,
        raw_data=data,
        processed_data=data,
        processing_settings=ProcessingSettings(),
    )
    result = execute_auto_workflow(spectrum, preset)
    if not result["success"]:
        raise RuntimeError(result["error_message"])

    fit = result["fit_result"]
    peaks: Dict[str, dict] = {}
    for p in fit.fitted_peaks:
        intensity, intensity_stderr = peak_intensity_and_stderr(p)
        peaks[p.label] = {
            "center": p.center,
            "center_stderr": p.center_stderr,
            "intensity": intensity,
            "intensity_stderr": intensity_stderr,
            "area": p.area,
            "area_stderr": p.area_stderr,
            "fwhm": p.width_fwhm,
            "fwhm_stderr": p.width_stderr,
            "shape": p.shape,
            "measured": peak_is_measured(p, fit),
        }
    return {
        "mode": spectrum.mode,
        "r_squared": fit.r_squared,
        "chi_squared": fit.chi_squared,
        "peaks": peaks,
    }


def tripwire_drift(actual: dict, expected: dict, portable: Optional[bool] = None) -> List[str]:
    """Lines describing every number in `actual` that left the recorded `expected`.

    The record is the program's own earlier output, so an empty list means
    "unchanged", not "correct" (Quality Guide §2.4). `portable` selects the
    other-machine comparison; None reads ``NEXANALYZER_GOLDEN_PORTABLE``.
    """
    if portable is None:
        portable = os.environ.get(PORTABLE_ENV) == "1"
    fit_rtol, peak_rtol = (PORTABLE_FIT_RTOL, PORTABLE_PEAK_RTOL) if portable else (TRIPWIRE_RTOL,) * 2
    drift: List[str] = []
    if actual["mode"] != expected["mode"]:
        drift.append(f"mode: {expected['mode']!r} -> {actual['mode']!r}")
    if set(actual["peaks"]) != set(expected["peaks"]):
        drift.append(f"peak labels: {sorted(expected['peaks'])} -> {sorted(actual['peaks'])}")
        return drift
    for key in ("r_squared", "chi_squared"):
        if not np.isclose(actual[key], expected[key], rtol=fit_rtol, atol=0):
            drift.append(f"{key}: {expected[key]!r} -> {actual[key]!r}")
    for label, exp_peak in expected["peaks"].items():
        if portable and not exp_peak.get("measured"):
            continue
        for key, exp_value in exp_peak.items():
            if portable and key not in PORTABLE_PEAK_KEYS:
                continue
            value = actual["peaks"][label][key]
            if not np.isclose(value, exp_value, rtol=peak_rtol, atol=1e-12, equal_nan=True):
                drift.append(f"{label}.{key}: {exp_value!r} -> {value!r}")
    return drift


def tolerance_drift(actual: dict, expected: dict) -> List[str]:
    """Validated-case comparison: every expected value is ``[value, tolerance]``.

    `expected` is a case's ``expected.json``: ``mode``, ``peaks`` mapping each
    label to ``{key: [value, tol]}`` for any of `PEAK_KEYS`, and optionally
    ``r_squared_min``. Only the keys the case states are checked, so a case
    that only vouches for centres says nothing about widths.
    """
    drift: List[str] = []
    if "mode" in expected and actual["mode"] != expected["mode"]:
        drift.append(f"mode: expected {expected['mode']!r}, got {actual['mode']!r}")
    if "r_squared_min" in expected and actual["r_squared"] < expected["r_squared_min"]:
        drift.append(f"r_squared: {actual['r_squared']:.5f} < {expected['r_squared_min']}")
    for label, keys in expected.get("peaks", {}).items():
        if label not in actual["peaks"]:
            drift.append(f"{label}: peak not fitted (got {sorted(actual['peaks'])})")
            continue
        for key, spec in keys.items():
            value, tol = spec
            got = actual["peaks"][label][key]
            if got is None or not np.isfinite(got) or abs(got - value) > tol:
                drift.append(f"{label}.{key}: expected {value} ± {tol}, got {got!r}")
    return drift
