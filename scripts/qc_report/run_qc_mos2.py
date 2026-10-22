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


def add_robust_cv_to_legends():
    """Add "Robust CV = x%" under each quality panel's "Median = y" legend line.

    Robust CV = 1.4826 * MAD / |median| * 100, over the same pooled points the median line is drawn from
    (per-position IQR-cleaned where the panel cleans them). The format matches QC_KRX261002/Robust CV.
    """
    import numpy as np
    from matplotlib.lines import Line2D
    import modules.spectra.viz.peak_quality as pq

    draw_panel = pq._draw_panel

    def draw_panel_with_cv(ax, by_point, positions, panel, is_bottom, rng, statistic, per_position_iqr):
        draw_panel(ax, by_point, positions, panel, is_bottom, rng, statistic, per_position_iqr)
        pooled = []
        for position in positions:
            values = by_point.get(position)
            if values is None or values.size == 0:
                continue
            pooled.extend((values[pq.iqr_mask(values)] if per_position_iqr else values).tolist())
        legend = ax.get_legend()
        if not pooled or legend is None:
            return
        pooled = np.asarray(pooled, float)
        median = np.median(pooled)
        cv = 1.4826 * np.median(np.abs(pooled - median)) / abs(median) * 100 if median else float("nan")
        handles, labels = ax.get_legend_handles_labels()
        handles.append(Line2D([], [], linestyle="none"))
        labels.append(f"Robust CV = {cv:.2f}%" if cv < 10 else f"Robust CV = {cv:.1f}%")
        ax.legend(handles, labels, loc=legend._loc, fontsize=legend.get_texts()[0].get_fontsize(),
                  framealpha=0.85)

    pq._draw_panel = draw_panel_with_cv


def add_raw_column_to_pl_figure(last_batch):
    """PL quality figure: "Raw Spectrum" column (Raw FWHM, Raw center) first, then the fitted peaks' FWHM and
    Peak Centers columns, no ratio column -- the layout of IMEC-Nov/pl_stats_with_raw.py for WSe2.

    Raw is the workbook's Raw row (peak_metrics.raw_peak_stats on the processed spectrum), for every spectrum;
    fitted peaks are added only where the fit passed the page's R^2 gate, exactly the pairs the page passes.
    The PL cleaning rule applies to Raw too (it removes cosmic-ray spikes read as a ~0-1 nm "peak"), but Raw is
    cleaned apart from the fitted peaks, so the fitted panels are point for point the standard figure's (asserted).
    """
    import copy
    import dataclasses

    import numpy as np
    import modules.spectra.viz.peak_quality as pq
    from modules.spectra.processing.peak_metrics import raw_peak_stats

    build, clean = pq.build_peak_quality_figure, pq._clean
    raw_column = ("Raw Spectrum", (
        pq.PeakPanel("Raw", "fwhm", "FWHM (nm)", "Raw FWHM"),
        pq.PeakPanel("Raw", "center", "Wavelength (nm)", "Raw center"),
    ))

    def build_with_raw(fits_by_point, sample_name, material_name="WSe₂", spec=pq.RAMAN_QUALITY, dpi=REPORT_DPI):
        if spec is not pq.PL_QUALITY or not last_batch:
            return build(fits_by_point, sample_name=sample_name, material_name=material_name, spec=spec, dpi=dpi)
        passed = {id(fit) for _, fit in fits_by_point}
        template_peak = next(p for _, fit in fits_by_point for p in fit.fitted_peaks)
        template_fit = fits_by_point[0][1]

        with_raw = []
        # From v5.28.0 the page draws a point of more than 25 spectra down to 25 after fitting; last_batch holds
        # all of them, so at such a point Raw keeps only the spectra the page kept (the fits it passed here).
        per_point = {}
        for point, _ in last_batch[0].pl_spectra:
            per_point[point] = per_point.get(point, 0) + 1
        for point, spectrum in last_batch[0].pl_spectra:
            fit = spectrum.fit_result
            if per_point[point] > 25 and (fit is None or id(fit) not in passed):
                continue
            peaks = list(fit.fitted_peaks) if fit is not None and id(fit) in passed else []
            raw = raw_peak_stats(spectrum.processed_data.X, spectrum.processed_data.Y)
            if raw is not None and raw.fwhm is not None:
                # No component curve, so peak_intensity() reads `area`: area carries the raw intensity.
                raw_peak = copy.copy(template_peak)
                raw_peak.label, raw_peak.center, raw_peak.width_fwhm = "Raw", float(raw.center), float(raw.fwhm)
                raw_peak.area, raw_peak.center_stderr, raw_peak.area_stderr = float(raw.intensity), 0.0, 0.0
                raw_peak.width_stderr, raw_peak.component_curve = 0.0, np.array([])
                peaks = [raw_peak] + peaks
            if peaks:
                with_raw.append((point, dataclasses.replace(
                    fit if fit is not None else template_fit, fitted_peaks=peaks,
                    r_squared=fit.r_squared if peaks[-1].label != "Raw" else 1.0)))

        labels = [p.label for _, fit in fits_by_point for p in fit.fitted_peaks]
        fit_columns = tuple(c for c in spec.columns_for(labels) if c[0] != "Diagnostic Ratios")
        raw_spec = dataclasses.replace(spec, columns=(raw_column,) + fit_columns)
        # Raw is cleaned on its own: in the 2 % floor it would become each spectrum's "strongest peak" (the raw
        # maximum is taller than any one fitted component) and drop weak B/A0 fits the standard figure keeps.
        def clean_raw_apart(records, rule):
            return (clean([r for r in records if r.label == "Raw"], rule)
                    + clean([r for r in records if r.label != "Raw"], rule))

        kept = [r for r in clean_raw_apart(pq._records(with_raw), pq.PL_CLEANING) if r.label == "Raw"]
        fitted = sorted((r.point, r.label, r.center, r.fwhm) for r in clean_raw_apart(pq._records(with_raw), spec.cleaning)
                        if r.label != "Raw")
        assert fitted == sorted((r.point, r.label, r.center, r.fwhm)
                                for r in clean(pq._records(fits_by_point), spec.cleaning))
        print(f"  PL Raw: {len(kept)} of {len(with_raw)} spectra after cleaning, centre median "
              f"{np.median([r.center for r in kept]):.2f} nm, FWHM median {np.median([r.fwhm for r in kept]):.2f} nm")
        pq._clean = clean_raw_apart
        try:
            return build(with_raw, sample_name=sample_name, material_name=material_name, spec=raw_spec, dpi=dpi)
        finally:
            pq._clean = clean

    pq.build_peak_quality_figure = build_with_raw


DELTA_LABEL = "Δω (A1g − E2g)"
DEFECT_PAIR = ("2LA(M)", "A1g")


def _delta_omega(fits):
    """A1g centre − E2g centre, per spectrum that has both."""
    out = []
    for fit in fits:
        centers = {p.label: p.center for p in fit.fitted_peaks}
        if "A1g" in centers and "E2g" in centers:
            out.append(centers["A1g"] - centers["E2g"])
    return out


def align_mos2_raman_with_wse2(last_batch):
    """Lay MoS2's Raman out the way the WSe2 QC reports do (IMEC-Nov, QC_KRX2610xx):

    * Raman stats figure: FWHM (E2g, A1g) | Center (E2g, A1g) | Δω -- panels 2, 2, 1. The 2LA(M) FWHM panel,
      the defect-ratio panel and the summary table were removed on 2026-10-19.
    * Summary page and workbook: the defect ratio I(2LA(M))/I(A1g) as a ratio row (median ± MAD), in place of
      WSe2's LA/E2g+A1g -- by giving the page this pair as its RAMAN_RATIO_PAIRS. The summary page also gets a
      Δω row; the workbook's ratio table is headed "intensity ratios", so Δω is left out of it.

    No spec lines: none has been measured for MoS2 ("—" in the Spec column).
    """
    import numpy as np
    import matplotlib.pyplot as plt
    from matplotlib import gridspec
    import core.report.summary_figure as sf
    import modules.spectra.processing.peak_metrics as pm
    import modules.spectra.viz.peak_quality as pq

    pm.RAMAN_RATIO_PAIRS = (DEFECT_PAIR,)

    build_summary = sf.build_summary_figure

    def build_summary_with_delta(*args, **kw):
        if last_batch:
            fits = [fit for _, fit in pm.filter_fits_by_quality(
                [(p, s.fit_result) for p, s in last_batch[0].raman_spectra])]
            values = np.asarray(_delta_omega(fits), float)
            if values.size:
                median = float(np.median(values))
                kw["raman_ratios"] = list(kw.get("raman_ratios") or []) + [
                    (DELTA_LABEL, (median, float(np.median(np.abs(values - median))), int(values.size)))]
        return build_summary(*args, **kw)

    sf.build_summary_figure = build_summary_with_delta

    build = pq.build_peak_quality_figure
    delta = pq.PeakPanel(DELTA_LABEL, "center", "Δω (cm⁻¹)", "Δω (A1g − E2g)")

    def build_mos2_raman(fits_by_point, sample_name, material_name="WSe₂", spec=pq.RAMAN_QUALITY, dpi=REPORT_DPI, **kw):
        labels = {p.label for _, fit in fits_by_point for p in fit.fitted_peaks}
        if spec is not pq.RAMAN_QUALITY or not {"E2g", "A1g"} <= labels:
            return build(fits_by_point, sample_name=sample_name, material_name=material_name, spec=spec, dpi=dpi, **kw)

        records = pq._clean(pq._records(fits_by_point), spec.cleaning)
        positions = sorted({point for point, _ in fits_by_point})
        iqr = spec.cleaning.per_position_iqr
        layer = kw.get("layer")
        # The customer spec (quote 20261009W001 §6.4, 6.7): |E2g - A1g| <= 20 cm-1 on 1L,
        # stated under the title by the app's own spec_summary_lines, and drawn as a red dashed line.
        summary = pq.spec_summary_lines(records, "Raman", layer, iqr) if hasattr(pq, "spec_summary_lines") else []
        delta_panel = (pq.dataclasses.replace(delta, spec=pq.MOS2_SPEC["Raman"]["delta_max"], spec_nominal=True)
                       if layer == "1L" and hasattr(pq, "MOS2_SPEC") else delta)

        by_spectrum = {}
        for r in records:
            by_spectrum.setdefault(r.spectrum, (r.point, {}))[1][r.label] = r.center
        delta_values = {}
        for point, centers in by_spectrum.values():
            if "A1g" in centers and "E2g" in centers:
                delta_values.setdefault(point, []).append(centers["A1g"] - centers["E2g"])
        delta_values = {p: np.asarray(v, float) for p, v in delta_values.items()}

        fwhm = [pq.PeakPanel(lab, "fwhm", "FWHM (cm⁻¹)", lab) for lab in ("E2g", "A1g")]
        center = [pq.PeakPanel(lab, "center", "Raman shift (cm⁻¹)", f"{lab} center") for lab in ("E2g", "A1g")]
        values = {panel: pq._peak_values(records, panel) for panel in fwhm + center}
        values[delta_panel] = delta_values

        # 2 | 2 | 1 panels: no 2LA(M) FWHM, no defect-ratio panel, no summary table (2026-10-19).
        n_rows = 2
        fig = plt.figure(figsize=(pq._COLUMN_W_IN * 3, pq._ROW_H_IN * n_rows), dpi=dpi)
        try:
            grid = gridspec.GridSpec(n_rows, 3, figure=fig, hspace=0.0, wspace=0.30,
                                     top=0.85 if summary else None)
            columns = (("FWHM", fwhm), ("Center", center), ("Δω", [delta_panel]))
            for column, (title, panels) in enumerate(columns):
                top = None
                for row, panel in enumerate(panels):
                    ax = fig.add_subplot(grid[row, column], sharex=top)
                    if top is None:
                        top = ax
                        ax.set_title(title, fontsize=pq._FS_TITLE, fontweight="bold", pad=10)
                    pq._draw_panel(ax, values[panel], positions, panel, is_bottom=(row == len(panels) - 1),
                                   rng=np.random.default_rng(42 + column * n_rows + row),
                                   statistic=spec.statistic, per_position_iqr=iqr)

            per_position = len(fits_by_point) / len(positions) if positions else 0
            marker = "median ± std" if spec.statistic == "median" else "mean ± std"
            fig.suptitle(f"{sample_name}  {material_name} — {spec.technique} quality analysis "
                         f"({len(positions)} positions × {per_position:.0f} spectra, {marker} per position)",
                         fontsize=pq._FS_SUPTITLE, fontweight="bold", y=1.0)
            if summary:
                fig.text(0.5, 0.962, chr(10).join(summary), ha="center", va="top",
                         fontsize=pq._FS_TITLE, color="#222222", linespacing=1.5)
            buf = __import__("io").BytesIO()
            fig.savefig(buf, format="png", bbox_inches="tight", facecolor="white")
            return buf.getvalue()
        finally:
            plt.close(fig)

    pq.build_peak_quality_figure = build_mos2_raman


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("samples", nargs="+", help="sample folders (absolute, or relative to the working directory)")
    ap.add_argument("--workers", type=int, default=max(1, (os.cpu_count() or 2) - 2))
    ap.add_argument("--material", default=MATERIAL, help="preset name (default %(default)s)")
    ap.add_argument("--name", default=None, help="report title / sample name (one sample; default: the folder name)")
    ap.add_argument("--suffix", default="", help="appended to the QC_Report_<date> folder name")
    args = ap.parse_args()

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
    # Robust CV is drawn by the app itself from v5.28.0; add_robust_cv_to_legends() would repeat it.
    import modules.spectra.viz.peak_quality as pq
    if not hasattr(pq, "robust_cv"):
        add_robust_cv_to_legends()
    # From NexAnalyzer v5.29.0 the app draws the Raw PL column itself; adding ours would draw it twice.
    if not hasattr(pq, "RAW_COLUMN"):
        add_raw_column_to_pl_figure(last_batch)
    align_mos2_raman_with_wse2(last_batch)

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

    samples = [Path(x).resolve() for x in args.samples]
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
