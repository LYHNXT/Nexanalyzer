"""scripts/qc_report/run_qc_mos2.py end to end, as an operator runs it.

Since v5.35.0 the runners call `modules.qc_report` directly instead of driving
the page through AppTest. This runs one on a synthetic Silicon sample (the
runner takes --material) with one pool worker, in a subprocess so the process
pool spawns the way it does from a shell.
"""

import subprocess
import sys
from pathlib import Path

from tests.integration.test_qc_report_flow import _write_multi_spectrum_raman, _write_om_frame

REPO = Path(__file__).resolve().parents[2]


def test_the_mos2_runner_builds_and_saves_a_report(tmp_path):
    sample = tmp_path / "SAMPLE01"
    sample.mkdir()
    for point in range(1, 10):
        _write_multi_spectrum_raman(sample / f"RM_{point}.txt", seed=point)
        _write_om_frame(sample / f"50X_{point}.png", seed=100 + point)

    result = subprocess.run(
        [sys.executable, str(REPO / "scripts" / "qc_report" / "run_qc_mos2.py"), str(sample),
         "--material", "Silicon", "--workers", "1", "--suffix", "_test"],
        capture_output=True, text=True, cwd=tmp_path, timeout=900,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    out = next(sample.glob("QC_Report_*_test"))
    names = sorted(p.name for p in out.iterdir())
    assert "SAMPLE01_QC_Report_1_Summary.png" in names
    assert "SAMPLE01_QC_Report_2_OM.png" in names
    assert "SAMPLE01_QC_Report_5_Raman.png" in names
    assert "SAMPLE01_QC_Report.xlsx" in names
    assert "raman: fitted" in result.stdout


def test_a_relative_sample_folder_resolves_against_the_callers_directory(tmp_path):
    result = subprocess.run(
        [sys.executable, str(REPO / "scripts" / "qc_report" / "run_qc_mos2.py"), "no_such_folder"],
        capture_output=True, text=True, cwd=tmp_path, timeout=120,
    )
    assert result.returncode != 0
    assert str(tmp_path / "no_such_folder") in result.stderr + result.stdout
