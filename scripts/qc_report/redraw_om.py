"""Redraw a QC report's OM figures (2_OM, 3_OM_diagnostic) under a new title -- no spectral fit.

Re-segments the sample folder's OM frames exactly as the QC Report page does (analyse_frame with the
material preset's optical parameters for the reference layer, low-coverage mode off -- the page's
default) and renders both figures with build_om_grid_figure, for grid points 1-9. Segmentation is
~1 s per frame; nothing else in the report is touched.

Usage: python scripts/qc_report/redraw_om.py SAMPLE_DIR NEW_TITLE LAYER MATERIAL MAGNIFICATION OUT_DIR
       e.g. ... KRX261006_VM2610020_MoS2 KRX261006_MoS2 1L MoS2_QC 50X .../QC_Report_2026-10-22_v5.32
"""
import sys
from pathlib import Path

APP = Path(__file__).resolve().parents[2]  # the repo root: scripts/qc_report/<this file>
sys.path.insert(0, str(APP))


def main(sample_dir, title, layer, material, magnification, out_dir):
    from modules.optical.processing.contrast import analyse_frame
    from modules.optical.viz.om_grid import build_om_grid_figure
    from modules.qc_report.processing.build import grid_points_only
    from modules.spectra.io.preset_store import load_presets
    from modules.spectra.processing.sample_scanner import scan_sample_folder

    scan = scan_sample_folder(sample_dir)
    # Grid points 1..GRID_POINTS only, as the report (v5.29.0): a wafer measured at a
    # 10th position (HADH71/75) has it left out of the OM figure too.
    scan, _dropped = grid_points_only(scan)
    om_paths = dict(scan.image_files.get(magnification, {}))
    if not om_paths:
        sys.exit(f"No {magnification} OM frames at points 1-9 in {sample_dir}")
    optical = load_presets()[material].optical.get(layer)
    kwargs = optical.as_kwargs() if optical is not None else {}
    frames = [analyse_frame(om_paths[p], point=p, ref_label=layer, name=f"{magnification}-{p}",
                            low_coverage=False, **kwargs) for p in sorted(om_paths)]
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    for number, name, histograms in ((2, "OM", False), (3, "OM_diagnostic", True)):
        png = build_om_grid_figure(frames, sample_name=title, magnification=magnification,
                                   show_histograms=histograms)
        (out / f"{title}_QC_Report_{number}_{name}.png").write_bytes(png)
        print(f"  {title}_QC_Report_{number}_{name}.png: {len(frames)} frames", flush=True)


if __name__ == "__main__":
    if len(sys.argv) != 7:
        sys.exit(__doc__.split("Usage: ")[1].split("\n")[0])
    main(*sys.argv[1:7])
