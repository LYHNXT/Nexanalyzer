"""MoS2 QC Reports for Samsung-Oct sample folders (QC_KRX261005, QC_KRX261006/VM26100xx), fitting in parallel.

Drives the real QC Report page (pages/2_QC_Report.py) through streamlit.testing.v1.AppTest, like
run_qc_reports.py beside it: preset MoS2_QC (--material to change), film 1L (the preset's only optical block), OM 50X, domain size
off, population split off. Output goes to <sample>/QC_Report_<date>/, so nothing in the folder is overwritten.

Two departures from the page, both outside its code:
* The files are named RM_MAPPING_<N>.txt / PL_MAPPING_<N>.txt, which the scanner does not recognise, so they
  are registered on the scan by hand. Raman is already in cm-1 and PL in nm; nothing is converted.
* `sample_batch.run_sample_batch` is swapped for a version that fits every spectrum in a process pool. Each
  worker runs the same parse_spectrum_multi + execute_auto_workflow per spectrum, and results are put back
  in the serial order, so the numbers are the serial run's.

Each quality-figure panel's legend also carries "Robust CV = x%" (1.4826 * MAD / |median| * 100 of the
pooled points) under its median, as in QC_KRX261002/Robust CV; added by wrapping peak_quality._draw_panel here.
The PL quality figure gets a leading "Raw Spectrum" column (Raw FWHM, Raw center) and no ratio column, as in
IMEC-Nov/pl_stats_with_raw.py. The Raman stats figure, summary page and workbook follow the WSe2 reports' layout,
with MoS2's Δω (A1g − E2g) and I(2LA(M))/I(A1g) in WSe2's ratio slots (see align_mos2_raman_with_wse2).

Usage: python run_qc_mos2.py SAMPLE_FOLDER [SAMPLE_FOLDER ...] [--workers N]
       (relative folders resolve against the working directory; the report is titled by the folder name)
"""
import argparse
import dataclasses
import os
import re
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from datetime import date
from functools import lru_cache
from pathlib import Path

APP = Path(__file__).resolve().parents[2]  # the repo root: scripts/qc_report/<this file>
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(APP))

MATERIAL = "MoS2_QC"  # --material overrides
LAYER = "1L"
MAGNIFICATION = "50X"
REPORT_DPI = 300  # every report PNG; the app's own default from v5.28.0 (core.report.REPORT_DPI)


@lru_cache(maxsize=4)
def _parse(path):
    from modules.spectra.processing.parser import parse_spectrum_multi
    return parse_spectrum_multi(path)


@lru_cache(maxsize=1)
def _preset(name):
    from modules.spectra.io.preset_store import load_presets
    return load_presets()[name]


def _fit_one(task):
    """(mode, point, index, multi, path, preset name) -> ("ok", SpectrumFile) | ("err", message)."""
    mode, point, index, multi, path, preset_name = task
    from modules.spectra.models.spectrum import ProcessingSettings, SpectrumFile
    from modules.spectra.processing.auto_workflow import execute_auto_workflow
    data = _parse(path)[index - 1]
    filename = f"{mode}_{point}__{index}" if multi else f"{mode}_{point}"
    try:
        spectrum_file = SpectrumFile(filename=filename, mode=mode, original_data=data, raw_data=data,
                                     processed_data=data, processing_settings=ProcessingSettings())
        result = execute_auto_workflow(spectrum_file, _preset(preset_name))
        if result["success"]:
            return "ok", spectrum_file
        return "err", result.get("error_message", "Unknown error")
    except Exception as e:
        return "err", str(e)


def make_parallel_batch(workers):
    from modules.spectra.processing.parser import parse_spectrum_multi
    from modules.spectra.processing.sample_batch import SampleBatchResult
    # AppTest replaces __main__, so the pool must find the worker under this file's module name.
    sys.path.insert(0, str(HERE))
    from run_qc_mos2 import _fit_one as fit_one

    def run_sample_batch(scan, raman_preset, pl_preset, progress_callback=None):
        result = SampleBatchResult()
        plan = []  # (mode, out_spectra, out_errors, tasks)
        for files, mode, preset, out_s, out_e in (
                (scan.raman_files, "Raman", raman_preset, result.raman_spectra, result.raman_errors),
                (scan.pl_files, "PL", pl_preset, result.pl_spectra, result.pl_errors)):
            if preset is None:
                continue
            tasks = []
            for point in sorted(files):
                try:
                    n = len(parse_spectrum_multi(files[point]))
                except Exception as e:
                    out_e.append((point, str(e)))
                    continue
                tasks += [(mode, point, i, n > 1, files[point], preset.material_name) for i in range(1, n + 1)]
            plan.append((mode, out_s, out_e, tasks))

        with ProcessPoolExecutor(max_workers=workers) as pool:
            for mode, out_s, out_e, tasks in plan:
                t0 = time.time()
                for done, (task, (status, value)) in enumerate(
                        zip(tasks, pool.map(fit_one, tasks, chunksize=4)), start=1):
                    (out_s if status == "ok" else out_e).append((task[1], value))
                    if progress_callback is not None:
                        progress_callback(mode, done, len(tasks))
                print(f"  {mode}: {len(tasks)} spectra in {time.time() - t0:.0f} s "
                      f"({len(out_s)} fitted, {len(out_e)} errors)", flush=True)
        return result

    return run_sample_batch


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
    import core.io.export as export_module
    import modules.optical.ui.qc_report_state as state_module
    import modules.spectra.processing.sample_batch as batch_module
    from modules.spectra.processing.sample_scanner import scan_sample_folder
    from streamlit.testing.v1 import AppTest

    settings = APP / "data" / "report_settings.json"
    backup = settings.read_bytes() if settings.exists() else None
    # Installed once: each wraps the module attribute it replaces, so installing twice would stack.
    parallel_batch, last_batch = make_parallel_batch(args.workers), []

    def run_sample_batch(*a, **kw):
        last_batch[:] = [parallel_batch(*a, **kw)]
        return last_batch[0]

    batch_module.run_sample_batch = run_sample_batch
    # The MoS2 layout (FWHM | Center | Δω, the 2LA(M)/A1g ratio, the Δω summary
    # row), Robust CV and the Raw PL column are all the app's own since v5.34.0
    # (and v5.28.0/v5.29.0): this runner no longer patches any of them.

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
        state.update(folder=str(sample), scan=scan, material=args.material, magnification=MAGNIFICATION,
                     reference_layer=LAYER, layer_source="operator", measure_domain_size=False,
                     split_size_populations=False, low_coverage=False)

        at = AppTest.from_file(str(APP / "pages" / "2_QC_Report.py"), default_timeout=3600)
        at.session_state["qc_report"] = state
        at.run()
        assert not at.exception, [e.value for e in at.exception]
        next(b for b in at.button if "Generate QC Report" in b.label).click().run()
        assert not at.exception, [e.value for e in at.exception]
        for w in list(at.warning) + list(at.error):
            print("  page:", w.value)
            if "failed" in str(w.value):
                raise RuntimeError(w.value)
        next(b for b in at.button if "Save QC Report" in b.label).click().run()
        assert not at.exception, [e.value for e in at.exception]
        s = at.session_state["qc_report"]
        suggestion = s.get("layer_suggestion")
        print(f"  layer suggestion: {getattr(suggestion, 'layer', None)}")
        for t in ("raman", "pl"):
            print(f"  {t}: fitted {s.get(t + '_fitted')}, dropped {s.get(t + '_dropped')}, "
                  f"unconverged {s.get(t + '_unconverged')}, errors {len(s.get(t + '_errors') or [])}")
        print(f"  {time.time() - t0:.0f} s -> {out}\n  " +
              "\n  ".join(sorted(p.name for p in out.iterdir())), flush=True)

    missing = [str(x) for x in samples if not x.is_dir()]
    if missing:
        raise SystemExit("not a folder: " + ", ".join(missing))
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
