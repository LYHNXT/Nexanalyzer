---
name: improve-tests
description: Strengthen the pytest suite of this data analysis repository based on REVIEW.md, adding known-answer, edge-case, regression and invariance tests without touching source code. Run manually with /improve-tests, optionally followed by a module name.
disable-model-invocation: true
argument-hint: "[optional module or folder to focus on]"
---

# Improve the test suite

Strengthen the tests of this data analysis repository (NexAnalyzer). Use `REVIEW.md` (test gaps table and findings) as the guide; if it does not exist, tell me to run `/data-review` first and stop.

Focus: if an argument is given, work only on tests for `$ARGUMENTS`. Otherwise follow the priority column in REVIEW.md.

## Rules

These keep test changes and bug fixes in separate, reviewable PRs.

- `git fetch`, then work on branch `test/improve-<module or date>` created from `origin/main` (`git switch -c test/improve-<x> origin/main`). Local `main` is often stale in this clone.
- Only add or edit files under `tests/`. Do not change source code, `ci.yml`, `pyproject.toml` or the requirements files. The one exception is a narrow `.gitignore` line for a new validated case's input (below).
- Never edit the locked records: `tests/unit/test_golden_fit.py`, `tests/unit/test_om_contrast.py` and anything under `tests/fixtures/golden_fit/`. `scripts/check_algo_change.py` fails CI when they change without a CHANGELOG entry, and a change there means the numbers moved, which is not a test improvement. Add new tests in new files instead.
- If a test reveals a real bug, mark it `@pytest.mark.xfail(strict=True, reason="REVIEW.md <row>: ...")` and list it; do not fix it. `strict=True` so the fix, when it lands, flips the test to XPASS and the marker is removed. Note in the report when the fix would touch an algorithm file, because that fix then also needs a version bump and CHANGELOG entry (`docs/Development_Process.md` section 2).
- Use pytest only. **No `hypothesis`**: CI installs `requirements.txt` plus pytest, pytest-cov, pytest-xdist and ruff, nothing from `requirements-dev.txt`, so an import of hypothesis passes locally and fails CI. Express properties with `pytest.mark.parametrize` over a few seeded inputs.
- Prefer generating synthetic spectra inside the test (write files to `tmp_path`). If a shared file is needed, put it in `tests/fixtures/`, under 100 KB, synthetic only, and never as `.txt`: `.gitignore` drops every `*.txt` outside its short allowlist, so the file would exist locally and be missing in CI. No real measurement data.
- No network, no hardware, no absolute paths, no reading the real DATALOG or OneDrive. Fix random seeds (`np.random.default_rng(0)`).
- CI runs the suite with `-n 4` on a Windows self-hosted runner, each test under a 10-minute faulthandler. So: no shared mutable state between tests, no writes into `data/` (the `tests/conftest.py` autouse fixtures redirect the page sidecars to a scratch dir; keep using them), `pathlib` not string paths, explicit `encoding=` on every open, and keep each test seconds long.

## Read first

- `CLAUDE.md`: the invariants a test must assert on. In particular a test asserts `intensity` (the maximum) or `area` (the integral), never `amplitude`; FWHM through `fitting.voigt_fwhm`; a ratio only from `peak_is_measured` peaks.
- `tests/golden/README.md`: the two tiers. Do not build a third.
- `tests/conftest.py` (what is already isolated for you) and `tests/integration/test_pages_render.py` (the `AppTest` pattern: `AppTest.from_file(str(PROJECT_ROOT / page))`, absolute, because Streamlit resolves a relative path against the caller).
- One existing file in the area you are extending, to match its style: a module docstring, `class Test<Behaviour>` holding `test_<what_the_behaviour_is>` methods whose names read as sentences, a comment citing `file:line` where a test pins a specific branch of the code, `pytest.approx` / `np.testing.assert_allclose` for every float.

## Measure first

Run `venv/Scripts/python -m pytest -q -p no:cacheprovider --cov=core --cov=modules --cov-report=term-missing` and record coverage before changes. The golden fit is strict on the laptop that owns the record; elsewhere set `NEXANALYZER_GOLDEN_PORTABLE=1`.

## What to add, in this order

1. **Known-answer tests**: synthetic data with a known result. Build the spectrum from the model under test, e.g. a Voigt at WSe2's E2g+A1g (about 250 cm-1) with a chosen centre and Voigt FWHM on a flat or sloped baseline, fit it through the material preset, and assert centre, `voigt_fwhm`, intensity and area each within a tolerance. Tolerances must be portable across machines: centre +-0.1 cm-1, FWHM +-0.05, intensity and area 1 % (`tests/golden/README.md`), never 1e-5. A unit conversion must round-trip. These catch the "runs fine but wrong number" class of bugs.
2. **Edge cases**: empty input, NaN, single point, malformed header, non-converging fit (assert `converged is False` and a NaN stderr, not 0.0), wrong delimiter or decimal separator, a preset whose `raman` or `pl` block is absent, a peak sitting on its width limit.
3. **Regression tests for whole pipelines**: this repo already has the tripwire (`test_golden_fit.py`, do not touch) for "unchanged". For "right", add a case under `tests/golden/cases/` following its README: synthetic `input.txt` named `RM_...`/`PL_...`, `settings.json` with the preset as used, `expected.json` with tolerances **and a `reference` that names where the numbers come from** (the generating function and its parameters is a valid reference for a synthetic case; the runner refuses a case without one). Add the one narrow `.gitignore` exception `!tests/golden/cases/<group>/<case>/input.txt`, the way `bad_input/*/input.txt` is added, and say so in the report. Do not store "whatever the program currently prints" as expected.
4. **Invariance tests** with `parametrize`: the fitted centre does not change when the input points are shuffled or when the spectrum is scaled by a constant (intensity and area scale, centre and FWHM do not); the ratio table and the quality figure read the same values for the same wafer; `layer_word()` and the stored `"1L"`/`"2L"` codes round-trip.
5. **Page smoke and flow tests** where a page or a button has none: `AppTest` as in `test_pages_render.py`; assert on the rendered elements and on the exported bytes, not only `not at.exception`.

Compare floats with `np.testing.assert_allclose` or `pytest.approx` and explicit tolerances.

## Finish

1. Run the full suite with coverage again. Run `venv/Scripts/python -m ruff check .` (CI lints the tests too).
2. Commit. The pre-commit hook runs the strict golden fit; the pre-push hook adds `scripts/check_algo_change.py origin/main`, which must pass untouched for a tests-only branch.
3. Ship it the repo's way: `powershell -ExecutionPolicy Bypass -File scripts\ship.ps1` runs the local gates, pushes, opens the PR with the template and waits for CI. It needs the `gh` CLI; if `gh` is missing, push the branch, give me the PR URL, and I will open it. In the PR body tick **No numbers move**.
4. Report in English: tests added by tier, coverage of `core`/`modules` before vs after, the new `--cov-fail-under` value you would set in `ci.yml` (just under the measured total; I will raise it, you do not edit `ci.yml`), the xfail tests that point to real bugs, and any `.gitignore` exception you added. STOP.
