"""Recommend a preset's `max_iterations` from the spectra it will actually fit.

    python scripts/recommend_max_iterations.py <folder> --preset WSe2 [--preset "WSe2 extended"]
                                               [--recursive] [--per-file 5] [--workers 8]
                                               [--headroom 1.5] [--csv fits.csv]

Every Raman/PL spectrum under <folder> (technique from the filename, as the app
reads it) is run through the app's own pipeline -- `execute_auto_workflow`, the
same call as Run Auto-Workflow -- with the preset's budget raised to the editor's
ceiling (20000), so each fit runs until it converges and reports how many
iterations that took. A map file contributes up to `--per-file` spectra, spread
across its columns.

The recommendation is the most iterations any converged fit needed, times
`--headroom`, rounded up to 100 and kept inside the editor's [500, 20000]. A fit
that still did not converge at the ceiling is counted separately: no budget
fixes it, and the usual cause is a peak the spectrum cannot determine (outside
the x-range, or fitted to zero).

With fit windows, each window is its own fit with its own budget; the iterations
reported for a spectrum are its most demanding window's.

Iterations, not evaluations: since v5.21.0 a preset's `max_iterations` is
passed to lmfit as `max_iterations * (variables + 1)` evaluations, so the
number printed here is directly the number to type into the preset.
"""
import argparse
import copy
import csv
import logging
import math
import os
import sys
from collections import defaultdict
from multiprocessing import Pool
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

PROBE = 20000          # the preset editor's ceiling (TechniquePreset.validate)
FLOOR = 500            # ... and its floor
_PRESETS = {}


def _quiet():
    # execute_auto_workflow touches st.session_state; outside `streamlit run`
    # that only logs a warning per access, which would bury the table.
    logging.disable(logging.CRITICAL)
    import warnings
    warnings.filterwarnings("ignore")


def _init(names):
    _quiet()
    from modules.spectra.io.preset_store import load_presets
    presets = load_presets()
    for name in names:
        _PRESETS[name] = presets[name]


def find_spectra(folder: Path, recursive: bool):
    from modules.spectra.processing.parser import detect_mode_from_filename
    pattern = "**/*.txt" if recursive else "*.txt"
    for path in sorted(folder.glob(pattern)):
        mode = detect_mode_from_filename(path.name)
        if mode:
            yield path, mode


def _columns(path: Path, per_file: int):
    from modules.spectra.processing.parser import count_spectra
    n = count_spectra(str(path))
    if n <= per_file:
        return list(range(n))
    if per_file == 1:
        return [n // 2]
    return sorted({round(i * (n - 1) / (per_file - 1)) for i in range(per_file)})


def fit_one(task):
    name, path, mode, col = task
    from modules.spectra.models.spectrum import ProcessingSettings, SpectrumFile
    from modules.spectra.processing import auto_workflow
    from modules.spectra.processing.parser import parse_spectrum_multi

    # A preset with fit windows runs one fit per window, each with its own
    # budget, so the number that has to fit under the cap is the *largest*
    # window's iterations -- not the total, which is all FitResult keeps.
    # Record every fit call's own iterations on the way past.
    calls = []
    original = auto_workflow.fit_voigt_peaks
    if not getattr(original, "_recording", False):
        def recording(*a, **k):
            result = original(*a, **k)
            if result.nfev is not None and result.max_nfev:
                calls.append(math.ceil(result.nfev * k["max_iterations"] / result.max_nfev))
            return result
        recording._recording = True
        recording._calls = calls
        auto_workflow.fit_voigt_peaks = recording
    calls = auto_workflow.fit_voigt_peaks._calls
    calls.clear()

    preset = _PRESETS[name]
    block = preset.block_for(mode)
    row = dict(preset=name, technique=mode, file=str(path), column=col,
               current_cap=block.max_iterations if block else None)
    if block is None:
        return {**row, "error": f"no {mode} block"}
    probe = copy.deepcopy(preset)
    probe.block_for(mode).max_iterations = PROBE
    try:
        data = parse_spectrum_multi(str(path))[col]
        spectrum = SpectrumFile(filename=path.name, mode=mode, original_data=data, raw_data=data,
                                processed_data=data, processing_settings=ProcessingSettings())
        result = auto_workflow.execute_auto_workflow(spectrum, probe)
    except Exception as e:  # a bad file is reported, not fatal
        return {**row, "error": str(e)[:120]}
    if not result["success"]:
        return {**row, "error": result["error_message"][:120]}
    fit = result["fit_result"]
    return {**row, "error": "", "converged": fit.converged, "nfev": fit.nfev,
            "iterations": max(calls), "windows": len(calls),
            "seconds": round(fit.convergence_time, 2), "r_squared": round(fit.r_squared, 4)}


def _pct(values, q):
    values = sorted(values)
    return values[min(len(values) - 1, max(0, math.ceil(q / 100 * len(values)) - 1))]


def summarise(rows, headroom):
    groups = defaultdict(list)
    for r in rows:
        groups[(r["preset"], r["technique"])].append(r)
    print()
    header = (f"{'preset':22s} {'tech':5s} {'fits':>5s} {'err':>4s} {'stuck':>5s} "
              f"{'it p50':>7s} {'it p90':>7s} {'it max':>7s} {'s/fit':>6s} {'s max':>6s} "
              f"{'cap now':>8s} {'cut now':>7s} {'recommend':>9s}")
    print(header)
    print("-" * len(header))
    for (name, tech), rs in sorted(groups.items()):
        ok = [r for r in rs if not r["error"]]
        conv = [r for r in ok if r["converged"]]
        stuck = len(ok) - len(conv)
        errors = len(rs) - len(ok)
        cap_now = rs[0]["current_cap"]
        if not conv:
            print(f"{name:22s} {tech:5s} {len(rs):5d} {errors:4d} {stuck:5d}   no converged fits to measure")
            continue
        its = [r["iterations"] for r in conv]
        secs = [r["seconds"] for r in conv]
        cut = sum(1 for r in ok if r["iterations"] > (cap_now or 0) or not r["converged"])
        rec = min(PROBE, max(FLOOR, math.ceil(max(its) * headroom / 100) * 100))
        print(f"{name:22s} {tech:5s} {len(rs):5d} {errors:4d} {stuck:5d} "
              f"{_pct(its, 50):7d} {_pct(its, 90):7d} {max(its):7d} {_pct(secs, 50):6.1f} {max(secs):6.1f} "
              f"{cap_now!s:>8s} {cut:7d} {rec:9d}")
    print()
    print("it = iterations to converge (the preset's max_iterations unit). 'cut now' = fits the")
    print("current cap would stop before convergence. 'stuck' = not converged even at the ceiling of")
    print(f"{PROBE}: fix the preset (peaks outside the x-range, peaks fitted to zero), not the budget.")
    bad = [r for r in rows if r["error"]]
    if bad:
        print(f"\n{len(bad)} spectra could not be fitted; first few:")
        for r in bad[:5]:
            print(f"  {os.path.basename(r['file'])}[{r['column']}] ({r['preset']}): {r['error']}")


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("folder", type=Path)
    ap.add_argument("--preset", action="append", required=True, help="material name; repeat for several")
    ap.add_argument("--recursive", action="store_true", help="also search subfolders")
    ap.add_argument("--per-file", type=int, default=5, help="spectra taken from each map file (default 5)")
    ap.add_argument("--workers", type=int, default=max(1, (os.cpu_count() or 2) - 1))
    ap.add_argument("--headroom", type=float, default=1.5, help="multiplier on the worst case (default 1.5)")
    ap.add_argument("--csv", type=Path, help="also write one row per fit here")
    args = ap.parse_args()

    _init(args.preset)   # validates the names before any work starts
    tasks = [(name, path, mode, col)
             for path, mode in find_spectra(args.folder, args.recursive)
             for col in _columns(path, args.per_file)
             for name in args.preset
             if _PRESETS[name].block_for(mode) is not None]
    if not tasks:
        sys.exit(f"No Raman/PL spectra for {args.preset} under {args.folder}")
    print(f"{len(tasks)} fits on {args.workers} workers (budget raised to {PROBE} so each runs to convergence)...")

    rows = []
    with Pool(args.workers, initializer=_init, initargs=(args.preset,)) as pool:
        for i, row in enumerate(pool.imap_unordered(fit_one, tasks), 1):
            rows.append(row)
            if i % 25 == 0 or i == len(tasks):
                print(f"  {i}/{len(tasks)}", flush=True)

    summarise(rows, args.headroom)
    if args.csv:
        keys = ["preset", "technique", "file", "column", "error", "converged", "nfev", "iterations",
                "windows", "seconds", "r_squared", "current_cap"]
        with open(args.csv, "w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=keys, extrasaction="ignore")
            w.writeheader()
            w.writerows(rows)
        print(f"\nPer-fit rows: {args.csv}")


if __name__ == "__main__":
    main()
