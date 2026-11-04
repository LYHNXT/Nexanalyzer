---
name: improve-tests
description: Strengthen the pytest suite of this data analysis repository based on REVIEW.md, adding known-answer, edge-case, regression and property tests without touching source code. Run manually with /improve-tests, optionally followed by a module name.
disable-model-invocation: true
argument-hint: "[optional module or folder to focus on]"
---

# Improve the test suite

Strengthen the tests of this data analysis repository. Use `REVIEW.md` (test gaps table and findings) as the guide; if it does not exist, tell me to run `/data-review` first and stop.

Focus: if an argument is given, work only on tests for `$ARGUMENTS`. Otherwise follow the priority column in REVIEW.md.

## Rules

These keep test changes and bug fixes in separate, reviewable PRs.

- Work on branch `test/improve-<module or date>` created from `main`.
- Only add or edit files under `tests/`. Do not change source code.
- Never edit the locked records: `tests/unit/test_golden_fit.py`, `tests/unit/test_om_contrast.py` and anything under `tests/fixtures/golden_fit/`. `scripts/check_algo_change.py` fails CI when they change without a CHANGELOG entry, and a change there means the numbers moved, which is not a test improvement. Add new tests in new files instead.
- If a test reveals a real bug, mark it `@pytest.mark.xfail(reason="...")` and list it; do not fix it.
- Use pytest. Prefer generating synthetic spectra inside the test (write them to `tmp_path`). If a shared file is needed, put it in `tests/fixtures/`, under 100 KB, synthetic only, and never as `.txt`: `.gitignore` drops every `*.txt` outside its short allowlist, so the file would exist locally and be missing in CI. No real measurement data.
- No network, no hardware, no absolute paths. Fix random seeds.

## Measure first

Run `venv/Scripts/python -m pytest --cov=core --cov=modules --cov-report=term-missing` and record coverage before changes.

## What to add, in this order

1. **Known-answer tests**: synthetic data with a known result, e.g. a Lorentzian at 520.7 cm-1 must be fitted within tolerance; a unit conversion must round-trip. These catch the "runs fine but wrong number" class of bugs.
2. **Edge cases**: empty input, NaN, single point, malformed file, non-converging fit, wrong delimiter or decimal separator.
3. **Regression tests**: for key pipelines, store a small expected output and compare with tolerance, so later changes that shift results are caught.
4. **Property tests** where useful (`hypothesis`): results unchanged when input order is shuffled, or scale correctly when input is multiplied by a constant.

Compare floats with `np.testing.assert_allclose` and explicit tolerances.

## Finish

1. Run the full suite with coverage again.
2. Commit, push, and open a PR to `main` if the `gh` CLI is available.
3. Report in English: tests added, coverage before vs after, and the xfail tests that point to real bugs. STOP.
