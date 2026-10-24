# Development process: branch, pull request, CI, release

How a change gets from a laptop to the app on every PC, and how the numbers the
app reports are protected on the way. Short version:

```
branch off main → commit (hook: golden fit) → push (hook: golden fit + algo guard)
→ pull request (template) → CI green (3 gates) → checked on a real wafer → merge
→ every start.bat pulls main
```

## 1. One-time setup per clone

```bash
git config core.hooksPath .githooks    # enables the commit and push hooks
```

## 2. Make the change on a branch

- Never commit to `main` directly. `git switch -c <short-name> origin/main`.
- Algorithm change (anything under `modules/*/processing/`, `modules/spectra/models/`,
  or `data/materials.json` / `om_calibration.json` / `datalog.json`):
  - bump `core/version.py` and add a CHANGELOG entry in the **same** change,
    saying which numbers move, on which data, by how much, or that none do;
  - make a new fitting behaviour **opt-in per preset**, so the frozen golden
    preset does not move;
  - add or extend a test that pins the new behaviour.

## 3. What the hooks check (locally)

| Hook | Checks |
| --- | --- |
| pre-commit | `tests/unit/test_golden_fit.py`: DDAA10 RM_1..3 (Raman) and HADI51 / VA1P012504023 PL_5 (PL), each under `tests/fixtures/golden_fit/<set>/`, must reproduce their `expected.json` to 1e-5 |
| pre-push | the same, plus `scripts/check_algo_change.py origin/main` |

## 4. Open a pull request into `main`

The template asks: do numbers move, was the version bumped, was a locked record
changed and why, and was it checked on a real wafer. Fill it in honestly; "no
numbers move" is a claim CI and reviewers can check.

## 5. CI: three gates (`.github/workflows/ci.yml`)

| Gate | Runner | What fails it |
| --- | --- | --- |
| **Algorithm-change guard** | Ubuntu, ~30 s | an algorithm file changed without `core/version.py` + `CHANGELOG.md`; or a locked record (golden-fit fixtures, `test_golden_fit.py`, `test_om_contrast.py`) changed without the CHANGELOG saying so ("golden", "locked" or "regenerated") |
| **Golden fit + locked numbers** | Windows | ruff; the golden fit (`NEXANALYZER_REQUIRE_GOLDEN=1`, so missing fixtures fail instead of skipping); OM contrast, zero-line, peak-metrics and fitting tests |
| **Full test suite** | Windows | any of the ~1,570 tests |

CI installs exactly the versions in `requirements-lock.txt`. The golden fit is
compared to 1e-5, and a numpy/scipy/lmfit upgrade can move numbers on its own;
pinning means a failure points at *our* change. Upgrading a library is its own
pull request: regenerate the lock, run the suite, and if the golden numbers move,
regenerate them and say so in the CHANGELOG.

## 6. Moving a locked number on purpose

1. `python tests/unit/test_golden_fit.py --regenerate` (or update the locked test).
2. In the same commit, a CHANGELOG line: *"Golden-fit record regenerated: <why>,
   <which numbers, by how much>."*
3. The guard accepts it because the CHANGELOG says so; the reviewer judges the why.

## 7. Merge

- Merge only when all three gates are green **and** the change was tried in the
  app on a recent wafer (the template's last box).
- Use a normal merge commit, so each version's commit stays visible in `main`.
- Delete the branch after merging.

## 8. After the merge

- Every PC running `start.bat` pulls `origin main` (`git pull --ff-only`) on its
  next start and gets the change.
- Tag versions that change numbers: `git tag v5.25.0 && git push origin v5.25.0`,
  so a report can always be traced to the code that made it.
- The customer edition (`C:\dev\NexAnalyzer_TSMC`) is built separately and does
  not update itself.

## 9. Protecting `main` on GitHub (one-time, repository owner)

Settings → Branches → Add branch ruleset (or classic rule) for `main`:

- Require a pull request before merging.
- Require status checks to pass: **Algorithm-change guard**, **Golden fit +
  locked numbers**, **Full test suite**; require branches to be up to date.
- Block force pushes and deletions.
- "Require review from Code Owners": only once a second reviewer is in
  `.github/CODEOWNERS`. GitHub does not let authors approve their own pull requests.

Rulesets on a **private** repository need a paid GitHub plan (Pro/Team). On the
free plan the CI still runs and shows red/green on every pull request; it just
cannot *block* the merge button, so the rule "don't merge red" is a habit.

CI minutes: Windows jobs count double against the private-repo allowance. A full
run is roughly 20–30 Windows minutes.
