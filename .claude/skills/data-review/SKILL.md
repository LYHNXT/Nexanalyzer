---
name: data-review
description: Review this data analysis repository for correctness bugs, security issues, maintainability and test gaps, and write the findings to REVIEW.md. Run manually with /data-review, optionally followed by a folder or file to limit the scope.
disable-model-invocation: true
argument-hint: "[optional path to limit scope, e.g. src/fitting]"
---

# Data analysis code review

Review this repository as a senior scientific-software engineer. It is an internal data analysis tool for materials characterization (Raman/PL spectra, optical images, process logs). The most damaging bugs here are the ones that do not crash but silently produce wrong numbers, so correctness comes first.

Scope: if an argument is given, review only `$ARGUMENTS` (but read whatever it depends on). Otherwise review the whole repository.

## Rules

- Do not modify any file except creating or overwriting `REVIEW.md`.
- Work on branch `review/<today's date>` created from `main`.
- Never print or copy secrets or real measurement data into the report.

## Step 1: Explore first

- Map entry points, data loading, processing, fitting, plotting and export. Skip `venv/`, data folders and generated outputs.
- Summarize the data flow in 5 to 10 lines.
- Run `venv/Scripts/python -m pytest --cov=core --cov=modules --cov-report=term-missing` (the project venv is `venv/`, and `core`/`modules` is the scope CI's 87 % floor measures). Note pass/fail, overall coverage and the least-covered modules.

## Step 2: Review in this priority order

1. **Correctness** (highest)
   - Units and conversions (nm vs cm-1, um vs m), calibration offsets
   - Array shape/axis mistakes, off-by-one, index misalignment after sorting or filtering (especially pandas)
   - NaN, empty input, zero, division by zero, silent dtype changes
   - Fitting: initial guesses, bounds, handling of non-convergence
   - File parsing: headers, delimiters, decimal separators, encoding
   - Results that depend on file order or are not reproducible
   - Plots or exports that do not match the computed data
2. **Security**
   - `eval`/`exec`, `pickle` or unsafe YAML loading of input files
   - `subprocess` with `shell=True`, path traversal in file handling
   - Hardcoded secrets, tokens, internal paths or IPs
   - Dependencies with known vulnerabilities or GPL-family licenses
3. **Readability and maintainability**
   - Duplicated logic, unclear names, magic numbers without units, missing docstrings, functions doing too much
4. **Hardware**: flag any code that controls instruments; otherwise skip.

## Step 2.5: Test quality

- Which critical functions (loading, unit conversion, fitting, export) have no tests or only "does not crash" tests?
- Are numeric results compared with tolerances (`np.testing.assert_allclose`) rather than exact equality?
- Are edge cases covered: empty file, NaN, single point, wrong units, malformed header, non-converging fit?
- Do tests depend on network, local paths, hardware, or unseeded randomness?
- Are any tests weak (no assertions, or only type checks)?

## Step 3: Verify before reporting

- Cite `file:line` for every finding.
- For correctness issues, prove them where practical with a short script or temporary test on synthetic data (do not commit it).
- If you cannot confirm an issue, label it "Needs verification". Do not report style nitpicks as bugs.

## Output

Write `REVIEW.md` with:

1. Data flow summary
2. Findings table: Severity (Critical/High/Medium/Low) | Category | file:line | Issue | Evidence | Fix
3. Top 5 fixes to do first
4. Test gaps table: Module | Current coverage | Missing cases | Priority

Commit `REVIEW.md`, then tell me in English the top 3 issues and STOP. Do not fix anything.
