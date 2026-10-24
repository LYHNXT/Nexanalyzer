"""What each page offers the OneNote page, read straight from its state.

These are pure state-to-bytes reads with no network, which is what lets the
page show exactly what would be uploaded before anyone signs in.
"""

from modules.onenote.processing import artifacts


def _qc_state(**overrides) -> dict:
    """A QC Report state dict with every figure key present but empty."""
    state = {
        "summary_png": None,
        "om_png": None,
        "om_diagnostic_png": None,
        "raman_grid_png": None,
        "raman_stats_png": None,
        "pl_grid_png": None,
        "pl_stats_png": None,
        "xlsx_bytes": None,
    }
    state.update(overrides)
    return state


def test_an_unbuilt_report_offers_nothing():
    """"Not built yet" is the normal state of a freshly-picked folder.

    Every figure is a derived key that an input change drops, so this is not
    an error and must not read as one.
    """
    assert artifacts.qc_report_payload(_qc_state(), "HADH51") is None


def test_all_eight_figures_go_inline():
    state = _qc_state(**{
        key: b"png" for _, key in artifacts._QC_FIGURES
    })

    payload = artifacts.qc_report_payload(state, "HADH51")

    assert len(payload.images) == 8
    assert payload.kind == artifacts.QC_REPORT


def test_figures_keep_the_page_s_own_order():
    """Summary first, then OM, then Raman, then PL — never alphabetical.

    Same rationale as `REPORT_FIGURES` (modules/qc_report): alphabetical order
    puts Summary last and interleaves the two techniques, so a reader meets
    them in an order nobody chose.
    """
    state = _qc_state(**{key: b"png" for _, key in artifacts._QC_FIGURES})

    captions = [image.caption for image in artifacts.qc_report_payload(state, "X").images]

    assert captions == [
        "Summary", "OM", "OM diagnostic", "Domain size",
        "Raman", "Raman stats", "PL", "PL stats",
    ]


def test_a_partial_report_offers_what_it_has():
    """Only the figures that exist — a PL-less sample is not a failure."""
    state = _qc_state(summary_png=b"png", raman_grid_png=b"png")

    payload = artifacts.qc_report_payload(state, "HADH51")

    assert [image.caption for image in payload.images] == ["Summary", "Raman"]


def test_the_workbook_is_attached_not_inline():
    state = _qc_state(summary_png=b"png", xlsx_bytes=b"xlsx")

    payload = artifacts.qc_report_payload(state, "HADH51")

    assert len(payload.images) == 1
    assert payload.attachments[0].filename == "HADH51_QC_Report.xlsx"


def test_the_workbook_alone_is_still_a_payload():
    payload = artifacts.qc_report_payload(_qc_state(xlsx_bytes=b"xlsx"), "HADH51")

    assert payload is not None
    assert not payload.images
    assert payload.attachments


def test_an_undrawn_recipe_offers_nothing():
    assert artifacts.runcard_payload({"figures": None}, "HADH51", "HADH51") is None
    assert artifacts.runcard_payload({"figures": {}}, "HADH51", "NOPE") is None


def test_the_datalog_never_carries_the_raw_csv():
    """A 6900-row CSV on a OneNote page is write-only storage.

    It is already on the share the tool wrote it to; the page gets the
    picture, the verdict, and the filename to trace it back.
    """
    payload = artifacts.datalog_payload(
        {}, "HADH51", chart_png=b"png", violations=[], source_filename="2026-09-03~HADH51.csv"
    )

    assert len(payload.images) == 1
    assert payload.tables[0][0] == "Threshold violations"
    assert "2026-09-03~HADH51.csv" in payload.notes[0]
    assert not payload.attachments


def test_a_datalog_with_neither_chart_nor_table_offers_nothing():
    assert artifacts.datalog_payload({}, "HADH51", chart_png=None) is None


def test_is_empty_reports_a_payload_with_nothing_in_it():
    assert artifacts.Payload(kind="X", wafer_id="Y").is_empty
    assert not artifacts.Payload(
        kind="X", wafer_id="Y", images=(artifacts.Image("c", b"d"),)
    ).is_empty


def test_qc_figures_match_the_qc_report_page():
    """The domain-size grid joined the QC Report at v5.16.0 and was missing here."""
    from modules.qc_report.processing.build import REPORT_FIGURES

    assert [key for _, _, key in REPORT_FIGURES] == [key for _, key in artifacts._QC_FIGURES]


def test_a_report_built_for_another_wafer_is_not_ready(tmp_path):
    mine, other = tmp_path / "HADH51", tmp_path / "HADH50"

    assert artifacts.qc_report_built_for({"folder": str(mine)}, mine)
    assert not artifacts.qc_report_built_for({"folder": str(other)}, mine)
    assert not artifacts.qc_report_built_for({"folder": None}, mine)
    assert not artifacts.qc_report_built_for({"folder": str(mine)}, None)


def test_only_this_wafer_s_recipe_counts(tmp_path):
    mine, other = tmp_path / "HADH51.csv", tmp_path / "HADH50.csv"
    state = {"figures": {str(mine): object(), str(other): object()}}

    assert artifacts.runcard_figures_for(state, mine) == [str(mine)]
    assert artifacts.runcard_figures_for(state, None) == []
    assert artifacts.runcard_figures_for({"figures": None}, mine) == []
