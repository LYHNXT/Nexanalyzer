"""Redraw a QC report's Raman/PL stats figures from its workbook -- no refit.

Reads <report>/<old title>_QC_Report.xlsx (sheets Raman, PL: one row per fitted peak of every spectrum the
report kept), rebuilds the fits the stats figure needs and calls NexAnalyzer's own build_peak_quality_figure,
so the figure is the app's at its current version (title, spec lines, panels). Everything else in the report
folder is copied across under the new title; only 6_Raman_stats / 8_PL_stats are redrawn.

What the workbook does not hold is rebuilt:
* `on_limit` -- centre on its range edge or Voigt FWHM on its cap -- from the preset's bounds, computed the
  way PeakDefinition.calculate_auto_bounds computes them (centre +- tolerance clipped to the x-range, width
  cap min(0.5 * range, 3 * width_fwhm)).
* the fit's residual noise -- sqrt(Chi_Squared / N), N the spectrum's points inside the preset's x-range --
  carried as a constant residual array so peak_metrics.fit_noise returns exactly that RMS.
* intensity -- carried on `area` with no component curve, which is what peak_intensity() reads then.
Together they reproduce peak_is_measured, so the ratio panels gate as the report did.

Usage: python scripts/qc_report/redraw_stats.py REPORT_DIR NEW_TITLE LAYER MATERIAL OUT_DIR
"""
import glob
import os
import shutil
import sys
from pathlib import Path

import numpy as np
import openpyxl

APP = Path(__file__).resolve().parents[2]  # the repo root: scripts/qc_report/<this file>
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(APP))
sys.path.insert(0, str(HERE))


def _bounds(preset_block, label, x_range):
    t = next(p for p in preset_block.peak_templates if p.peak_label == label)
    tol = t.center_tolerance
    c_min, c_max = max(x_range[0], t.center - tol), min(x_range[1], t.center + tol)
    w_max = min(0.5 * (x_range[1] - x_range[0]), 3.0 * t.width_fwhm)
    return c_min, c_max, w_max


def _n_points(source, sample_dir, x_range, offset=0.0):
    from modules.spectra.processing.parser import parse_spectrum_multi
    hits = [p for p in glob.glob(os.path.join(sample_dir, "**", source), recursive=True)
            if "QC_Report" not in p]
    if not hits:
        return None
    # The x-range is applied after zero-line calibration has shifted the axis by `offset`.
    x = np.asarray(parse_spectrum_multi(hits[0])[0].X, float) - offset
    return int(((x >= x_range[0]) & (x <= x_range[1])).sum())


def rebuild(rows, header, block, sample_dir):
    """(point, FitResult) pairs and Raw (point, centre, FWHM) from one sheet's rows."""
    from modules.spectra.models.peak import FitResult, FittedPeak
    col = {h: i for i, h in enumerate(header)}
    x_range = (block.x_min, block.x_max) if block.x_range_enabled else (-np.inf, np.inf)
    spectra, raw, n_cache = {}, [], {}
    for r in rows:
        point, label = int(r[col["Point"]]), r[col["Peak_Label"]]
        if label == "Raw":
            raw.append((point, float(r[col["Center"]]), float(r[col["FWHM"]])))
            continue
        spectra.setdefault((point, r[col["Spectrum"]]), []).append(r)
    pairs = []
    for (point, _), peaks in sorted(spectra.items(), key=lambda kv: (kv[0][0], kv[0][1])):
        first = peaks[0]
        source = first[col["Source_File"]]
        offset = float(first[col["Zero_Offset"]] or 0.0)
        key = (source, round(offset, 1))
        if key not in n_cache:
            n_cache[key] = _n_points(source, sample_dir, x_range, offset) if np.isfinite(x_range[0]) else None
        n = n_cache[key] or 1000
        chi2 = first[col["Chi_Squared"]] or 0.0
        fitted = []
        for r in peaks:
            label = r[col["Peak_Label"]]
            c, w = float(r[col["Center"]]), float(r[col["FWHM"]])
            try:
                c_min, c_max, w_max = _bounds(block, label, x_range)
                on_limit = (abs(c - c_min) <= 1e-6 * max(1, abs(c_min)) or abs(c - c_max) <= 1e-6 * max(1, abs(c_max))
                            or abs(w - w_max) <= 1e-6 * max(1, w_max))
            except StopIteration:
                on_limit = False
            fitted.append(FittedPeak(label=label, center=c, center_stderr=0.0, area=float(r[col["Intensity"]]),
                                     area_stderr=0.0, width_fwhm=w, width_stderr=0.0,
                                     shape=float(r[col["Shape"]] or 0.0), component_curve=np.array([]),
                                     on_limit=on_limit))
        noise = float(np.sqrt(chi2 / n))
        pairs.append((point, FitResult(success=True, fitted_peaks=fitted, total_fit_curve=np.zeros(n),
                                       residuals=np.full(n, noise), chi_squared=chi2,
                                       r_squared=float(first[col["R_Squared"]]), convergence_time=0.0,
                                       converged=bool(first[col["Converged"]]))))
    return pairs, raw


def main(report_dir, title, layer, material, out_dir):
    from modules.spectra.io.preset_store import load_presets
    from modules.spectra.processing.peak_metrics import filter_fits_by_quality
    import modules.spectra.viz.peak_quality as pq

    report_dir, out_dir = Path(report_dir), Path(out_dir)
    # A delivered report sits in <sample>/<deliver...>/<report>; the source spectra
    # that _n_points globs for are under <sample>.
    sample_dir = report_dir.parent.parent if "deliver" in report_dir.parent.name else report_dir.parent
    xlsx = next(report_dir.glob("*_QC_Report.xlsx"))
    old = xlsx.name[: -len("_QC_Report.xlsx")]
    out_dir.mkdir(parents=True, exist_ok=True)
    for f in report_dir.iterdir():
        if f.is_file():
            shutil.copy2(f, out_dir / f.name.replace(old, title, 1))

    preset = load_presets()[material]
    # Read both sheets up front and close: a read-only workbook keeps its file
    # handle open until closed, which on Windows locks the report's .xlsx.
    wb = openpyxl.load_workbook(xlsx, read_only=True)
    try:
        sheets = {name: list(wb[name].iter_rows(values_only=True))
                  for name in ("Raman", "PL") if name in wb.sheetnames}
    finally:
        wb.close()
    for sheet, spec, number in (("Raman", pq.RAMAN_QUALITY, 6), ("PL", pq.PL_QUALITY, 8)):
        block = preset.block_for(sheet)
        if sheet not in sheets or block is None:
            continue
        rows = sheets[sheet]
        if len(rows) < 2:
            continue
        pairs, raw = rebuild(rows[1:], rows[0], block, sample_dir)
        pairs = filter_fits_by_quality(pairs)
        kw = dict(sample_name=title, material_name=material, spec=spec)
        if sheet == "PL":
            kw["raw_by_point"] = raw
        png = pq.build_peak_quality_figure(pairs, layer=layer, **kw)
        name = f"{title}_QC_Report_{number}_{sheet}_stats.png"
        (out_dir / name).write_bytes(png)
        print(f"  {name}: {len(pairs)} spectra", flush=True)


if __name__ == "__main__":
    if len(sys.argv) != 6:
        sys.exit("usage: python scripts/qc_report/redraw_stats.py REPORT_DIR NEW_TITLE LAYER MATERIAL OUT_DIR")
    main(*sys.argv[1:6])
