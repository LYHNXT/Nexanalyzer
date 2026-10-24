"""fit_record's two comparisons: the validated golden set's tolerances, and the tripwire's.

The same module ships in NexAnalyzer_TSMC, where the compiled self-test uses
`tripwire_drift` with `portable=True`; these tests keep the rule identical here.
"""

import json

from modules.spectra.processing.fit_record import tolerance_drift, tripwire_drift


def _record():
    return {
        "mode": "Raman", "r_squared": 0.99, "chi_squared": 1000.0,
        "peaks": {
            "A": {"center": 250.0, "intensity": 100.0, "area": 500.0, "fwhm": 4.0,
                  "shape": 0.5, "center_stderr": float("nan"), "measured": True},
            "B": {"center": 30.0, "intensity": 1.0, "area": 6.0, "fwhm": 6.0,
                  "shape": 0.8, "center_stderr": float("nan"), "measured": False},
        },
    }


def _copy(record):
    return json.loads(json.dumps(record))


def test_tolerance_drift_checks_only_what_the_case_states():
    actual = {"mode": "Raman", "r_squared": 0.99, "chi_squared": 1.0,
              "peaks": {"A": {"center": 250.3, "fwhm": 4.2, "intensity": 100.0}}}
    assert tolerance_drift(actual, {"mode": "Raman", "peaks": {"A": {"center": [250.25, 0.1]}}}) == []
    lines = tolerance_drift(actual, {"peaks": {"A": {"fwhm": [4.0, 0.05]}}})
    assert lines and lines[0].startswith("A.fwhm")


def test_strict_tripwire_names_what_moved():
    record = _record()
    moved = _copy(record)
    moved["peaks"]["A"]["center"] += 1.0
    moved["r_squared"] *= 0.9
    lines = tripwire_drift(moved, record, portable=False)
    assert any(line.startswith("A.center") for line in lines)
    assert any(line.startswith("r_squared") for line in lines)


def test_portable_ignores_what_rounding_moves_and_catches_what_it_cannot():
    record = _record()

    rounding = _copy(record)
    rounding["chi_squared"] *= 1 + 2e-4
    rounding["peaks"]["B"]["center"] *= 1.03         # unmeasured: rounding decides it
    rounding["peaks"]["A"]["shape"] += 0.002         # shape: not compared
    rounding["peaks"]["A"]["center_stderr"] = 0.07   # stderrs: may or may not exist
    assert tripwire_drift(rounding, record, portable=True) == []
    assert tripwire_drift(rounding, record, portable=False) != []

    algorithm = _copy(record)
    algorithm["peaks"]["A"]["fwhm"] *= 1.05
    assert any(line.startswith("A.fwhm") for line in tripwire_drift(algorithm, record, portable=True))


def test_portable_follows_the_environment_when_not_told(monkeypatch):
    record = _record()
    rounding = _copy(record)
    rounding["peaks"]["B"]["center"] *= 1.03
    monkeypatch.setenv("NEXANALYZER_GOLDEN_PORTABLE", "1")
    assert tripwire_drift(rounding, record) == []
    monkeypatch.delenv("NEXANALYZER_GOLDEN_PORTABLE")
    assert tripwire_drift(rounding, record) != []
