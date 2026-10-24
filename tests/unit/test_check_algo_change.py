"""scripts/check_algo_change.py must block an unannounced algorithm change.

Each test builds a throwaway git repository with a `main` branch, makes a change
on a feature branch, and runs the guard against `main`.
"""
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "check_algo_change.py"

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git not available")


def _git(repo, *args):
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


def _write(repo, rel, text):
    path = repo / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


@pytest.fixture
def repo(tmp_path):
    _git(tmp_path, "init", "-q", "-b", "main")
    _git(tmp_path, "config", "user.email", "t@example.com")
    _git(tmp_path, "config", "user.name", "t")
    _write(tmp_path, "modules/spectra/processing/fitting.py", "x = 1\n")
    _write(tmp_path, "tests/fixtures/golden_fit/expected.json", "{}\n")
    _write(tmp_path, "core/version.py", '__version__ = "1.0.0"\n')
    _write(tmp_path, "CHANGELOG.md", "# Changelog\n")
    _write(tmp_path, "README.md", "hi\n")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "-m", "base")
    _git(tmp_path, "switch", "-q", "-c", "feature")
    return tmp_path


def _guard(repo):
    return subprocess.run([sys.executable, str(SCRIPT), "main"], cwd=repo, capture_output=True, text=True)


def _commit(repo):
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "change")


def test_a_non_algorithm_change_passes(repo):
    _write(repo, "README.md", "hello\n")
    _commit(repo)
    assert _guard(repo).returncode == 0


def test_an_algorithm_change_without_a_version_bump_fails(repo):
    _write(repo, "modules/spectra/processing/fitting.py", "x = 2\n")
    _commit(repo)
    result = _guard(repo)
    assert result.returncode == 1
    assert "version bump" in result.stdout


def test_an_algorithm_change_with_version_and_changelog_passes(repo):
    _write(repo, "modules/spectra/processing/fitting.py", "x = 2\n")
    _write(repo, "core/version.py", '__version__ = "1.1.0"\n')
    _write(repo, "CHANGELOG.md", "# Changelog\n\n## 1.1.0\n- x is 2; no numbers move.\n")
    _commit(repo)
    assert _guard(repo).returncode == 0


def test_a_locked_record_moved_without_saying_so_fails(repo):
    _write(repo, "tests/fixtures/golden_fit/expected.json", '{"a": 1}\n')
    _write(repo, "CHANGELOG.md", "# Changelog\n\n- tidied tests\n")
    _commit(repo)
    result = _guard(repo)
    assert result.returncode == 1
    assert "locked record" in result.stdout


def test_a_locked_record_moved_and_stated_passes(repo):
    _write(repo, "tests/fixtures/golden_fit/expected.json", '{"a": 1}\n')
    _write(repo, "CHANGELOG.md", "# Changelog\n\n- Golden-fit record regenerated: baseline fix.\n")
    _commit(repo)
    assert _guard(repo).returncode == 0


@pytest.mark.parametrize("path", [
    "modules/spectra/viz/peak_quality.py",
    "core/report/summary_figure.py",
    "modules/spectra/io/results_excel.py",
    "modules/spectra/io/results_csv.py",
    "core/blas.py",
])
def test_report_number_code_outside_processing_is_guarded(repo, path):
    """These compute what a report prints (fences, uniformity CV, tables, the
    BLAS kernel) without being refitted, so the golden fit cannot catch them."""
    _write(repo, path, "changed = True\n")
    _commit(repo)

    result = _guard(repo)

    assert result.returncode == 1
    assert path in result.stdout
