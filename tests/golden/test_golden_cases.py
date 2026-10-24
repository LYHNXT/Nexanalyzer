"""Every validated golden case also runs under pytest, so CI and the hook see it."""

import pytest

from tests.golden import run as golden

CASES = golden.discover()


@pytest.mark.parametrize("case_dir", CASES, ids=[f"{c.parent.name}/{c.name}" for c in CASES])
def test_golden_case(case_dir):
    result = golden.run_case(case_dir)
    assert result.passed, "\n".join(result.lines)


def test_a_case_without_a_reference_is_refused(tmp_path):
    case = tmp_path / "bad_input" / "unreferenced"
    case.mkdir(parents=True)
    (case / "input.txt").write_bytes(b"")
    (case / "expected.json").write_text('{"error": "EmptyFile"}')
    result = golden.run_case(case)
    assert not result.passed and "reference" in result.lines[0]


def test_report_lists_every_case(tmp_path):
    results = [golden.run_case(c) for c in CASES[:2]]
    report = golden.write_report(results, tmp_path)
    text = report.read_text("utf-8")
    for r in results:
        assert r.name in text and r.reference in text
    assert (tmp_path / "golden_report.html").exists()
