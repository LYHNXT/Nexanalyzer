# QC Report — customer spec and how the stats figures report it

Current as of v5.34.0. Code: `modules/spectra/viz/peak_quality.py`
(`CUSTOMER_SPEC`, `spec_summary_lines`, `_draw_panel`),
`modules/spectra/processing/peak_metrics.py` (`RATIO_FROM_EVERY_SPECTRUM`,
`ratio_values_by_point`),
`modules/spectra/processing/spectrum_selection.py` (25 spectra per region),
`modules/spectra/processing/sample_scanner.py` (`sample_title`).

## The customer spec

3 × 3 grid per wafer → 9 regions, 25 spectra per region.

| Technique | Layer | Criterion | Limit |
| --- | --- | --- | --- |
| Raman | 1L | E₂g + A₁g hybrid mode present | ~250 cm⁻¹ |
| Raman | 2L | B₂g present | ~308 cm⁻¹ |
| Raman | 2L | FWHM of E₂g + A₁g | < 7 cm⁻¹ |
| Raman | 2L | LA(M) relative intensity, I(LA)/I(E₂g+A₁g) | < 13 % |
| Raman | all | Uniformity: E₂g + A₁g position / FWHM variation across the wafer | ≤ 0.5 % / ≤ 20 % |
| PL | 1L | FWHM of the Raw spectrum | ≤ 40 nm |
| PL | all | Uniformity: Raw position / FWHM variation across the wafer | ≤ 1.5 % / ≤ 25 % |
| Raman | 1L MoS₂ | \|E₂g − A₁g\| | ~20 cm⁻¹ (quote: ≤ 20) |
| PL | 1L MoS₂ | FWHM of the Raw spectrum | ~40 nm (quote: ≤ 40) |

Source: Samsung (SAIT) quote 20261009W001, §6. The quote samples 5 points per
region; the reports use 25. The two MoS₂ values are shown as nominal targets
("~", grey dashed line) by Nexstrom's choice; the quote writes them as limits.

## What the figure shows

- **Title, two lines under the suptitle.** First line: the layer's criteria,
  value and limit only (`2L spec:  B₂g 308.92 cm⁻¹ (spec ~308) · E₂g+A₁g FWHM
  5.12 cm⁻¹ (spec < 7) · I(LA)/I(E₂g+A₁g) 7.9 % (spec < 13 %)`). Second line:
  uniformity (`Uniformity:  E₂g+A₁g position 0.04 % (spec ≤ 0.5 %) · FWHM 2.38 %
  (spec ≤ 20 %)`). **No verdict** — the reader compares value and limit.
- **Panels state the same numbers.** Each panel's legend is `Median = …` and
  `Uniformity = …`, computed from the same cleaned values the title reads, so a
  quantity never has two values on one figure.
- **Ratios are in percent** (7.9 %, spec line at 13), as the spec states them.
- **Spec lines are red dashed**: E₂g+A₁g FWHM 7, LA ratio 13 %, PL Raw FWHM 40.
- **PL is judged on the Raw spectrum** — each spectrum's own maximum and
  half-height width, no fit — so the title's PL values and the 40 nm line sit
  on the Raw column; Exciton and Trion carry no line.
  B₂g ~308 is a presence criterion, so it is stated in the title and has no
  line. A line is drawn only for the report layer's own criteria, as the
  title does: FWHM 7 and LA 13 % on 2L Raman, Raw FWHM 40 on 1L PL. A 1L
  Raman figure has no line — 13 % is not a monolayer limit.
- **A 1L report hides B₂g** — its centre panel and the bilayer ratio
  I(B₂g)/I(E₂g+A₁g). A monolayer has no B₂g; those panels would plot a
  component the fit parked on noise. The layer comes from the report's
  reference layer (the folder's `_1L` / `_2L`).
- **MoS₂** states |E₂g−A₁g| (Raman) and Raw FWHM (PL) against ~20 cm⁻¹ and
  ~40 nm, grey dashed, plus E₂g/A₁g and Raw uniformity. Its PL figure shows
  only the A exciton (A0) and A⁻ trion beside Raw; B is fitted, not drawn.
- **The PL title does not list its extra cleaning** (FWHM ≤ 5 nm and the
  widest 5 % per peak are still dropped; `PL_CLEANING`).

## How each value is computed

- **Value of a criterion** — the median of all cleaned spectra pooled across
  the 9 regions (the panel's dashed line). Positions and widths to 2 decimals,
  ratios to 1 decimal in %.
- **Uniformity** — the coefficient of variation of the **9 region medians**:
  CV = σ / μ × 100 %, with mᵢ the median of region i's cleaned spectra,
  μ = mean(m₁…m₉) and σ their sample standard deviation (n − 1). It measures region-to-region variation; scatter
  inside a region does not count. (Until v5.31.0 the panels showed a pooled
  Robust CV, 1.4826 · MAD / median over all spectra, which also counted
  within-region scatter and so read higher — e.g. 0.06 % vs 0.04 %.)
- **Cleaning** — Raman: a 1.5 × IQR fence within each region; PL: FWHM ≤ 5 nm,
  peaks under 2 % of the spectrum's strongest, and the widest 5 % per fitted
  peak are dropped. The Raw band keeps every spectrum above the 5 nm floor
  (v5.57.0; until then it went through the widest-5 % cut too, so the title's
  Raw FWHM was the median of the narrowest 95 %).

## The ratio rule: 25 per region where the data allow

- **25 spectra per region.** A region measured with more (a 36-spectrum map)
  loses its outliers — R² gate, or a main peak outside the region's 1.5 × IQR
  fence on centre, FWHM or intensity — and 25 of the rest are kept at random,
  seeded by the region (`select_spectra_per_point`).
- **LA, 2LA and B₂g ratios use every one of those spectra**
  (`RATIO_FROM_EVERY_SPECTRUM`), then two 1.5 × IQR fences drop only extreme
  values: one over the whole wafer, then the region's own. The wafer fence
  catches a weak spectrum in a widely spread region (KRX261003 point 5: 20 %
  against 8–16 % in its region), the region fence a broken fit (260 %). Most
  regions keep 23–25.
  *Why:* on a weak region LA sits just under 3 × the fit noise. Gating on noise
  kept only the strongest few and biased the region high (KRX261001 point 7:
  9.7 % from 5 spectra, 8.3 % from all 25). A strict 25 everywhere would mean
  plotting broken fits, so the fence stays.
- **B₂g on a monolayer:** a 1L report hides B₂g's panels, so a ratio formed
  from noise fits is never shown as a bilayer signal. On 2L the gate kept only
  3–19 of 25, because B₂g's width ends on its cap.
- The **summary table and workbook** use the same rule, summarized by median
  and MAD, so the table and figure agree. Since v5.34.0 that is enforced: both
  read `peak_metrics.ratio_values_by_point` and the region fence. Until then
  the table took a plain median over unfenced values and could differ from the
  figure's title (10.2 % against 9.8 %).
- **MoS₂** (v5.34.0, in the app; until then only `run_qc_mos2.py`): the Raman
  figure is FWHM (E₂g, A₁g) | Center (E₂g, A₁g) | Δω, with the nominal ~20 cm⁻¹
  line on Δω for 1L. The summary page adds a Δω row and the 2LA(M)/A₁g defect
  ratio (gated by `peak_is_measured`, like C/LB); the workbook carries the
  ratio, not Δω.
- *Caveat:* where LA sits on a fit limit across a whole region, that region now
  shows it (KRX261004 point 2: 18 % against ~12.5 % elsewhere) instead of
  hiding it.

## Report title

The report and its files are titled by the sample folder name **without the
wafer ID**: `KRX261001_HADH82_2L` → `KRX261001_2L`, `KRX261005_VM2610012_MoS2`
→ `KRX261005_MoS2`. Only a middle token that looks like a wafer ID (letters
then digits) is dropped; the first and last tokens always stay.

## Redrawing without a refit

The stats figures can be redrawn from a report's workbook alone
(`scripts/qc_report/redraw_stats.py REPORT_DIR NEW_TITLE LAYER MATERIAL OUT_DIR`): the Raman/PL sheets hold every kept
spectrum's centre, FWHM, intensity, R² and χ², and the fit-limit flags and noise
are rebuilt from the preset bounds and √(χ²/N). On KRX261001 it reproduced the
original panels exactly. The fit-grid and summary images need a refit.
