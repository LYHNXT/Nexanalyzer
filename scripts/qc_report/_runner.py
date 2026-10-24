"""What run_qc_wse2.py and run_qc_mos2.py share: a parallel fitting batch and one report run.

Both used to drive the QC Report page through `AppTest` -- faking the save
dialog, clicking buttons by label, and aborting a whole wafer on any page
message containing "failed". Since v5.35.0 they call the page's own pipeline,
`modules.qc_report.processing.build.build_qc_report`, directly: same inputs,
same numbers, and a per-point fit error is reported rather than fatal.
"""
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from functools import lru_cache
from pathlib import Path

APP = Path(__file__).resolve().parents[2]  # the repo root: scripts/qc_report/<this file>
HERE = Path(__file__).resolve().parent
for _p in (str(APP), str(HERE)):
    if _p not in sys.path:
        sys.path.insert(0, _p)


@lru_cache(maxsize=4)
def _parse(path):
    from modules.spectra.processing.parser import parse_spectrum_multi
    return parse_spectrum_multi(path)


@lru_cache(maxsize=1)
def _preset(name):
    from modules.spectra.io.preset_store import load_presets
    return load_presets()[name]


def fit_one(task):
    """(mode, point, index, multi, path, preset name) -> ("ok", SpectrumFile) | ("err", message).

    The same parse + `execute_auto_workflow` per spectrum as `sample_batch`,
    so a pooled run's numbers are the serial run's.
    """
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
    """A drop-in for `sample_batch.run_sample_batch` that fits in a process pool,
    putting results back in the serial order."""
    from modules.spectra.processing.parser import parse_spectrum_multi
    from modules.spectra.processing.sample_batch import SampleBatchResult

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


def run_report(scan, material, layer, magnification, out_dir, workers):
    """Build one sample's QC Report exactly as the page does and save it to `out_dir`.

    Domain size and the population split are off and low-coverage labels too,
    the page's defaults; the layer is the caller's. Returns the artifacts.
    """
    from core.io.om_calibration import get_um_per_px, load_om_calibration
    from core.report.progress import build as build_progress
    from modules.qc_report.processing.build import QcReportInputs, build_qc_report, grid_points_only, save_report
    from modules.spectra.processing.parser import count_spectra

    preset = _preset(material)
    scan, dropped = grid_points_only(scan)
    if dropped:
        print(f"  point(s) {', '.join(map(str, dropped))} left out: the QC grid is points 1-9", flush=True)
    om_paths = {p: path for p, path in scan.image_files.get(magnification, {}).items() if p >= 1}
    raman = preset if (scan.raman_files and preset.block_for("Raman")) else None
    pl = preset if (scan.pl_files and preset.block_for("PL")) else None
    fit_files = (list(scan.raman_files.values()) if raman else []) + (list(scan.pl_files.values()) if pl else [])
    progress = build_progress(
        lambda _fraction, _message: None,
        has_raman=raman is not None, has_pl=pl is not None, has_optical=bool(om_paths),
        has_segmentation=bool(om_paths), has_domain_size=False,
        fit_spectra=sum(count_spectra(path) for path in fit_files),
    )
    result = build_qc_report(
        QcReportInputs(
            scan=scan, material=material, magnification=magnification, reference_layer=layer,
            om_paths=om_paths, optical=preset.optical_for(layer) if om_paths else None,
            raman_preset=raman, pl_preset=pl,
            um_per_px=get_um_per_px(load_om_calibration(), magnification),
        ),
        progress,
        warn=lambda message: print("  warning:", message, flush=True),
        run_batch=make_parallel_batch(workers),
    )
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    save_report(result, str(out_dir / f"{scan.sample_name}_QC_Report"))
    for technique in ("raman", "pl"):
        errors = result.get(technique + "_errors") or []
        print(f"  {technique}: fitted {result.get(technique + '_fitted')}, dropped {result.get(technique + '_dropped')}, "
              f"unconverged {result.get(technique + '_unconverged')}, errors {len(errors)}, "
              f"selection {result.get(technique + '_selection')}", flush=True)
        for point, message in errors[:5]:
            print(f"    point {point}: {message}", flush=True)
    return result
