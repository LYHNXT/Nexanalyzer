"""
Build a QC Report from a scanned sample folder -- the pipeline behind the QC
Report page's "Generate" button, outside Streamlit.

Until v5.35.0 these ~480 lines lived inside `pages/2_QC_Report.py`, so the
only way to run them was to drive the page: the batch runners in
`scripts/qc_report/` did it through `AppTest`, faking the save dialog and
clicking buttons by label, and nothing below could be unit-tested. The page
now builds a `QcReportInputs`, calls `build_qc_report`, and stores what comes
back; a script can call it directly.

`build_qc_report` returns the derived artifacts under the same keys the page's
session state uses (`modules.optical.ui.qc_report_state`), so the page stores
the result with one `state.update(...)`. It reports through two callables:
`progress` (a `core.report.progress` tracker) and `warn` (one line per problem
that drops a frame or a point but not the report -- the page shows them with
`st.warning`). Fingerprints are the page's concern and are not set here.

No number differs from the page's own pipeline: the code moved, it did not
change. The golden fit and the QC Report flow tests are what show it.
"""

import dataclasses
import io
from dataclasses import dataclass, field
from datetime import date
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from PIL import Image

from core.io.export import export_figure_png
from core.report.summary_figure import (
    FIT_COLUMN_ASPECT_RATIO,
    FIT_GRID_COLUMNS,
    build_fit_grid_figure,
    build_summary_figure,
)
from modules.optical.io.frame_tables import frame_class_stats
from modules.optical.processing.contrast import analyse_frame, class_labels
from modules.optical.processing.domain_size import TARGETS, domain_size_stat, measure_domains
from modules.optical.viz.domain_grid import build_domain_grid_figure
from modules.optical.viz.om_grid import build_om_grid_figure
from modules.spectra.io.results_excel import TechniqueResults, build_sample_results_xlsx
from modules.spectra.processing.peak_metrics import (
    NEAR_ZERO_FRACTION,
    RAMAN_RATIO_PAIRS,
    aggregate_fit_results,
    aggregate_raw_peak_stats,
    compute_peak_intensity_ratio,
    filter_fits_by_quality,
    raw_peak_stats,
)
from modules.spectra.processing.sample_batch import SampleBatchResult, run_sample_batch
from modules.spectra.processing.spectrum_selection import select_spectra_per_point
from modules.spectra.viz.fit_plot import (
    axis_label,
    fit_legend_entries,
    peak_normalization_scale,
    plot_fit_column,
    shared_axis_ranges,
    y_axis_title,
)
from modules.spectra.viz.peak_quality import (
    DELTA_LABEL,
    PL_QUALITY,
    RAMAN_QUALITY,
    build_peak_quality_figure,
    delta_omega_summary,
    is_mos2,
)

#: The QC grid is 3 x 3 (v5.29.0). A folder measured at more positions (HADH71
#: and HADH75 carry a 10th) keeps points 1-9; the rest are left out of every
#: figure, table and the workbook, and the page says which.
GRID_POINTS = 9

#: The saved file set, in the order the report is meant to be read:
#: (number, file suffix, artifact key). The numeric prefix is load-bearing:
#: alphabetical order in a file browser puts "Summary" last and interleaves the
#: two techniques, so a colleague handed the folder would meet the figures in an
#: order nobody chose. `onenote.processing.artifacts._QC_FIGURES` follows it.
REPORT_FIGURES = (
    (1, "Summary", "summary_png"),
    (2, "OM", "om_png"),
    (3, "OM_diagnostic", "om_diagnostic_png"),
    (4, "Domain_size", "domain_grid_png"),
    (5, "Raman", "raman_grid_png"),
    (6, "Raman_stats", "raman_stats_png"),
    (7, "PL", "pl_grid_png"),
    (8, "PL_stats", "pl_stats_png"),
)

#: CLAUDE.md: a fixed-contrast threshold is meaningful only when it clears about
#: twice the frame's own noise (sigma_noise / mode); below that the cut sits
#: inside the reference distribution and the class percentages are noise.
THRESHOLD_NOISE_RATIO = 2.0


def grid_points_only(full):
    """`full` (a `ScanResult`) with its spectra and frames limited to points
    1..GRID_POINTS, and the sorted list of points dropped. None gives (None, [])."""
    if full is None:
        return None, []
    keep = lambda d: {p: v for p, v in d.items() if 1 <= p <= GRID_POINTS}  # noqa: E731
    dropped = sorted({p for d in (full.raman_files, full.pl_files, *full.image_files.values())
                      for p in d if not 1 <= p <= GRID_POINTS})
    if not dropped:
        return full, []
    return dataclasses.replace(full, raman_files=keep(full.raman_files), pl_files=keep(full.pl_files),
                               image_files={m: keep(d) for m, d in full.image_files.items()}), dropped


def threshold_noise_warning(frames, om_kwargs) -> Optional[str]:
    """The frames whose fixed threshold is inside their own noise (v5.23.0), as
    one sentence, or None when every frame clears it (or no pair is set)."""
    pair = om_kwargs.get("abs_threshold")
    if pair is None or not frames:
        return None
    below, above = pair if isinstance(pair, (tuple, list)) else (pair, pair)
    bad = []
    for f in frames:
        noise_pct = 100.0 * f.sigma_noise / f.mode if f.mode else float("inf")
        ratio = min(below, above) / noise_pct if noise_pct > 0 else float("inf")
        if ratio < THRESHOLD_NOISE_RATIO:
            bad.append(f"P{f.point} ({ratio:.1f}x)")
    if not bad:
        return None
    return (
        f"**{len(bad)} frame(s) are too noisy for the fixed threshold:** "
        + ", ".join(bad)
        + f". The threshold should clear about {THRESHOLD_NOISE_RATIO:.0f}x the frame's "
        "noise; below that the class percentages for these frames are noise, not film."
    )


@dataclass
class QcReportInputs:
    """Everything one report is built from -- the page's selections, resolved.

    `raman_preset` / `pl_preset` are the material preset when it has that
    technique's block and the folder has its spectra, else None (that technique
    is skipped). `optical` is the preset's `OpticalParams` for the layer, or
    None: no segmentation then, only the raw frames on the summary page.
    `om_paths` maps grid point to frame path for the chosen magnification.
    """

    scan: object
    material: str
    magnification: Optional[str]
    reference_layer: str
    om_paths: Dict[int, str] = field(default_factory=dict)
    optical: object = None
    raman_preset: object = None
    pl_preset: object = None
    low_coverage: bool = False
    measure_domain_size: bool = False
    domain_target: str = "above"
    split_size_populations: bool = False
    um_per_px: Optional[float] = None
    report_date: Optional[str] = None


def _no_warn(_message: str) -> None:
    pass


def build_qc_report(inputs: QcReportInputs, progress, warn: Callable[[str], None] = _no_warn,
                    run_batch: Callable = run_sample_batch) -> dict:
    """Run the whole report and return its artifacts, keyed as the page's state is.

    `progress` is a `core.report.progress` tracker built for the same stages
    (`build_progress(..., has_raman=..., has_pl=..., has_optical=...,
    has_segmentation=..., has_domain_size=...)`); `warn` gets one sentence per
    frame or point that drops out without failing the report. `run_batch`
    replaces `sample_batch.run_sample_batch` (same signature and result) -- the
    batch runners pass a process-pool version that keeps the serial order.
    """
    scan = inputs.scan
    om_paths = inputs.om_paths
    will_fit_raman = inputs.raman_preset is not None
    will_fit_pl = inputs.pl_preset is not None
    has_segmentation = bool(om_paths) and inputs.optical is not None
    report_date = inputs.report_date or date.today().isoformat()
    domain_class = dict(zip(
        TARGETS, class_labels(inputs.reference_layer, inputs.low_coverage)
    ))[inputs.domain_target]

    out: dict = {"report_date": report_date, "frames": None, "optical_threshold_pair": None,
                 "domain_stat": None}

    # ---------------------------------------------------------- OM frames
    # Per image, not per batch: these are ~0.6 s each for a real 2240x1680
    # microscope frame, and if the folder lives on OneDrive a cloud-only file
    # has to be downloaded first, which no estimate can predict. Ticking keeps
    # the bar moving through it either way.
    om_png_bytes = {}
    if om_paths:
        progress.start("optical_images", detail=f"0/{len(om_paths)}")
        for done, (point, path) in enumerate(sorted(om_paths.items()), start=1):
            try:
                with Image.open(path) as im:
                    buf = io.BytesIO()
                    im.convert("RGB").save(buf, format="PNG")
                om_png_bytes[point] = buf.getvalue()
            except Exception as e:
                warn(f"Could not load image for point {point}: {e}")
            progress.tick("optical_images", done, len(om_paths))

    # ------------------------------------------------------- Segmentation
    if has_segmentation:
        optical = inputs.optical
        # Only the fields the preset actually sets; everything else keeps
        # contrast.py's default.
        om_kwargs = optical.as_kwargs()
        # One ruler for every wafer. Recorded so the summary page and the
        # workbook can both name the cut their numbers came from.
        out["optical_threshold_pair"] = om_kwargs.get("abs_threshold")

        progress.start("optical_segmentation", detail=f"0/{len(om_paths)}")
        frames = []
        for done, point in enumerate(sorted(om_paths), start=1):
            # Per frame: one corrupt, truncated, locked or cloud-only image
            # used to raise through every later stage and discard the whole
            # run -- including the Raman and PL halves, which have nothing to
            # do with it -- so a bad frame drops out and the rest reports.
            try:
                frames.append(analyse_frame(
                    om_paths[point], point=point,
                    ref_label=inputs.reference_layer,
                    name=f"{inputs.magnification}-{point}",
                    low_coverage=inputs.low_coverage,
                    keep_segmentation=bool(inputs.measure_domain_size),
                    **om_kwargs,
                ))
            except Exception as e:
                warn(f"Could not segment point {point}: {e}")
            progress.tick("optical_segmentation", done, len(om_paths))
        out["frames"] = frames
        message = threshold_noise_warning(frames, om_kwargs)
        if message:
            warn(message)

        # `build_om_grid_figure` raises on an empty list, and a figure of nine
        # blanks would claim a segmentation that never happened.
        if frames:
            # Two renders off one segmentation; each is about 5 s.
            progress.start("om_figures", detail="clean copy")
            out["om_png"] = build_om_grid_figure(
                frames, sample_name=scan.sample_name,
                magnification=inputs.magnification, show_histograms=False,
            )
            progress.tick("om_figures", 1, 2, detail="diagnostic copy")
            out["om_diagnostic_png"] = build_om_grid_figure(
                frames, sample_name=scan.sample_name,
                magnification=inputs.magnification, show_histograms=True,
            )
        else:
            warn("No optical frame could be segmented — the OM figures and sheets are omitted.")
        progress.complete("om_figures")

        # ---------------------------------------------------- Domain size
        # Same `om_kwargs` the segmentation used: the mask this re-derives is
        # the population the OM overlay already shows, and measuring it with
        # different tuning would make the two figures disagree.
        if frames and inputs.measure_domain_size:
            progress.start("domain_size", detail=f"0/{len(frames)}")
            domain_results = []
            for done, frame in enumerate(frames, start=1):
                try:
                    domain_results.append(measure_domains(
                        frame, um_per_px=inputs.um_per_px,
                        target=inputs.domain_target,
                        marker_margin_sigma=optical.marker_margin_sigma,
                        marker_min_distance=optical.marker_min_distance,
                        mask_closing_radius=optical.mask_closing_radius,
                        detect_size_populations=inputs.split_size_populations,
                        min_domain_um=optical.min_domain_um_for(inputs.magnification),
                        **om_kwargs,
                    ))
                except Exception as e:
                    warn(f"Could not measure domains for point {frame.point}: {e}")
                progress.tick("domain_size", done, len(frames))
            out["domain_results"] = domain_results
            # Domain sizing reused each frame's segmentation; drop it so the
            # session does not hold ~16 MB a frame.
            frames = [dataclasses.replace(f, segmentation=None) for f in frames]
            out["frames"] = frames
            out["domain_stat"] = domain_size_stat(
                domain_results, detect_size_populations=inputs.split_size_populations,
            )
            if domain_results:
                out["domain_grid_png"] = build_domain_grid_figure(
                    frames, domain_results,
                    sample_name=scan.sample_name,
                    magnification=inputs.magnification,
                    class_label=domain_class,
                    detect_size_populations=bool(inputs.split_size_populations),
                )
            progress.complete("domain_size")

    # ------------------------------------------------------------ Fitting
    has_spectra = will_fit_raman or will_fit_pl
    if has_spectra:
        progress.start("fit")
    batch_result = run_batch(
        scan,
        inputs.raman_preset,
        inputs.pl_preset,
        progress_callback=progress.sub_callback(
            "fit",
            # Both techniques share the one fitting stage, and each counts
            # from 1 again, so the stage needs their combined total or the bar
            # stalls through the second.
            totals={
                "Raman": len(scan.raman_files) if will_fit_raman else 0,
                "PL": len(scan.pl_files) if will_fit_pl else 0,
            },
        ) if has_spectra else None,
    )
    if has_spectra:
        progress.complete("fit")
    # A point measured as a map of more than SPECTRA_PER_POINT spectra loses
    # its outliers and is then drawn down to SPECTRA_PER_POINT (v5.28.0).
    # Applied here, once, so every surface below describes the same spectra.
    raman_kept, out["raman_selection"] = select_spectra_per_point(batch_result.raman_spectra)
    pl_kept, out["pl_selection"] = select_spectra_per_point(batch_result.pl_spectra)
    batch_result = SampleBatchResult(
        raman_spectra=raman_kept, pl_spectra=pl_kept,
        raman_errors=batch_result.raman_errors, pl_errors=batch_result.pl_errors,
    )
    out["batch_result"] = batch_result

    # One quality gate, applied once, feeding every summary below -- stats,
    # ratios, quality figures and the .xlsx -- so no two surfaces work from a
    # different set of fits. Pairs, not bare fits: outlier removal is per point.
    raman_pairs = filter_fits_by_quality(
        [(point, s.fit_result) for point, s in batch_result.raman_spectra])
    pl_pairs = filter_fits_by_quality(
        [(point, s.fit_result) for point, s in batch_result.pl_spectra])

    out["raman_errors"] = batch_result.raman_errors
    out["pl_errors"] = batch_result.pl_errors
    # Both counts, not just the survivors: what the gate removed is reported
    # and cannot be recovered from the stats.
    out["raman_fitted"] = len(batch_result.raman_spectra)
    out["raman_dropped"] = len(batch_result.raman_spectra) - len(raman_pairs)
    out["pl_fitted"] = len(batch_result.pl_spectra)
    out["pl_dropped"] = len(batch_result.pl_spectra) - len(pl_pairs)
    # Fits that stopped on max_iterations are kept but counted; see
    # FitResult.converged.
    out["raman_unconverged"] = sum(1 for _, s in batch_result.raman_spectra if not s.fit_result.converged)
    out["pl_unconverged"] = sum(1 for _, s in batch_result.pl_spectra if not s.fit_result.converged)

    def grid(point_spectra, technique, stage_key):
        return build_fit_grid_figure(
            sample_name=scan.sample_name,
            material_name=inputs.material,
            report_date=report_date,
            technique=technique,
            column_images=_fit_columns(point_spectra, technique, stage_key, progress),
            legend=fit_legend_entries([s.fit_result for _, s in point_spectra]),
            x_label=axis_label(technique),
            y_label=y_axis_title(normalized=True),
        )

    if batch_result.raman_spectra:
        out["raman_grid_png"] = grid(batch_result.raman_spectra, "Raman", "raman_figures")
    if raman_pairs:
        out["raman_stats"] = aggregate_fit_results(raman_pairs)
        out["raman_stats_png"] = build_peak_quality_figure(
            raman_pairs, sample_name=scan.sample_name,
            material_name=inputs.material, spec=RAMAN_QUALITY,
            layer=inputs.reference_layer,
        )
        out["raman_ratios"] = [
            (f"{numerator} / {denominator}", ratio)
            for numerator, denominator in RAMAN_RATIO_PAIRS
            for ratio in [compute_peak_intensity_ratio(raman_pairs, numerator, denominator)]
            if ratio is not None
        ]
        # MoS2's Δω: a summary-page row, not a workbook one -- the workbook's
        # table is headed "intensity ratios".
        out["raman_delta"] = (
            delta_omega_summary(raman_pairs)
            if is_mos2([p.label for _, f in raman_pairs for p in f.fitted_peaks], "Raman")
            else None
        )

    if batch_result.pl_spectra:
        out["pl_grid_png"] = grid(batch_result.pl_spectra, "PL", "pl_figures")
        # PL leads with the empirical measurement -- each processed spectrum's
        # tallest point, no fit involved -- then the fitted peaks, with the PL
        # quality figure's near-zero rule.
        pl_stats = aggregate_fit_results(
            pl_pairs, relative_intensity_floor=NEAR_ZERO_FRACTION) if pl_pairs else []
        raw_stat = aggregate_raw_peak_stats([s for _, s in batch_result.pl_spectra])
        out["pl_stats"] = ([raw_stat] + pl_stats) if raw_stat else pl_stats
    if pl_pairs:
        # The Raw column: each spectrum's own maximum and half-height width.
        pl_raw = [
            (point, r.center, r.fwhm)
            for point, s in batch_result.pl_spectra
            for r in [raw_peak_stats(s.processed_data.X, s.processed_data.Y)]
            if r is not None
        ]
        out["pl_stats_png"] = build_peak_quality_figure(
            pl_pairs, sample_name=scan.sample_name,
            material_name=inputs.material, spec=PL_QUALITY,
            raw_by_point=pl_raw, layer=inputs.reference_layer,
        )

    # ------------------------------------------------- Summary + workbook
    frames = out["frames"]
    threshold_pair = out["optical_threshold_pair"]
    progress.start("compose", detail="summary page")
    out["summary_png"] = build_summary_figure(
        sample_name=scan.sample_name,
        material_name=inputs.material,
        report_date=report_date,
        magnification_label=inputs.magnification,
        om_image_bytes=om_png_bytes,
        om_classes=frame_class_stats(frames or []),
        om_threshold_note=(
            "preset contrast pair -{:.2f} / +{:.2f} %".format(*threshold_pair)
            if threshold_pair
            else ("preset nsigma threshold" if frames else None)
        ),
        raman_stats=out.get("raman_stats"),
        pl_stats=out.get("pl_stats"),
        raman_ratios=(out.get("raman_ratios") or []) + (
            [(DELTA_LABEL, out["raman_delta"])] if out.get("raman_delta") else []
        ),
        domain_stat=out["domain_stat"],
        domain_class_label=domain_class if inputs.measure_domain_size else None,
    )

    # Built from the very stats the summary page was built from, and the very
    # frames the OM figures were drawn from, so the workbook cannot disagree
    # with the images beside it.
    progress.tick("compose", 1, 2, detail="workbook")
    techniques = []
    if batch_result.raman_spectra:
        techniques.append(TechniqueResults(
            label="Raman", point_spectra=batch_result.raman_spectra,
            stats=out.get("raman_stats"), source_files=scan.raman_files,
            errors=batch_result.raman_errors, ratios=out.get("raman_ratios") or (),
        ))
    if batch_result.pl_spectra:
        techniques.append(TechniqueResults(
            label="PL", point_spectra=batch_result.pl_spectra,
            stats=out.get("pl_stats"), source_files=scan.pl_files,
            errors=batch_result.pl_errors,
            # The empirical row, per point, matching the summary page's PL table.
            include_raw_row=True,
        ))
    if techniques or frames:
        out["xlsx_bytes"] = build_sample_results_xlsx(
            sample_name=scan.sample_name,
            material_name=inputs.material,
            report_date=report_date,
            techniques=techniques,
            optical_frames=frames or (),
            optical_threshold_pair=threshold_pair,
            domain_stat=out["domain_stat"],
            domain_class_label=domain_class if out["domain_stat"] else None,
        )
    progress.complete("compose")
    return out


def save_report(artifacts, base: str) -> List[str]:
    """Write a built report's figures and workbook next to `base`; return the paths.

    One name, many files: `base` (a path without extension) supplies the stem
    and each figure appends its own number and suffix, so the set stays together
    and none can overwrite another -- `<base>_1_Summary.png` ... and
    `<base>.xlsx`. Figures the report does not have are skipped.
    """
    saved = []
    for number, suffix, key in REPORT_FIGURES:
        payload = artifacts.get(key)
        if not payload:
            continue
        target = f"{base}_{number}_{suffix}.png"
        with open(target, "wb") as f:
            f.write(payload)
        saved.append(target)
    if artifacts.get("xlsx_bytes"):
        target = f"{base}.xlsx"
        with open(target, "wb") as f:
            f.write(artifacts["xlsx_bytes"])
        saved.append(target)
    return saved


def _fit_columns(point_spectra, mode: str, stage_key: str, progress) -> Dict[int, bytes]:
    """One image per grid column, each holding that column's three points on a
    single shared X-axis: column 0 is points 1/4/7, column 1 is 2/5/8, column 2
    is 3/6/9.

    Same traces as the Spectra page's fit results (data + total fit + peak
    components), minus the residuals strip, rendered at the report page's
    column aspect ratio. Reports per column: each is one kaleido rasterization
    costing ~1.3 s.
    """
    column_width_px = 1000
    column_height_px = round(column_width_px / FIT_COLUMN_ASPECT_RATIO)

    # One panel per grid point, but a multi-spectrum file contributes many fits
    # at the same point: show each point's best-fitting spectrum, not whichever
    # was parsed last.
    def quality(spectrum):
        return spectrum.fit_result.r_squared if spectrum.fit_result else -1.0

    by_point = {}
    for point, spectrum in point_spectra:
        best = by_point.get(point)
        if best is None or quality(spectrum) > quality(best):
            by_point[point] = spectrum

    # Ranges from the normalized series, because that is what gets drawn --
    # every point divided by its own tallest peak, so each panel's peak is 1.
    normalized = []
    for s in by_point.values():
        scale = peak_normalization_scale(s.processed_data.Y, s.fit_result)
        curve = s.fit_result.total_fit_curve if s.fit_result else None
        normalized.append((
            s.processed_data.X,
            s.processed_data.Y / scale,
            None if curve is None else curve / scale,
        ))
    x_range, y_range = shared_axis_ranges(normalized)

    images = {}
    progress.start(stage_key, detail=f"0/{FIT_GRID_COLUMNS}")
    for col in range(FIT_GRID_COLUMNS):
        # A missing point keeps its row as an empty slot, so point 7 never
        # moves up beside points 4 and 6.
        column_points: List[Tuple] = [
            (point, by_point[point].processed_data.X, by_point[point].processed_data.Y,
             by_point[point].fit_result) if point in by_point else (point, None, None, None)
            for point in (col + 1, col + 4, col + 7)
        ]
        if all(x is None for _p, x, _y, _f in column_points):
            continue  # placeholder column; nothing to render
        fig = plot_fit_column(
            column_points, mode=mode, show_components=True,
            x_range=x_range, y_range=y_range, normalize=True,
        )
        images[col] = export_figure_png(fig, width=column_width_px, height=column_height_px, scale=2.0)
        progress.tick(stage_key, col + 1, FIT_GRID_COLUMNS)
    return images


__all__: Sequence[str] = (
    "GRID_POINTS", "QcReportInputs", "REPORT_FIGURES", "THRESHOLD_NOISE_RATIO",
    "build_qc_report", "grid_points_only", "save_report", "threshold_noise_warning",
)
