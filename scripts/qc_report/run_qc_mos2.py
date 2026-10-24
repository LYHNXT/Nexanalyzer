"""MoS2 QC Reports for Samsung-Oct sample folders (QC_KRX261005, QC_KRX261006/VM26100xx), fitting in parallel.

Runs the QC Report page's own pipeline (modules.qc_report, v5.35.0) through _runner.run_report: preset
MoS2_QC (--material to change), film 1L (the preset's only optical block), OM 50X, domain size off, population
split off. Output goes to <sample>/QC_Report_<date>/, so nothing in the folder is overwritten.

One departure from the page, outside its code: the files are named RM_MAPPING_<N>.txt / PL_MAPPING_<N>.txt,
which the scanner does not recognise, so they are registered on the scan by hand. Raman is already in cm-1
and PL in nm; nothing is converted. The MoS2 layout (FWHM | Center | Δω, the 2LA(M)/A1g ratio, the Δω summary
row), Robust CV and the Raw PL column are the app's own (v5.34.0, v5.28.0, v5.29.0).

Usage: python run_qc_mos2.py SAMPLE_FOLDER [SAMPLE_FOLDER ...] [--workers N]
       (relative folders resolve against the working directory; the report is titled by the folder name)
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
sys.path.insert(0, str(HERE))  # _runner

MATERIAL = "MoS2_QC"  # --material overrides
LAYER = "1L"
MAGNIFICATION = "50X"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("samples", nargs="+", help="sample folders (absolute, or relative to the working directory)")
    ap.add_argument("--workers", type=int, default=max(1, (os.cpu_count() or 2) - 2))
    ap.add_argument("--material", default=MATERIAL, help="preset name (default %(default)s)")
    ap.add_argument("--name", default=None, help="report title / sample name (one sample; default: the folder name)")
    ap.add_argument("--suffix", default="", help="appended to the QC_Report_<date> folder name")
    args = ap.parse_args()
    # Before the chdir: relative folders are relative to the caller's directory.
    samples = [Path(x).resolve() for x in args.samples]

    os.chdir(APP)
    from modules.spectra.processing.sample_scanner import scan_sample_folder
    from _runner import run_report

    def run_one(sample):
        t0 = time.time()
        scan = scan_sample_folder(str(sample))
        for f in sample.glob("*_MAPPING_*.txt"):
            point = int(re.search(r"_(\d+)$", f.stem).group(1))
            (scan.raman_files if f.name.upper().startswith("RM") else scan.pl_files)[point] = str(f)
        if args.name:
            scan = dataclasses.replace(scan, sample_name=args.name)  # ScanResult is frozen
        print(f"{scan.sample_name}: {len(scan.raman_files)} Raman, {len(scan.pl_files)} PL, "
              f"OM {MAGNIFICATION} of {scan.magnifications()}, {args.workers} workers", flush=True)
        out = sample / f"QC_Report_{date.today().isoformat()}{args.suffix}"
        run_report(scan, args.material, LAYER, MAGNIFICATION, out, args.workers)
        print(f"  {time.time() - t0:.0f} s -> {out}\n  " +
              "\n  ".join(sorted(p.name for p in out.iterdir())), flush=True)

    missing = [str(x) for x in samples if not x.is_dir()]
    if missing:
        raise SystemExit("not a folder: " + ", ".join(missing))
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
