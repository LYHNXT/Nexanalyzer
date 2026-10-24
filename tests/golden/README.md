# Validated golden set

`python -m tests.golden.run` runs every case under `cases/`, compares the
result with the case's independently referenced expected values, and writes
`dist/golden/golden_report.md` and `.html`. `pytest` runs the same cases
(`test_golden_cases.py`). See the docstring of `run.py` for the file layout.

## Two tiers

| Tier | Where | Question it answers | Reference |
|---|---|---|---|
| Tripwire | `tests/unit/test_golden_fit.py` (+ `.githooks/`) | Did the code change the numbers? | The program's own earlier output, 1e-5 |
| Validated | `tests/golden/cases/` (this folder) | Are the numbers right? | An independent check, with a tolerance per value |

## Adding a spectral case

1. Copy `cases/spectral_fit/_TEMPLATE/` to `cases/spectral_fit/<case>/`.
2. Put the spectrum in as `input.txt`, named so the mode is read from it
   (`RM_...`, `PL_...`), or set `"mode"` in `settings.json`.
3. `settings.json`: the preset **as used**, i.e. the `to_dict()` of the
   material block (copy it from `data/materials.json`), so the case does not
   move when the shipped preset is tuned later.
4. `expected.json`: the values and tolerances, and a `reference` that names
   where they came from (literature, an Origin or WITec Project fit, a
   by-eye classification). Rule of thumb for tolerances (Quality Guide §2.5):
   smaller than the instrument resolution, larger than numerical noise, e.g.
   centre ±0.1 cm⁻¹, FWHM ±0.05.

Never use "whatever the program currently prints" as the reference; the
runner refuses a case with no reference, and the tripwire already covers
"unchanged". Measurement data is kept out of git by `.gitignore`; add each
case's `input.*` with an explicit exception the way
`tests/fixtures/golden_fit` is added.

## First batch to collect (Quality Guide §2.3, §3.6)

- Typical 1L, 2L and 3L WSe₂ Raman and PL.
- Hard cases: weak signal, heavy noise, overlapping peaks.
- Cases that once failed: the Voigt-FWHM bug (v3.4.0) and the E2g+A1g
  width cap (v5.14.0, the 6.21 cm⁻¹ pile-up).
- Bad input (shipped here): empty file, header without data, an image, a
  single column. The expected result is a specific error, never a number.
