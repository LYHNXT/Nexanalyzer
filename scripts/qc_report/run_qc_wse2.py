"""WSe2 QC Reports for customer sample folders (written for Samsung-Oct KRX261001..04), fitting in parallel.

Drives the real QC Report page (pages/2_QC_Report.py) through streamlit.testing.v1.AppTest, like
run_qc_mos2.py beside it, whose process-pool batch it reuses (same per-spectrum workflow, serial order).
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
    import core.io.export as export_module
    import modules.optical.ui.qc_report_state as state_module
    import modules.spectra.processing.sample_batch as batch_module
    from modules.spectra.processing.sample_scanner import scan_sample_folder
    from streamlit.testing.v1 import AppTest
    from run_qc_mos2 import make_parallel_batch

    settings = APP / "data" / "report_settings.json"
    backup = settings.read_bytes() if settings.exists() else None
    batch_module.run_sample_batch = make_parallel_batch(args.workers)

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
        out.mkdir(exist_ok=True)
        target = out / f"{scan.sample_name}_QC_Report.png"
        export_module.prompt_save_path = lambda default_dir, default_filename, **kw: str(target)

        store = {}
        real_st = state_module.st
        state_module.st = type("_St", (), {"session_state": store})()
        try:
            state_module.initialize_qc_report_state()
        finally:
            state_module.st = real_st
        state = store["qc_report"]
        state.update(folder=str(sample), scan=scan, material=MATERIAL, magnification=magnification,
                     reference_layer=args.layer, layer_source="operator", measure_domain_size=False,
                     split_size_populations=False, low_coverage=False)

        at = AppTest.from_file(str(APP / "pages" / "2_QC_Report.py"), default_timeout=3600)
        at.session_state["qc_report"] = state
        at.run()
        assert not at.exception, [e.value for e in at.exception]
        next(b for b in at.button if "Generate QC Report" in b.label).click().run()
        assert not at.exception, [e.value for e in at.exception]
        for w in list(at.warning) + list(at.error) + list(at.info):
            print("  page:", w.value)
            if "failed" in str(w.value):
                raise RuntimeError(w.value)
        next(b for b in at.button if "Save QC Report" in b.label).click().run()
        assert not at.exception, [e.value for e in at.exception]
        s = at.session_state["qc_report"]
        for t in ("raman", "pl"):
            print(f"  {t}: fitted {s.get(t + '_fitted')}, dropped {s.get(t + '_dropped')}, "
                  f"unconverged {s.get(t + '_unconverged')}, errors {len(s.get(t + '_errors') or [])}, "
                  f"selection {s.get(t + '_selection')}")
        print(f"  {time.time() - t0:.0f} s -> {out}\n  " +
              "\n  ".join(sorted(p.name for p in out.iterdir())), flush=True)

    failed = []
    try:
        for sample in samples:
            try:
                run_one(sample)
            except Exception as e:
                print(f"  FAILED {sample.name}: {type(e).__name__}: {e}", flush=True)
                failed.append(sample.name)
    finally:
        if backup is not None:
            settings.write_bytes(backup)
    if failed:
        raise SystemExit("failed: " + ", ".join(failed))


if __name__ == "__main__":
    main()
