"""Golden-fit regression: two real spectra must fit to the numbers on record.

The safety check for the spectral fit algorithm. One Raman and one PL spectrum
from HKU-20230524-2 (WO3+Se, i.e. WSe2) are run through the same
``execute_auto_workflow`` the app's "Run" button calls -- parse, x-range,
despike, baseline, Voigt fit -- and every reported number is compared with
``tests/fixtures/golden_fit/expected.json``.

The preset is frozen in ``wse2_preset.json`` rather than read from
data/materials.json, so tuning the shipped WSe2 preset does not trip this test.
What does trip it is a change to code: the parser, despike, baseline, fitting,
or ``peak_metrics``. That is the point -- a failure here means the algorithm
moved, and the numbers every report shows moved with it.

A change that is *meant* to move the numbers regenerates the record, in the
same commit, with a CHANGELOG entry saying so::

    python tests/unit/test_golden_fit.py --regenerate

The two spectra are committed by an explicit .gitignore exception (measurement
data is otherwise kept out of the repo). Should they ever be missing this test
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
EXPECTED = GOLDEN_DIR / "expected.json"
SPECTRA = ("RM_1.txt", "PL_1.txt")

# Same machine, same libraries: the fit is bit-for-bit deterministic. The slack
# is for a numpy/scipy/lmfit upgrade reordering floating-point sums, which moves
# the 8th digit, never the 5th. A real algorithm change moves far more.
RTOL = 1e-5


def fit_summary(filename: str) -> dict:
    """Fit one golden spectrum and return every number the app reports for it."""
    preset = MaterialPreset.from_dict(json.loads((GOLDEN_DIR / "wse2_preset.json").read_text("utf-8")))
    data = parse_spectrum(str(GOLDEN_DIR / filename))
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
        "peaks": peaks,
    }


def _require_fixtures():
    missing = [f for f in SPECTRA if not (GOLDEN_DIR / f).exists()]
    if not missing:
        return
    message = f"golden spectra not present in {GOLDEN_DIR}: {', '.join(missing)}"
    if os.environ.get("NEXANALYZER_REQUIRE_GOLDEN"):
        pytest.fail(message)
    pytest.skip(message)


@pytest.mark.parametrize("filename", SPECTRA)
def test_fit_matches_golden_record(filename):
    _require_fixtures()
    expected = json.loads(EXPECTED.read_text("utf-8"))[filename]
    actual = fit_summary(filename)

    assert actual["mode"] == expected["mode"]
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
        f"{filename}: the fit no longer reproduces the golden record -- the "
        f"algorithm changed.\n  " + "\n  ".join(drift) + "\nIf intended, run "
        "`python tests/unit/test_golden_fit.py --regenerate` and say so in CHANGELOG."
    )


if __name__ == "__main__":
    if "--regenerate" not in sys.argv:
        sys.exit("usage: python tests/unit/test_golden_fit.py --regenerate")
    record = {f: fit_summary(f) for f in SPECTRA}
    EXPECTED.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {EXPECTED}")
