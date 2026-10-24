"""Algorithm-change guard: a change to how numbers are computed must say so.

Run by CI on every pull request and by the pre-push hook:

    python scripts/check_algo_change.py [BASE]      # BASE defaults to origin/main

It compares HEAD with the merge base of BASE and fails when

1. an **algorithm file** changed (see ALGO_PATHS) but ``core/version.py`` and
   ``CHANGELOG.md`` did not -- CLAUDE.md's rule that a behaviour change bumps the
   version and records what moved;
2. a **locked record** changed (the golden-fit spectra or expected numbers, or a
   test that pins algorithm output) and the new CHANGELOG text does not say so
   with one of LOCK_WORDS -- moving a locked number must be a stated decision,
   never a side effect of "making the tests pass".

It cannot tell a good algorithm change from a bad one; the golden-fit and
locked-number tests do that. It makes sure no such change arrives unannounced.
"""
import subprocess
import sys

#: Code and data that decide the numbers a report shows.
ALGO_PATHS = (
    "modules/spectra/processing/",
    "modules/spectra/models/",
    "modules/optical/processing/",
    "modules/runcard/processing/",
    "modules/datalog/processing/",
    "modules/qc_report/processing/",
    # Outside processing/ but still deciding reported numbers: the quality
    # figures' fences, uniformity CV and title values, the summary page's
    # tables and the workbook.
    "modules/spectra/viz/peak_quality.py",
    "core/report/summary_figure.py",
    "modules/spectra/io/results_excel.py",
    "modules/spectra/io/results_csv.py",
    "data/materials.json",
    "data/om_calibration.json",
    "data/datalog.json",
)
#: Records that pin algorithm output. Changing one moves the bar itself.
LOCKED_PATHS = (
    "tests/fixtures/golden_fit/",
    "tests/unit/test_golden_fit.py",
    "tests/unit/test_om_contrast.py",
)
#: Words the CHANGELOG addition must contain when a locked record moves.
LOCK_WORDS = ("golden", "locked", "regenerat")


def git(*args):
    return subprocess.run(["git", *args], check=True, capture_output=True, text=True,
                          encoding="utf-8", errors="replace").stdout


def main():
    base = sys.argv[1] if len(sys.argv) > 1 else "origin/main"
    merge_base = git("merge-base", base, "HEAD").strip()
    changed = [f for f in git("diff", "--name-only", merge_base, "HEAD").splitlines() if f]
    algo = [f for f in changed if f.startswith(ALGO_PATHS)]
    locked = [f for f in changed if f.startswith(LOCKED_PATHS)]
    problems = []

    if algo and not {"core/version.py", "CHANGELOG.md"} <= set(changed):
        problems.append(
            "Algorithm files changed without a version bump and CHANGELOG entry:\n    "
            + "\n    ".join(algo)
            + "\n  Bump core/version.py and add a CHANGELOG.md entry saying which numbers move "
              "(or that none do)."
        )
    if locked:
        added = "\n".join(line[1:] for line in git("diff", merge_base, "HEAD", "--", "CHANGELOG.md").splitlines()
                          if line.startswith("+") and not line.startswith("+++")).lower()
        if not any(w in added for w in LOCK_WORDS):
            problems.append(
                "A locked record changed:\n    " + "\n    ".join(locked)
                + "\n  Moving a locked number must be stated: the CHANGELOG addition has to say so "
                  f"(one of {', '.join(LOCK_WORDS)}), e.g. 'golden-fit record regenerated because ...'."
            )

    print(f"algo guard: {len(changed)} files changed vs {base}; {len(algo)} algorithm, {len(locked)} locked")
    if problems:
        print("\nFAILED\n- " + "\n- ".join(problems))
        return 1
    print("OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
