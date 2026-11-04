---
name: data-review
description: Review this data analysis repository for correctness bugs, security issues, maintainability and test gaps, and write the findings to REVIEW.md. Run manually with /data-review, optionally followed by a folder or file to limit the scope.
disable-model-invocation: true
argument-hint: "[optional path to limit scope, e.g. modules/spectra/processing]"
---

# Data analysis code review

Review this repository as a senior scientific-software engineer. It is NexAnalyzer, Nexstrom's internal analyzer for materials characterization (Raman/PL spectra, optical microscope frames, tool process logs): a Streamlit app (`app.py`, `pages/`) over `core/` and `modules/`. The most damaging bugs here are the ones that do not crash but silently produce wrong numbers, so correctness comes first.

Scope: if an argument is given, review only `$ARGUMENTS` (but read whatever it depends on). Otherwise review the whole repository.

## Rules

- Do not modify any file except creating or overwriting `REVIEW.md`.
- `git fetch`, then work on branch `review/<today's date>` created from `origin/main` (`git switch -c review/<date> origin/main`). Local `main` is often stale in this clone.
- Never print or copy secrets or real measurement data into the report.
- Scripts you write to prove a finding go in a temp folder outside the repo, never in the tree.

## Step 0: Read the rules of this repo before reading code

1. `CLAUDE.md`: every bullet under "Two quantities, two names" and "Other invariants" is a bug class that has already shipped once. Review against them explicitly (Step 2).
2. `docs/Summary.md` (architecture, current) and `docs/QC_Report_Spec.md` (the customer spec the figures state).
3. The "Doc trust levels" section of `CLAUDE.md`: `docs/Fitting_Algo.md` §5-8 and `docs/Baseline_Algo.md` are history, not spec. Do not report code for disagreeing with them; check the code against the current docs and the tests.
4. `docs/reference/` is a vendored specification kept byte-identical. Do not review it; do review `modules/runcard/` against it.

## Step 1: Explore first

- Map entry points, data loading, processing, fitting, plotting and export. Skip `venv/`, `Example/`, `dist/`, data folders and generated outputs.
- Summarize the data flow in 5 to 10 lines.
- Run `venv/Scripts/python -m pytest -q -p no:cacheprovider --cov=core --cov=modules --cov-report=term-missing` (about 1,800 tests, 8-10 min serial; `core`/`modules` is the scope CI's 87 % floor measures). On the laptop that owns the golden record the strict golden fit passes as-is; on any other machine set `NEXANALYZER_GOLDEN_PORTABLE=1` first, and a failure there is still a real change. Note pass/fail, overall coverage and the least-covered modules.

## Step 2: Review in this priority order

1. **Correctness** (highest). First the repo's own invariants, then the generic list.

   Repo invariants (`CLAUDE.md` is the authority; cite it):
   - Intensity is the curve maximum, area is the integral (lmfit's `amplitude`). Every reporting surface (Fit Results table, both CSVs, QC Report tables and panels, ratios) must show **intensity**, and the word `amplitude` may appear only where lmfit's own parameter is addressed.
   - FWHM means the Voigt FWHM from `fitting.voigt_fwhm`; `2.355 * sigma` is a bug. The width bound is on `vfwhm`, with the Gaussian floor kept.
   - A fit that ran out of iterations is `converged = False`, and every surface shows it. A stderr lmfit could not estimate is NaN, never 0.0.
   - Ratios: one constant (`peak_metrics.RAMAN_RATIO_PAIRS`), one value source (`ratio_values_by_point`), gated by `peak_is_measured` (not `on_limit`), except the `RATIO_FROM_EVERY_SPECTRUM` pairs with their IQR fences. A new ratio surface that reads anything else makes the table and figure disagree.
   - Spec lines are drawn and never evaluated; only the ones in `LAYER_SPEC_LINES` / `MOS2_SPEC`, only on the layer whose criteria include them; figure titles state value and limit, no verdict. PL has no centre spec and no ratio spec. Uniformity is the CV of the 9 region medians, stated identically in title and legend.
   - `calculate_auto_bounds()` is the only writer of `center_min`/`center_max`; tolerance lives on `PeakDefinition.center_tolerance`. Fit windows are separate fits joined only for display.
   - A preset is keyed by material; technique comes from the filename (`preset.block_for(spectrum.mode)`, a block may be absent). `data/materials.json` is schema v2.
   - Optical layers are stored as `"1L"`/`"2L"`; `OpticalParams` `None` means contrast.py's default; the threshold pair is the preset's for every wafer, never derived per wafer.
   - Runcard: the growth window is exactly the stretch at the peak (not a within-5 degC band); matching runs on the wait clock, not `growth_window.build_timeline`.
   - `modules/*` may import `core`; `core` never imports `modules`. `core/version.py` and CHANGELOG move together with any user-visible change.

   Generic:
   - Units and conversions (nm vs cm-1, eV gaps, um vs m), calibration offsets, zero-line shifts applied twice
   - Array shape/axis mistakes, off-by-one, index misalignment after sorting or filtering (especially pandas)
   - NaN, empty input, zero, division by zero, silent dtype changes
   - Fitting: initial guesses, bounds, non-convergence, multi-start opt-in per preset
   - File parsing: headers, delimiters, decimal separators, encoding; results that depend on file order
   - Plots or exports that do not match the computed data; Streamlit `session_state` and `st.cache_data` keys that let one wafer's result show under another
2. **Security**
   - `eval`/`exec`, `pickle` or unsafe YAML loading of input files
   - `subprocess` with `shell=True`, path traversal in file handling
   - Hardcoded secrets or tokens; dependencies with known vulnerabilities or GPL-family licenses
3. **Readability and maintainability**
   - Duplicated logic, unclear names, magic numbers without units, missing docstrings, functions doing too much. Numbers that are the customer spec belong in `peak_quality.CUSTOMER_SPEC`, not inline.
4. **Hardware**: this app reads tool logs copied by robocopy; it controls no instrument. Flag it if that changes; otherwise skip.

By design, not findings: the `*.txt` ignore rule and its five golden exceptions; local OneDrive paths inside gitignored `data/*.json` sidecars; `Example/` being ignored; the golden record reproducing only to 1e-3 / 1 % on other machines; `docs/reference` not passing ruff.

## Step 2.5: Test quality

This repo has two golden tiers (`tests/golden/README.md`): the tripwire `tests/unit/test_golden_fit.py` proves *unchanged*, the validated set `tests/golden/cases/` proves *right* against an independent reference. Judge the rest of the suite against that:

- Which critical functions (loading, unit conversion, baseline, fitting, ratios, export) have no tests or only "does not crash" tests?
- Which numbers have only the tripwire behind them and no validated case with an independent reference?
- Are numeric results compared with tolerances (`np.testing.assert_allclose`, `pytest.approx`) rather than exact equality, and are the tolerances portable across machines (1e-3 / 1 %, not 1e-5)?
- Are edge cases covered: empty file, NaN, single point, wrong units, malformed header, non-converging fit, a preset block that is absent?
- Do tests depend on network, local paths, hardware, the real DATALOG, or unseeded randomness? Would they survive `-n 4` on Windows (CI runs them that way)?
- Are any tests weak (no assertions, or only type checks)?

## Step 3: Verify before reporting

- Cite `file:line` for every finding, and the `CLAUDE.md` bullet or doc it violates where one exists.
- For correctness issues, prove them where practical with a short script or temporary test on synthetic data, written outside the repo.
- If you cannot confirm an issue, label it "Needs verification". Do not report style nitpicks as bugs.

## Output

Write `REVIEW.md` with:

1. Data flow summary
2. Findings table: Severity (Critical/High/Medium/Low) | Category | file:line | Issue | Evidence | Fix | **Moves numbers?** (yes if the fix changes any fit, contrast, ratio or figure value: then the fix also needs a `core/version.py` bump, a CHANGELOG entry saying which numbers move on which data by how much, and the golden record regenerated on the laptop that owns it; `docs/Development_Process.md` sections 2 and 6)
3. Top 5 fixes to do first, with number-moving fixes grouped, because each is its own version bump
4. Test gaps table: Module | Current coverage | Missing cases | Priority | Tier (unit, validated case, page smoke)

Commit `REVIEW.md` (the pre-commit hook runs the strict golden fit; that is expected), then tell me in English the top 3 issues and STOP. Do not fix anything.
