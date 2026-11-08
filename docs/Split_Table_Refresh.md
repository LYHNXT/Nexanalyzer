# Split Table: Refresh now

Current as of 5.47.0+local.4 (2026-11-08). The Split Table page's **Refresh
now** button, VB1P01 only. HA1P01's table is still built by its SPLITTABLE
scripts.

## What one press does

`modules/splittable/io/refresh.py`, in this order. Each step reads only what
changed since the last press.

| Step | Reads | Writes |
| --- | --- | --- |
| 1. QC summary | `OPTICAL/<YYYYMM>/<wafer>/*_QC_Report.xlsx` new or changed (modified time, size) | `OPTICAL/VB_QC_summary.xlsx` |
| 2. Run list | `DATALOG/20YY_MM/*.csv` new or changed; `DATALOG/runcard_tags.json` | `SPLITTABLE/datalog_runs.csv` |
| 3. Rows | every runcard (cached by modified time), the Milestone sheet | -- |
| 4. Write | the workbook as it is now | `VB_SplitTable_from_runcards.xlsx`, `wafer_w_source.csv` |

The cache of read logs and parsed cards is `SPLITTABLE/_cache/refresh_cache.pkl`.
Deleting it is safe: the next press rebuilds it, which costs one read of
each log not cited in `datalog_runs.csv`. A full re-read of all 1972 logs
took eight minutes from OneDrive on 2026-11-08.

The Milestone sheet is `../HA1P01/SPLITTABLE/Milestone and Schedule.xlsx`,
relative to the VB1P01 folder, so it resolves on any PC that syncs the
Data Collection share.

## The QC summary

`VB_QC_summary.xlsx` has these sheets:

- **QC_Reports** — one row per report file and preset, with the column names
  of the old summaries (`Raman_E2g+A1g_FWHM`, `OM_Reference_pct`, ...).
  - A report regenerated with the same preset replaces its row.
  - A report regenerated with another preset adds a row and keeps the old one,
    marked `Current` = False. Every preset a wafer was reported with stays
    recorded.
  - The wafer is the folder, not the sample name: `202610/VBCA00` holds
    VBBG00's report too.
- **Legacy** — a frozen copy of the values the split table used before the
  refresh existed, made once. It is never rewritten.
  - From `OM_Raman_PL_summary.xlsx` [`1_WSe2_-5-500_all`] for every wafer.
  - For VBCA, also `VB_Raman_PL_summary_WSe2_QC.xlsx` [`3_WSe2_QC`] plus
    `OM_WSe2_QC.csv`.
  - One row per wafer and fit mode.
- **Missing** — wafer folders that have no QC Report.

OM columns come from a report's `OM_Points` sheet, the same route as
`OPTICAL/_tools/build_om_wse2_qc.py`:

- coverages are the frame means;
- Std is the sample standard deviation;
- `OM_Sigma_Pct_Mode` is the mean of `Sigma_Noise / Green_Mode`;
- `OM_Discriminable` is true when the smaller cut is at least 2 times that.

On VBCA00 and VBCA03 this reproduces the legacy values to the legacy's three
decimals.

Since 5.47.0+local.4 a report's Summary sheet also states its `Layer` and the
`NexAnalyzer` version. For older reports the layer is read from
`OM_Points.Reference_Layer`.

## Which QC a row shows

`processing/qc_select.py`, user's order (2026-11-08):

1. A fit mode containing "QC". Of two, the newer report wins (report date,
   then file time). A legacy row has no date, so it loses a tie to any dated
   report.
2. Otherwise, the newest report with any other preset.
3. Otherwise, the legacy value.

The row's `QC_Source`, `QC_Fit_Mode`, `QC_Layer`, `QC_Version` and
`QC_Report_Date` say which source won. `OM_Reference_pct` is the area at
`QC_Layer`, so a 1L row and a 2L row measure different things. VBBF66-70
switched from 2L legacy values to 1L reports on the first refresh.

**Gaps are filled block by block** (user, 2026-11-08: "fill them, add a note
in the remark; if this happens again, do so"). A block is `OM` or a
technique and peak (`PL_Raw`, `Raman_C/LB`, ...). When the chosen row has
no column of a block, the whole block comes from the best-ranked other row
that has it, and `Remark` gets `QC filled: <block> from <report> (<fit mode>,
<date>)` after Milestone's own remark. A block is never put together from
two rows, and `QC_Source`/`QC_Fit_Mode`/`QC_Layer` stay the chosen row's. On
the first fill, VBCA00-20 (legacy 3_WSe2_QC) took `PL_Raw` and
`Raman_C/LB` from their WSe2 / WSe2_truncate reports.

## W source

`processing/vb_build.w_source_for` tries these in order:

1. The Milestone W column.
2. Where that is NA or blank, the run-ID rule (`core/io/precursor_rules.py`,
   Material Presets > Precursor by run ID).
3. Otherwise, the series majority.

`W_source_rule` names the rule that was used. `Precursor_check` names a
Milestone value that the run-ID rule disagrees with.

`W_source_recipe` (the MFC-2 bubbler rule) and `wafer_w_source.csv` are
unchanged from `build_vb_splittable.py`.

## Writing in place

`io/table_writer.py`:

- **Rows** are matched by run:
  - a logged run by `Process_Start`, because a log can be renamed but its
    start time cannot change;
  - any other row by `WaferID`.
- **Hand edits:** a hidden `_Computed` sheet holds what the last refresh
  computed. A visible cell that differs from it was edited by hand.
  - The refresh keeps that cell and names its column in `Hand_Edits`, with
    "(now x)" when the sources now say something else.
  - Columns and rows nobody's refresh made are carried over as they are.
- **The first refresh** has no `_Computed` sheet to compare against. It takes
  the computed values and lists every cell it changed in `Refresh_Changes`.
  The old file is in `_backups/`.
- **Header styles** are copied from the old header, by column name. A new
  column gets its group's colour.
- **Busy checks:** nothing is written while Excel has the file open (its
  `~$` owner file exists, or the file refuses a write handle), or while another
  refresh holds `<table>.refresh.lock`. A lock older than 30 minutes is
  treated as abandoned.
- **Safe write:** the new workbook is saved to a temp file. The old one is
  copied to `_backups/<name>.bak_<time>` before the swap.
- **Nothing new, nothing written:** when no row comes or goes and no cell
  differs from the sheet, the workbook is not rewritten and no backup is made.

## Where a press spends its time

Measured on 2026-11-08 with a warm cache (1632 rows):

| Step | Time |
| --- | --- |
| QC Reports, datalogs, Milestone and runcards (what changed) | ~1 s |
| Build rows, read the workbook, merge | ~5 s |
| Write the workbook (openpyxl load + save) and the QC summary | ~7 s |

So a press costs about 7 s when nothing changed and about 15 s when
something did, whatever changed. Limiting a press to some folders or some
kinds of data would save under a second; the reading is already incremental.
Until 5.47.0+local.4's fix, greying the non-growth rows indexed
each row (`ws[i]`), which made openpyxl rescan the sheet each time and cost
another ~6 s.

## Datalogs named twice

VB1P01's copier re-creates a renamed log under its old name. After the
VBCA→VBAC fix of 2026-11-08, for example, both `~VBCA02.csv` and
`~VBAC02.csv` exist for one run.

Runs are keyed by start time. Of two tagged files, the refresh cites the one
`runcard_tags.json` records. The old name keeps coming back until the file is
renamed on the tool PC too.

## Differences from build_vb_splittable.py

The old script and `match_datalogs.py` (copies in
`SPLITTABLE/original version/`) rebuilt everything on every run. The refresh
differs in these ways:

- **Matching:** an untagged run matched once keeps its name. It is not
  re-matched jointly with later runs. An unmatched run is retried on each
  refresh.
- **Removed:** the `VBCAxx-2608` rename and the hard-coded VA1P0125/26 serial
  rule. The serial rule is now two run-ID rules.
- **Row order:** by Process_Start, or the card's save time for a row without
  one. Previously, logged runs came first and unlogged wafers were appended.
- **QC columns:** QC comes from the summary, and five QC columns were added,
  plus `W_source_rule`, `Precursor_check` and `Hand_Edits`.
- **Still the same:** the spec gates (`*_In_Spec`), evaluated as the old
  script did.

Running `build_vb_splittable.py` again rebuilds the table from scratch. That
drops hand edits and the QC Report columns.
