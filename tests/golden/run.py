"""
Validated golden set: run every case, compare with its independently
referenced expected values, and write a pass/fail report.

    python -m tests.golden.run [--out dist/golden] [--cases tests/golden/cases]

Two tiers exist (Quality Guide §2): ``tests/unit/test_golden_fit.py`` is the
*tripwire* (the program's own earlier output, 1e-5, "did the code change?").
This runner is the *validated* tier: each case's ``expected.json`` carries a
``reference`` naming the independent check its numbers came from (literature,
another program's fit, a by-eye classification, or the spec for bad input),
and a tolerance per value. A case without a reference is refused, because a
reference that is "whatever the program printed" only locks in today's errors
(§2.4).

Case layout -- ``tests/golden/cases/<kind>/<case>/``:

- ``spectral_fit``: ``input.txt`` (named ``RM_*.txt`` or ``PL_*.txt`` so the
  mode is read from it, or ``mode`` set in settings), ``settings.json``
  (``{"preset": <MaterialPreset.to_dict()>}``) and ``expected.json``
  (``{"reference": ..., "mode": ..., "peaks": {label: {key: [value, tol]}},
  "r_squared_min": ...}``; keys from ``fit_record.PEAK_KEYS``).
- ``bad_input``: ``input.<ext>`` and ``expected.json``
  (``{"reference": ..., "error": "EmptyFile", "message_contains": "empty"}``).
  The expected result is a specific error, not a crash and not a number.

The report (``golden_report.md`` and ``.html``) lists every case with its
reference and, for failures, expected against actual. Archive it with each
release; it doubles as the validation record (§4.3). Exit code 1 on any
failure or refused case, so CI and the release checklist can gate on it.
"""

from __future__ import annotations

import argparse
import datetime as dt
import html
import json
import sys
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

HERE = Path(__file__).resolve().parent
DEFAULT_CASES = HERE / "cases"
KINDS = ("spectral_fit", "bad_input")


@dataclass
class CaseResult:
    kind: str
    name: str
    reference: str
    passed: bool
    lines: List[str] = field(default_factory=list)   # expected vs actual, or the reason
    actual: Optional[dict] = None


def discover(cases_dir: Path = DEFAULT_CASES) -> List[Path]:
    """Every case folder holding an expected.json, skipping ``_``-prefixed templates."""
    found = []
    for kind in KINDS:
        for case in sorted((cases_dir / kind).glob("*/expected.json")):
            if not case.parent.name.startswith("_"):
                found.append(case.parent)
    return found


def _input_file(case_dir: Path) -> Path:
    inputs = [p for p in case_dir.iterdir() if p.name.startswith("input")]
    if len(inputs) != 1:
        raise FileNotFoundError(f"{case_dir}: expected exactly one input* file, found {len(inputs)}")
    return inputs[0]


def run_case(case_dir: Path) -> CaseResult:
    kind = case_dir.parent.name
    name = case_dir.name
    expected = json.loads((case_dir / "expected.json").read_text("utf-8"))
    reference = str(expected.get("reference") or "").strip()
    if not reference:
        return CaseResult(kind, name, "", False, ["refused: expected.json has no `reference` (Quality Guide §2.4)"])
    try:
        if kind == "spectral_fit":
            return _run_spectral_fit(case_dir, name, reference, expected)
        if kind == "bad_input":
            return _run_bad_input(case_dir, name, reference, expected)
        return CaseResult(kind, name, reference, False, [f"unknown case kind {kind!r}"])
    except Exception:
        return CaseResult(kind, name, reference, False,
                          ["crashed: " + traceback.format_exc().strip().splitlines()[-1]])


def _run_spectral_fit(case_dir: Path, name: str, reference: str, expected: dict) -> CaseResult:
    from modules.spectra.models.preset import MaterialPreset
    from modules.spectra.processing.fit_record import fit_record, tolerance_drift

    settings = json.loads((case_dir / "settings.json").read_text("utf-8"))
    preset = MaterialPreset.from_dict(settings["preset"])
    actual = fit_record(_input_file(case_dir), preset, mode=settings.get("mode"))
    drift = tolerance_drift(actual, expected)
    return CaseResult("spectral_fit", name, reference, not drift, drift, actual)


def _run_bad_input(case_dir: Path, name: str, reference: str, expected: dict) -> CaseResult:
    from modules.spectra.processing import parser

    path = _input_file(case_dir)
    want_error = expected["error"]
    want_text = expected.get("message_contains", "")
    try:
        spectra = parser.parse_spectrum_multi(str(path))
    except Exception as exc:
        got = type(exc).__name__
        lines = []
        if got != want_error:
            lines.append(f"error: expected {want_error}, got {got}: {exc}")
        if want_text and want_text not in str(exc):
            lines.append(f"message: expected to contain {want_text!r}, got {str(exc)!r}")
        return CaseResult("bad_input", name, reference, not lines, lines or [f"{got}: {exc}"],
                          {"error": got, "message": str(exc)})
    return CaseResult("bad_input", name, reference, False,
                      [f"expected {want_error}, but the file parsed as {len(spectra)} spectrum/spectra"])


def write_report(results: List[CaseResult], out_dir: Path, now: Optional[dt.datetime] = None) -> Path:
    from core.version import __version__

    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = (now or dt.datetime.now()).isoformat(timespec="seconds")
    passed = sum(r.passed for r in results)
    lines = [
        "# NexAnalyzer validated golden set — report", "",
        f"NexAnalyzer {__version__} · run {stamp}", "",
        f"**{passed} / {len(results)} cases passed.**", "",
        "| Kind | Case | Result | Reference |", "|---|---|---|---|",
    ]
    for r in results:
        lines.append(f"| {r.kind} | {r.name} | {'PASS' if r.passed else 'FAIL'} | {r.reference or '—'} |")
    failures = [r for r in results if not r.passed]
    if failures:
        lines += ["", "## Failures: expected vs actual", ""]
        for r in failures:
            lines.append(f"### {r.kind} / {r.name}")
            lines += [f"- {line}" for line in r.lines] + [""]
    md = "\n".join(lines) + "\n"
    (out_dir / "golden_report.md").write_text(md, encoding="utf-8")

    rows = "".join(
        f"<tr><td>{html.escape(r.kind)}</td><td>{html.escape(r.name)}</td>"
        f"<td class={'ok' if r.passed else 'bad'}>{'PASS' if r.passed else 'FAIL'}</td>"
        f"<td>{html.escape(r.reference or '—')}</td></tr>" for r in results)
    fails = "".join(
        f"<h3>{html.escape(r.kind)} / {html.escape(r.name)}</h3><ul>"
        + "".join(f"<li>{html.escape(line)}</li>" for line in r.lines) + "</ul>" for r in failures)
    page = (
        "<!doctype html><meta charset='utf-8'><title>NexAnalyzer golden report</title>"
        "<style>body{font:14px sans-serif;margin:2em}table{border-collapse:collapse}"
        "td,th{border:1px solid #ccc;padding:4px 8px}.ok{color:#080}.bad{color:#b00;font-weight:bold}</style>"
        f"<h1>NexAnalyzer validated golden set — report</h1>"
        f"<p>NexAnalyzer {html.escape(__version__)} · run {stamp}</p>"
        f"<p><b>{passed} / {len(results)} cases passed.</b></p>"
        f"<table><tr><th>Kind</th><th>Case</th><th>Result</th><th>Reference</th></tr>{rows}</table>"
        + (f"<h2>Failures: expected vs actual</h2>{fails}" if failures else "")
    )
    (out_dir / "golden_report.html").write_text(page, encoding="utf-8")
    return out_dir / "golden_report.md"


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--cases", default=str(DEFAULT_CASES))
    ap.add_argument("--out", default=str(HERE.parents[1] / "dist" / "golden"))
    args = ap.parse_args(argv)

    sys.path.insert(0, str(HERE.parents[1]))

    cases = discover(Path(args.cases))
    if not cases:
        print(f"no cases under {args.cases}")
        return 1
    results = [run_case(c) for c in cases]
    for r in results:
        print(f"{'PASS' if r.passed else 'FAIL'} {r.kind}/{r.name}" + ("" if r.passed else "  " + "; ".join(r.lines)))
    report = write_report(results, Path(args.out))
    passed = sum(r.passed for r in results)
    print(f"\n{passed}/{len(results)} passed; report: {report}")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
