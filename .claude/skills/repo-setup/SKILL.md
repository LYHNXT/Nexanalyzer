---
name: repo-setup
description: One-time setup of local testing, dev requirements and the GitHub Actions test workflow for this repository. Run manually with /repo-setup.
disable-model-invocation: true
argument-hint: "[phase number, e.g. 1, or empty to start at phase 1]"
---

# Repository setup: local tests + CI

Set up local testing and the GitHub Actions test workflow for this internal data analysis repository. If an argument is given ($ARGUMENTS), start at that phase; otherwise start at Phase 1.

## Ground rules

These exist because this is company code under review by humans before anything reaches `main`.

- Work only on branch `chore/ci-setup`; create it from `main` if missing. Never commit to `main`.
- Do not modify application source code. Only touch the files named in each phase.
- Never overwrite an existing config file (`pytest.ini`, `pyproject.toml`, `requirements-dev.txt`, `.gitignore`, an existing workflow). Show the proposed diff and wait for approval.
- Use the project virtual environment `.venv`; install nothing globally.
- Never print, copy or commit secrets, tokens, passwords or real measurement data.
- Do one phase at a time. At the end of each phase STOP and report in English: what you did, files changed, problems, and what you need from me. Wait for "continue".

## Phase 1: Local test environment

1. Inspect the repo: Python version, how dependencies are declared (`requirements.txt`, `pyproject.toml`, conda `environment.yml`, other), where tests live.
2. Create `.venv` and install dependencies the way the repo declares them.
3. Make `pytest`, `pytest-cov` and `hypothesis` available. Create `requirements-dev.txt` with them if it does not exist; otherwise propose additions.
4. Ensure `.venv/`, `htmlcov/`, `.coverage`, `coverage.xml` are in `.gitignore` (add only missing lines).
5. Run `pytest --cov --cov-report=term-missing`.
6. Report: Python version, pass/fail counts, overall coverage, the 5 least-covered modules, and any test needing network, hardware, absolute paths or real data.

## Phase 2: GitHub Actions workflow

1. Copy `assets/tests.yml` (next to this SKILL.md) to `.github/workflows/tests.yml`.
2. Set `python-version` to the version found in Phase 1, and adapt the install step if the repo uses conda or another method.
3. Keep `--cov-fail-under=0` for now; it will be raised later as tests improve.
4. Report what you changed and why.

## Phase 3: Commit and open a PR

1. Run the tests once more.
2. Commit with clear messages, e.g. `chore: add dev requirements and gitignore entries`, `ci: add GitHub Actions test workflow`.
3. Push the branch. If the `gh` CLI is installed and logged in, open a PR to `main` titled "Set up local tests and CI"; otherwise tell me to open it on GitHub.
4. STOP. Do not merge. I will confirm the `tests` check passes and set branch protection myself.
