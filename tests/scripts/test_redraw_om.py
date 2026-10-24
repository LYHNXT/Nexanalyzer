"""scripts/qc_report/redraw_om.py, run as an operator runs it.

The QC runners are not imported by the app, so nothing else would notice them
break. These call `main` on a synthetic sample folder: a tenth OM frame
(HADH71/75 carry one) must be left out exactly as the QC Report page leaves
it out, and a folder with no frames at P1-P9 must stop with a sentence, not
a ValueError from deep inside the figure builder.
"""

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

from tests.integration.test_qc_report_flow import _write_om_frame

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts" / "qc_report" / "redraw_om.py"


def _load():
    spec = importlib.util.spec_from_file_location("redraw_om", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_it_writes_both_figures_from_points_one_to_nine(tmp_path, monkeypatch, capsys):
    sample = tmp_path / "SAMPLE"
    sample.mkdir()
    for point in range(1, 11):
        _write_om_frame(sample / f"50x_{point}.png", seed=100 + point)
    out = tmp_path / "out"

    _load().main(str(sample), "NEW", "2L", "Silicon", "50x", str(out))

    written = sorted(p.name for p in out.iterdir())
    assert written == ["NEW_QC_Report_2_OM.png", "NEW_QC_Report_3_OM_diagnostic.png"]
    assert all((out / name).read_bytes().startswith(b"\x89PNG") for name in written)
    assert capsys.readouterr().out.count(": 9 frames") == 2


def test_a_folder_without_grid_frames_exits_with_a_message(tmp_path):
    sample = tmp_path / "EMPTY"
    sample.mkdir()

    with pytest.raises(SystemExit, match="No 50x OM frames"):
        _load().main(str(sample), "NEW", "2L", "Silicon", "50x", str(tmp_path / "out"))


def test_the_wrong_argument_count_prints_the_usage():
    result = subprocess.run([sys.executable, str(SCRIPT), "only-one"],
                            capture_output=True, text=True, cwd=REPO)
    assert result.returncode != 0
    assert "SAMPLE_DIR NEW_TITLE" in result.stderr
