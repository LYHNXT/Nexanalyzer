"""Golden-fit regression: real spectra must fit to the numbers on record.

The safety check for the spectral fit algorithm. Each golden set is a folder of
spectra, a frozen preset and an ``expected.json``; every spectrum is run through
the same ``execute_auto_workflow`` the app's "Run" button calls -- parse,
x-range, despike, baseline, Voigt fit -- and every reported number is compared
with the record. The sets (``GOLDEN_SETS``):

* ``tests/fixtures/golden_fit/`` -- HKU-20230524-2 (WO3+Se, i.e. WSe2) RM_1 and
  PL_1, preset ``wse2_preset.json``.
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
from modules.spectra.processing.peak_metrics import peak_intensity_and_stderr  # noqa: E402

GOLDEN_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "golden_fit"
#: (set name, folder holding the spectra, preset and expected.json, preset file, spectra)
GOLDEN_SETS = (
    ("HKU-20230524-2", GOLDEN_DIR, "wse2_preset.json", ("RM_1.txt", "PL_1.txt")),
    ("DDAA10", GOLDEN_DIR / "DDAA10", "wse2_qc_preset.json", ("RM_1.txt", "RM_2.txt", "RM_3.txt")),
)
CASES = [(name, filename) for name, _, _, spectra in GOLDEN_SETS for filename in spectra]

# Same machine, same libraries: the fit is bit-for-bit deterministic. The slack
# is for a numpy/scipy/lmfit upgrade reordering floating-point sums, which moves
# the 8th digit, never the 5th. A real algorithm change moves far more.
RTOL = 1e-5


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

    drift = []
    for key in ("r_squared", "chi_squared"):
        if not np.isclose(actual[key], expected[key], rtol=RTOL, atol=0):
            drift.append(f"{key}: {expected[key]!r} -> {actual[key]!r}")
    for label, exp_peak in expected["peaks"].items():
        for key, exp_value in exp_peak.items():
            value = actual["peaks"][label][key]
            if not np.isclose(value, exp_value, rtol=RTOL, atol=1e-12, equal_nan=True):
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
