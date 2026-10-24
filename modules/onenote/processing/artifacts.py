"""What each page has ready to publish, and what it is called on the page.

The three source pages keep their results in three separate session-state
namespaces that never see each other, and they are filled at different times:
a runcard exists before the wafer is grown, the QC report a day or two after
it is measured. So this module reports what is *currently* available rather
than assuming a sitting where all three are present — an upload is one
artifact at a time, appended to the wafer's page as each becomes ready.

Nothing here touches the network. It reads state and hands back bytes, so the
page can show exactly what would be uploaded before any sign-in happens.
"""

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

#: The three artifact kinds, used as log keys and as section headings.
QC_REPORT = "QC Report"
RUNCARD = "Runcard"
DATALOG = "Datalog"

#: The QC Report's eight figures, in the order they are shown on the page and
#: written to disk. Same ordering rationale as `qc_report.processing.build.
#: REPORT_FIGURES`: alphabetical order would put Summary last and
#: interleave the two techniques, so a reader would meet them in an order
#: nobody chose. All eight go inline. Keep in step with `REPORT_FIGURES`: the
#: domain-size grid was added there in v5.16.0 and missed here until v5.18.0.
_QC_FIGURES = (
    ("Summary", "summary_png"),
    ("OM", "om_png"),
    ("OM diagnostic", "om_diagnostic_png"),
    ("Domain size", "domain_grid_png"),
    ("Raman", "raman_grid_png"),
    ("Raman stats", "raman_stats_png"),
    ("PL", "pl_grid_png"),
    ("PL stats", "pl_stats_png"),
)


@dataclass(frozen=True)
class Image:
    """One PNG destined for the page, inline."""

    caption: str
    data: bytes


@dataclass(frozen=True)
class Attachment:
    """One file destined for the page as an attachment."""

    filename: str
    data: bytes
    content_type: str


@dataclass(frozen=True)
class Payload:
    """Everything one upload puts on the page, before it is rendered to HTML."""

    kind: str
    wafer_id: str
    images: tuple[Image, ...] = ()
    attachments: tuple[Attachment, ...] = ()
    tables: tuple[tuple[str, object], ...] = ()  # (caption, DataFrame)
    notes: tuple[str, ...] = field(default=())

    @property
    def is_empty(self) -> bool:
        return not (self.images or self.attachments or self.tables)


def _same_path(a, b) -> bool:
    if not a or not b:
        return False
    try:
        a, b = Path(a).resolve(), Path(b).resolve()
    except OSError:
        pass
    # normcase: the same folder picked once by dialog and once from the tool
    # layout can differ in case on Windows.
    return os.path.normcase(str(a)) == os.path.normcase(str(b))


def qc_report_built_for(state: dict, optical_folder) -> bool:
    """Whether the QC Report's current folder is this wafer's optical folder.

    The QC Report state outlives a wafer change on this page — it is another
    page's namespace — so without this check a report built for one wafer was
    offered as "ready" under every wafer picked after it.
    """
    return _same_path(state.get("folder"), optical_folder)


def runcard_figures_for(state: dict, runcard_path) -> list:
    """The Runcard page's drawn charts that are this wafer's recipe.

    `figures` is keyed by recipe path and holds whatever is ticked on the
    Runcard page, for any wafer; only the one this wafer resolves to counts.
    """
    figures = state.get("figures") or {}
    return [path for path in figures if _same_path(path, runcard_path)]


def qc_report_payload(state: dict, wafer_id: str) -> Optional[Payload]:
    """The QC Report's eight figures inline, plus the workbook attached.

    Returns None when the report has not been built — the figures are derived
    keys that every input change drops, so "not built yet" is the normal
    state of a freshly-pointed folder rather than an error.
    """
    images = tuple(
        Image(caption, state[key])
        for caption, key in _QC_FIGURES
        if state.get(key)
    )

    attachments = ()
    if state.get("xlsx_bytes"):
        attachments = (
            Attachment(
                f"{wafer_id}_QC_Report.xlsx",
                state["xlsx_bytes"],
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            ),
        )

    if not images and not attachments:
        return None

    return Payload(
        kind=QC_REPORT,
        wafer_id=wafer_id,
        images=images,
        attachments=attachments,
    )


def runcard_payload(state: dict, wafer_id: str, path: str) -> Optional[Payload]:
    """One ticked recipe's profile chart, rendered to PNG.

    Looked up by recipe *path*, which is what the Runcard page keys `figures`
    on — looking it up by run id, as this did before v5.18.0, never matched.

    The Runcard page holds live Plotly figures rather than bytes, so this is
    where the render happens — via the same `export_figure_png` the page's own
    Save button uses, so the uploaded image is the one the operator saw.
    Imported lazily because it pulls in kaleido, which is slow to import and
    not needed to render the rest of this page.
    """
    figures = state.get("figures") or {}
    figure = figures.get(path)
    if figure is None:
        return None

    from core.io.export import export_figure_png
    from modules.runcard.io.parser import derive_run_id

    run_id = derive_run_id(path)
    png = export_figure_png(figure, width=1200, height=700, scale=2.0)

    return Payload(
        kind=RUNCARD,
        wafer_id=wafer_id,
        images=(Image(f"Recipe profile — {run_id}", png),),
        notes=(f"Recipe: {run_id}",),
    )


def datalog_payload(
    state: dict,
    wafer_id: str,
    chart_png: Optional[bytes],
    violations=None,
    source_filename: str = "",
) -> Optional[Payload]:
    """The datalog's chart and violations table — never the raw CSV.

    A ~6900-row CSV attached to a OneNote page is write-only storage: nobody
    opens it there, and it is already on the share the tool wrote it to. What
    the team needs on the page is the picture and the verdict, so the CSV
    contributes only its filename, as text, so the run can be traced back.
    """
    images = (Image("Datalog", chart_png),) if chart_png else ()
    tables = (("Threshold violations", violations),) if violations is not None else ()

    if not images and not tables:
        return None

    notes = (f"Source: {source_filename}",) if source_filename else ()

    return Payload(
        kind=DATALOG,
        wafer_id=wafer_id,
        images=images,
        tables=tables,
        notes=notes,
    )
