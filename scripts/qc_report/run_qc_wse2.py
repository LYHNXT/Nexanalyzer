"""WSe2 QC Reports for customer sample folders (written for Samsung-Oct KRX261001..04), fitting in parallel.

Runs the QC Report page's own pipeline (modules.qc_report, v5.35.0) through _runner.run_report, fitting
in a process pool (same per-spectrum workflow, serial order).
Preset WSe2_QC, reference layer 2L, domain size off, population split off; OM 100x where the folder has it,
else 50X. Everything else -- outlier removal and 25 spectra per point, Robust CV, Raw PL column -- is the
app's own (v5.28.0+). Output goes to <sample>/QC_Report_<date>/.

Spectrum files the scanner does not recognise are registered by hand (all already in cm-1 / nm):
  QC_KRX261003: RMVABD75<NN>.txt / PLVABD75<NN>.txt      QC_KRX261004: RMPL/RM_<N>.txt / RMPL/PL_<N>.txt
QC_KRX261001/02's RM_MAPPING_<N>.txt are found by the scanner.

Usage: python scripts/qc_report/run_qc_wse2.py SAMPLE_FOLDER [...] [--workers N] [--suffix S] [--layer 1L|2L] [--name TITLE]
       (relative folders resolve against the working directory)
"""
import argparse
import dataclasses
import os
import re
import sys
import time
from datetime import date
from pathlib import Path

APP = Path(__file__).resolve().parents[2]  # the repo root: scripts/qc_report/<this file>
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(APP))
sys.path.insert(0, str(HERE))

MATERIAL = "WSe2_QC"
LAYER = "2L"


def register_unrecognised(sample, scan):
    patterns = [(sample.glob("RMVABD*[0-9][0-9].txt"), r"(\d\d)$", "raman"),
                (sample.glob("PLVABD*[0-9][0-9].txt"), r"(\d\d)$", "pl"),
                ((sample / "RMPL").glob("RM_*.txt"), r"_(\d+)$", "raman"),
                ((sample / "RMPL").glob("PL_*.txt"), r"_(\d+)$", "pl"),
                (sample.glob("MAPPING_*.txt"), r"^MAPPING_(\d+)$", "raman")]  # IMEC HADH71/75: Raman in cm-1
    for files, rx, kind in patterns:
        for f in files:
            m = re.search(rx, f.stem)
            if not m:
                continue
            point = int(m.group(1))
            (scan.raman_files if kind == "raman" else scan.pl_files)[point] = str(f)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("samples", nargs="+", help="sample folders (absolute, or relative to the working directory)")
    ap.add_argument("--workers", type=int, default=max(1, (os.cpu_count() or 2) - 2))
    ap.add_argument("--suffix", default="", help="appended to the QC_Report_<date> folder name")
    ap.add_argument("--layer", default=LAYER, help="reference layer (default %(default)s)")
    ap.add_argument("--name", default=None, help="report title / sample name (default: the folder name)")
    args = ap.parse_args()
    # Before the chdir: relative folders are relative to the caller's directory.
    samples = [Path(x).resolve() for x in args.samples]
    missing = [str(x) for x in samples if not x.is_dir()]
    if missing:
        raise SystemExit("not a folder: " + ", ".join(missing))

    os.chdir(APP)
    from modules.spectra.processing.sample_scanner import scan_sample_folder
    from _runner import run_report

    def run_one(sample):
        t0 = time.time()
        scan = scan_sample_folder(str(sample))
        register_unrecognised(sample, scan)
        if args.name:
            scan = dataclasses.replace(scan, sample_name=args.name)  # ScanResult is frozen
        mags = scan.magnifications()
        magnification = next((m for m in mags if m.lower() == "100x"), next(iter(mags), "50X"))
        print(f"{scan.sample_name}: {len(scan.raman_files)} Raman, {len(scan.pl_files)} PL, "
              f"OM {magnification} of {mags}, {args.workers} workers", flush=True)
        out = sample / f"QC_Report_{date.today().isoformat()}{args.suffix}"
        run_report(scan, MATERIAL, args.layer, magnification, out, args.workers)
        print(f"  {time.time() - t0:.0f} s -> {out}\n  " +
              "\n  ".join(sorted(p.name for p in out.iterdir())), flush=True)

    failed = []
    for sample in samples:
        try:
            run_one(sample)
        except Exception as e:
            print(f"  FAILED {sample.name}: {type(e).__name__}: {e}", flush=True)
            failed.append(sample.name)
    if failed:
        raise SystemExit("failed: " + ", ".join(failed))


if __name__ == "__main__":
    main()
