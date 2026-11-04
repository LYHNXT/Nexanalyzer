# Code review — 2026-11-04

Scope: whole repository, `origin/main` at `b8a8866` (v5.53.0). Reviewed by `/data-review`.
Depth: `modules/spectra/processing/*`, `peak_quality.py`, `contrast.py`, `parser`, `sample_scanner`,
`qc_report/build.py`, exports, `native_dialog.py`, `graph_client.py` read line by line; `datalog`,
`runcard`, `splittable`, `onenote`, UI and figure-drawing modules skimmed for the generic checks only.
Every correctness finding below was reproduced on synthetic data (script kept outside the repo).

## 1. Data flow

1. A sample folder is scanned by filename (`sample_scanner`): `RM_<n>.txt` / `PL_<n>.txt` are spectra
   at grid point n, any other prefix with an image extension is an OM frame.
2. Each spectrum file is parsed (`parser`: header/footer skipped, delimiter sniffed, one X and 1+ Y
   columns) into `SpectrumData` (float64, finite, 100–100 000 points).
3. `auto_workflow.execute_auto_workflow` runs the preset block for the file's technique: reset to the
   recorded data → optional zero-line shift (Raman) → x-range crop → despike (`remove_spikes` for
   Raman, `remove_spikes_local` for PL) → baseline (polynomial/ALS, optional exclusions) → Voigt fit
   (`fitting.fit_voigt_peaks`, lmfit; width parameterised as the Voigt FWHM), either whole-spectrum or
   per fit window stitched into one `FitResult`.
4. `qc_report/build.py` draws each point down to 25 spectra (`spectrum_selection`), gates fits on
   R² > 0.5 (`filter_fits_by_quality`), then feeds the same pairs to: the stats tables
   (`aggregate_fit_results`), the ratios (`compute_peak_intensity_ratio` → `ratio_values_by_point`),
   the quality figures (`peak_quality`) and the workbook (`results_excel`). Intensity everywhere is
   `peak_metrics.peak_intensity` (the curve maximum), never the area.
5. OM frames go through the vendored contrast segmentation (`optical/contrast.py`), with the preset's
   threshold pair for every wafer.
6. Outputs: summary PNG, fit grids, quality figures, one .xlsx; CSVs from the Spectra page; OneNote
   publish via MSAL device-code + Graph.

Checked and clean (CLAUDE.md invariants): no `amplitude` outside lmfit's parameter name; FWHM only via
`voigt_fwhm`; `on_limit` is not used as a ratio gate; `center_min/max` written only in
`PeakDefinition.calculate_auto_bounds`; one `RAMAN_RATIO_PAIRS`, one `ratio_values_by_point`, read by
table, figure and workbook; spec lines drawn only (`LAYER_SPEC_LINES`), titles state value + limit, no
verdict; uniformity = CV of the 9 region medians in title and legend alike; `core` never imports
`modules`; growth window is the stretch at the peak with the 5 °C band explicitly retired.

Test run (this laptop, strict golden): **1810 passed in 8 m 42 s; coverage 93 % of `core`+`modules`**
(7 885 statements, 563 missed; CI floor is 87). Least covered: `splittable/viz/charts.py` 67 %,
`onenote/io/graph_client.py` 71 %, `spectra/processing/fit_record.py` 72 %, `core/viz/render.py` 75 %,
`spectra/viz/live_plot.py` 77 %, `datalog/ui/datalog_state.py` 79 %, `spectra/ui/workspace.py` 82 %.

## 2. Findings

| # | Severity | Category | file:line | Issue | Evidence | Fix | Moves numbers? |
|---|---|---|---|---|---|---|---|
| 1 | **High** | Correctness | `modules/spectra/processing/despiking.py:120-137` (Raman path, chosen at `auto_workflow.py:211`) | `remove_spikes` replaces each flagged pixel by the median of its neighbours *including the other flagged pixels*, so a cosmic ray wider than one pixel is only partly removed and the remainder goes into the fit unflagged. `remove_spikes_local` (PL, v5.41.0) fixed exactly this but Raman still uses the old routine. | Synthetic: baseline 100 ± 2, 3-pixel ray at +2000, threshold 100 → all 3 px flagged, **+1000 remains on all three** after "cleaning" (50 % of the ray, worse than the 20 % the PL docstring quotes). | Exclude flagged pixels from the replacement window (widen until ≥ 2 clean neighbours, as `remove_spikes_local` does). Keep scoring unchanged. | **Yes** — any Raman spectrum with a ≥ 2-px ray. Golden Raman is unaffected per CLAUDE.md, but re-run it; version bump + CHANGELOG. |
| 2 | **Medium** | Correctness | `modules/spectra/processing/parser.py:222-233` with `models/spectrum.py:82-83` | One blank or non-numeric cell in one Y column of a multi-Y file makes that column NaN; `SpectrumData` refuses NaN, the loop swallows the exception, and the **whole spectrum is dropped silently** — an error is raised only when *every* column fails. A 36-spectrum map with one bad cell reports 35 with no message. `test_a_missing_value_keeps_its_column` tests the DataFrame, not this. | 3-column file, one empty cell on row 0 → `parse_spectrum_multi` returns **1** spectrum (control file: 2). Also: `_is_numeric_row` accepts `nan`, `inf`, `1_000` (Python float literals), so a `nan` in the data block takes this path too. | Return/raise the per-column errors (the page can show "column 3 skipped: …") and reject non-finite tokens in `_is_numeric_row`. | No (which spectra are counted changes only where the bug bit). |
| 3 | **Medium** | Correctness | `modules/spectra/processing/peak_metrics.py:51-58` | Intensity is the **sampled** maximum of the component curve. A peak narrower than ~6 grid steps is under-read by an amount that depends on where the grid falls relative to the centre, so every intensity and every ratio carries a spectrum-dependent error. The analytic Voigt height already exists in `fitting.py:100` (`_UNIT_HEIGHT_EXPR`). | Voigt FWHM 5 cm⁻¹ on a 0.9 cm⁻¹ grid (WSe₂ E₂g+A₁g): under-read **up to 2.4 %, mean 0.8 %**; FWHM 4: up to 3.7 %. LA (FWHM ~30) is under-read by < 0.1 %, so I(LA)/I(E₂g+A₁g) is biased **high** by up to 2.4 % of its value, varying per spectrum — pure scatter added to the 13 % spec value and to uniformity. | Compute intensity analytically from (area, σ, γ) = `area · Re w(iγ/σ√2)/(σ√2π)`; rescale stderr the same way. | **Yes** — all intensities and ratios by ≤ 2–4 %; regenerate golden on this laptop; version bump + CHANGELOG stating the shift. |
| 4 | **Medium** | Correctness | `modules/spectra/processing/peak_metrics.py:92-98` | Raw PL FWHM is the distance between the **first and last** sample above half maximum, with no interpolation. (a) Any second band above half maximum (MoS₂ B exciton when I(B)/I(A) > 0.5; a trion shoulder) makes "Raw FWHM" the span of both bands, and the ≤ 40 nm spec is then judged on a different quantity. (b) The value is quantised to the sample spacing. | (a) 30 nm band + second band at 0.6 height 60 nm away → **79.5 nm**. (b) True 39.0 nm on a 1 nm grid → 38.0. | (a) Take the contiguous above-half region around the maximum. (b) Interpolate linearly at the two crossings. | **Yes** — PL Raw centre/FWHM (not in the golden tripwire, which locks fit parameters). (a) *Needs verification on real MoS₂ data*: whether I(B)/I(A) > 0.5 occurs on production wafers. (b) confirmed. |
| 5 | **Medium** | Correctness / spec | `modules/spectra/viz/peak_quality.py:191-196, 893-896, 624` vs `docs/QC_Report_Spec.md` "How each value is computed" | The Raw records are passed through `PL_CLEANING`, so the title's "Raw FWHM x (spec ≤ 40)" is the median **after the widest 5 % of raw spectra are dropped**. The spec doc describes that cut for fitted peaks and says a criterion's value is the median of all cleaned spectra; Raw is not a fitted peak. Optimistic by about half a percentile — only matters near the limit, which is where it is read. | Code path: `records += _clean(raw, spec.cleaning)` with `fwhm_top_fraction=0.05`. | Decide: exempt Raw from `fwhm_top_fraction` (recommended: the spec is "each spectrum's own width"), or state the cut in the title and the spec doc. | **Yes** if exempted (PL Raw title/panel). Needs decision. |
| 6 | Low | Correctness | `modules/spectra/processing/sample_scanner.py:82-87` | Two files resolving to the same point (`RM_1.txt` + `Raman_1.txt`, or `rm-1.txt`) silently overwrite each other; the loser is neither used nor listed in `ignored_files`. | Dict assignment, last in sorted order wins. | Record the collision in `ignored_files` (or a `duplicates` list) and warn on the page. | No. |
| 7 | Low | Consistency | `core/report/summary_figure.py:490-493` vs `modules/spectra/viz/peak_quality.py:782-784, 810` | The summary table prints ratios as fractions (`0.079 ± 0.004`), the quality figure and its title in percent (`7.9 %`). Same number, two presentations on one report. | Formatting only. | Print the table row in percent too (`7.9 ± 0.4 %`), as the spec states it. | No (presentation). |
| 8 | Low | Maintainability | `modules/spectra/processing/auto_workflow.py:10, 32, 179-196, 226-239` | The processing pipeline imports `streamlit` and writes `st.session_state` / view options directly. Processing depends on UI state; headless callers (`scripts/qc_report/_runner.py`, `recommend_max_iterations.py`) carry Streamlit along. | Imports at module top. | Return the view-option changes to the caller (the page applies them); keep `execute_auto_workflow` free of `st`. | No. |
| 9 | Low | Maintainability | `requirements.txt:11`, `requirements-lock.txt:63` | `scikit-image` is declared and pinned (0.26.0) but never imported — `contrast.py:23` replaced it with scipy on purpose. Dead dependency: install time, lock churn, attack surface. | `grep skimage` hits comments only. | Remove it from both files; `pip check` in CI confirms. | No. |
| 10 | Low | Maintainability | `modules/spectra/processing/baseline.py:1-11`; `fitting.py:51-66` | Baseline docstring lists Rolling Ball, Spline and airPLS, which do not exist (only Polynomial and ALS). In `fitting.py` the docstring explaining `START_MARGIN` sits under `START_PERTURBATION`. | Read the files. | Fix the docstrings. | No. |
| 11 | Low | Correctness (verified harmless) | `modules/spectra/processing/fitting.py:545-549` with `models/peak.py:163-170` | `on_limit` checks centre and width bounds but not the area ceiling. Checked: the ceiling is 5·y_max·template FWHM·1.064, and a Lorentzian peak at 3× the template width and y_max tall needs 4.7·y_max·FWHM, so the cap cannot bind on real data. | Arithmetic. | Document that reasoning next to the check, or include `amplitude@max` for completeness. | No. |
| 12 | Low | Robustness | `modules/spectra/processing/despiking.py:101-102` | `np.median(y)` has no NaN policy while the MAD uses `nan_policy='omit'`: one NaN makes every z-score NaN and despiking silently does nothing. Unreachable through `SpectrumData` (NaN refused), reachable for a direct caller. | Read the code. | `np.nanmedian`, or assert finite input. | No. |

Security: **no findings.** No `eval`/`exec`/`pickle`/unsafe YAML; the only `subprocess` passes an argv
list with the request as JSON on stdin (`native_dialog.py:59-64`); the MSAL cache (a refresh token)
is written under `%LOCALAPPDATA%\NexAnalyzer`, outside the repo, per Windows user; `requests` sends the
bearer only to `graph.microsoft.com` URLs it builds itself. Pins are current (requests 2.34.2,
urllib3 2.8.0, pillow 12.3.0, streamlit 1.64.0) and `pip check` runs in CI. Licences: Apache/BSD/MIT/
PSF only, no GPL family. The client/tenant IDs in `data/onenote.json` are public-client identifiers,
not secrets. Hardware: none controlled.

## 3. Top 5 fixes, in order

Number-moving fixes grouped: 1, 3, 4 and 5 each need a `core/version.py` bump, a CHANGELOG line saying
which numbers move on which data by how much, and (1, 3) the golden record regenerated on this laptop
(`docs/Development_Process.md` §2, §6). Do them as separate PRs so each shift is attributable.

1. **#2 parser drops a spectrum on one bad cell** — no numbers move, pure silent data loss; surface
   the per-column errors. Add the `nan`/`inf`/`1_000` rejection in the same change.
2. **#1 Raman despiker leaves half of a multi-pixel ray** — port `remove_spikes_local`'s clean-neighbour
   replacement into `remove_spikes` (scoring unchanged). Moves numbers only where a ray was.
3. **#3 analytic intensity** — removes up to 2–4 % of grid-phase noise from every intensity and ratio.
   Largest systematic effect on the customer-spec LA ratio; regenerate golden.
4. **#4 raw FWHM** — interpolate the crossings now (b); decide (a) after checking real MoS₂ wafers.
5. **#5 Raw records and the widest-5 % cut** — one-line decision, then either the code or the doc.

Then the cheap ones: #9 (drop scikit-image), #7 (table ratios in percent), #6 (duplicate points).

## 4. Test gaps

| Module | Current coverage | Missing cases | Priority | Tier |
|---|---|---|---|---|
| `tests/golden/cases/spectral_fit/` | **empty** (only `_TEMPLATE`; `bad_input` has 3 cases) | The validated "is it right" tier has **no spectral case at all**: nothing checks a fit against an independent reference. One synthetic Raman (Voigt at 250 cm⁻¹, chosen FWHM/intensity/area) and one synthetic PL (exciton + trion with a known gap) case, `reference` = the generating function. | **High** | validated case |
| `spectra/processing/fitting.py` | 95 % | `test_fitting.py` checks convergence and R² only; no known-answer: fitted centre, `voigt_fwhm`, intensity and area recovered within portable tolerance (±0.1 cm⁻¹, ±0.05, 1 %). Non-converging fit → `converged is False`, stderr NaN not 0.0. Area ceiling never binds (finding 11). | High | unit |
| `spectra/processing/parser.py` | high | Through `parse_spectrum_multi`: one bad cell in one Y column (finding 2); `nan`/`inf`/`1_000` tokens; decimal-comma file is *refused*, not misparsed (assert the refusal); `count_spectra` agrees with the parse when row 0 has a gap. | High | unit |
| `spectra/processing/despiking.py` | high | Raman path: a 2- and 3-pixel ray is fully removed (finding 1); a NaN input is refused or handled (finding 12). | High | unit |
| `spectra/processing/peak_metrics.py` | high | `raw_peak_stats`: a second band above half max; half-max crossing between samples (finding 4); `peak_intensity` within 1 % of the analytic height for FWHM 4–7 at 0.9 spacing (finding 3). `ratio_values_by_point`: a denominator of 0 or NaN is skipped. | Medium | unit |
| `spectra/viz/peak_quality.py` | — | Title criterion value equals the panel's dashed median for the same records (Raman FWHM, LA ratio, PL Raw FWHM); Raw records and the top-5 % cut (finding 5) pinned one way or the other. | Medium | unit |
| `spectra/processing/sample_scanner.py` | high | Two files for one point: which wins, and that it is reported (finding 6). | Low | unit |
| `splittable/viz/charts.py` | 67 % | Trend and X-vs-Y charts on a 3-row synthetic table; empty filter; a column group with one column. | Low | unit |
| `onenote/io/graph_client.py` | 71 % | `_call` error paths (4xx with and without a JSON body), `_list` pagination, `publish_section` created vs appended — with a fake `session`. | Low | unit |
| `spectra/processing/fit_record.py` | 72 % | Round-trip of a saved record with pre-v5.21.0 fields missing. | Low | unit |
| `spectra/ui/workspace.py` 82 %, `viz/live_plot.py` 77 % | — | Run All Files with one failing file; export cache invalidation after a preset edit. | Low | page smoke / flow |
| Suite-wide | — | 127 `assert x == <float literal>` on numeric results; most are exact by construction, but each should be `pytest.approx` unless the test is *about* exactness (the golden tripwire). No test depends on network, hardware or absolute paths; `tmp_path` is used throughout; CI runs `-n 4` on Windows and passes. | Low | — |
