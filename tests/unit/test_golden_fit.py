"""Golden-fit regression: real spectra must fit to the numbers on record.

The safety check for the spectral fit algorithm. Each golden set is a folder of
spectra, a frozen preset and an ``expected.json``; every spectrum is run through
the same ``execute_auto_workflow`` the app's "Run" button calls -- parse,
x-range, despike, baseline, Voigt fit -- and every reported number is compared
with the record. The sets (``GOLDEN_SETS``):

* ``tests/fixtures/golden_fit/HADI51/`` and ``.../VA1P012504023/`` -- one PL
  spectrum each (PL_5) from an HA1P01 and a VA production wafer, preset
  ``wse2_qc_pl_preset.json`` (WSe2_QC's PL block at v5.34.0). Chosen in v5.34.0
  from a screen of HA/VA PL spectra for R2 >= 0.999 and well-conditioned fits
  (a 1e-9 input perturbation moves no reported number by more than 3e-8).

The HKU-20230524-2 set (RM_1, PL_1, the old research "WSe2" preset) was
retired in v5.34.0: RM_1 was an 8-peak fit at R2 0.896, ill-conditioned enough
to move ~1e-4 between CPUs, and PL_1 a 2023 non-production sample on a preset
no report uses.
* ``tests/fixtures/golden_fit/DDAA10/`` -- DDAA10 RM_1..RM_3, three single
  Raman spectra of a WSe2 film, preset ``wse2_qc_preset.json`` (WSe2_QC's Raman
  block at v5.30.0: one 100-315 cm-1 window, zero-line calibration, one start,
  E2g+A1g 249+-5, 2ZA(M) 240+-3 with width cap 9). Added in v5.30.0 so the QC preset's path is locked too.

The presets are frozen rather than read from data/materials.json, so tuning a
shipped preset does not trip this test.
What does trip it is a change to code: the parser, despike, baseline, fitting,
or ``peak_metrics``. That is the point -- a failure here means the algorithm
moved, and the numbers every report shows moved with it.

A change that is *meant* to move the numbers regenerates the record, in the
same commit, with a CHANGELOG entry saying so::

    python tests/unit/test_golden_fit.py --regenerate [SET ...]

Naming sets regenerates only those, so adding a set cannot rewrite another's.

The spectra are committed by explicit .gitignore exceptions (measurement data
is otherwise kept out of the repo). Should any be missing this test
skips, and the git hooks in .githooks/ set NEXANALYZER_REQUIRE_GOLDEN=1, which
turns that skip into a failure: a hook that quietly checked nothing would be
worse than none.
"""

import json
import os
import sys
from pathlib import Path

import numpy as np
import pytest

if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from modules.spectra.models.preset import MaterialPreset  # noqa: E402
from modules.spectra.models.spectrum import ProcessingSettings, SpectrumFile  # noqa: E402
from modules.spectra.processing.auto_workflow import execute_auto_workflow  # noqa: E402
from modules.spectra.processing.parser import detect_mode_from_filename, parse_spectrum  # noqa: E402
from modules.spectra.processing.peak_metrics import peak_intensity_and_stderr, peak_is_measured  # noqa: E402

GOLDEN_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "golden_fit"
#: (set name, folder holding the spectra, preset and expected.json, preset file, spectra)
GOLDEN_SETS = (
    ("DDAA10", GOLDEN_DIR / "DDAA10", "wse2_qc_preset.json", ("RM_1.txt", "RM_2.txt", "RM_3.txt")),
    ("HADI51", GOLDEN_DIR / "HADI51", "wse2_qc_pl_preset.json", ("PL_5.txt",)),
    ("VA1P012504023", GOLDEN_DIR / "VA1P012504023", "wse2_qc_pl_preset.json", ("PL_5.txt",)),
)
CASES = [(name, filename) for name, _, _, spectra in GOLDEN_SETS for filename in spectra]

# Same machine, same libraries: the fit is bit-for-bit deterministic. The slack
# is for a numpy/scipy/lmfit upgrade reordering floating-point sums, which moves
# the 8th digit, never the 5th. A real algorithm change moves far more.
RTOL = 1e-5

# On another machine (CI, a second PC) the same code fits to different last
# digits, and an ill-conditioned fit amplifies them: on the retired HKU RM_1 a
# 1-ulp change in the Voigt function moved chi-squared by ~1e-4 and the
# unmeasured C and LB peaks by ~3 %, with the BLAS kernel pinned or not. With
# NEXANALYZER_GOLDEN_PORTABLE=1 (set by CI) the fit is compared at
# PORTABLE_FIT_RTOL and only the peaks the record marks measured
# (peak_metrics.peak_is_measured) at PORTABLE_PEAK_RTOL, on PORTABLE_PEAK_KEYS:
# rounding drifts those by <= 1e-3, a real algorithm change by percent. Shape is
# left out (it drifts freely near 0) and so are the stderrs (whether lmfit can
# estimate an ill-conditioned covariance at all flips with the last digit).
# The strict comparison still runs in the git hooks, on the machine that wrote
# the record.
PORTABLE = os.environ.get("NEXANALYZER_GOLDEN_PORTABLE") == "1"
PORTABLE_FIT_RTOL = 1e-3
PORTABLE_PEAK_RTOL = 1e-2
PORTABLE_PEAK_KEYS = ("center", "intensity", "area", "fwhm", "measured")


def _golden_set(name: str):
    return next(s for s in GOLDEN_SETS if s[0] == name)


def fit_summary(set_name: str, filename: str) -> dict:
    """Fit one golden spectrum and return every number the app reports for it."""
    _, folder, preset_file, _ = _golden_set(set_name)
    preset = MaterialPreset.from_dict(json.loads((folder / preset_file).read_text("utf-8")))
    data = parse_spectrum(str(folder / filename))
    spectrum = SpectrumFile(
        filename=filename,
        mode=detect_mode_from_filename(filename),
        original_data=data,
        raw_data=data,
        processed_data=data,
        processing_settings=ProcessingSettings(),
    )
    result = execute_auto_workflow(spectrum, preset)
    assert result["success"], result["error_message"]

    fit = result["fit_result"]
    peaks = {}
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
            # Which peaks a portable comparison may trust (see PORTABLE).
            "measured": peak_is_measured(p, fit),
        }
    return {
        "mode": spectrum.mode,
        "r_squared": fit.r_squared,
        "chi_squared": fit.chi_squared,
        # Recorded so the golden fit is known to be a finished fit. Before
        # v5.21.0 the Raman record was a fit that had stopped on its budget
        # (every stderr 0), and nothing in the record could show it.
        "converged": fit.converged,
        "peaks": peaks,
    }


def _require_fixtures(set_name: str):
    _, folder, _, spectra = _golden_set(set_name)
    missing = [f for f in spectra if not (folder / f).exists()]
    if not missing:
        return
    message = f"golden spectra not present in {folder}: {', '.join(missing)}"
    if os.environ.get("NEXANALYZER_REQUIRE_GOLDEN"):
        pytest.fail(message)
    pytest.skip(message)


@pytest.mark.parametrize("set_name, filename", CASES)
def test_fit_matches_golden_record(set_name, filename):
    _require_fixtures(set_name)
    _, folder, _, _ = _golden_set(set_name)
    expected = json.loads((folder / "expected.json").read_text("utf-8"))[filename]
    actual = fit_summary(set_name, filename)

    assert actual["mode"] == expected["mode"]
    assert actual["converged"] == expected["converged"], "convergence changed"
    assert set(actual["peaks"]) == set(expected["peaks"]), "peak labels changed"

    fit_rtol, peak_rtol = (PORTABLE_FIT_RTOL, PORTABLE_PEAK_RTOL) if PORTABLE else (RTOL, RTOL)
    drift = []
    for key in ("r_squared", "chi_squared"):
        if not np.isclose(actual[key], expected[key], rtol=fit_rtol, atol=0):
            drift.append(f"{key}: {expected[key]!r} -> {actual[key]!r}")
    for label, exp_peak in expected["peaks"].items():
        if PORTABLE and not exp_peak["measured"]:
            continue
        for key, exp_value in exp_peak.items():
            if PORTABLE and key not in PORTABLE_PEAK_KEYS:
                continue
            value = actual["peaks"][label][key]
            if not np.isclose(value, exp_value, rtol=peak_rtol, atol=1e-12, equal_nan=True):
                drift.append(f"{label}.{key}: {exp_value!r} -> {value!r}")

    assert not drift, (
        f"{set_name} {filename}: the fit no longer reproduces the golden record -- the "
        f"algorithm changed.\n  " + "\n  ".join(drift) + "\nIf intended, run "
        "`python tests/unit/test_golden_fit.py --regenerate` and say so in CHANGELOG."
    )


if __name__ == "__main__":
    if "--regenerate" not in sys.argv:
        sys.exit("usage: python tests/unit/test_golden_fit.py --regenerate [SET ...]")
    wanted = [a for a in sys.argv[1:] if a != "--regenerate"] or [s[0] for s in GOLDEN_SETS]
    for name, folder, _, spectra in GOLDEN_SETS:
        if name not in wanted:
            continue
        record = {f: fit_summary(name, f) for f in spectra}
        (folder / "expected.json").write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
        print(f"wrote {folder / 'expected.json'}")
