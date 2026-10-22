# CLAUDE.md

NexAnalyzer — Nexstrom's measurement data analyzer. Streamlit app: raw spectra
and microscope frames in, fitted results and QC report figures out.

Architecture, data model and the reasoning behind them live in
[docs/Summary.md](docs/Summary.md); version history in
[CHANGELOG.md](CHANGELOG.md). This file holds only what's easy to get wrong.

## Commands

```bash
pytest                  # ~1,560 tests; pythonpath and testpaths come from pyproject.toml
python -m ruff check .   # F + E9 only — deliberately narrow, so a hit is real breakage
streamlit run app.py     # or start.bat, which also creates venv and pulls updates
```

## Golden fit — commits and pushes are gated on it

`.githooks/` (enable per clone: `git config core.hooksPath .githooks`) runs
[tests/unit/test_golden_fit.py](tests/unit/test_golden_fit.py) before every
commit and push two golden sets, each fitted with its own frozen preset and
reproducing its `expected.json` to 1e-5: HKU-20230524-2's RM_1 and PL_1 (the
WSe2 preset, `tests/fixtures/golden_fit/`) and DDAA10's RM_1..3 (WSe2_QC's
Raman block at v5.30.0, `tests/fixtures/golden_fit/DDAA10/`).
A failure means the parser, despike, baseline or fit changed. Preset edits don't
trip it. Moving the numbers on purpose: `python tests/unit/test_golden_fit.py
--regenerate [SET]` in the same commit, and say so in CHANGELOG. The five `.txt`
spectra are committed through `.gitignore` exceptions — the only measurement
data in the repo; don't widen them further.

**CI repeats this on every pull request** (`.github/workflows/ci.yml`), on the
versions pinned in `requirements-lock.txt`, and adds `scripts/check_algo_change.py`:
an algorithm file changed without a version bump and CHANGELOG entry, or a locked
record (golden fixtures, `test_golden_fit.py`, `test_om_contrast.py`) changed
without the CHANGELOG saying so, fails the build. Procedure:
[docs/Development_Process.md](docs/Development_Process.md).

## Two quantities, two names — never "amplitude"

A peak has a maximum and it has an area. These are different numbers, they differ
by a factor of ~FWHM × 1.064, and confusing them has caused real reporting bugs
here (v3.3.0 shipped a report that disagreed with its own CSV).

| Quantity | Our name | lmfit's name |
| --- | --- | --- |
| The fitted curve's maximum | **intensity** | — |
| The area under the peak | **area** | `amplitude` |

- `peak_metrics.peak_intensity()` / `peak_intensity_and_stderr()` is the only way
  to get an intensity. `PeakDefinition.intensity`, `PeakStat.intensity_*`,
  `intensity_max`, and the `Intensity` column all mean the maximum.
- `FittedPeak.area` / `area_stderr` is the integral. It is named `area` precisely
  *because* lmfit calls it `amplitude`.
- **Every reporting surface shows intensity** — the on-screen Fit Results table,
  both CSVs, the QC Report's summary tables and quality panels, and the
  LA/E2g+A1g and C/LB ratios. They agree, and must keep agreeing.
- The word "amplitude" appears only where lmfit's own parameter is addressed by
  name (`params.add(f"{prefix}amplitude", ...)`). Don't reintroduce it anywhere
  else. Guard tests in
  [tests/unit/test_peak_metrics.py](tests/unit/test_peak_metrics.py) assert that
  `amplitude*` and `height*` stay gone from the models.

The full reasoning is in the
[peak_metrics.py](modules/spectra/processing/peak_metrics.py) module docstring.

## Other invariants

- **`modules/*` may import `core`; `core` never imports `modules`.** See "The one
  rule" in docs/Summary.md.
- **The diagnostic ratios live in one constant.** `peak_metrics.RAMAN_RATIO_PAIRS`
  is `LA/E2g+A1g` (defect), `2LA/E2g+A1g` (layer/resonance, v5.29.0, not a defect
  metric), `C/LB` and `B2g/E2g+A1g` (restored in v5.25.0); only LA has a spec line. The
  figure skips a ratio whose peaks a preset does not fit, as the table already does. The quality figure and the QC Report's summary
  table both read it; don't write the pair out again.
- **Spec lines are drawn and never evaluated, and there are only three**
  (red dashed since v5.32.0). `peak_quality` carries Raman's `E2g+A1g` FWHM =
  7 and `LA/E2g+A1g` = 0.13 (drawn as 13 %), and PL's Raw FWHM = 40 (on
  Exciton/Trion at 35 until v5.31.0, at 40 in v5.31.0; the PL spec is judged on
  the Raw spectrum since v5.32.0). The `B2g` centre line at 308 was removed in v5.32.0:
  "~308" is a presence criterion, stated in the title. A line is drawn only
  on a report of the layer whose criteria include it (`LAYER_SPEC_LINES`):
  FWHM 7 and LA 13 % on Raman 2L, Raw FWHM 40 on PL 1L -- a 1L Raman figure
  has none, as Samsung's quote 20261009W001 §6.5-6.6 lists them. MoS2 adds two
  *nominal* values, grey dashed and "~" in the title (`MOS2_SPEC`,
  `spec_nominal`): |E2g-A1g| ~20 cm-1 and Raw FWHM ~40 nm; the quote writes
  both as "<=", the report states them as targets by Nexstrom's choice.
  The first three are inherited literals from the wafer-comparison scripts;
  40 and the title's spec lines come from the customer QC spec
  (`peak_quality.CUSTOMER_SPEC`), not preset data. The quality figures'
  titles state each customer-spec value beside its limit -- value and limit
  only, **no verdict and no other detail**. Uniformity is the CV of the 9
  region medians, and each panel's legend states the same number ("Uniformity
  = x %", v5.32.0; the pooled Robust CV is gone), so title and panels agree.
  Ratio panels are drawn in percent. **PL has no
  centre spec and no ratio spec** — the ancestor gates its only PL reference
  line on the FWHM panel, and the preset's 755/780 nm are *fit-initialisation*
  guesses. Promoting those to `spec=` would invent a tolerance nobody measured.
- **FWHM means the Voigt FWHM.** Use `fitting.voigt_fwhm(sigma, gamma)`. Reporting
  `2.355 * sigma` — the Gaussian half, ignoring the Lorentzian — was a real bug
  fixed in v3.4.0. It under-reports every width.
- **The width bound is on the Voigt FWHM, not on sigma and gamma.** The fit
  varies `vfwhm` in `[width_min, width_max]` and `gmix` (the Gaussian share,
  between its floor f_G >= width_min/2 and 1); sigma and gamma are derived
  expressions. Keep the Gaussian floor: without it a capped peak goes pure
  Lorentzian to grow wings. Bounding the two separately (each to
  half of `width_max`) capped Lorentzian peaks at ~0.52 x `width_max` — WSe2's
  E2g+A1g piled up at 6.21 until v5.14.0. A "on its limit" check reads
  `vfwhm@max`, not `gamma@max`.
- **A fit that ran out of iterations is not a converged fit, and lmfit will not
  tell you.** lmfit prints "Tolerance seems to be too small" for MINPACK ier 5
  *and* for its own max_nfev abort (ier -1), the same words it uses for a fit
  that cannot improve further (ier 6/7/8). `fit_voigt_peaks` tells them apart by
  `ier` and `nfev`, keeps the truncated fit with `FitResult.converged = False`,
  and every surface shows that flag. A stderr lmfit could not estimate is NaN,
  never 0.0. The preset's `max_iterations` is passed as
  `max_nfev = max_iterations * (variables + 1)` -- iterations, as its name says
  and as lmfit's own default scales it; passing it straight through gave an
  8-peak fit 1/33 of its budget and a fifth of real WSe2 spectra stopped on it.
- **A ratio is formed only from measured peaks -- except LA, 2LA and B2g.**
  `peak_metrics.peak_is_measured` (not `FittedPeak.on_limit`, and at least 3x
  the fit's residual noise) gates both `compute_peak_intensity_ratio` and the
  quality figure's ratio panels; a new ratio surface must use it too, or the
  table and figure disagree. The pairs in `RATIO_FROM_EVERY_SPECTRUM`
  (LA, 2LA and B2g over E2g+A1g, v5.32.0) are formed from every spectrum, then
  a wafer-wide and the region's IQR fence drop only broken fits (most regions
  keep 23-25 of 25): the gate kept only the strongest LA on weak regions and biased them
  high, and dropped most B2g, whose width ends on its cap. A 1L figure hides
  B2g's panels, so no bilayer ratio from noise fits is shown on a monolayer.
  Only C/LB is still gated.
  The criteria and rules are in [docs/QC_Report_Spec.md](docs/QC_Report_Spec.md). Multiple
  starts (`TechniquePreset.fit_starts`) are opt-in per preset so the golden fit,
  whose frozen preset uses one start, never moves with them.
- **The preset owns position and width; the data owns intensity.** `fit_voigt_peaks()`
  auto-estimates the initial intensity from the spectrum at fit time, because
  intensity depends on measurement conditions while center/FWHM are material
  properties. `PeakDefinition.intensity` is a required-but-unused placeholder.
  The *tolerance* is part of "owns position": carry it on
  `PeakDefinition.center_tolerance`, never by writing `center_min`/`center_max`
  from a caller. `calculate_auto_bounds()` runs on every fit and is the only
  thing that writes those two, so bounds set anywhere else are overwritten with
  the mode default — which is what cost WSe₂ five of its seven tolerances before
  v4.0.0.
- **Fit windows are separate fits, joined only for display.** A block with
  `fit_windows` baselines and fits each window on its own (peaks go to the
  window owning their centre, `x_min <= x < x_max`, last window inclusive) and
  `auto_workflow._fit_in_windows` stitches the results into one
  `processed_data` / `background` / `FitResult`, component curves zero outside
  their window. Downstream code must keep assuming nothing beyond "one
  spectrum, one fit" -- that is what lets the QC Report and ratios work
  unchanged. A per-window baseline under a broad feature (WSe2's LA hump)
  reads lower than a whole-spectrum one; choose window edges on flat ground.
- **A preset is keyed by material alone; technique comes from the filename.**
  `MaterialPreset` holds `raman` / `pl` `TechniquePreset` blocks and an
  `optical` dict of `OpticalParams` keyed by layer. Resolve with
  `preset.block_for(spectrum.mode)`; a block may legitimately be absent.
  `data/materials.json` is v2 (an object with `schema_version`); the v1 array is
  migrated on load by a shim in `preset_store`, and
  [tests/unit/test_preset_migration.py](tests/unit/test_preset_migration.py)
  asserts the committed file is already v2 so that shim stays deletable.
- **Optical layers are stored as `"1L"`/`"2L"`, never as the word.**
  `contrast.layer_word()` is one-way presentation and passes unknown values
  through, so keying persisted JSON by its output would orphan every stored
  block the day the wording changed. Render the word, store the code.
- **Thresholding has two modes, and `nsigma` is still the default.**
  `classify()` cuts at `mode ± nsigma · min(sigma_l, sigma_r)` unless
  `abs_threshold` is set, which replaces it with a fixed green contrast in
  percent — a scalar, or a `(below, above)` pair, since 2L→3L is one layer step
  up while 2L→substrate can be several down. `min(sigma_l, sigma_r)` protects
  against *one* contaminated side; a film with small pervasive domains widens
  both halves equally and then raises its own bar until the domains vanish
  (HADH51: sigmas equal to twelve decimals, 4σ landing at +7.1 % against a ~5 %
  layer step). A fixed contrast is only meaningful when
  `abs_threshold / (sigma_noise / mode)` clears ~2; below that the cut is inside
  the reference distribution and the percentages are noise. See the v4.4.0
  section in [docs/OM_Contrast_Algo.md](docs/OM_Contrast_Algo.md).
  **The pair is the preset's, for every wafer** (v5.6.0). The per-wafer
  *adaptive* derivation that stood here from v4.5.0 to v5.5.0 is gone: it
  placed 28 of its 42 cuts on the 2σ noise floor, which turns a non-detection
  into a number, and its stored results could not be reproduced from the
  frames that produced them. Coverage is a comparison — wafer against spec,
  wafer against wafer — and that needs one ruler.
- **`OpticalParams` fields default to `None` meaning "contrast.py's default".**
  Keep the numbers in `contrast.py` alone, so a material with no optical block
  runs `analyse_frame(**{})` — which is what keeps
  [tests/unit/test_om_contrast.py](tests/unit/test_om_contrast.py)'s locked
  numbers meaningful. `mask_margin` and `ff_divisor` apply to **both** frame
  types: `is_circular()` decides that per image at runtime, so a preset cannot
  address one of them. `ff_divisor` is a divisor, not a sigma — larger means a
  smaller blur.
- **`core/version.py` is the single source of truth for the version.** Bump it and
  add a CHANGELOG entry in the same change as any user-visible behaviour change;
  note explicitly when numbers don't move (renames) versus when they do.

- **The runcard recipe semantics have a vendored spec, and it wins.**
  [docs/reference/render_runcard.py](docs/reference/render_runcard.py) is the
  standalone CLI `modules/runcard/` is derived from — read it before changing
  the clock, the growth window, or how a channel's setpoints become segments,
  and run it beside the app to compare. It is excluded from ruff so it can stay
  byte-identical; do not tidy it. **The growth window is exactly the stretch at
  the peak**, ended by whichever comes first of heater-off and a commanded
  ramp-down. It is *not* a "within 5 °C" band — that is a defect the reference
  records as previously fixed, and which this app reintroduced at v5.1.0 by
  porting an older ancestor. See [docs/reference/README.md](docs/reference/README.md).

- **Runcard matching counts only `Wait` time, and on HA1P01 the filename carries the tag.**
  `processing/runcard_match.py` aligns a card's steps with a log's on a *wait
  clock*; Pumping and Check Status last as long as the chamber takes, so they are
  off the clock on both sides. Do not swap in `growth_window.build_timeline`,
  whose Check Status costs 10 s — right for drawing a recipe, wrong for matching.
  HA1P01's DATALOG is filled by a one-way robocopy (SulfurSync) that also copies
  the tool PC's `runcard_tags.json` over the OneDrive one, so a tag saved only
  in OneDrive is temporary; renames survive because the HA-DataRecord job runs
  with `keepRenamed`. See [docs/Runcard_Matching.md](docs/Runcard_Matching.md).

## Doc trust levels

- [docs/Summary.md](docs/Summary.md) — current.
- [docs/QC_Report_Spec.md](docs/QC_Report_Spec.md) — current as of v5.32.0:
  the customer spec and how the QC Report's stats figures state it.
- [docs/Runcard_Matching.md](docs/Runcard_Matching.md) — current as of v5.7.0; its
  validation numbers were measured on HA1P01 on 2026-09-28.
- [docs/Fitting_Algo.md](docs/Fitting_Algo.md) — §1–§4 verified against the code at
  v3.6.0. §5–§8 are history; their listings quote code as it stood at the time.
- [docs/Baseline_Algo.md](docs/Baseline_Algo.md) — paths repointed at v3.6.0, but the
  algorithm content has not been re-verified since v2.9.0. Check against the code
  before relying on it.
- [docs/OM_Contrast_Algo.md](docs/OM_Contrast_Algo.md) — the spec for
  `modules/optical/processing/contrast.py`, vendored verbatim from the research
  script at v3.10.0. The code was checked against it and against that script's
  own output; keep them in step, and read it before touching the segmentation.
  Its tuning figures are still the defaults, but since v4.0.0 they are
  overridable per material and layer from the preset, and the doc's
  "circular"-labelled rows (σ_ff, margin) are applied to both frame types.
  Its "Second divergence" section is **history**: the adaptive per-wafer pair
  it specifies was retired in v5.5.0 and the section is marked as such.
  Its final section documents v4.4.0's `abs_threshold`, which is a deliberate
  divergence from the vendored algorithm rather than a restatement of it.
