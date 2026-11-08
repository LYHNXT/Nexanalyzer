# Changelog

All notable changes to NexAnalyzer will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## Local additions (on top of 5.47.0; app version 5.47.0+local.4)

Installation-specific changes carried on top of upstream. `core/version.py`
reports them as `<upstream>+local.N`: the upstream release they sit on, plus a
build tag that can never collide with a future upstream number.

Rebuilt on 2026-10-30 from upstream 5.46.0 rather than merged: the previous
local line (`5.13.0+local.8`, kept on branch `backup/local-v5.13.0-local.8`)
conflicted with upstream in 20 files. Three of its changes are carried over;
the rest are dropped in favour of upstream's own versions:

- the Raman axis correction to the laser line (Local 5.7.2) -- upstream's
  zero-line calibration (5.24.0) does the same job, per preset;
- the linked-peak engine (Local 5.7.0) -- upstream's energy gaps (5.46.0) and
  dominant peak (5.26.0) express the X-T linked preset, below;
- the QC Report figure changes (Local 5.7.1, 5.7.3-5.7.5) -- upstream rebuilt
  those figures;
- the in-app edits to WSe2_truncate (Raman 110-320 cm-1, a 1L optical block,
  2L -9.0/+4.5) -- upstream's WSe2_truncate is used as is.

### 5.47.0+local.4 - 2026-11-08

#### Added

- **Split Table: Refresh now** (VB1P01). One press brings
  `VB_SplitTable_from_runcards.xlsx` up to date, reading only what changed:
  new or regenerated QC Reports into `OPTICAL/VB_QC_summary.xlsx` (one row
  per wafer and preset; a report regenerated with another preset keeps the
  old row), new datalogs named by tag or matched to a runcard (reads are
  cached, so a press takes seconds instead of the eight minutes a full match
  takes), runcards and the Milestone sheet. The workbook is updated in place:
  cells edited by hand are kept and named in a new `Hand_Edits` column (a
  hidden `_Computed` sheet holds what the refresh computed), columns and rows
  added by hand are carried, the old file is copied to `_backups/` first, and
  nothing is written while Excel has the file open or another refresh is
  running. Press-only, never on a timer. Ported from Ang-Yu Lu's
  `build_vb_splittable.py` / `match_datalogs.py`
  (`modules/splittable/processing/vb_build.py`, `datalog_runs.py`); those
  scripts stay in SPLITTABLE because HA1P01's `build_ha_w_source.py` imports
  them, and `update_vb_splittable.py` beside them runs the refresh without
  the app. HA1P01 is unchanged. See docs/Split_Table_Refresh.md.
- **QC choice per wafer** (`processing/qc_select.py`, user 2026-11-08): a
  preset containing "QC" first (the newer of two), else the newest QC Report,
  else the frozen legacy value (`OM_Raman_PL_summary.xlsx`, and
  `VB_Raman_PL_summary_WSe2_QC.xlsx` + `OM_WSe2_QC.csv` for VBCA, as the old
  builder used them). New columns `QC_Source`, `QC_Fit_Mode`, `QC_Layer`,
  `QC_Version`, `QC_Report_Date` say which. The table now also carries the
  PL Raw spectrum's `PL_Raw_Center`, `PL_Raw_FWHM` (the width the PL spec is
  judged on) and `PL_Raw_Intensity`; they are blank where the legacy value is
  shown, because the old summaries never had them -- see the gap fill below.
  OM numbers are taken from the
  report's `OM_Points` the way `build_om_wse2_qc.py` takes them; on VBCA00
  and VBCA03 they reproduce the legacy WSe2_QC values to the legacy's three
  decimals.
- **QC gaps are filled from the next source** (user, 2026-11-08). When the
  chosen QC row has none of a measurement block (`PL_Raw`, `Raman_C/LB`,
  `OM`, ...), the whole block comes from the next-ranked row that has it,
  and the row's `Remark` says `QC filled: <block> from <report> (<fit mode>,
  <date>)`. VBCA00-20 (legacy 3_WSe2_QC) take `PL_Raw` and `Raman_C/LB`
  from their WSe2 / WSe2_truncate reports.
- **A press with nothing new writes nothing**: no cell changed means no
  rewrite and no backup (about 7 s instead of 15 s).
- **W_source uses the run-ID rules**: Milestone's W column, then the
  *Precursor by run ID* rule where it is NA or blank, then the series
  majority. New columns `W_source_rule` (which one) and `Precursor_check`
  (Milestone disagreeing with the rule).
- **The QC Report workbook's Summary sheet states the reference layer and the
  NexAnalyzer version** that wrote it, after Sample / Material / Date (which
  keep their rows), so a summary collected from many reports can say which fit
  made each number. No number in the report moves.

#### Changed

- **Numbers that move in the VB split table** on its first refresh (all
  listed cell by cell in the workbook's `Refresh_Changes` sheet): VBBF66-70
  take their QC Reports (preset WSe2, judged at 1L) instead of the legacy
  values (judged at 2L); VBCA25-41 gain QC from their reports; VBCA00-24
  are unchanged (legacy 3_WSe2_QC still outranks their WSe2 / WSe2_truncate
  reports). VBCA's W_source becomes W foil (rule VBC) where Milestone says
  NA; it was the series majority, WO3, carried from the August typo series.
  Rows follow Process_Start, else the card's save time. No fitted number
  moves.

#### Fixed

- **The refresh's write took ~6 s longer than it needed**: greying the
  non-growth rows indexed each row with `ws[i]`, and openpyxl rescans the
  whole sheet for its width on every such call. One pass over the rows now.
- **The August 2026 VBAC runs are their own rows again.** Their logs were
  tagged VBCA02-17 (a typo, renamed to VBAC on 2026-11-08) and joined as
  `VBCAxx-2608`; that workaround is gone. VB1P01's copier re-creates the old
  `~VBCAxx` names beside the renamed files, so of two tagged files for one
  run the refresh cites the one `runcard_tags.json` records.

### 5.47.0+local.3 - 2026-11-08

#### Added

- **Precursor by run ID** -- a new card on the Material Presets page, after
  *Objective calibration*: which W precursor a run used, read off its run ID.
  Rules are grouped by the run ID's first two letters (the system: VA, VB, ...,
  because the same series letters can mean different precursors on different
  tools), and inside a group the longest matching prefix wins. Stored in the
  committed `data/precursor_rules.json` (module `core/io/precursor_rules.py`),
  apart from the material presets: a fit preset changes with the fitting, a
  run's precursor never does. Defaults (user, 2026-11-08): VA -- VAA W foil,
  VAB WO3, VAHA W(CO)6, VA1P0125 W(CO)6, VA1P0126 W foil; VB -- VBA WO3,
  VBB W(CO)6, VBC W foil. Nothing reads the rules yet: the split-table refresh
  that will (Milestone W column, then this rule where it is NA or blank, then
  the series majority) is the next step. No number moves.

### 5.47.0+local.2 - 2026-11-08

#### Fixed

- **The Split Table page reads a workbook that is open in Excel.** It showed
  `Could not read VB_SplitTable_from_runcards.xlsx: [Errno 13] Permission
  denied` instead: Excel and OneDrive hold the file with delete access, and
  Python's `open()` does not share delete, so Windows refused it. The read now
  shares delete as Explorer does (`core/io/shared_read.py`) and gets the last
  saved version. No number moves.

### 5.47.0+local.1 - 2026-11-03

#### Changed

- **Moved onto upstream 5.47.0** (the local commit replayed on top of it; the
  5.46.0 line is kept on branch `backup/local-v5.46.0-local.1`). Only
  `core/version.py` conflicted.
- **`WSe2 X-T linked` is switched off (`enabled: false`)**, like the eight
  presets upstream 5.47.0 switched off, so the dropdowns offer only `MoS2_QC`
  and `WSe2_QC` and upstream's `test_only_the_two_qc_presets_are_offered`
  holds. Turn it back on from the Material Presets page when needed. No
  number in the preset moves.

### 5.46.0+local.1 - 2026-10-30

#### Added

- **µm/px from a scale-bar image** (was Local 5.7.6). The Material Presets
  page's *Objective calibration* card has a **Measure from a scale-bar image**
  expander. Upload an OM image exported with a burned-in scale bar and the bar
  is found and measured (sub-pixel, light-on-dark or dark-on-light), and its
  label ("20 µm", "500 nm", ...) is read by template matching against DejaVu
  Sans / Arial glyphs -- no Tesseract install needed. If the label can't be
  read, the operator types the length; both lengths stay editable. A
  downsized export is scaled to the native camera width (default 2240 px),
  because the calibration is per native pixel: VBCA13's 1024-px
  `Scale bar.png` (193 px = 20 µm) gives 0.10363 µm/px on the export and
  **0.04737 µm per native px**. The stored value for the chosen magnification
  is shown with the % difference before **Save** writes that one entry.
  Module `modules/optical/processing/scale_bar.py`, unchanged from Local 5.7.6.
- **`WSe2 X-T linked` preset** (was Local 5.7.0), rebuilt on upstream's own
  constraints. Raman and PL blocks are copies of upstream 5.46.0's WSe2 (so
  the Raman block now carries 2ZA(M)'s 1.64 width and the PL despike
  threshold is 10, not 8). On PL: `energy_gaps` Exciton → Trion **30 meV,
  tolerance 0** (a fixed gap: the trion's centre is computed from the
  exciton's), `dominant_peak` Exciton (trion intensity ≤ exciton intensity),
  and `fit_starts` 5. The Trion template starts at 769.0493 nm, which is
  30 meV below the Exciton's 755 nm, as the gap check requires. The fixed gap
  lives on this preset only; WSe2 and WSe2_QC are untouched. On the two PL
  golden spectra the fitted gap is 30.0000 meV and I(Trion)/I(Exciton) is
  0.63 / 0.66, against 36.3 / 46.7 meV and 0.53 / 0.42 with WSe2.

#### Changed — numbers move

- **100x recalibrated from that scale bar: 0.04669 → 0.047372 µm/px
  (+1.46 %)** (was Local 5.7.6). Every 100x domain size grows by 1.46 % (area
  by 2.9 %) and 100x nucleation density falls by 2.9 %. 50x is unchanged.

#### Fixed

- **A fixed energy gap (`tolerance_meV` 0) could never validate.** The check
  that the templates already respect the gap compared floats for exact
  equality, and no float centre meets it: 755 nm and the centre computed 30 meV
  below it sit 30.00000000000003 meV apart. The check now allows 1e-6 meV
  (`preset._GAP_CHECK_SLACK_MEV`). A gap with a tolerance validates exactly as
  before. No number moves.

## [Unreleased]

### Changed

- **The repository moved to the Nexstrom organisation:
  `https://github.com/Nexstrom/NexAnalyzer`** (was `angyulu/NexAnalyzer`,
  transferred 2026-10-28). The sidebar's User Guide / Report Issues links, the
  installer's default `NEXA_REPO`, and the clone and download links in README
  and USER_GUIDE point there. Existing clones keep working through GitHub's
  redirect; `git remote set-url origin https://github.com/Nexstrom/NexAnalyzer.git`
  makes it explicit. No numbers move.
- **Golden-fit record regenerated for DDAA10 RM_2: the golden laptop moved
  from Windows 10 (19045) to Windows 11 (26300).** No code, preset or package
  changed (numpy 2.5.3, scipy 1.18.1, lmfit 1.3.4 as locked); the OS runtime
  moved the last digits of this ill-conditioned fit past the strict 1e-5:
  R² 4e-8, chi² 6e-6, centres <= 1.1e-6, FWHM <= 6e-6, intensity and area
  <= 4e-5 relative (B2g's Gaussian share, ~0, from 2e-8 to 3e-7). RM_1, RM_3
  and the two PL records are unchanged to the bit, and portable mode passed
  before the regeneration. The customer edition's record is regenerated
  identically.

## [5.47.0] - 2026-11-02

### Changed

- **Only `MoS2_QC` and `WSe2_QC` are offered in the material dropdowns.** The
  other eight shipped presets (`MoS2`, `Silicon`, `WSe2`, `WSe2 extended`,
  `WSe2 extended V2`, `WSe2_Aggressive`, `WSe2_QC_broad`, `WSe2_truncate`) are
  switched off: `enabled: false` in `data/materials.json`, which hides them from
  Spectra, QC Report and OneNote while leaving every number they hold in the
  file. The Material Presets page still lists them, marked disabled, and the
  toggle there brings one back. Nothing that names a material directly changes
  -- a saved workspace, a QC runner or a test that asks for `Silicon` by name
  still gets it.

- **Runcard detection now assigns a series' cards in run order, as one
  sequence rather than run by run.** The matching still costs every run
  against every candidate on its own; what follows it changed. Where it used
  to swap two runs' cards when they came out of order -- and only when the
  recipes' contents alone paid for the swap -- it now re-solves each wafer
  series together, by a dynamic program over (run in time order, card number
  in order), so the chosen cards can never go backwards in time and every
  card the runs could take is in play, not only the two being swapped. A
  series that cannot be placed in order at all is left as the matching had
  it: that is a renumbering, not an ordering to repair. The rerun exception is
  unchanged -- a card tagged on a failed attempt is still free for the rerun
  that follows it, and an aborted attempt still shares its rerun's card.

  Measured on HA1P01, runs 2026-06-01 to 2026-11-02, duplicates counted once:
  all runs untagged at once, growth runs 302 / 308 (was 300 / 308);
  leave-one-out, 305 / 308 (was 304 / 308). The two it adds are the pair the
  tool ran on 2026-10-31 and 2026-11-01, where the later run's card had been
  re-saved just before the earlier run and content alone read the pair
  backwards. Cleanings and the other common recipes are untouched: they belong
  to no series and no number.

- **The save-time prior is unchanged, and deliberately so.** A card is usually
  written in the minutes before its run, which is already worth -1.5 to that
  run; sharpening the hours after that (3x the slope, or a 30-minute instead
  of a 6-minute window) was measured on the same set and either changed
  nothing or cost 6 to 17 growth runs, because the card for the *next* run is
  often saved just as the current one starts. Order, not a closer save time,
  is what tells those two apart.

## [5.46.0] - 2026-10-26

### Added

- **PL exciton-trion energy gap.** A PL block can hold one peak a photon
  energy below another: `energy_gaps`, each `upper`, `lower`, `gap_meV`,
  `tolerance_meV` (Material Presets page: **Energy gaps**, PL blocks only).
  The lower peak's centre is no longer free: it follows the upper peak's,
  E_lower = E_upper - gap, with the gap itself fitted inside
  gap_meV +- tolerance_meV (fixed at tolerance 0). The fit keeps its number of
  free variables, the gap is explored by every start, and the lower peak's
  own centre window no longer applies. A gap that ends on its limit flags the
  lower peak **on its limit** -- the constraint, not the data, placed it.
  `fitting.fit_voigt_peaks(gaps=)`; Raman blocks refuse it, and a block whose
  template centres sit outside their own gap window does not validate.
- **WSe2_QC: Exciton - Trion = 35 +- 10 meV; MoS2_QC: A0 - A- = 20 +- 10 meV.**
  The literature trion binding energies are ~30 meV for WSe2's negative trion
  (21-36 meV across trion species) and ~18-20 meV for MoS2 (Mak et al. 2013).
  On our data, free fits put WSe2's gap at a median 38 meV (736 clean HA, VB
  and TSMC spectra): 35 +- 10 fits them as well as the free fit (median chi^2
  ratio 1.00, 7 % of spectra > 20 % worse) and leaves Trion/Exciton at 0.48
  (free 0.47). MoS2's free gap is undetermined (bimodal, 5-12 and 40-46 meV,
  on the Samsung-Oct maps) because its two peaks overlap; 20 +- 10 costs a
  median 2 % in chi^2. MoS2_QC's A- start moves 700 -> 690 nm (21 meV below
  A0's 682) so the template sits inside its window.

### Changed

- **Numbers move: PL on WSe2_QC and MoS2_QC** -- Exciton/Trion and A0/A-
  centres, widths and intensities, and the PL quality figure's Exciton panels.
  Raw FWHM (the PL customer spec) is not a fit and does not move. No other
  preset changes. The golden fit and the validated golden set run their own
  frozen presets and are unchanged.

## [5.45.0] - 2026-10-26

### Changed

- **Nexstrom's colours and logo.** No numbers move: styling only.
  - The palette is nexstrom.com's own: the sidebar is Nexstrom navy
    (#002E5D), headings heading navy (#073A6C), buttons, links and toggles
    the accent blue (#2F72BF), secondary text the body slate (#486284).
  - The Nexstrom logo sits at the top of the sidebar, in white on the navy;
    the mark alone shows when the sidebar is collapsed and is the browser
    tab icon. The PNGs are in `assets/` (`nexstrom_logo.png` is the
    original navy one, for light backgrounds).
  - **Easier to read**: body text 15 px (was 14), headings a step larger,
    text near-black (#0B1220), and captions solid slate (#344B68, about
    8:1 on white) instead of Streamlit's 60% fade. The Spectra page's
    buttons got wider columns so their labels still fit. The sidebar's page
    names and footer are 15 px, its section labels 14 px.
- **Run Auto-Workflow reports in one line**: "Auto-workflow completed
  successfully!", with the stages, fit quality and the preset's notes
  (baseline and peak count per technique, its description) in a collapsed
  **Workflow details** panel under it. The notes no longer sit under the
  Run button, and the same R² and χ² are in the cards under the plot. A
  failed run still shows everything, as before.

## [5.44.0] - 2026-10-25

### Changed

- **A new look across every page**, from the "NexAnalyzer UI" design canvas.
  No numbers move: this is layout and styling only.
  - **Theme** (`.streamlit/config.toml`, now committed; the rest of
    `.streamlit/` stays ignored): grey page, white cards, a dark navigation
    sidebar, IBM Plex Sans / Mono and one blue accent (#1D5FB8). The fonts
    load from Google Fonts and fall back to the system sans when offline.
  - **Navigation** uses line icons instead of emoji.
  - **`core/ui/theme.py`**: `page_header()` gives every page the same
    header (section · page eyebrow, title, one-line purpose), and `card()`
    replaces `st.container(border=True)` so sections draw as white cards.
  - **Every page** opens with that header and lays its sections out as
    cards, in the canvas's order. Emoji in titles, buttons and status
    messages became Material icons; the main action on each page is the
    blue primary button. Widget keys, session state and the order things
    run in are unchanged.
  - **Spectra**: the previous / next file buttons are chevron icons.
  - **QC Report**: setup is five cards (Sample, OM magnification, Film
    layer, Material, Run); results are OM coverage, Figures, Run notes
    (drawn only when a note has something to say) and Save report.
  - **Material Presets**: "New material" sits above the list; Enabled and
    each technique are switches; Raman / PL / Optical are tabs; a technique
    block reads Processing, Peak templates, Fit windows; an optical layer is
    a Segmentation row and a Domain size row instead of 12 narrow columns.
  - **Datalog**: Source (with the three filename tools as expanders), Find
    runs, Runs and Selected run cards; Channels now come before the
    tolerance settings, and violations / summary statistics are tabs.
  - **Runcard**: Runcard folder, Recipes and Profile cards; Scan / Reload
    is the primary button.
  - **Split Table**: Workbook, Find runs (Wafer search first), Runs, Open
    run and Charts cards; the open-run buttons read "Open in …".
  - **OneNote**: Wafer and Material check side by side, then the three
    "What's ready" cards with a status badge each, Publish, and Not uploaded.
  - **Tools**: a card saying which file holds folders on this PC and which
    holds the shared layouts; one card per tool; "Add a tool" at the bottom.

## [5.43.0] - 2026-10-24

### Changed

- **The Spectra page's workflow moved out of the sidebar into the page**,
  top to bottom in the order it is used (`modules/spectra/ui/workspace.py`,
  replacing `sidebar.py`; the sidebar is now only the page navigation):
  - **Run setup**: the file switcher (◀ / dropdown / ▶, a position counter,
    **Add files…**, **Remove File**, **Delete All**) and, for 2-30 files, a
    row of file buttons with a fit-status dot (green fitted, orange stopped at
    max_iterations, grey not run); then **Material preset**, **Technique**
    (what the filename says, or "assumed"), **Run Auto-Workflow**, **Run All
    Files (N)** and **Reset to Raw**. Run and Run All Files are shown
    disabled until a preset is picked, rather than hidden.
  - **Fitted spectrum**: an **On background / Baseline removed** switch
    replaces the "Peaks on Background" checkbox and is pinned the same way;
    the other View Options are a row of toggles above the plot instead of an
    expander at the bottom of the sidebar. The plot is the same Plotly figure
    from `create_unified_figure`.
  - **Fit results**: the table gains **Area** (the integral) and **Status**
    (`peak_is_measured`: Measured, On fit limit, Below 3× noise) and names its
    unit in each header; R², χ², fit status and the pipeline are cards under it.
  - **Exports**, top right: **Quick export** (PNG, HTML, CSV of this file) and
    **Batch export** (Save Master CSV…, Save PNGs…), shown disabled until
    there is something to export.
- The integration tests that drove the sidebar now drive the page, and were
  renamed `test_workspace_*`; `test_spectra_workspace.py` covers the new
  switcher, the baseline switch and the table's columns.

### Removed

- **The legacy FWHM (v1)** -- the pre-v3.4.0 `2.355 x sigma` width that
  dropped the Lorentzian half and understated every width by 59-108 %. Gone
  from everywhere it could still appear: the QC Report's "Include legacy FWHM
  (v1) column" option (summary figure tables and the workbook's
  `FWHM_v1`/`FWHM_v1_Stderr`/`FWHM_v1_Mean`/`FWHM_v1_Std` columns), the
  Spectra page's Fit Results column, `FittedPeak.width_fwhm_v1`, its
  computation in `fit_voigt_peaks`, and `PeakStat.fwhm_v1_mean`/`fwhm_v1_std`.
  A fit saved with the old key still loads; the key is ignored.

No numbers move: nothing here touches parsing, the fit itself or any reported
value. The fit no longer computes the removed v1 width, which nothing reported
by default; FWHM is still the Voigt FWHM. The table's Intensity is still
`peak_intensity()`, the same number both CSVs carry.

## [5.42.0] - 2026-10-24

### Added

- **Split Table page** (Process group). Reads a tool's
  `SPLITTABLE/<TOOL>_SplitTable_from_runcards.xlsx` -- declared per tool as
  `splittable.file` in `data/tool_layouts.json`, HA1P01 and VB1P01 for now --
  and never writes it; the SPLITTABLE scripts still build it. Filter by month,
  run type, series and wafer; show or hide column groups from the workbook's
  `Column_Design` sheet; save the filtered view as XLSX or CSV (the workbook
  itself is refused as a target). Charts: any numeric columns against run
  order or process start, growth runs as points, cleanings / anneals / other
  runs as vertical lines, hollow markers where `OM_Discriminable` is false,
  error bars from the table's std columns, and no 95 % line (not one of the
  three spec lines); and any column against any other, coloured by series.
  The workbook is read as bytes, because openpyxl's seek on the OneDrive file
  fails (`Errno 22`) on VB's.
- **Picking a row opens its run** in Datalog, Runcard or the QC Report.
  Datalog and Runcard take a *focus* (`datalog_state.focus_run`,
  `runcard_state.focus_runcard`): the table cannot be given a tick, so the
  page shows the handed-over run as selected until the operator ticks another
  row (or, on Datalog, changes the seeded search). Runcard scans the folder on
  arrival instead of waiting for Scan / Reload. A datalog renamed since the
  table was written (`13184401.csv` -> `13184401~HADI51.csv`) is still found.

No numbers move: nothing here touches parsing, fitting or the OM algorithm.

## [5.41.0] - 2026-10-24

### Numbers that move

- **PL is despiked by its steps, not by its levels** (`remove_spikes_local`,
  Whitaker-Hayes). `remove_spikes` scores every point against the whole
  spectrum's median and MAD, so on PL -- one broad band over a flat background
  -- it flagged the band itself (~290 of 1650 points on a clean production
  spectrum) and smoothed it, and it left 20 % of a 3-pixel cosmic ray. A step
  score leaves a clean band alone (~10 noise points touched, maximum and Raw
  FWHM unchanged) and removes rays: 98 % of rays at 20 x the noise, all
  larger ones, 3-pixel rays to under 1 %. **Every preset's PL despike
  threshold is 10** (was 8; the number now scores steps). Raman keeps
  `remove_spikes`: on the golden Raman it touches nothing in the fit window.
  On 93 production PL spectra (HA HADI*, VA 2025-04, WSe2_QC), old vs new:
  Raw centre and FWHM -- the customer-spec quantities -- change by 0 in the
  median, 95 % within 2.1 nm / 0.7 nm, at most 3.4 / 2.6 nm (VA1P012504020
  PL_10); fitted Exciton centre and FWHM 95 % within 0.11 / 0.20 nm. The
  exceptions are 3 spectra (HADI40 PL_2/PL_3, HADI43 PL_3) whose band does not
  separate cleanly into Exciton and Trion: the two-peak split flips and the
  Exciton lands on its 780 nm bound (up to 9.9 nm and 23 nm FWHM). HADI14 PL_1's
  "maximum" was a cosmic ray (one point at 854 nm); both despikers remove it.
- **Golden-fit record regenerated for the PL sets** (HADI51, VA1P012504023
  PL_5), their frozen presets now WSe2_QC's PL block at v5.41.0 (threshold 10):
  centres move <= 0.05 nm, FWHM <= 0.08 nm, intensities <= 0.2 %; chi-squared
  rises 4-7 % because the band's real noise is no longer smoothed away. The
  Raman record (DDAA10) does not move.
- **Domain sizing finds markers on the green channel**, the channel its
  thresholds come from. Luminance sat 0.2-2.6 sigma from green depending on
  the film's colour, so the marker floor's 0.5 sigma margin meant something
  different on every film. Domain counts: HA wafers (HADH20/30/35) within
  1.5 % and mean size unchanged; VA1P012504002 +13 % domains "above"
  (2,767 -> 3,136, mean diameter 26.7 -> 25.5 px). OM coverage is unaffected.

## [5.40.0] - 2026-10-24

### Fitting

- **Fits are faster, and bit-identical.** Each peak's sigma and gamma were
  three lmfit expressions re-evaluated through its expression interpreter on
  every residual call, ~40 % of a fit. A fit without a dominant peak now uses a
  Voigt parameterised by its FWHM and Gaussian share (`_voigt_by_width`) that
  computes them inline with the same operations in the same order, so every
  float is the same. Verified identical on **481 spectra**: the 5 golden
  (DDAA10 RM_1-3, HADI51 and VA1P012504023 PL_5, strict), 332 HA/VA production
  spectra (WSe2_QC, and MoS2_QC's dominant path) and 144 Samsung MoS2 map
  spectra (MoS2_QC). WSe2 Raman fits run ~2.3x faster, PL ~1.4x; MoS2 maps
  barely change. A dominant-peak fit (MoS2_QC PL) keeps the expression model,
  since its constrained area needs lmfit's propagated stderr.
  `test_fitting` asserts the inline sigma/gamma equal lmfit's evaluation
  exactly over 500 random inputs. **No number moves.**

## [5.39.0] - 2026-10-24

### Reports

- **Figures render through one persistent Chrome instead of one per image.**
  `export_figure_png` started a fresh headless Chrome for every PNG (plotly's
  one-shot kaleido path). Measured here: 1.2 s a render, ~4 temp folders left
  behind each, and one of the first 15 renders never answered -- the QC flow
  test that hung for 12 h, and the 43 min CI stall in the customer repo. It now
  starts kaleido's sync server once per process: 500 of 500 renders at 0.07 s,
  pixel-identical output. Renders are serialised (the server's one queue would
  otherwise hand two Streamlit sessions each other's image) and time out after
  120 s with a clear error, after which a fresh server is used. The QC Report
  flow tests ran 3 x 46/46, at 5.2 min a run instead of ~8. **No number moves.**

### Process

- **`scripts/ship.ps1`: one command from a finished branch to a PR with CI.**
  Strict golden fit first (Raman and PL fit consistency on the recording
  machine), then the validated set, ruff and the algorithm guard; nothing is
  pushed if one fails. Then push, open or reuse the PR (description from the
  branch's CHANGELOG lines plus the template), wait for CI and report. It never
  merges; on the free plan main cannot block a red merge, so the PR page's
  status is the gate. docs/Development_Process.md §4 describes it.

## [5.38.0] - 2026-10-24

### Validated golden set (from NexAnalyzer_TSMC)

- **A second golden tier: "right", not just "unchanged".** The tripwire
  (`test_golden_fit.py`) proves the numbers did not move. It cannot prove
  they are correct, because its record is the program's own output.
  `tests/golden/` holds cases whose expected values carry an independent
  `reference` (a WITec or Origin fit, literature, a by-eye classification)
  and a tolerance per value. The runner refuses a case without a reference.
  - Run with `python -m tests.golden.run`. It writes
    `dist/golden/golden_report.md` / `.html`, the validation record.
  - It also runs under pytest (`tests/golden/test_golden_cases.py`).
  - CI runs it in the `algorithms` job and uploads the report as an artifact.
  - Shipped with the three bad-input cases and a `spectral_fit/_TEMPLATE`.
    The spectral cases need data with an independent reference, which is
    still to be chosen.
- `modules/spectra/processing/fit_record.py`: a fit's reported numbers as a
  record, the validated comparison, and the tripwire comparison with its
  portable rule. It is the same module as the customer edition's;
  `tests/unit/test_fit_record.py` covers it.
- **Typed input errors.** The parser raises `EmptyFile` / `UnsupportedFormat`
  (both `SpectrumFileError`, still `ValueError`, so every existing handler
  catches them). The messages are written for the operator and name the
  file, e.g. "Unsupported file format: OM_1.bmp. Expected a text file of X
  and Y columns separated by tab, comma, semicolon or spaces". Before, the
  message was "Failed to parse spectrum file '<full path>': ...".
- The three bad-input `input.txt` files get a narrow `.gitignore` exception.
  They are hand-written and hold no measurement.
- **No number moves.** The golden tripwire passes 5/5 strict and is
  unchanged. Only error types and messages change.

## [5.37.0] - 2026-10-24

### Spectra

- **A PL file exported as Raman shift (cm-1) is refused, not fitted as nm.**
  HADH08/20/22/23 (2026-09) and VBBE68 (2026-08) carry PL on a 3876-9558 axis;
  the fit treated it as wavelength, so their QC Reports printed a Raw PL centre
  of ~7,300 "nm" and Exciton/Trion pinned at the axis start (R2 about -0.1).
  `execute_auto_workflow` now stops a PL spectrum whose axis median is above
  2000 (`PL_MAX_NM`, no PL in nm reaches it) with "This PL file's axis runs
  3876-9558, which is Raman shift (cm-1), not wavelength (nm). Re-export the
  PL spectrum in nm." The QC Report lists it per point and omits PL rather than
  report it. The data files are untouched; converting would need the
  excitation wavelength, which the file does not carry. No other number moves
  (golden fit 5/5).

## [5.36.0] - 2026-10-24

### QC Report

- **The QC Report pipeline is a module, `modules/qc_report/processing/build.py`.**
  The ~480 lines behind the page's "Generate" button -- segmentation, domain
  sizing, fitting, selection, quality gate, ratios, figures, summary page and
  workbook -- moved out of `pages/2_QC_Report.py` (1,401 lines to about 900)
  into `build_qc_report(QcReportInputs, progress, warn, run_batch)`, which
  returns the artifacts under the page's own state keys. `save_report()` and
  `REPORT_FIGURES` (the numbered file set) moved with it, so the page, OneNote
  and the scripts share one list. `GRID_POINTS` / `grid_points_only` and the
  noise-threshold warning live there too. **No number moves**: the code moved,
  it did not change (golden fit 5/5, strict; the QC Report flow tests pass
  unchanged apart from where two of them patch a function).
- **The batch runners call the pipeline instead of driving the page.**
  `run_qc_wse2.py` and `run_qc_mos2.py` used AppTest, a faked save dialog and
  button clicks by label, and aborted a whole wafer on any page message
  containing "failed"; they now share `scripts/qc_report/_runner.py`
  (`run_report`, and the process-pool batch passed as `run_batch`). A
  per-point fit error is printed and the report is still saved.
  `redraw_om.py` uses the shared grid filter.
- The algorithm-change guard covers `modules/qc_report/processing/`.
- New tests: `test_qc_report_build.py` (the pipeline with no page: artifacts,
  warnings, a replaced batch runner, saving, the grid filter, the noise
  check) and `tests/scripts/test_qc_runners.py` (a runner end to end in a
  subprocess, with a process pool).

## [5.35.0] - 2026-10-24

### Golden fit

- **The OpenBLAS kernel pin (v5.33.0) is removed, and the golden fit gains a
  portable comparison instead.** The pin could not do what it was for: a
  1-ulp change in the Voigt function alone moved HKU RM_1's chi-squared by
  ~1e-4 and its unmeasured C and LB peaks by ~3 %, kernel pinned or not, so
  no pin makes a fit bit-reproducible across machines. `core/blas.py`,
  `core/launch.py` and `tests/unit/test_blas.py` are deleted, `start.bat` /
  `start.sh` launch with `streamlit run` again, and `core/blas.py` leaves
  check_algo_change's guarded paths.
- **Portable mode** (`NEXANALYZER_GOLDEN_PORTABLE=1`, set by CI): R2 and
  chi-squared at 1e-3, and only measured peaks' (`peak_is_measured`) centre,
  FWHM, intensity and area at 1 %. Shape and stderrs are left out. Across six
  rounding perturbations (1-ulp Voigt changes, another kernel) and the pinned
  v5.34.0 record, every set passes. Measured peaks drift by <= 7e-5, fits by
  <= 1e-5. The git hooks keep the strict 1e-5 comparison. The record is
  regenerated only on the designated laptop; a clone on another PC opts its
  hooks into portable mode with `git config nexanalyzer.goldenPortable true`,
  and a strict failure prints that hint. The
  same change is in the customer edition (its CI went green with it on
  2026-10-24).
- **Golden-fit record regenerated** without the pin, and with a `measured`
  flag per peak. **Numbers move, below any instrument resolution.** HADI51 and
  VA1P012504023 PL move by <= 1e-6, apart from shape parameters near 0. In
  DDAA10, the measured E2g+A1g, 2LA and LA move by <= 7e-5. The unmeasured
  B2g / 2ZA(M) stderrs and near-zero shapes move more, which is why portable
  mode skips them.

## [5.34.0] - 2026-10-24

A whole-repo review. **The Raman golden fit does not move** (DDAA10 RM_1-3
reproduce `expected.json` to 1e-5), the PL record is replaced (below), and the
locked OM contrast numbers do not move. What does move is listed under
"Numbers that move".

### Golden-fit record

- **The HKU-20230524-2 golden set is retired from the locked record.** Raman
  is locked by DDAA10 RM_1-3 alone. HKU RM_1 was an 8-peak fit of the old
  research "WSe2" preset at R2 0.896, ill-conditioned enough to move ~1e-4
  between CPUs (the reason for the v5.33.0 BLAS pin), and ~55 s of every
  commit hook; DDAA10 fits the WSe2_QC path customers' reports use, at R2
  0.99. HKU PL_1 was a 2023 non-production sample on the same unused preset.
- **PL is locked by two production spectra instead**: HADI51 PL_5 (HA1P01,
  2026-10) and VA1P012504023 PL_5 (VA, 2025-04), fitted with WSe2_QC's PL block
  frozen at v5.34.0 (`golden_fit/<wafer>/`), recorded with this release's code.
  Picked from a screen of HA/VA PL spectra: R2 0.9997 and 0.9992, and a 1e-9
  input perturbation moves no reported number by more than 3e-8 (HADH72,
  HADI05 and several VA spectra fit as well but moved 1e-4 to 1, so they would
  have flaked like HKU RM_1). The HKU spectra, preset and record are deleted.

### Numbers that move

- **Summary-table and workbook ratios now match the quality figure.** The table
  took a plain median over every spectrum; the figure's title fenced first
  (wafer-wide IQR, then per region, v5.32.0), so one report could print LA
  10.2 % in the table and 9.8 % in the title. Both now read
  `peak_metrics.ratio_values_by_point` and the region fence. LA, 2LA and B2g
  table values move toward the figure's; `n` counts the ratios kept.
- **Runcard matching reads `Heater Soak,800`** as the setpoint step the
  datalog shows (shared `growth_window.soak_value`). On HA1P01 Jul-Oct, 9
  CLEANING-5 runs drop from cost 1.40 to 0.00 (one 2.10 to 0.70); two runs of
  an earlier CLEANING-5 edit (2026-09-22/23) rise 1.24 to 2.64, because the
  matcher now sees that they held 900 where today's card soaks at 800; 343
  tagged runs are unchanged.
- **Runcard gas markers mark a flow change between two nonzero values**
  (`H2Se → 10`); repeated "off" commands still mark nothing. 249 of 2,349 real
  cards gain a marker. Recorded as departure 5 in `docs/reference/README.md`.
- **A growth window with no heater-off and no ramp-down runs to the card's
  end**, as the reference does (`growth_end = total_sec`); it was zero-length.
  No current card has this shape.
- **Intensity stderr is NaN, not 0.0, for a peak fitted to zero area** (MoS2 A0
  on about a third of points printed "0 ± 0"), and a peak with no component
  curve reports NaN intensity instead of its *area* under "Intensity".
- **Raw FWHM is positive on a high-to-low X axis** (it came out negative and
  the PL cleaning floor dropped every Raw record).
- **Baselines on spectra with values below 1 count** may differ at rounding
  level: the v2.1 auto-shift wrapper was a no-op (ALS and a polynomial with a
  constant term follow any offset) and is gone. Bit-identical for every golden
  spectrum. `ProcessingSettings.y_shift` stays, always 0.0, so saved projects
  load.

### QC Report

- **Changing the layer drops every result**, not only the OM ones: the layer
  also picks the Raman/PL stats figures' spec lines, and a 2L-spec figure could
  be saved beside a 1L choice.
- **OneNote "Build for <wafer>" forgets an operator's layer choice** for the
  previous wafer (`qc_report_state.select_folder`, shared with the page's own
  picker); a hand-picked 1L was carried into the next, 2L, wafer.
- **MoS2 Raman is drawn by the app as the runner drew it**: FWHM (E2g, A1g) |
  Center (E2g, A1g) | Δω, the nominal ~20 cm-1 line on Δω for 1L, a Δω row on
  the summary page and the `2LA(M)/A1g` defect ratio in `RAMAN_RATIO_PAIRS`
  (table and workbook). `run_qc_mos2.py` no longer monkeypatches anything; a
  page-built and a runner-built report are the same. "WSe2 extended V2", which
  also fits E2g and A1g apart, is not mistaken for MoS2.
- **The summary page's tables stay on the page.** A 20-peak preset pushed the
  PL and domain tables below 7.5 in, where they silently vanished; the stack
  now shrinks rows and font together to fit, and refuses below 7 pt. WSe2_QC
  and MoS2_QC pages are pixel-identical to v5.33.0.
- **The OM figures' legend sits just under the last row**, and on the
  diagnostic copy it clears the histogram row's tick labels instead of printing
  over them. Layout only.
- Titles print "—", not "nan %", for a CV with fewer than two regions.
- **Domain sizing reuses each frame's own segmentation** instead of segmenting
  it again, and places its fallback markers in one pass: 6-15x faster per
  frame, identical domains (checked on HA and VA frames, all three targets).

### Fitting and processing

- **Fits refuse what they cannot honour**: a range narrower than a peak's
  template FWHM (inverted width bounds, which lmfit swapped silently and called
  a success at R² -3), a spectrum with no positive signal, and a masked ALS
  baseline whose exclusions leave fewer than 10 points (it returned a baseline
  of 0 and "corrected" nothing). Each is now a clear error.
- **OM flat-field runs its three channels on threads**: about 3x faster per
  frame, bit-identical. A mask margin that leaves no pixels is a clear error.
- `FittedPeak.to_dict` keeps the legacy FWHM (v1).
- Removed code nothing called: `baseline_rolling_ball`, `baseline_spline`,
  `baseline_airpls`, `calculate_baseline_quality_metrics`, the auto-shift
  wrappers, `auto_find_peaks`, `detect_overlapping_peaks`,
  `estimate_spectral_resolution`, `validate_spectrum_file`, `count_spikes`,
  `spike_fraction`, `suggest_threshold`.

### Datalog, Runcard, OneNote, Spectra

- Detect runcards skips a malformed card (`csv.Error`) instead of failing.
- Renamed files whose tag did not follow are reported as such, not as
  "Not renamed". The violations table's sort is stable.
- The run-parse cache is bounded (a live run added a DataFrame every minute).
- Runcard save no longer overwrites a chart sharing another's run id.
- OneNote: a sign-in or network failure is an error message, not a page-wide
  traceback, and MSAL is built once per session rather than per rerun.
- One native-dialog helper (`core/io/native_dialog.py`) replaces four copies:
  titles with quotes or non-Latin text work, and no temp file is left behind.
- The Spectra sidebar builds the master CSV only on Save.
- `find_wafer` survives a file OneDrive removes or locks mid-scan.

### Scripts, launchers, CI

- `start.bat` / `start.sh` install with `-c requirements-lock.txt`, as CI does.
- QC runners resolve relative sample folders against the caller's directory;
  `redraw_om.py` keeps points 1-9 and exits with a message on an empty folder;
  `redraw_stats.py` finds a delivered report's source spectra and closes the
  workbook.
- CI: coverage with an 87 % floor (93 % measured), JUnit and coverage reports
  uploaded, `pip check`, a weekly scheduled run, and the golden fit no longer
  run twice. The algorithm guard also covers `peak_quality.py`,
  `summary_figure.py`, `results_excel.py`, `results_csv.py` and `core/blas.py`.
  The pre-push hook says when it skips the guard.
- New tests: the native dialog round trip (against a stub `tkinter`), the
  flat-field threading, `redraw_om.py` end to end, and one per fix above.

## [5.33.0] - 2026-10-23

### Reproducibility

- **One OpenBLAS kernel on every machine** (`core/blas.py`). numpy and scipy
  each pick an OpenBLAS kernel for the CPU they find, and the kernels round
  differently: the golden RM_1 fit (8 peaks, R2 0.896) settled at chi-squared
  28637.11 on the SkylakeX laptop that wrote the record, 28637.44 under
  Haswell and 28641.97 on a GitHub runner, so the tripwire held on one PC and
  failed on the next, and two PCs could disagree on the same spectrum. The
  kernel is now pinned to Haswell (AVX2) with one thread, before numpy loads:
  `core/__init__.py` does it for any entry point that imports `core` or
  `modules` first, and `start.bat` / `start.sh` launch through
  `python -m core.launch` because Streamlit loads numpy before app.py runs.
  A CPU without AVX2 keeps its own kernel rather than fault.
  `tests/unit/test_blas.py` reads back the kernel that actually loaded.
- **Numbers move, Raman only, below any instrument resolution.** The
  golden-fit record is regenerated on the pinned kernel (both sets,
  `HKU-20230524-2` and `DDAA10`); PL_1 is unchanged. Largest moves: the weak
  C centre 12.92 -> 12.95 cm-1, LB intensity 0.3 %, everything else <= 0.1 %.
  `test_golden_fit.py` imports `core` before numpy so `--regenerate` writes
  on the same kernel.

## [5.32.0] - 2026-10-22

### QC Report

- **The customer-spec lines under the quality-figure titles carry the value
  and the limit only.** The presence count ("above 3x noise in N/M spectra")
  and the "(CV of 9 region medians)" note are gone, e.g. "2L spec:  B2g 308.9
  cm-1 (spec ~308) · E2g+A1g FWHM 5.12 cm-1 (spec < 7) · I(LA)/I(E2g+A1g)
  7.9 % (spec < 13 %)" and "Uniformity:  E2g+A1g position 0.04 % (spec <=
  0.5 %) · FWHM 2.38 % (spec <= 20 %)". Uniformity is still the CV of the
  region medians; positions and widths carry the panels' two decimals.
- **Each panel's legend states "Uniformity = x %", the CV of the region
  medians -- the same number as the title**, in place of the pooled "Robust
  CV" (v5.28.0), which also counted scatter within a region (KRX261001:
  E2g+A1g position 0.06 % pooled against 0.04 % across regions). Title and
  panels now print one value for each quantity.
- **Ratio panels are drawn in percent**, as the customer spec states them:
  I(LA)/I(E2g+A1g) "Median = 7.9 %", spec line at 13.
- **I(LA)/I(E2g+A1g) and I(2LA)/I(E2g+A1g) are formed from every spectrum
  in the figure, the title and the summary table**
  (`peak_metrics.RATIO_FROM_EVERY_SPECTRUM`); they were gated by
  `peak_is_measured`. A 1.5x IQR fence over the whole wafer, then the region's
  own, drop broken fits (one 2LA ratio at 260 %; a KRX261003 LA ratio at 20 %
  from a half-intensity spectrum in a widely spread region), so most regions
  keep 23-25 of their 25. On weak regions LA sits just
  under 3x the fit noise, so the gate kept only the strongest few and biased
  them high: KRX261001 point 7 read 9.7 % from 5 of 25 spectra, 8.3 % from all
  25; the other regions' medians move by <= 0.3 %. Where LA sits on a fit limit
  across a region it now shows (KRX261004 point 2: 18 % against ~12.5 %).
  Numbers move for these ratios.
- **A 1L report's Raman figure hides B2g** -- its centre panel and the bilayer
  ratio (`BILAYER_ONLY_PEAKS`); a monolayer has no B2g. On a 2L report the
  bilayer ratio is formed from every spectrum too (25 per region; the gate kept
  3-19 because B2g's width ends on its cap).
- **Spec lines are red dashed** (were solid grey), and the **B2g centre line at
  308 is gone**: "~308" is a presence criterion, stated in the title. Three
  spec lines remain: E2g+A1g FWHM 7, LA ratio 13 %, PL Raw FWHM 40.
- **The PL spec is judged on the Raw spectrum** (each spectrum's own maximum
  and half-height width, no fit): the title states Raw FWHM against 40 nm and
  Raw position / FWHM uniformity, and the 40 nm line moves from the Exciton and
  Trion FWHM panels to Raw FWHM.
- **MoS2 carries its quote spec** (20261009W001 §6.4, 6.9, 6.10; `MOS2_SPEC`):
  the Raman title states |E2g-A1g| against ~20 cm-1 and E2g/A1g position and
  FWHM uniformity; the PL title Raw FWHM against ~40 nm and Raw uniformity. The
  two values are drawn as nominal targets -- "~" and grey dashed
  (`spec_nominal`) -- though the quote writes "<="; uniformity stays a limit.
  The Δω line is drawn by the Samsung runner, which owns the Δω panel.
- **MoS2 PL figures show only A0 and A-** beside the Raw column
  (`MOS2_PL_SHOWN`); B is still fitted, not drawn. 2 | 2 | 2 grid.
- **Spec lines follow the report's layer** (`LAYER_SPEC_LINES`), as the title
  does: the customer spec lists FWHM < 7 and LA < 13 % under 2L and Raw FWHM
  <= 40 nm under 1L, so a 1L Raman figure draws no line and a 2L PL figure
  none. KRX261003 (1L) reads I(LA)/I(E2g+A1g) 13.2 %, where 13 % is a 2L
  limit; its value has read ~13.4 % in every version, so the line is what
  changed, not the number.
- **New doc, docs/QC_Report_Spec.md**: the customer spec, what the figure
  shows, how each value is computed, the ratio rule and the title rule.

### Scripts

- **`scripts/qc_report/`**: the batch QC-report helpers used for Samsung-Oct
  and IMEC-Nov. `run_qc_wse2.py` / `run_qc_mos2.py` drive the real QC Report
  page per sample folder with a process pool (`--layer`, `--name`, `--suffix`);
  `run_qc_mos2.py` also draws MoS2's Raman layout with the Δω panel.
  `redraw_stats.py` redraws a report's Raman/PL stats figures from its workbook
  with no refit (fit limits and noise rebuilt from the preset and χ²), and on
  KRX261001 reproduces the delivered figure byte for byte. Not part of the app.
- **`scripts/qc_report/redraw_om.py`**: redraws a report's OM and OM-diagnostic
  figures under a new title by re-segmenting its frames with the preset's
  optical parameters (seconds, no spectral fit). Used to retitle the Samsung-Oct
  and IMEC-Nov OM figures; coverage matches each workbook.
- **The PL quality figure's title no longer lists its extra cleaning**
  ("excludes FWHM <= 5 and widest 5% per peak, unlike the summary table"). The
  cleaning itself is unchanged (`PL_CLEANING`). No numbers move.
- **A sample is titled by its folder name without the wafer ID**
  (`sample_scanner.sample_title`): `KRX261001_HADH82_2L` -> `KRX261001_2L`,
  `KRX261005_VM2610012_MoS2` -> `KRX261005_MoS2`. Only a middle token that
  looks like a wafer ID (letters then digits: HADH82, VABD42, VM2610012) is
  dropped; first and last tokens are kept, and a folder without one keeps its
  name. The title also names the saved report files.


### QC Report

- **The Raman and PL quality figures state the customer QC spec under the
  title** (`peak_quality.CUSTOMER_SPEC`, `spec_summary_lines`): each criterion's
  summarized value beside its limit, with no pass/fail verdict. The layer
  criteria follow the report's reference layer:
  - Raman 1L: E2g+A1g position, and in how many spectra it stands above 3x
    the fit's noise (spec: present, ~250 cm-1).
  - Raman 2L: B2g position and presence (spec: present, ~308), E2g+A1g FWHM
    (spec < 7 cm-1), I(LA)/I(E2g+A1g) (spec < 13 %).
  - PL 1L: Exciton FWHM (spec <= 40 nm).
  - Every layer, uniformity: the CV (sample std / mean) of the 9 region
    medians of the main peak's position and FWHM -- Raman E2g+A1g (spec <=
    0.5 % / 20 %), PL Exciton (spec <= 1.5 % / 25 %). This is not the panels'
    Robust CV, which pools all spectra and so includes scatter within a region.
  Values come from the same cleaned records the panels draw. "Present" counts
  the signal half of `peak_is_measured` only: B2g's width ends on its cap in
  nearly every spectrum, and the full test reported it absent on QC_KRX261001,
  where it is clearly there (197/224 above noise).
- **The PL FWHM spec line is at 40 nm, was 35** -- the customer spec's 1L limit.
  No fitted numbers move in this release.


### Presets

- **`WSe2_QC` 2ZA(M) starts at 3.0 cm-1, was 1.64, so its width cap is 9 (was
  4.92).** The cap is 3x the starting width (`calculate_auto_bounds`), and
  2ZA(M) sat on 4.92 in every spectrum. Numbers move for every WSe2_QC Raman
  fit: on Samsung-Oct QC_KRX261001/02 (450 spectra) E2g+A1g FWHM drops
  5.43/5.17 -> 4.78/4.63 cm-1, its intensity ~9 % lower, LA/E2g+A1g rises
  0.076/0.067 -> 0.085/0.074, chi2 -13 %.
  - 2ZA(M) still sits on its cap at every value tried (7.5, 9, 10.5, 12, 15:
    100 % up to 12), so its width remains a fit bound, not a measurement. 9 is
    the widest cap that keeps its centre off the E2g+A1g side: from 10.5 the
    median centre moves to the 246 bound (61-97 % of spectra) and the band
    takes E2g+A1g's wings. Uncapped it runs to 17-68 cm-1 and is background.
- **`WSe2_QC` E2g+A1g is held to 249 +- 5 (244-254), was 250.5 +- 3.5
  (247-254), and 2ZA(M) to 240 +- 3 (237-243), was 243 +- 3**, so the two
  windows still do not overlap. On films whose main peak sits at 246-247.5
  cm-1 (Samsung-Oct QC_KRX261003/04, VA films) E2g+A1g could not reach it: the
  fit gave the main peak to 2ZA(M) and left "E2g+A1g" a flat band on the 247
  bound and its 9 cm-1 width cap (11 of 324 and 48 of 324 spectra at v5.30's
  cap, 28 and 79 of 225 reported at v5.29), so the reported E2g+A1g intensity,
  FWHM and LA ratio were 2ZA(M)'s. With the new windows none is on the cap and
  R2 rises (0.939/0.935 -> 0.946/0.948). Not a calibration error: the laser
  line sits near +1 cm-1 on these films as on QC_KRX261001/02; their weak line
  is found on only part of the spectra, which leaves a ~1 cm-1 mix of corrected
  and uncorrected spectra (not addressed here).
  - On films at ~250 (QC_KRX261001/02) 2ZA(M) moves off E2g+A1g's flank and
    gives part of the cap-9 change back: E2g+A1g FWHM 4.78/4.63 -> 5.12/4.95,
    intensity +7-10 %, LA/E2g+A1g 0.085/0.074 -> 0.079/0.070. Against v5.29 the
    net change is FWHM -0.3/-0.2, LA ratio +0.003.
  - On 03/04 2ZA(M) now sits on its 237 lower bound; like its width, its
    position there is a fit bound.
  - `WSe2_QC_broad` and `WSe2` are unchanged.

### Tests

- **A second golden-fit set: DDAA10 RM_1..RM_3** (`tests/fixtures/golden_fit/DDAA10/`),
  fitted with a frozen copy of WSe2_QC's Raman block at v5.30.0
  (`wse2_qc_preset.json`), so the QC preset's path -- single 100-315 window,
  zero-line calibration, E2g+A1g/2ZA(M) windows, 2ZA(M) cap -- is locked by the commit/push hooks and
  CI as well. The three spectra are committed through a new `.gitignore`
  exception. The HKU-20230524-2 golden record is unchanged (regenerated
  byte-identical). `test_golden_fit.py --regenerate` now takes set names, so
  adding a set cannot rewrite another's record.

## [5.29.0] - 2026-10-21

### Presets

- **`WSe2_QC` Raman fits 100-315 cm-1 in one window on one baseline**, was
  -5-330 in three windows (-5-40, 40-210 with a straight line under LA,
  210-330). The block's ALS baseline (lambda 50000, p 0.005) runs once over the
  whole range. The low-frequency peaks -- LB, C and the laser-line `center` --
  are gone, and with them the C/LB ratio, which WSe2_QC reports no longer
  carry. Zero-line calibration stays on: it reads the laser line from the whole
  spectrum before the x-range crop. B2g is held to 308 +- 5 (was 310 +- 10) so
  it cannot park on the new 315 edge. Numbers move for every WSe2_QC Raman fit.
  `WSe2_QC_broad` is unchanged.
  - Range and background were chosen on 16 samples (IMEC-Nov, DDAA10/12,
    HADG38/HADH04/HADH12, VBAC21/VBBF00/VBAD00; 377 spectra). Against 80-350,
    100-315 leaves E2g+A1g within +-0.04 cm-1 and its within-point spread
    unchanged; R2 rises because the 315-350 tail, where ALS drooped below the
    data, is no longer scored. A deg-2 polynomial anchored on flat ground
    removed ALS's tilt but cut into the peaks after 2LA on high-signal samples,
    so ALS stays.
  - Shoulder widths were tested and left alone: 2ZA(M), B2g and 2LA grow to
    whatever cap they are given, and uncapped they turn into background (2LA
    28-135 cm-1, B2g 135) and take 1.2-1.4 cm-1 off E2g+A1g. A broad
    second-order continuum peak freed 2LA but itself sat on its limits and took
    the uncertainties with it. Their widths are fit bounds, not measurements.
- **`WSe2_QC` PL Exciton starts at 765 nm, was 755** (tolerance still 15, so
  750-780). On IMEC-Nov 1L films the Exciton sat on its 770 nm upper bound in
  80-99 % of IMC260801/02/05 spectra; at 765 it is 0-6 %.

### QC Report

- **The Raman quality figure shows I(2LA)/I(E2g+A1g) instead of 2LA FWHM.**
  2LA's width sits on its 12 cm-1 cap on most VA films, so the panel plotted the
  limit. 2LA(M) is a two-phonon double-resonance band that needs no defects;
  its ratio to E2g+A1g follows resonance and layer number (1L 0.24-0.37, 2L
  0.10-0.13 on the samples above) and is labelled "Layer / resonance ratio",
  with no spec line. It is added to `peak_metrics.RAMAN_RATIO_PAIRS`, so the
  summary table and workbook carry it too. The defect ratio stays
  I(LA)/I(E2g+A1g).
- **The Raman quality figure is 2 | 2 | 2 for WSe2_QC:** E2g+A1g FWHM with the
  bilayer ratio I(B2g)/I(E2g+A1g) under it, the E2g+A1g and B2g centres, then
  the defect and layer ratios. The bilayer ratio is still read from
  `RAMAN_RATIO_PAIRS`; only where it is drawn moved.
- **The PL quality figure is Raw | FWHM | centres (2 | 2 | 2), as the MoS2
  reports were:** a Raw Spectrum column (each spectrum's own maximum and
  half-height width, no fit -- the summary table's Raw row), then Exciton and
  Trion FWHM and centres. The Exciton/Trion ratio column is gone. Raw is cleaned
  apart from the fitted peaks, so their panels are unchanged. A material whose
  own peaks build the columns (MoS2) gets the Raw column too.
- **The QC Report keeps grid points 1-9.** A folder measured at more positions
  (HADH71/75 carry a 10th) has the extra ones left out of every figure, table
  and the workbook, and the page names them.
- **A ratio whose peaks a preset does not fit is no longer drawn** as an empty
  panel (C/LB under WSe2_QC); the summary table already left it out.

## [5.28.0] - 2026-10-19

### QC Report

- **A point with more than 25 spectra loses its outliers and is drawn down to
  25** (`spectrum_selection.select_spectra_per_point`, `SPECTRA_PER_POINT`).
  For 36-spectrum maps: a spectrum is an outlier if its fit fails the R² gate
  or one of its main peaks lies outside the point's 1.5x IQR fence on centre,
  FWHM or intensity; 25 of the rest are kept at random, seeded by the point so
  a rerun keeps the same ones. The main peaks are read from the data: the
  strongest by median intensity and any within half of it. Applied once after
  fitting, so the stats tables, ratios, quality figures, fit grids and workbook
  all describe the same 25. The page says how many were fitted, removed and
  left out. Numbers move for every point that had more than 25 spectra; a point
  with 25 or fewer is untouched.
- **Each quality-figure panel's legend shows "Robust CV = x %" under its
  median** (`peak_quality.robust_cv`): 1.4826 x MAD / |median| x 100 over the
  same pooled, cleaned points the median line is drawn from, two decimals
  below 10 % and one above. Drawn only; no table or workbook number moves.
- **Every QC Report PNG is 300 dpi** (were 144 for the summary and fit-grid
  pages, 160 for the OM, domain-size and quality figures), from one constant,
  `core.report.REPORT_DPI`. The figures are laid out in inches, so only the
  pixel count changes: the summary page is now 4000x2250. No numbers move.

### Presets

- **`WSe2_QC` fits Raman from 1 start, down from 3.** A 36-spectrum map per
  point (324 Raman spectra per wafer) took about an hour on one core at ~12 s a
  spectrum; one start is 4x faster. PL keeps its 3 starts (0.5 s a spectrum),
  and `WSe2_QC_broad` is unchanged. Numbers move for Raman spectra where an
  extra start found a lower chi-squared.
  - Measured on 72 spectra from QC_HADI09 and QC_HADI05 (P1/P5/P9, every third
    spectrum): 222 s against 900 s. Every 1-start fit converged, so convergence
    cannot be used to decide when more starts are needed. 12 of 72 reached a
    worse minimum than 3 starts (chi-squared +2 to +25 %), all in the
    low-frequency peaks LB, C and the zero-line `center`; E2g+A1g's FWHM agreed
    to 0.01 cm-1 on every one. C/LB is the reported number that can move.

## [5.27.0] - 2026-10-19

### Presets

- **WSe2's bilayer (2L) "below" cut is now -7.8 %, up from -9.0 %** (the pair is
  -7.8 / +4.5) in `WSe2`, `WSe2_QC` and `WSe2_QC_broad`. The "above" cut and the
  1L pair (-6.0 / +4.2) do not change; the two cuts are independent, so Above 2L
  does not move. Numbers move: Below 2L rises and Bilayer falls by the same
  amount on every WSe2 wafer segmented on 2L; stored results from before this
  version are not comparable on those two columns.
  - Measured on 98 labelled 2L frames (12 HA wafers, HADH/HADI): in a 2L frame
    the 2L film's own tail falls steeply to about -7.5 % and the monolayer forms
    a flat band from about -8 to -10 % (pixel density 0.70-0.72 % per 1 %
    contrast), so -9.0 sat in the middle of the monolayer and missed its upper
    half. Median Below 2L over the frames: 0.47 % at -9.0, 0.76 % at -7.8;
    HADH26 6.8 -> ~10.8 %, HADH38 8.3 -> ~10.5 %, HADH70 6.4 -> ~8.2 %.
  - Noise: the frames' sigma_noise/mode is 1.1 % median, 2.0 % p90, 2.8 % max,
    so -7.8 is >= 2.8x the noise on every frame (CLAUDE.md asks for ~2).

## [5.26.0] - 2026-10-18

### Fitting

- **A preset can name a dominant peak** (`dominant_peak`, per technique; the
  Material Presets page has a selector under the peak table). Every other
  peak's fitted maximum is then held at or below that peak's: the fit varies
  each other peak's height as a fraction (`hratio`, 0-1) of the dominant
  peak's height and derives its area from it, so the bound is on the
  *intensity* every surface reports, not on the area. A peak held level with
  the dominant one (`hratio` at 1) is the constraint's value, not the data's,
  and is flagged `on_limit`, so ratios skip it as unmeasured. Unset, every
  area fits freely as before; the golden-fit record does not move.

### Presets

- **`MoS2_QC` PL holds A0 dominant** over A- and B. The neutral exciton is the
  main emission of these 1L films, but fitted freely A- came out taller
  (QC_KRX261005 with the MoS2 preset: A- 1624 against A0 1460 on average, A0
  found in 120 of 225 spectra). Numbers move for MoS2_QC PL.
- **`WSe2_QC` and `MoS2_QC` get `max_iterations` = 10000** for Raman and PL
  (were 5700/500 and 500/2100), so fits stop on convergence rather than on the
  budget. Numbers move only for fits that used to run out.

## [5.25.0] - 2026-10-17

### Presets

- **`WSe2_QC` PL fits from 3 starting points** (was 1). On 110 real WSe2 PL
  spectra (HA1P01, VB1P01, TSMC) the preset's single start reached the best of
  five starts on 71 %; three starts reach it on 98 % (median R2 0.9919 -> 0.9970).
  The misses were mostly VA/VB spectra, some with 10-50x the best fit's residual
  and Exciton/Trion centres up to ~13 nm off. A PL fit takes ~0.5 s, so three
  cost ~1.5 s. Numbers move for WSe2_QC PL; Raman does not change.
- **New preset `WSe2_QC_broad`** for samples whose main band is wider than
  WSe2_QC allows (DDAA08: 12.2 cm-1 measured directly from the data, against
  E2g+A1g's 9 cm-1 ceiling, which it sat on in 98 % of spectra). Identical to
  `WSe2_QC` except the main band's width ceilings: E2g+A1g 14, 2LA 15, 2ZA(M)
  7.5 cm-1. On 40 DDAA08 spectra E2g+A1g fits 12.5 cm-1 and sits on its ceiling
  in 2 % (main-band R2 0.959 -> 0.979). Choose it per folder; raising the
  ceiling in `WSe2_QC` itself lets noisy spectra widen falsely. E2g and A1g
  stay one peak: fitting them separately was tested and every part filled its
  own ceiling.

### QC Report

- **B2g / E2g+A1g is reported again**, as a third diagnostic ratio beside
  LA/E2g+A1g and C/LB (summary table and quality figure, which now has three
  rows in its ratio column). B2g appears with a second layer, so a 1L film's
  B2g is under 3x the noise and the ratio reads as absent, while a 2L film shows
  a measurable one. No spec line: the threshold is the reader's.
- **A missing grid point no longer moves the one below it up.** The fitted-
  spectra grid stacked a column's points in whatever order they existed, so with
  point 4 absent, point 7 was drawn in the middle row beside points 5 and 6. The
  missing point now keeps its row, empty and labelled "no spectrum".

The golden-fit record does not move.

## [5.24.0] - 2026-10-17

### Added

- **Zero-line calibration for Raman.** New block setting
  `zero_line_calibration` (Material Presets: a checkbox on Raman blocks). Before
  cropping, baseline and fitting, the laser line is located within +-3 cm-1 of 0
  (sub-point, by a parabola through the maximum) and its position is subtracted
  from the axis; the offset is recorded on the spectrum and written to both CSVs
  and the workbook (`Zero_Offset`), and the run summary states it. A spectrum
  whose line does not stand 10x above the side-band noise is left as recorded.
  On 1,527 real WSe2 spectra the line sat at a median +0.1 cm-1 (spread 0.46
  between sessions, 0.1 within a wafer) and its offset correlated with the
  E2g+A1g position (r = 0.45); subtracting it narrowed the E2g+A1g scatter from
  1.10 to 0.98 cm-1 across spectra and from 0.82 to 0.64 cm-1 across wafer
  medians. The window is narrow on purpose: at +-20 cm-1 the notch filter's
  edge, a few cm-1 up, was taken for the line on 18 % of spectra, and correcting
  by it doubled the scatter. On in `WSe2_QC` and `MoS2_QC`; off elsewhere, so
  the golden fit does not move. Numbers move for those two presets: every Raman
  position shifts by its spectrum's offset (e.g. HADI49 E2g+A1g -0.68 cm-1,
  Samsung QC_MoS2 A1g -1.14 cm-1).

## [5.23.0] - 2026-10-17

The remaining items of the 2026-10-15 review, except three left open on purpose
(the LA/E2g+A1g 0.13 spec line, which needs re-deriving on WSe2_QC data; the
domain-marker colour-scale mismatch; and the fitted-spectra grid restacking a
missing middle point). The golden-fit record does not move: every new fitting
behaviour is opt-in per preset and the frozen golden preset uses none of it.

### Fitting

- **A fit can run from several starting points and keep the best.** New block
  setting `fit_starts` (Material Presets: *Fit starting points*, default 1).
  Start 1 is the preset's own guesses; each further start moves every starting
  value by up to 25 % of its bounded range (seeded, so a refit reproduces), and
  the fit with the lowest chi-squared is kept, a converged fit beating one that
  ran out of budget. Levenberg-Marquardt stops at the nearest minimum: on 91
  real WSe2 spectra the preset's single start reached the best main-band fit
  62 % of the time. `WSe2_QC` now uses 3 starts: 89 %, at a median 16 s per
  spectrum (4 s with one start; the old `WSe2` took 29 s). `MoS2_QC` PL uses 3.
- **Every fitted peak records whether it ended on a fit limit**
  (`FittedPeak.on_limit`: centre on its range edge, or Voigt width on its
  ceiling).
- **Ratios use only measured peaks.** LA/E2g+A1g and C/LB (summary table and
  quality figure alike) now skip a point where either peak is on a fit limit
  or under 3x its fit's residual noise; `n` counts the points used, and a ratio
  with none reads as absent. On HA1P01 WSe2 spectra C sits on its range edge and
  width ceiling ~70 % of the time, fitting the laser line's tail, and LB is
  under 3x the noise on many. Four window-1 set-ups were tested on 91 spectra;
  the only one that freed C (window from 8 cm-1, quadratic baseline) showed LB
  under 3x the noise on 74 % of spectra, so the setup cannot rescue C/LB on
  these wafers. Numbers move: C/LB is reported on fewer points, often none for
  HA wafers; LA/E2g+A1g loses the points whose E2g+A1g sits on its ceiling (on
  the test set, the noisiest 12 %: median SNR 19 against 117).
- **`MoS2_QC` PL ranges widened to +-12 nm** (A0 682, A- 700, B 632) with 3
  starts: A0 on its range edge 24 % -> 7 %, B 5 % -> 1 %; A- still presses its
  712 nm edge on 18 % (the emission's red tail). R2 median 0.9990 unchanged.

### QC Report and exports

- **Map files named `RM_MAPPING_N.txt` / `PL_MAPPING_N.txt` are found** as Raman
  / PL point N (they were ignored, so a mapped sample reported no spectra).
- **The workbook's per-point sheets gain a `Spectrum` column** ("Raman_1__3" is
  the third column of a map). A 25-spectrum map used to give 25 x n_peaks rows
  all reading Point 1 / Raman_1.txt.
- **Batch PNG export follows "Peaks on Background"** as Quick Export does.
- **The PL quality figure says what it drops**: its title names the FWHM floor
  and top-5 % cut the summary table does not apply, which is why their `n`
  differ. Two comments claiming they could not disagree were corrected.

### OM

- **A tenth grid point is analysed** (segmentation, figures, layer suggestion)
  and drawn on the summary page in a fourth row; it was dropped without a word.
- **A mask margin of 0 no longer crashes circular frames** (scipy read zero
  erosion iterations as "erode until empty"); it now analyses the whole
  aperture.
- **The page warns when a fixed threshold sits inside a frame's noise**
  (threshold under 2x sigma_noise/mode, the CLAUDE.md rule), naming the frames.

### Runcard

- **The temperature trace no longer runs backwards after heater-off.** A
  `Heater Ramp` issued after heater-off is ignored, as the reference ignores
  it; a ramp still running when the heater is cut ends at the temperature it
  had reached (interpolated), and the cooldown starts there. The reference pins
  the ramp's target instead: a deliberate departure, recorded in
  docs/reference/README.md. Numbers move on 1 of 2,349 real cards:
  NSVA1P012512040 goes from Peak 850 °C with no window to Peak 564 °C with a
  zero-length window at 35 min.
- **Zero PC, RTV and spin setpoints are drawn.** `MFC/PC,PC-2,0`, `RTV ...,0` and
  `Stage Rot,0` used to end their gantt row; each is now held to the next
  command or the end of the run, labelled "0", "vac" or "0 rpm" as the reference
  does. Gas rows keep their idle floor. Gantt rows change on 330 of 2,349 cards
  (324 gain a zero segment, 6 lose a zero-length one) and now match the
  reference on all 2,250 non-Pulse cards. Peaks, windows and the summary block
  do not move.
- **`Heater Soak,<T>` with T != 0 is a setpoint step, not heater-off.** On the
  tool `Heater Soak,600,--` steps the heater to 600 °C and holds it; only
  `Heater Soak,0` turns it off. The trace holds the soak temperature and the
  cooldown starts at the zero soak (a deliberate departure from the reference).
  The trace changes on 60 of 2,349 cards; on the NSHA1N012506029-036 two-step
  family the window and peak do not move (50-70 min at 900 °C). The only window
  change is ymc/TestSoak.csv: now 0-80 min at 900 °C instead of none.

### Datalog

- **Bulk rename skips stale copies the run list hides.** A byte-identical
  `06173312.csv` beside its renamed run was renamed to `2026-08-06_173312.csv`,
  came back as a second row and could never be cleaned up. On HA1P01 this would
  have hit 12 hidden copies (7 renames, 5 collisions); now 0 are planned.
- **The runcard matcher forgives an aborted run for its cut-short last wait.**
  The rule needed the run's last block to align with the card's last, which a
  run stopped mid-card never does. Costs drop for aborted runs only (HADH79
  0.58 -> 0, HADH71_FAIL 1.28 -> 0.65); suggestions and confidence unchanged
  across 125 tagged runs.
- **The violations table no longer judges setpoints this run didn't command.**
  The opening stretch where a channel's SV is still the previous run's is
  skipped (the block the comparison alignment already excludes), as are MFC
  setpoints below 0.05 sccm and pressure setpoints below 1.0. On HADI49 this
  removes the heater's 844 s at 96.6 % and MFC-3's 930 % at 0.01 sccm (87 -> 85
  segments; P1's PV sits on the 5 % line and still flickers, and the heater's
  post-growth cooldown with SV left at 900 remains: no heater-on column exists
  to detect it). thresholds.json is unchanged.
- **A run whose last line has an empty Time field no longer crashes the page**
  (NaT slipped past the start-time fallback).
- **Opening a run no longer fails on a non-UTF-8 byte or a BOM.**
- **Duplicate cleanup re-checks each copy (size and SHA-256) immediately before
  the Recycle Bin**, skipping with the reason one that changed or whose keeper
  disappeared.
- **Saving tolerances keeps the override of a channel the open run lacks**;
  only the channels shown are rewritten, against the file as it is at save time.

### Insight scripts (Result/Raman_insights/_scripts, outside the app)

- **Wavelength-axis Raman is converted with each file's own laser line.** QC_KRX261001/2
  were converted with the nominal 488.0 nm and put every WSe2 peak ~+5 cm-1 high
  (main band 255, B2g 313): their Rayleigh line sits at ~488.12 nm. The line is
  now located per file (median over the map's spectra, sub-point centroid) and
  used when it stands clear of the background. With 3 starts, KRX's E2g+A1g centre on its range edge drops from 97 % to 19 % of
  spectra and 2LA-taller-than-E2g+A1g from 41 % to 9 %. Its width still reaches the
  9 cm-1 ceiling on 56 % (noisy map spectra), as does DDAA08's on 71 % (a main band
  genuinely broader than 9 cm-1); those fits are flagged on-limit and their FWHM is
  a lower bound.
- **Folder overrides apply to the TSMC tree only, and VABD90 is skipped in every
  source**, as the TSMC discovery and the extraction scripts already did; the
  VB1P01 copy was refitted with the TSMC copy's "names swapped" override and
  failed its nine PL spectra.

## [5.22.0] - 2026-10-16

### Added

- **Fit windows: a preset can split its spectrum into stretches, each with its
  own baseline and its own fit, shown together as one.** A Raman or PL block
  takes an optional *Fit windows* table on the Material Presets page. Each row
  is an x range; the peaks whose centre falls in it are fitted there, against a
  baseline computed on that range alone (its own algorithm, lambda/p/degree and
  exclusions, or the block's when left empty). The windows' corrected data,
  baselines, fits and residuals are then put side by side, so the Spectra page,
  the exports, the ratios and the QC Report read one spectrum and one fit as
  before. A window owns `x_min <= x < x_max` (the last one includes its end);
  points outside every window are dropped from the processed spectrum.
  - Why: on 91 real WSe2 Raman spectra, one 8-peak fit over -5..500 cm-1 took
    a median 29 s (worst 136 s); the same peaks in three windows took 4 s
    (worst 41 s) with the main-band E2g+A1g FWHM within 0.08 cm-1.
  - `max_iterations` is spent per window; the fit is `converged` only if every
    window's fit is.
  - Separate baselines meet with a step at each window edge; *Peaks on
    Background* shows it.
  - A window the spectrum does not reach (under 10 points) is skipped and its
    peaks are absent, so a curve covering only 200-330 cm-1 still gets its
    main band fitted; only a spectrum that reaches no window fails.
- `scripts/recommend_max_iterations.py`: runs a folder of spectra through a
  preset with the budget at its ceiling and prints, per preset and technique,
  how many iterations the fits needed, how many the current cap would cut
  short, and a recommended cap.

- **New preset `WSe2_QC`**, beside `WSe2` (which is unchanged). Raman is fitted
  in three windows, -5..40, 40..210 and 210..330 cm-1, each on its own
  baseline: ALS (the block's settings) for the first and last, a straight line
  fitted outside 80-180 cm-1 under the LA hump. Centre ranges were set from
  where the peaks sit on 91 real spectra so the main-band peaks cannot trade
  places: 2ZA(M) 240-246, E2g+A1g 247-254, 2LA 255-266, LA 119-135 (start
  width 30). Other widths are WSe2's: narrower or wider starts were tested and
  made the fits worse. `max_iterations` is 5700 for Raman (1.5x the most any of
  795 September HA1P01 spectra needed) and 500 for PL.
  - On those 795 spectra: median 1.4 s per fit against 12.1 s for `WSe2`,
    worst 34 s against 185 s. On the 91-spectrum test set the preset's start
    reaches the best main-band fit 62 % of the time (37 % before the new
    ranges), and 2LA comes out taller than E2g+A1g on 2 % (23 %).
  - LA/E2g+A1g reads lower than with `WSe2` (median 0.08 against 0.15): the
    straight-line baseline leaves the whole LA hump to LA, whose width now
    settles at ~38 cm-1 instead of on its 45 cm-1 limit. The 0.13 spec line
    was set on the old method and is not comparable.

- **New preset `MoS2_QC`**, beside `MoS2` (unchanged). Raman is MoS2's: on
  the Samsung QC_MoS2 maps (225 spectra) E2g, A1g and 2LA(M) are never on a
  bound and fit in 0.1 s, so it needs no windows. PL moves A0 to 682 +- 8 nm and
  A- to 697 +- 8 nm, onto the measured ~688 nm emission; MoS2 starts A0 at
  662 nm, where it fitted to near zero (median intensity 103 against A-'s 1561)
  and sat on its range edge in a third of spectra. On the 225 PL spectra the
  preset's start now reaches the best of three starts 75 % of the time (52 %),
  with median R2 0.9989 (0.9980). `max_iterations` is 500 for Raman and 2100
  for PL (1.5x the most any spectrum needed; MoS2's 2000 cut 2 PL fits short).
  Optical block as MoS2.

- **OM: the layer suggestion follows copied presets, and refuses noisy frames.**
  The 1L/2L colour calibration was looked up by exact material name, so
  `WSe2_QC` (and `WSe2 extended`, `WSe2_truncate`) got no suggestion and the
  QC Report silently fell back to 2L. A name now falls back to its leading
  word (`WSe2_QC` -> `WSe2`); `MoS2` and `MoS2_QC` remain uncalibrated. Frames
  whose noise exceeds 3 % of the film level now get no suggestion: on 26 WSe2
  wafers both confidently wrong calls had 4.2 % and 5.3 % noise, every correct
  one 2.2 % or less.
- **OM: the minimum domain size is set in microns, per magnification.** New
  optical fields `min_domain_um_50x` / `min_domain_um_100x` (equivalent-circle
  diameter). The fixed 40-pixel floor was 1.07 um at 50x but 0.53 um at 100x.
  `WSe2_QC` sets 1.0 um at 50x (35 px) and 0.5 um at 100x (36 px), both close
  to before: a 0.5 um floor at 50x, the lens's resolution limit, multiplied
  HADH51's domain count 12x. Empty keeps 40 px.
- **OM: `domain_size_enabled = no` hides the domain-size measurement** for a
  material and layer, and a material whose optical block names a single layer
  is analysed at that layer without asking. `MoS2_QC` uses both: monolayer
  only (below 1L / 1L / above 1L), no layer suggestion, no domain size,
  thresholds 3.5 % below (MoS2's step to substrate, ~7 %, is smaller than
  WSe2's, so WSe2's 6 % cut sits on the substrate) and 4.2 % above (copied from
  WSe2; no MoS2 2L has been measured), flat-field divisor 12 as MoS2.

### Unchanged

- Numbers do not move for any preset without fit windows, which is every
  shipped preset: the single-fit path is the same code, and the golden-fit
  record passes unchanged.

## [5.21.0] - 2026-10-15

Four defects found by a full review of the algorithms and their outputs. The
golden-fit record was regenerated: the Raman numbers move (see the first item),
the PL numbers do not.

### Fixed

- **A Raman fit that ran out of iterations was reported as converged, with
  every uncertainty printed as 0.** lmfit gives the message "Tolerance seems to
  be too small" both to a fit that cannot improve further and to one that hit
  `max_nfev` (MINPACK ier 5, or lmfit's own ier -1 abort), and
  `fit_voigt_peaks` accepted the message. On the shipped WSe2 preset a fifth
  of real spectra stop this way (2 of 9 on HADI44, 2 of 6 on HADI49; the
  golden RM_1 needed 13,889 evaluations against a budget of 10,000), and the
  numbers depended on the budget: the golden spectrum's C/LB stacking ratio
  read 3.75 at 2,000, 2.20 at 10,000 and 2.07 converged.
  - `FitResult.converged`, `nfev` and `max_nfev` record what happened. A
    budget-exhausted fit is kept — its peaks are the best found — but flagged
    on the Spectra page (beneath the Fit Results table and in the Run summary),
    in the batch run's summary, in a `Converged` column in both CSVs and the
    workbook's per-technique sheets, and as a warning in the QC Report's
    Results section with the count.
  - The preset's `max_iterations` now means iterations. It was passed straight
    to lmfit as `max_nfev`, which counts function evaluations; one iteration
    costs (variables + 1) of them, so an 8-peak fit had 1/33 of the budget its
    preset named. It is now `max_iterations * (variables + 1)`, lmfit's own
    default scaling. Every budget-stopped real spectrum tested converges
    within 24,000–38,000 evaluations (17–28 s at 0.72 ms per evaluation); a
    fit that converges quickly is unaffected.
  - A standard error lmfit could not estimate is NaN — an empty cell in the
    CSVs and the workbook — never 0.0. This is common on converged WSe2 Raman
    fits too: a peak fitted to zero or a width on its bound makes the
    covariance singular, and the old 0.0 printed as "perfectly determined".
  - Golden record regenerated. RM_1 is now a converged fit: R² 0.89574 →
    0.89625, B2g centre 313.98 → 318.33, C centre 18.01 → 12.92 and width
    4.57 → 14.93, LB intensity 1.29 → 0.76, E2g+A1g within 0.02 cm⁻¹. PL_1 is
    unchanged to every digit. The record now carries `converged`.
- **Runcard: the growth window of a pulsed-growth recipe was 0 min.** Every
  recipe written since mid-2025 grows with `Pulse H2Se,N,half` (72 pulses of
  5 s on / 5 s off on HADI-series cards), which neither the vendored reference
  nor the port put on the clock. Heater-off then landed on the same second the
  peak was reached: 44 of the 2,349 real HA1P01/VB1P01 cards reconstructed a
  zero-length window, the caption read "Growth (0 min @ 890 °C)" on a
  13-minute growth, total time was short by it, and every "at the growth
  midpoint" value was read from the instant the gas was switched off. `Pulse`,
  `Pulse H2Se` and `Pulse O2` now cost `N × 2 × half` plus a measured 1.5 s of
  controller switching per pulse (834 s logged for `Pulse H2Se,72,5`, 139 s
  for `12,5`, 367 s for `Pulse,6,30`). `Check PC-*` and `Stage Pos` stay off
  the clock, like `Pumping`, as holds of unstated length. Recorded as a
  deliberate divergence in docs/reference/README.md.
- **Runcard: the inner/outer tube rows used HA plumbing for every VB1P01
  card.** The series was read from the run id's first two letters, which on
  every production card are the tool-file prefix `NS` (`NSHA1P01…`,
  `NSVA1P01…`), so all 1,065 such cards fell back to HA's MFC 1+3+4 / 6+7+8 —
  right by luck for the 492 NSHA cards, wrong for the 577 NSVA cards, whose
  outer row summed the H2Se line alone instead of MFC 5–8. The series is now
  read past the prefix, and a run id that names no series gets a note under
  its chart saying the tube rows assume HA, as the reference prints.
- **Datalog: runcard matching charged every October run a flat 2.0.** The
  matcher assumed MFC-8 (H2Se) logs its 0.01 sccm setpoint; since 2026-10 the
  controller logs it as 0 (0 of 103 October logs show it, 101 of 137 in
  September; MFC-3 still logs its 0.01), so each card's two MFC-8 0.01 steps
  cost 1.0 against a log that shows nothing. Growth runs scored at the
  "medium" ceiling and several (HADI33/38/40/42/49) fell to "low" and were not
  pre-ticked, while the "Differences" column listed MFC-8 steps on every run.
  Which MFCs can show a sub-resolution setpoint is now read off each log
  (`RunSteps.coarse`: any channel never logged strictly between 0 and
  0.05 sccm in that run); the fixed list remains only as the fallback for a
  caller with no log. On the 25 newest tagged runs the confidence counts go
  from 2 high / 17 medium / 6 low to 19 high / 4 medium / 2 low, with 24 of
  25 named correctly either way -- the same split September's runs score.

## [5.20.0] - 2026-10-14

### Added

- **The QC Report suggests the layer.** Section 3 now reads the frames'
  colour and pre-fills *The film being measured* with 1L or 2L, with the
  confidence and the evidence in a caption beneath it. Until now the layer was
  always the operator's guess, defaulting to 2L, and a wrong guess relabels
  every class by one layer with nothing on screen to show it.
  - WSe2 only. Any other material keeps the plain 2L default.
  - It is a suggestion. A layer you pick is never overwritten; the caption
    says when your choice disagrees with the colour. A new folder clears your
    pick; a new magnification or material re-suggests unless you picked.
  - When the colour is ambiguous (confidence below 0.6) the selector is left
    empty and the page stops until you choose, instead of quietly running 2L.
  - Evidence: B/G and R/G at the film mode, plus the share of the frame 7-14 %
    darker than the film, where monolayer sits inside a bilayer film. Absolute
    brightness is not used: the camera auto-exposes.
  - Calibrated on 2,423 frames from 296 wafers labelled by PL (strong PL = 1L)
    and tool (VA/VB1P01 = 1L, HA1P01 from 2026-09 = 2L). Cross-validated by
    wafer: 94 % agree with their label; 84 % clear the confidence floor and
    96 % of those agree; 6 of the 7 HA wafers with strong PL are called 1L.
    Weakest at 100x (79 %), where most disagreements are early-September HA
    wafers labelled 2L by date whose Raman also looks monolayer-like.
  - New module `modules/optical/processing/layer_suggest.py`.
- No existing number moves: segmentation, thresholds and every table are
  unchanged for a given layer.

## [5.19.0] - 2026-10-14

### Changed

- **WSe2's bilayer (2L) contrast pair is now -9.0 / +4.5 %, up from
  -6.0 / +4.25 %.** Every WSe2 wafer segmented on 2L moves: the Bilayer share
  rises and both Below 2L and Above 2L fall, since both cuts sit further from
  the film mode. Stored QC results from before this version are not comparable
  with new ones on these columns. The 1L pair (-6.0 / +4.2 %) is unchanged.
  - Measured on 884 labelled frames before the change: the 1L<->2L green step
    is about 10 %, so in a 2L frame monolayer regions sit near -9 to -10 %.
    A -9 % cut lies on that population rather than between the layers, so
    some monolayer will now read as Bilayer.
- **WSe2 1L domain marker spacing is 8 px, down from 80** (8 is the code
  default). Watershed markers may now sit ten times closer, so 1L domain
  counts rise and domain sizes fall. Layer coverage percentages do not move.
- The WSe2 preset description names both layers' pairs.
- The two QC Report integration tests that restated the WSe2 2L pair now read
  it from the preset.

## [5.18.0] - 2026-10-13

### Added

- **OneNote Publish works.** Step 4 signs in with Microsoft (device code:
  open the link, type the code) and uploads the ticked artifacts.
  - Pages go to the notebook named in `data/onenote.json`, in a section named
    for the tool (e.g. `HA1P01`), on a page titled with the wafer ID. The
    notebook, section and page are created on first use; later uploads append
    a dated section to the wafer's page.
  - The QC Report uploads its eight figures inline and its workbook as an
    attachment, and carries the material-check verdict. The Runcard uploads
    this wafer's profile chart.
  - Failures are listed under *Not uploaded* and never block the app.
  - Needs a one-time setup: put the Nexstrom app registration's `client_id`
    and `tenant_id` in `data/onenote.json` (delegated `Notes.ReadWrite.All`,
    public client flows on). Set `site` to `<host>:/sites/<path>` for a
    SharePoint team notebook; leave it empty for your own.
  - The sign-in token is cached under `%LOCALAPPDATA%/NexAnalyzer`, not in
    the repo.
- New dependencies: `msal`, `requests`.

### Fixed

- **OneNote "What's ready" now reflects the picked wafer.**
  - The QC Report counts as ready only when it was built from this wafer's
    optical folder. Before, a report built for one wafer showed as ready for
    every wafer picked after it. When the QC Report holds another wafer, the
    page names that wafer.
  - The Runcard column counts only this wafer's recipe chart, not every chart
    ticked on the Runcard page, and says *not linked* when the wafer has no
    runcard.
  - The QC Report's domain-size grid (added in v5.16.0) is now included, so the
    count is eight figures, not seven.
  - The Datalog column says it is not tracked yet: the Datalog page keeps no
    chart in session state for this page to read.
- `artifacts.runcard_payload()` looked charts up by run id, but the Runcard
  page keys them by recipe path, so it never found one. It now takes the path.

No fitted or reported numbers change.

## [5.17.0] - 2026-10-13

### Added

- **Runcard: Inner tube / Outer tube rows.** Two rows at the bottom of the
  recipe gantt sum the inner-tube and outer-tube MFC flows (sccm), so the rule
  that the inner tube runs at least 60 sccm above the outer can be checked at a
  glance. Which MFCs feed which tube is chosen from the run id's first two
  letters: VA and VB sum MFC 1+2+3+4 / 5+6+7+8, HA sums MFC 1+3+4 / 6+7+8, and
  any other series falls back to HA. The row label names the MFCs it sums.
  Stretches with zero total flow are left blank. Recipes with any gas line are
  two rows (60 px) taller; the PNG export follows. The right margin now widens
  to fit the longest row label, so these labels are not clipped.
  **No numbers move**: the metrics, growth window and summary are unchanged.
- `docs/reference/render_runcard.py` updated to the version that draws these
  rows (the only change is the combined-flow block).

### Changed

- **Runcard gantt colours match the reference's contrast.** Bars are opaque and
  filled at discrete levels: the dark stop at a row's maximum with pale text,
  the light stop below it with dark text, and a mid level for the pressure
  controllers. Before, every bar faded toward white at 85% opacity with grey
  text, which looked washed out beside the reference PNGs. Row names are
  printed in their row's colour.

### Fixed

- **Runcard gantt bars sat off their row labels**, by more the further down
  the row was. Each row is its own bar trace, and Plotly's default grouped bar
  mode gave every trace its own sub-slot in each row.

## [5.16.0] - 2026-10-13

### Added

- **Merged LYNIRS/NexAnalyzer's local additions** (listed below under "Local
  additions", previously shipped as `5.13.0+local.1`): flake domain size and
  nucleation density on the QC Report (Figure 4, `Domain_Size` sheet, editable
  µm/px calibration in `data/om_calibration.json`), the small/large population
  split, the "Low coverage" checkbox, per-preset watershed fields, the
  `WSe2` 1L optical block and `WSe2_Aggressive` preset, and the Spectra page's
  "Save PNGs to folder" batch export.

Numbers move only where those additions apply (domain size, density, WSe2 1L
optical segmentation); Raman fitting is unchanged from 5.15.0, including the
5.14.0 Voigt-FWHM bound and `2ZA(M)` at 1.64 cm-1.

## [5.15.0] - 2026-10-13

### Added

- **Tools page** (new "Setup" section): records where each tool folder is on
  this PC, once. It shows each tool's datalog, runcard and optical folders
  with whether each one is really there. You can edit a tool's subfolders and
  add new tools on the page.
- **Tool dropdown on Datalog, Runcard, QC Report and OneNote.**
  - **Datalog** and **Runcard**: choosing a tool points the page at that
    tool's folder.
  - **QC Report**: lists the tool's wafers, newest first, and picking one
    loads its sample folder.
  - **OneNote**: opens on the last tool used.
  - Every page keeps its folder picker for one-off folders.
- **VB1P01 in `data/tool_layouts.json`**: optical data in `OPTICAL`, runcards
  in `RUNCARD/LYH` (wafer lookup also searches `RUNCARD/JK`), datalogs in
  `DATALOG`.

### Changed

- Folder locations are split into two files:
  - `data/tool_layouts.json` (committed): each tool's subfolders, relative to
    the tool folder.
  - `data/tool_roots.json` (new, gitignored): where each tool folder is on
    this PC. Absolute paths include the Windows user name, so they cannot be
    shared.
- `tool_layout` moved from `modules/onenote/io` to `core/io`, because four
  pages now read it.
- The Datalog page's runcard-matching folder is still guessed from the
  datalog folder (`RUNCARD/lcy`). It does not come from the layout:
  HA1P01's CLEANING cards sit one level above the layout's
  `RUNCARD/lcy/NSHA1P01`, so the layout's folder would miss them.

No numbers move.

## [5.14.0] - 2026-10-09

### Fixed

- **A peak's width bound is now on its Voigt FWHM, so Lorentzian-shaped peaks
  are no longer capped at about half of it.** The fitter bounded sigma and
  gamma separately, each to half of `width_max` (3x the preset width), so a
  peak whose fit wanted a mostly Lorentzian shape stopped at ~0.52 x
  `width_max` with gamma on its bound: WSe2's E2g+A1g (preset 4 cm-1) piled up
  at 6.21 cm-1 across the TSMC/HA/VB datasets and the 7 cm-1 FWHM spec line
  could hardly be crossed. The fit now varies the Voigt FWHM itself
  (`vfwhm`, bounded to `[width_min, width_max]`) and `gmix`, which places the
  Gaussian share between its floor and 1; sigma and gamma are derived by an
  exact inversion of `voigt_fwhm`. The Gaussian floor is the one sigma always
  had (f_G >= width_min / 2) -- without it a capped peak turns pure Lorentzian
  to grow wings. The reported FWHM's stderr now comes straight from lmfit's
  covariance, so `voigt_fwhm_stderr` is gone.
- **`2ZA(M)` starts at 1.64 cm-1 (was 2.0) in `WSe2` and `WSe2_truncate`**, so
  its ceiling stays at ~4.9 cm-1 as chosen in v5.12.1. Under the new bound 2.0
  would have let it reach 6.0.

Numbers move. All datasets (TSMC_Raman_Spec + HA1P01 + VB1P01): the E2g+A1g
pile-up at 6.15-6.25 cm-1 is gone and R2 rises ~0.01. E2g+A1g's shape is
unchanged (median Lorentzian share 0.82), but 2ZA(M) -- no longer forced to
sit with both components on their maxima -- is more Lorentzian (0.54 -> 0.83)
and takes a little more of E2g+A1g's low-side wing: E2g+A1g FWHM narrows by a
median ~0.13 cm-1 on spectra that were not capped, while the formerly capped
ones widen. Other peaks that sat on the old cap now reach their real
`width_max`: Mode 1's LA goes from ~36.8 to its 45 cm-1 limit in most fits,
2LA spreads further. `WSe2 extended` / `V2` were not retuned.

## Local additions (on top of 5.13.0; app version 5.13.0+local.1)

Installation-specific changes carried on top of upstream. They were first
numbered 5.3.1-5.6.1 on a v5.3.0 checkout, which collides with upstream's own
5.4.0-5.6.0, so the numbers below are kept only as labels. Merged onto 5.10.5
on 2026-10-02 following upstream's 5.6.0 removal of the per-wafer adaptive
threshold: domain size and nucleation density now segment on the preset's
fixed pair, like every other OM figure. Re-merged onto 5.13.0 on 2026-10-06;
the WSe2 Raman range and iteration budget follow upstream (-5-500 cm-1, 10000),
with 200-300 cm-1 now upstream's own WSe2_truncate preset.

From Local 5.6.3 on, `core/version.py` reports these as `5.13.0+local.N`:
the upstream release they sit on, plus a build tag that can never collide
with a future upstream number. Local 5.6.3-5.6.6 ship as `5.13.0+local.1`.

### Local 5.6.6 - 2026-10-09

#### Changed

- **Figure 4's captions and title are three rows: flake count, nucleation
  density, domain size.** Previously the count and size shared one line
  with density below it. E.g. a frame now reads `n=86 + 8 edge` /
  `135.4/(100µm)²` / `2.23±0.59µm`. A split frame keeps the same order with
  one line per population for density and size (`n=2 small · 84 large + 8
  edge`, then both densities, then both sizes); the split title puts each
  row on one line. Uncalibrated figures skip the density row. No numbers
  move.

#### Fixed

- **Figure 4's first-row captions no longer run into the title.** A split
  frame's caption is five lines (two densities, two sizes, the edge count),
  and the header only reserved space for the title itself; it now also
  reserves room for the first row's tallest caption.

### Local 5.6.5 - 2026-10-08

#### Changed

- **Flakes cut by the frame edge now count one half toward nucleation
  density — density numbers move up.** `measure_domains()` used to drop
  every domain touching the analysed region's border from both size and
  count while still counting the full area, so density read low (1-18 % of
  domains per frame on VB1P01 test frames, most on sparse frames). Edge
  domains are still not sized, since their true size is cut off, but they
  are now kept as `DomainResult.n_edge`/`edge_mask` and counted
  `domain_size.EDGE_WEIGHT` = ½ each: a flake straddling the border is shared
  with the neighbouring field, so ½ is unbiased on average. One rule for
  rectangular and circular frames. `MIN_DOMAIN_PX` applies to edge domains
  too, unchanged. Density is now (sized + ½ · edge) / area on figure 4's
  captions and title, the summary-page table (new "Edge (×½)" column), the
  `Domain_Size` sheet (new `Edge_N` column, unsplit row only) and the
  on-screen caption. Size statistics (median, mean, std) and the small/large
  split do not move; per-population densities still count only sized
  domains, since an edge domain belongs to neither population.
- **Figure 4 draws edge domains** in the measured-domain red at a much lower
  alpha (0.25 vs 0.65), with a legend entry, so coverage matches the OM
  overlay.

### Local 5.6.4 - 2026-10-08

#### Fixed

- **Figure 4's title split into small/large populations with "Split into
  small/large size populations" unticked.** The per-frame panels, the
  summary-page table and the `Domain_Size` sheet already honoured the box,
  but `build_domain_grid_figure()` re-ran the pooled split for its title on
  its own. It now takes `detect_size_populations`, and the QC Report page
  passes the checkbox to it like it does to `measure_domains()` and
  `domain_size_stat()`. Unticked, the title shows the single pooled median,
  mean and density; ticked, nothing changes. No measured numbers move.

### Local 5.6.3 - 2026-10-08

#### Fixed

- **Domain size was under-reported by the frame's resize factor — numbers
  move.** `contrast.load()` shrinks every frame to `MAXDIM` (1400 px) before
  analysis, but the µm/px calibration (`data/om_calibration.json`) is
  measured on the full-size camera image and was applied to those shrunk
  pixels unconverted. On the VB1P01 camera's 2240×1680 frames each working
  pixel is 1.6 camera pixels, so every diameter read 1.6× too small and
  nucleation density 2.56× (1.6²) too high. `FrameResult` now carries
  `scale` (working ÷ native pixels, from the new `contrast.load_scaled()`),
  and `measure_domains()` divides the calibration by it. Diameters in µm go
  up ×1.6 and density per 100 µm² down ×2.56 on 2240-px frames; frames
  already ≤1400 px, pixel-unit (uncalibrated) results, the OM segmentation
  itself, and the small/large population split (a ratio test) do not move.
  The stored calibration values are unchanged and keep their meaning:
  microns per native camera pixel. The reported size is still the
  equivalent circular diameter, 2·√(area/π).

### Local 5.6.2 - 2026-10-03

#### Added

- **Batch image export on the Spectra page.** The "Batch Export" section
  (previously just "Save Master CSV to folder") gains a second button,
  "Save PNGs to folder": one native folder-picker dialog, then one static
  fit-results PNG per fitted file written straight to that folder — the
  same `plot_composite()` rendering and View Options (`show_components`/
  `show_residuals`) Quick Export's own single-PNG download already uses, so
  a batch image matches what the operator is currently looking at. New
  `core.io.export.prompt_save_folder()` (an `askdirectory` dialog, the
  multi-file sibling of `prompt_save_path()`'s single-file `asksaveasfilename`)
  backs it. Kaleido missing is reported once and stops the batch, rather
  than repeating the same error per file; a per-file failure that isn't
  that is listed in a collapsed "N file(s) failed" expander instead of
  aborting the rest.

### Local 5.6.1 - 2026-10-02

#### Changed

- **Nucleation density is now reported per (100 µm)² instead of per µm²**
  (`domain_size.DENSITY_AREA_UM2`), everywhere 5.6.0 added it — the figure,
  the summary table, the xlsx `Domain_Size` sheet's `Density_per_100um2`
  column (renamed from `Density_um2`), and the on-screen caption. A rate per
  one µm² is a fraction far below one for any real sample; scaled up to a
  block size an operator can picture, the same numbers read as whole-ish
  numbers instead. `DomainSizeStat.density_per_100um2` and
  `SizePopulationStat.density_per_100um2` replace the old `density_um2`
  fields (`area_um2` itself is unchanged — still the real analyzed area in
  µm²). Also switched the display format from 3 significant figures to a
  fixed one decimal place: `.3g` existed only to stop a small population's
  density rounding to "0.00" under the old, much smaller per-µm² numbers,
  and would have rendered the new, larger ones in scientific notation
  instead (e.g. "2.96e+03") for no reason, now that the scale change alone
  keeps every value readable.

### Local 5.6.0 - 2026-10-01

#### Added

- **Nucleation density** — domain count over the frame's own analyzed area,
  reported as flakes/µm² (and per population — small/large each get their
  own — when the split is on). Area is `valid.sum() * um_per_px²`, the same
  calibration every other domain-size number already uses; no new preset
  field. Shown everywhere domain size already is: each frame's own panel and
  the sample-level title on the domain-size figure (each on its own new
  line — a combined "small: X/µm² · large: Y/µm²" line was wide enough to
  bleed into the next panel's own label before it was split in two), a new
  "Density (/µm²)" column on the summary page's table, a new `Density_um2`
  column on the xlsx `Domain_Size` sheet, and the on-screen caption.
  Omitted, not invented, when the sample isn't calibrated — a density in
  1/px² isn't comparable across magnifications.
- **"Split into small/large size populations" is now a checkbox on the QC
  Report page**, next to "Measure domain size," off by default and
  remembered per installation (`report_settings.json`, same mechanism as the
  domain-size checkbox itself). The split (added in 5.5.0) had been firing
  on frames with no real second population at all — pure background texture
  with enough size spread to clear the ≥2x ratio test on its own — so
  running it unconditionally meant trusting every result without a way to
  turn it off. `detect_size_populations` (the new argument on
  `measure_domains()`/`domain_size_stat()`) short-circuits the split call
  entirely when off, not just discards its result.
- **Watershed mask edge smoothing is now a per-material/per-layer preset
  field**, `OpticalParams.mask_closing_radius` (mirroring
  `marker_min_distance`/`marker_margin_sigma`, off/0 by default).
  `classify()`'s per-pixel threshold reproduces a flake's real, noisy edge as
  a ragged boundary rather than a clean line; wherever that raggedness opens
  a small gap, the domain on the far side becomes its own separately-counted
  domain sitting right at or inside the real flake's footprint. Raising the
  radius applies a morphological closing to the target mask before
  connected-component/marker/watershed, bridging small gaps so a fringe
  fragment is measured as part of its parent flake instead. Both new fields
  are now editable on the Material Presets page, alongside
  `marker_margin_sigma` — `marker_min_distance` had been added in 5.5.0 as a
  preset field but was never wired into that page's own editing form, so the
  only way to set it was hand-editing `materials.json`.

### Local 5.5.0 - 2026-09-22

#### Added

- **Domain-size analysis now separates a sample's flakes into a small and a
  large size population when they genuinely differ by at least 2x**, instead
  of reporting one pooled mean that blends two real, differently-sized
  populations into a number that describes neither (found on a real sample:
  a few ~micron-scale flakes among an excess of sub-micron ones — a
  continuous, unimodal spread and a genuine two-population sample look
  different on a density plot, not just by eye). `domain_size
  .split_size_populations()` looks for a valley in a Gaussian KDE over
  `log10(diameter)` rather than the single biggest gap between adjacent
  sorted diameters — that check is dominated by the sparse tail's random
  spacing on real (noisy) data and misses a genuine valley that still has a
  few points scattered through it. Below `MIN_DOMAINS_FOR_SPLIT` (20) domains,
  or when no valley clears the 2x ratio, it reports one population exactly as
  before — this is a strict addition, not a change to the ordinary case.
  When the QC Report page's own "Split into small/large size populations"
  checkbox (added in 5.6.0) is on, each frame's domain-size figure colours
  that frame's own two populations separately (small/large) when that
  frame's own domains split, and falls back to today's single-colour look
  otherwise; the summary page's table and the on-screen caption instead use
  one consistent split computed from the whole sample's pooled diameters,
  which is what the `Domain_Size` xlsx sheet also reads.
- **Domain size reaches the xlsx export for the first time**, as a new
  `Domain_Size` sheet — one row normally, two (`Small`/`Large`) when the
  sample's domains split. Previously only the on-screen figure and the
  PDF-style summary table carried this number; the workbook had none of it.
- **Watershed marker spacing is now a per-material/per-layer preset field**,
  `OpticalParams.marker_min_distance` (mirroring `marker_margin_sigma`
  exactly), rather than the fixed module constant
  `domain_size.DEFAULT_MARKER_MIN_DISTANCE` (8, unchanged as the default — the
  value the "above" path was validated against for VBBF46). Raise it for a
  material whose flakes show internal reflection highlights bright enough to
  be watershed-split into several markers, and so several domains, instead of
  staying one — which is also what a real size-population split needs to see
  past: oversplitting one large flake into same-sized-as-noise fragments
  smears its true size down into the small population's range and can hide
  a real gap entirely.

### Local 5.4.0 - 2026-09-20

#### Added

- **The QC Report measures flake domain size, as its own numbered figure and
  a summary-page table.** "4 — Domain size analysis" sits between OM
  diagnostic and the (renumbered 5-8) Raman/PL figures, splitting one of
  `classify()`'s three OM classes into individual flakes via watershed, using
  each flake's own optical extremum (a reflection highlight, or for a
  below-reference class its own darkest point) as its marker. **Which class
  is operator's choice** — below reference / reference / above reference, the
  same three `contrast.class_labels()` already names on the OM table, shown
  as a selector once the checkbox below is on; it shares the existing
  reference-layer choice rather than triggering a second segmentation pass.
  Size is reported as equivalent circular diameter, `2*sqrt(area/pi)`, pooled
  across a sample's frames into a median and mean ± std on the summary page.
  A domain touching the analysed region's border is excluded — its true size
  is unknown, not zero. It is opt-in: a checkbox on the QC Report page, off
  by default and remembered per installation in `report_settings.json`,
  since the measurement re-runs a segmentation-equivalent pass per frame and
  roughly doubles optical analysis time.
- **A new, editable µm/px calibration, keyed by magnification.** Domain size
  is reported in physical units only once a magnification has a calibration
  entered on the Material Presets page; before that it honestly reports
  pixels rather than inventing a number. Magnification, not material, is the
  key — a 50x objective's field of view doesn't depend on what's under it.
  Stored in the new, committed `data/om_calibration.json` (`core/io/om_calibration.py`),
  separate from the existing per-installation `report_settings.json`. The
  lookup is case-insensitive: a folder's magnification prefix is read
  verbatim from its filenames (`sample_scanner`'s "any other prefix"), so
  "50X" on disk and "50x" typed into the calibration table must still match,
  the way `default_magnification` already treats them as the same
  magnification — a mismatch silently fell back to pixel units with no error.
- `scikit-image` is now a dependency, for the domain split's watershed and
  local-maximum marker search.
- **A raw OM component with no point clearing the watershed marker floor now
  survives as one domain instead of vanishing.** Previously a flat,
  low-contrast component — common outside the sparse-nucleation samples the
  method was validated against — produced zero markers and was silently
  dropped from the count entirely, undercounting badly against the OM class
  table's own (floor-free) component count. One fallback marker per
  un-marked component, at its own extremum regardless of the floor, fixes
  this without touching components that already had a real marker.
- **The marker floor's sensitivity is now a per-material/per-layer preset
  field**, `OpticalParams.marker_margin_sigma` (edited on the Material
  Presets page as "domain marker sensitivity (σ)"), rather than the fixed
  module constant `domain_size.MARKER_MARGIN_SIGMA`. `None` (unset, the
  default) keeps today's 0.5; lower it when real domains are being missed
  because they're too flat to clear the default floor. Domain-size-only —
  never reaches `analyse_frame`/`classify()`, so it cannot change the OM
  segmentation itself.
- **A "Low coverage" checkbox on the QC Report page**, for sparse-nucleation
  samples (a WSe2 1L wafer that's mostly bare substrate, say). `classify()`
  always calls the frame's most common pixel value "the reference" — the
  chosen reference layer is purely a label, never part of the threshold math
  — so on a substrate-majority sample the substrate itself was always
  reading as "1L" and the real 1L flakes as "Above 1L". Ticking the checkbox
  relabels the classes "Below Substrate" / "Substrate" / the named film
  (e.g. "Monolayer") instead, without touching the segmentation itself —
  same masks, same tuning, only `contrast.class_labels()`'s wording changes,
  which the OM table, the OM figure's own title, and the domain-size class
  selector all inherit for free. Still two populations underneath: it
  doesn't separate a thicker layer from the named one if both sit above
  substrate in the same frame.

### Local 5.3.1 - 2026-09-20

#### Fixed

- **The launcher's auto-update pulls from `origin` explicitly, not whatever
  the branch happens to track.** `start.bat` ran a bare `git pull --ff-only`,
  which follows `branch.main.remote` -- and that can silently drift to a
  different remote (a personal fork added later, say) without any error. The
  pull then "succeeds" against the wrong repo every launch, reporting
  up to date while the real repo moves on. Pinned to `git pull --ff-only origin main`.
## [5.13.0] - 2026-10-06

### Added

- **Peaks on Background** (View Options, on by default after a fit): the
  Spectra plot draws the subtracted baseline as a gray line and lifts the data,
  the total fit and every component onto it, so the peaks are seen sitting on
  the background they were separated from, as in a fitted XPS figure. The data
  shown is the de-spiked spectrum (baseline-corrected + background), labelled
  "Data". Quick Export's PNG/HTML follow the toggle.
- **Residuals have their own panel** under the Spectra plot, sharing its X axis
  (zoom one, both follow), as Quick Export already drew them. On the same axis
  as data lifted onto a ~500-count background they squashed the peaks to a
  sliver. The plot grows from 600 to 760 px when the panel is shown.
  **No numbers move**: only what's drawn changes; the fit, CSVs, Excel and
  reports are unchanged, and the QC/Sample Report grids still draw
  baseline-corrected.
- The auto-workflow now keeps the baseline it subtracts
  (`SpectrumFile.background`, saved with projects). Files fitted before this
  version, or projects saved before it, have none and show the
  baseline-corrected view with a note to re-run.

## [5.12.2] - 2026-10-05

### Fixed

- **A narrow peak no longer freezes at its starting guess.** The fitter started
  gamma exactly on its lower bound whenever a template's shape split put the
  Lorentzian half under its floor (shape ≤ 0.25, or a peak narrow against the
  data spacing). lmfit enforces bounds through a sine whose slope is zero at
  the bound, so Levenberg–Marquardt could stop on its first step with every
  parameter unchanged and still report success: a synthetic shape-0.3 peak came
  back after 11 evaluations at R² 0.94 with 78% of its area. Every starting
  value — centre, sigma, gamma and area — now sits at least 1% of its range
  inside its bounds (`fitting.START_MARGIN`). The bounds themselves are
  unchanged.
- **A failed single Run Auto-Workflow now says why.** A trailing `st.rerun()`
  discarded the success summary and, on a failure, the error and its
  suggestions, one frame after drawing them — while the workflow had already
  reset the spectrum to raw, so the previous fit vanished without a word. The
  batch button lost the same rerun in an earlier release.
- **Saving a runcard tag can no longer wipe `runcard_tags.json`.** Every save
  reads the whole file, changes one entry and writes it back, and the read
  treated a file it could not open -- locked by robocopy or datalog_monitor,
  caught half-copied, or damaged -- as empty. The write then replaced every
  stored tag with the one being saved; `thresholds.json` lost its overrides and
  timing keys the same way. A save now reads strictly: it retries a locked
  file briefly, and if the file still cannot be read it writes nothing and the
  page says so (`tag_store.SidecarUnreadable`). Pages that only *show* tags
  still open on a damaged file, as before. A sidecar holding a JSON list rather
  than an object now reads as empty instead of raising.
- **Two files with the same name from different folders both load.** Files
  were keyed by bare name, so loading `W01/RM_center.txt` and
  `W02/RM_center.txt` kept the first and dropped the second as "already
  loaded" -- and the notice saying so was discarded by the rerun after
  loading. The second now loads as `RM_center (W02).txt` (with a counter if
  the folder names clash too), the page lists the files it renamed, and only
  the same name from the same folder counts as a re-load. The new name is
  what the master CSV's `Filename` column and the export filenames carry, so
  the two wafers stay apart there too.

Numbers move. On 22 well-fitted WSe2 spectra from Internal_QC (R² > 0.9, every
third file), R² never falls by more than 0.0007. E2g+A1g intensity and FWHM
and LA/E2g+A1g barely move (mostly under 1%); the B2g centre moves most, from a
302.6–318.9 cm⁻¹ scatter to 306.4–311.4, because B2g was one of the peaks that
froze. WSe2 extended moves more — 17 of its peaks started on a bound — with
R² up to +0.024 and LA/E2g+A1g down by up to 0.06 on the same files. MoS2 and
Silicon are unchanged.
## [5.12.1] - 2026-10-05

### Changed

- **`2ZA(M)` starts at 2.0 cm-1 FWHM (was 4.0) in `WSe2` and
  `WSe2_truncate`.** The fitter bounds a width to 0.5-3x its start, so this
  caps 2ZA(M)'s Voigt FWHM at ~4.9 cm-1. At 4.0 the cap was ~9.8 and 2ZA(M)
  sat on it in over 75% of TSMC_May spectra, acting as a broad base that
  took E2g+A1g's low-side wing; capped, it fits at 4.4-4.8 cm-1 centred
  243-244, the De Luca 244.2 band. **WSe2's numbers move**: on TSMC_May the
  E2g+A1g FWHM median rises ~0.3-0.4 cm-1 against the 4.0 start (e.g.
  TSM260500 4.08 -> 4.41).
- **`WSe2` Raman x range is -5 to 500** (was 200-300 at v5.12.0), edited on the
  Material Presets page, so LA, B2g, LB, C and center are fitted on data again
  and the LA/E2g+A1g ratio is meaningful.

### Added

- **A `WSe2_truncate` preset**: `WSe2` with the Raman window at 200-300 cm-1,
  for the E2g+A1g / 2LA / 2ZA(M) region only. Peaks outside the window are
  carried but meaningless there. Added on the Material Presets page.

## [5.12.0] - 2026-10-05

### Added

- **`2ZA(M)` in the `WSe2` preset** (244.2 cm-1, +/-4, FWHM 4), for the
  shoulder on the low side of E2g+A1g; the band is De Luca et al.'s Table 1
  244.2 peak, as in `WSe2 extended`. **WSe2's numbers move**: E2g+A1g no
  longer absorbs the shoulder, so its FWHM narrows and its center can shift.
  Its +/-7 window overlaps 2ZA(M)'s, so on spectra without a shoulder 2ZA(M)
  may fit near zero.

### Changed

- **`WSe2` and `WSe2 extended` Raman**, edited on the Material Presets page:
  x range 6-400 -> 200-300, ALS baseline p 0.02 -> 0.005, max_iterations
  2000 -> 10000. **Numbers move.** With 200-300, B2g, LA, LB, C and center
  (and `WSe2 extended`'s LA and LO2(K)-ZA(K)) lie outside the fitted range,
  so their values, and the LA/E2g+A1g ratio, are not meaningful there.
- **The Fit Total line is solid black, not dashed.** The style is shared, so
  this applies to the Spectra page plot and the exported fit figures alike. On
  the Spectra page it is also 1 px wider (1.5 -> 2.5). The components keep
  their own colors, so black alone tells the fit apart from them. Display
  only: no numbers move.
- **View Options start on the fitted-spectrum view**: Show Baseline-corrected,
  Fit, Components and Residuals on; Raw and De-spiked off (was Raw only). This
  is the set a finished fit already derives; earlier stages still pick their
  own layers until you set one by hand.

## [5.11.0] - 2026-10-03

### Added

- **Spectrum files with a header now load.** Before, any non-numeric line
  failed the whole file with "Ensure file contains only numeric data with no
  header row". The parser (`modules/spectra/processing/parser.py`) now takes
  the data to start at the first row of two or more numbers that the next four
  non-blank rows confirm. Everything above that row is skipped as header, and
  everything after the last numeric row as footer. That covers a row of column
  names (`Raman shift (cm-1)<TAB>Intensity (a.u.)`), `#key=value` acquisition
  blocks, `[Header]`/`[Data]` sections and `>>>>>Begin/End Spectral Data<<<<<`
  markers without recognising any of them by name.
- Semicolon is accepted as a delimiter, after tab and comma and before
  whitespace.
- Files are decoded as UTF-8 (with or without BOM), UTF-16 by its BOM, or
  Windows-1252. Before, a "µ" or "°" in a cp1252 header failed the parse with
  a misleading "must have at least 2 columns".

### Changed

- A column that is empty in every row, left by a delimiter at the end of each
  line, is dropped instead of being reported as an unusable Y column.

No numbers move. On 400 headerless RM/PL files under Data Collection and
Sample Requests the new parser returns identical arrays to the old one, about
5x faster (1.9 s against 9.5 s). Of the .txt files there that start with a
non-numeric line, the only spectrum is the digitized TSMC spec file, which now
loads. The rest (OM `_Analyzed.txt` tables, logs, JEOL SEM metadata) still
fail, with "No rows of numbers found".

A header line that is itself a row of numbers (`532<TAB>600`) directly above
the data is read as the first point; a lone number (`1024`) is skipped.

## [5.10.6] - 2026-10-02

### Fixed

- **The QC Report's PL quality figure is no longer empty for MoS2.** Its
  cleaning kept only PL peaks centred above 700 nm, a constant inherited from
  WSe2's emission band, so every MoS2 peak (A0, A-, B at 620–680 nm) was
  discarded and each panel read "no fits". That floor is gone. It never
  removed a WSe2 fit: the fitter's centre bounds keep every WSe2 PL peak above
  740 nm.
- **A PL peak fitted to near zero is left out of the PL figure and the PL
  summary table.** A peak under 2% of the strongest peak in the same spectrum
  still reports a centre and a width, but they describe nothing. On
  SU261001/SU261002, A0 fits to zero on 3 of 9 points, and the table averaged
  those meaningless centres in (A0 664.9 ± 8.8 nm, n = 9).
  - One constant, `peak_metrics.NEAR_ZERO_FRACTION` (0.02), feeds both the
    figure's cleaning (`CleaningRule.relative_intensity_floor`) and the table
    (`aggregate_fit_results(relative_intensity_floor=...)`). So the table's
    `n` and the figure's points count the same peaks.
  - Such a peak is skipped whole. `n` now counts the points where the peak
    was found, and its intensity mean is over those points.
  - The floor is per spectrum: a dim point is judged against itself, not
    against the wafer's brightest.
  - The workbook's per-point PL sheet still lists every fitted peak.
- **The OM summary no longer prints "nan ± nan" for a class some frames lack.**
  A frame with no pixels in a class records its contrast as nan, and the
  wafer mean took it in. On SU261001, a few of the nine frames have no
  "Above 1L" pixels. The mean is now over the frames that have the class, and
  it is empty when no frame has it.

Numbers move only in the PL summary table and the PL figure (near-zero peaks
leave both), and in the OM contrast cells that used to read nan. Raman,
coverage and the per-point sheets are unchanged. For WSe2, a Trion fitted to
under 2% of its Exciton now leaves the table and figure as well.

## [5.10.5] - 2026-10-02

### Changed

- **The despike threshold now goes up to 500, from 30.** The modified
  Z-score is taken against the whole spectrum's median and MAD, so a strong
  real peak scores like a cosmic ray, and below its score its top is replaced
  by the median of its neighbours. On TSMC_May TSM260500 (900 spectra, the
  WSe2 presets' 6–400 cm⁻¹ window), E2g is the highest point in 879 spectra
  and scores 12.6–30.4. In the other 21 the highest point is elsewhere, most
  likely a cosmic ray, scoring 89–1521. At the presets' threshold of 8, every
  spectrum has points flagged, and its highest point drops by a median 6.1%.
  On this wafer a threshold between 31 and 89 leaves every E2g alone and still
  catches those 21. At 500, 12 of the 21 would get through.
  - The range is one constant, `DESPIKE_THRESHOLD_MIN` / `_MAX` in
    `models/spectrum.py`. The despiker, `ProcessingSettings`, the preset's
    `validate()` and the Material Presets editor all read it. Before, each one
    had its own copy of 30.

- **`WSe2`, `WSe2 extended` and `WSe2 extended V2` despike Raman at 100, up
  from 8.** On TSM260500 that leaves every E2g alone; 2 of the 21 likely
  cosmic rays (scores 89 and 100) get through. Their PL blocks stay at 8.

WSe2 Raman numbers move: E2g's top is no longer flattened, so its intensity
and the ratios built on it change. The size of the shift on fitted values is
not yet measured. MoS2 and Silicon are untouched.

## [5.10.4] - 2026-10-02

### Changed

- **`WSe2 extended V2` now sits at the measured frequencies, keeps the
  E2g/A1g split, and has a 5000-evaluation fit budget.** It is still the
  preset for checking the split fit; tuned on TSMC_May TSM260500 (900
  spectra).
  - The multi-phonon bands, LA and LO2(K)-ZA(K) move from the calculated
    column of De Luca et al. Table 1 to the measured one (the frequencies
    `WSe2 extended` uses). At the calculated positions, peaks sat on a centre
    limit in up to 12% of fits (TO1(K)), 10% (TA(M)+LA(M)), 8% (LA) and 7%
    (E2g).
  - E2g and A1g move from 250.8 / 251.3 ± 1.5 to 249.65 / 250.15 ± 2.0. That
    keeps the calculated 0.5 cm⁻¹ split, centred on the measured 249.9. The
    ± 2 covers TSM260504, whose first-order mode sits near 251.4. On
    TSM260500 the two are never on a limit and swap order in 1 of 900 fits.
    Their individual intensities and widths remain poorly determined, but the
    width of their summed curve is stable: median 5.40 cm⁻¹, against 6.31 for
    `WSe2`.
  - LA becomes the broad ~125 cm⁻¹ band: FWHM 20 ± 10, up from 4 ± 4. With
    the old values it sat on its width ceiling (9.8 cm⁻¹) in 28% of fits. Now
    it reaches the ceiling (≈ 49) in 15% and its centre limit in 1%.
  - LO2(K)-ZA(K) starts at FWHM 8, ± 6, up from 4 ± 4; the ± 6 takes it off
    its centre limit (7.6% of fits at ± 4, 1.4% at ± 6). C starts at FWHM 8, up
    from 5. C still reaches its ceiling (≈ 20) in 11% of fits. Starting C at
    10 halves that, but then C takes LB's signal: LB is fitted at zero in 9%
    of fits instead of 5%.
  - `max_iterations` goes from 2000 to 5000. At 2000, 46% of fits stopped at
    the budget; at 5000, 23% still do. Such fits are still reported as
    successful (`fitting.py` accepts lmfit's "Tolerance seems to be too
    small" message, which is also what a budget stop leaves).
  - On TSM260500, R² rises from 0.869 to 0.897, and the 100–160 cm⁻¹
    residual falls to 0.28× of the old one.
  - Four bands are below the noise there: TO1(K), TA(K)+ZA(K), ZA(K)+LA(K)
    and ZO2(M), at 0.2–1.5× the noise, fitted at zero in 3–49% of fits.
    They stay in, at their measured positions, but their numbers mean
    nothing on that wafer.

`WSe2 extended V2` numbers move. `WSe2` and `WSe2 extended` are untouched.

## [5.10.3] - 2026-10-02

### Changed

- **`WSe2` PL windows move from Exciton 770 ± 15 / Trion 800 ± 15 to
  Exciton 755 ± 15 / Trion 780 ± 15 nm, and the Trion's starting FWHM goes
  from 50 to 40.** On monolayer HU261001, points 1 and 3 peak at 749 and
  751 nm. That is below the old Exciton window (755–785), so the Exciton sat
  pinned at 755, the fitted curve ran to the right of the data (R² 0.979), and
  the Trion, unable to go below 785, fitted to zero. Now R² is 0.9994 and both
  peaks are present.
  - Across 63 spectra from eight WSe2 sets (HU261001–3, NUS261001, SU261003,
    HADH72, HADI05), peaks on a window edge drop from 47 of 126 to 26, the
    worst R² rises from 0.978 to 0.992, and the Exciton and Trion never swap.
  - The 26 that remain are all the Trion sitting on its new 765 nm floor. At
    room temperature these spectra don't resolve a separate trion, so the
    Trion settles as close to the Exciton as its window allows, and that
    floor is what keeps the two about 10 nm (≈ 22 meV) apart. Windows that
    free it let the two collapse to within 4 nm, so treat the Trion centre as
    constrained rather than measured. The old 785 floor forced a split of at
    least 30 nm (≈ 65 meV), too large for a trion.
  - `WSe2 extended` and `WSe2 extended V2` keep the old 770/800 PL windows.

WSe2 PL numbers move. That includes HA1P01 wafers: HADI05's Trion goes from
791 to 765 nm and its share from 0.00 to 0.19, and HADH72's Exciton FWHM from
30 to 26 nm. Raman, OM, MoS2 and Silicon are untouched.

## [5.10.2] - 2026-10-02

### Changed

- **MoS2 has a PL block, and its Raman block is retuned.** Both were fitted
  against the monolayer-on-sapphire spectra in HA1N01 (2504–2508), 02N01 and
  VM1N01, converted from the 488 nm exports' wavelength axis.
  - **Raman** is cropped to 340–480 cm⁻¹ and fits `E2g` 384 ± 6, `A1g` 405 ± 6
    and `2LA(M)` 455 ± 15. The old block fitted the whole exported range
    (−500 to +1200 cm⁻¹) and started from bulk positions (383/408) with
    10–12 cm⁻¹ widths. Measured widths are 5.7 and 7.1, so `width_fwhm` is now
    6 and 7, which also sets the fit's 0.5×–3× width bounds. `2LA(M)` is fitted
    because its band sits on A1g's high side, the same reason WSe2 fits `2LA`.
    The baseline exclusions are gone; masking let `2LA(M)` run wide.
  - **PL** is cropped to 540–800 nm, masks 590–760 nm from the ALS baseline
    (unmasked, the baseline ate the emission), and fits `A0` 662 ± 20,
    `A-` 680 ± 20 and `B` 620 ± 20 nm. The A0/A- windows overlap, and across
    188 spectra the fit never swapped them. The median split is 10.4 nm
    (≈ 29 meV), the trion binding energy.
  - **Optical**: a `1L` block with `ff_divisor` 12. At the default /8, the
    flat-field blur leaves the corner vignetting of 50X rectangular frames
    about 1 % dark, which the 4σ cut (about −1.4 % at these frames' noise)
    counted as "Below 1L": 0.13–0.96 % per wafer. At /12 that falls to
    0.01–0.08 % on SU261001, SU261002, HU261004 and HU261005. Uniform patches
    up to 200 px are still fully recovered at /12; a 350 px patch is 94–96 %
    recovered. Every other optical setting stays at `contrast.py`'s default.
    There is no fixed contrast pair, because no frame yet shows bare sapphire
    or a 2L island to set it from.

  Both spectra blocks were checked again on the Witec cm⁻¹ exports of those
  four wafers (33 Raman and 30 PL spectra). Raman: R² 0.97–0.995 with no
  peak at a bound. PL: R² ≥ 0.997, but on the red-shifted SU wafers (emission
  at 675–695 nm) `A0` often fits to zero and `A-` carries the peak. Read the
  A0/A- split there with care.

  Peaks are deliberately not named `LA`, `C`, `LB`, `2LA`, `Exciton` or
  `Trion`. `QualityFigureSpec.columns_for` keeps WSe2's inherited panels,
  spec lines included, whenever any one of those labels is present, so a
  MoS2 Raman fit named `LA` would render WSe2's mostly empty figure.

MoS2 numbers move: every MoS2 Raman centre, width and intensity changes, PL
is newly fittable, and the 1L OM coverage moves (false "Below 1L" falls by
about an order of magnitude). WSe2, its extended presets and Silicon are
untouched.

## [5.10.1] - 2026-10-02

### Fixed

- **The OneNote page's "Found on disk" ticks now follow the selected wafer.**
  They were set once per session, by the first wafer picked. A keyed checkbox
  is identified by its key alone, so `value=` is only its first render's
  default, and the three keys never changed between wafers. Picking a wafer
  with no runcard or datalog and then HADH78 showed HADH78's filenames —
  `HADH78.csv`, `2026-09-21_121151~HADH78.csv` — beside empty boxes, which
  read as "not found". The lookup was always right; only the ticks were stale.
  The page now writes each tick to its key on every run.

- **A second wafer picked straight after the first no longer snaps back.**
  The Wafer dropdown had no key, so its identity included its `index`, and
  that index came from the wafer chosen on the previous run. The second pick
  arrived for a widget that no longer existed and was dropped. The dropdown is
  now keyed and seeded only when its key is absent or names a wafer the
  current tool doesn't list.

No numbers move: nothing here is computed, and the page still publishes
nothing.

## [5.10.0] - 2026-10-02

### Added

- **A `WSe2 extended V2` material preset**: `WSe2 extended` moved to the
  theoretical frequencies of De Luca et al., arXiv:1910.12503, Table 1
  (column 2, 0 K DFT), for comparison against the experimental-frequency
  preset. Where a row lists two calculated values, the first assignment's is
  used (`TA(M)+LA(M)` 228.5, `2ZA(M)` 244.0, `ZO1(K)` 257.3); `LA` sits at
  130.1, the `*LA(M)` value; `Unassigned 238` is dropped, having no
  calculated value. Added peaks use +/-4 cm-1 tolerance and 4 cm-1 FWHM.
- The 1L first-order mode is **split** into `E2g` (E', 250.8) and `A1g`
  (A'1, 251.3), each +/-1.5. The two are 0.5 cm-1 apart with identical
  starting widths, so expect their split of intensity to be poorly
  determined. With no `E2g+A1g` peak, the QC Report's LA/E2g+A1g ratio and
  E2g+A1g panels are blank for this preset; it is not meant for QC reports.
  C, LB, center, 2LA and B2g are `WSe2`'s own.

### Changed

- **`WSe2 extended` tolerances, edited on the Material Presets page.** Its
  table peaks widen from +/-1.5 to +/-4 cm-1; `E2g+A1g` goes from +/-7 to
  +/-4 with a 2.5 cm-1 starting FWHM (was 4); `2LA` from +/-7 to +/-4.
  **Its fitted numbers move.** `WSe2` itself is unchanged.
- The page's save also writes each block's `max_iterations` (2000, the
  existing default) explicitly. No numbers move from that.

## [5.9.0] - 2026-10-01

### Added

- **A `WSe2 extended` material preset** with the monolayer multi-phonon peaks
  of De Luca et al., arXiv:1910.12503, Table 1. It copies `WSe2` (processing
  settings, PL block, optical block) and adds the twelve 208-270 cm-1 bands at
  the paper's experimental frequencies (column 1, +/-1 cm-1), labelled by their
  first assignment with the BZ-edge `*` dropped; the unassigned 237.8 band is
  `Unassigned 238`. New peaks use +/-1.5 cm-1 tolerance and 2.5 cm-1 FWHM,
  because several sit 1-2 cm-1 apart. `E2g+A1g` and `2LA` stay as in `WSe2` in
  place of the table's 249.9 and 261.0 rows.
- The broad `LA` band (135, FWHM 15) is replaced in this preset by the table's
  two components: `LA` at 130.5 (the `*LA(M)` zone-edge mode) and
  `LO2(K)-ZA(K)` at 137.4. The label `LA` is kept so the defect ratio still
  reports, but **its numbers move**: LA/E2g+A1g on `WSe2 extended` measures the
  narrow 130.5 component, not the whole hump, and is not comparable to `WSe2`
  or the 0.13 reference line. `WSe2` itself is unchanged.

### Changed

- **A fit may carry up to 20 peaks, up from 10.** The cap now lives in one
  constant, `models.peak.MAX_PEAKS`, used by preset validation,
  `fit_voigt_peaks()` and `FitResult`; before it was a literal `10` in each.
  No existing preset's numbers move. A 20-peak fit is slower, and closely
  spaced peaks (e.g. ZO2(K) 262.3 / ZO1(M) 263.3) can carry large stderrs.

## [5.8.0] - 2026-09-30

### Added

- **A OneNote page, under a new "Publish" nav section.** The team keeps one
  OneNote page per wafer and fills it by hand — saving figures out of this app
  and pasting them in. This page is the first half of doing that directly:
  it names the wafer, checks its material, and shows what would be published.
  Sign-in and the upload itself are not wired up yet, so nothing leaves the
  machine at this version; the material check is live.

  "Publish" is the one nav section not named for a data source, and it has to
  be: OneNote reads no measurements and no tool logs, it sends the other
  pages' finished results out. Filing it under "Analysis" or "Process" would
  have falsified the rule those two headings state, so the rule now says it
  holds for exactly the two sections that state it.

- **A wafer-prefix -> material table, `data/material_prefixes.json`.** A wafer
  ID is `[tool 2][version 2][sequence 2]`, and the material is a property of
  the tool: `HA`, `VA`, `VB`, `DU`, `DD` grow WSe2, `QU` grows MoS2. The table
  is keyed on those first two letters.

  It is a convention written down, not a record, because no per-wafer material
  record exists. Three searches — the Data Collection share, the Google Drive
  mount, this repo — found material asserted only at container level: a sheet
  named `WSe2_SEP Control Table`, a toolkit named `wse2_optical_analysis`, a
  generated deck titled `HADH06 | Material: WSe2`. Never a field with a value
  per wafer. Those three conventions can silently disagree; one reviewed file
  replaces them.

  The check is a guardrail, not validation: five of six tool lines grow WSe2,
  so it agrees for almost every wafer. It earns its place on the QU wafer left
  on the previous sample's WSe2 preset, and on the tool line nobody has added
  yet. A mismatch warns and uploads anyway — a check that blocked would assume
  the table is always right, and the day a tool changes chemistry it would be
  worked around rather than corrected. "Unknown prefix" renders differently
  from "mismatch" on purpose: *we did not check* and *we checked and it
  disagrees* must not read alike.

- **A gitignored overlay, `data/material_prefixes.local.json`.** An unknown
  prefix can be added from the app, but only to this file, layered over the
  committed one. Letting the button edit the shared table would put one
  operator's guess on one machine — uncommitted, invisible to everyone else —
  while both saw a green verdict. Anything resolved from the overlay is marked
  *locally added, not yet shared* wherever it appears. Same split this repo
  already draws between `data/materials.json` and `data/report_settings.json`.

- **The OneNote page takes a tool folder and lists its wafers.** Point it at
  `...\Data Collection\HA1P01` once and pick a wafer from a dropdown; the
  three artifacts are found under it. Previously it wanted a single wafer
  folder, which meant re-picking for every wafer and gave no way to see what a
  wafer actually had.

- **`data/tool_layouts.json`, declaring where each tool keeps its artifacts.**
  The layout is declared per tool rather than inferred, because a survey of
  all twelve tool folders found there is no convention to infer: the runcard
  folder is spelled four ways (`RUNCARD`, `Runcard`, `Run Card`, absent), the
  optical folder six (`OPTICALS`, `Optical Properties`, `Raman&PL`,
  `RAMAN&PL`, `RAMAN`, `Characterization`), depth to a wafer runs one to four
  levels, and the intermediate tier is a month in one tool, a wafer-prefix in
  another, a technique in a third. Only two of the twelve have a datalog at
  all, and the older eight key their files on a run serial rather than a wafer
  ID, so a wafer lookup in them can never match.

  An undeclared tool raises and names the file to add it to. A loader that
  guessed would return empty for most tools while looking like it had
  searched, which for a QC tool is worse than saying it does not know.

  Ships with HA1P01 alone. The optical side needs two patterns because the
  naming changed mid-2026: `OPTICALS/202609/<WAFER>/` is a folder per wafer,
  the twelve earlier months are flat `.wip` files, and the five before those
  key on a run serial and are unreachable by wafer ID at all.

- **A read-only "found on disk" panel per wafer**, and it says *not linked*,
  never *missing*. 1186 of HA1P01's 1404 datalog CSVs carry no `~<WAFER>` in
  the filename, so 58% of its wafers have a log that exists but cannot be
  addressed by name; 19 wafers have optical data and no runcard under any
  glob. Calling either "missing" would send someone hunting for a file that is
  sitting in the folder. Measured against the real share: 526 wafers, 100%
  optical, 80% runcard, 32% datalog.

### Notes

- The upload log deliberately survives `reset_results`, breaking the pattern
  every other state module follows. A derived key is dropped on an input
  change because a stale figure under a new sample's name is a lie; the log
  records what *happened*, which stays true whatever is picked next. Dropping
  it would erase the record of a failed upload at exactly the moment the
  operator moved on.

- The runcard and datalog loaders are unchanged. Both already recurse and both
  already gate on content — a recipe's clock advances where a datalog's does
  not — so they only ever needed a better starting folder. Pointing them at
  the tool root instead would be actively harmful: the Datalog page's bulk
  rename walks from its root, and `runcard_tags.json` is keyed by path
  relative to it, so moving the root would orphan all 231 existing tags.

- `BACKUP/` is excluded from the wafer list. It holds a full parallel
  `OM/<YYYYMM>/<WAFER>/` tree, so every wafer in it would otherwise appear
  twice with nothing to tell the two apart.

## [5.7.1] - 2026-09-30

### Fixed

- **A View Option you set by hand now stays set.** The six `show_*` checkboxes
  are one global set of choices, but four separate paths wrote to them as if
  they were state derived from the current file's processing stage: the file
  switch (`_update_visibility_for_file`), each of the auto-workflow's four
  stages, the sidebar after a single and after a batch run, and
  `compute_default_visibility` inside the plot builder. Whichever ran last won,
  so a checkbox survived only until the next file was selected — the reported
  "change the data and it turns back to the original setting".

  Toggling a checkbox now *pins* it, and `set_view_options()` — the single
  automatic writer the other paths were rewritten to go through — skips pinned
  keys. Untouched checkboxes still follow the data, so the common case of
  loading a file and seeing the right layers is unchanged. A "Follow the data
  again" button appears once anything is pinned, because a sticky choice with
  no way back is the opposite bug.

- **The four writers disagreed about components at the fit stage.**
  `compute_default_visibility` set `components` off for a completed fit while
  every other writer set it on, and it had no `residuals` key at all — so that
  layer could only ever be off on the fallback path, whatever the file's stage.
  Both now match the other writers. This path is reached only when
  `create_unified_figure` is called without a `layer_config`, which the Spectra
  page never does, so nothing on screen moves today; it was the half of the
  disagreement waiting for the next caller.

- **Quick Export follows View Options instead of hardcoding them.** The
  exported PNG/HTML passed `show_components=True` and let `show_residuals`
  default to True, so the downloaded figure could disagree with the plot the
  user had just tuned above it.

## [5.7.0] - 2026-09-30

### Added

- **The Datalog page detects which runcard produced each untagged run.**
  *2. Filenames → Detect runcards for untagged runs* replays every recipe in the
  tool's runcard folder (guessed from the datalog folder: `…\HA1P01\RUNCARD\lcy`)
  against each untagged run and proposes the card that ran it, with a
  confidence and the exact step differences. CLEANING-1~5 and the other files at
  the folder's top level can match any number of runs; every other card matches
  one. **Rename** gives the accepted files `<timestamp>~<tag>.csv` through the
  existing renamer (a file written to in the last 30 minutes is skipped as still
  logging); **Save as tags only** writes `runcard_tags.json` in one write instead.
  Method and numbers: [docs/Runcard_Matching.md](docs/Runcard_Matching.md).

  Consecutive growth cards are routinely identical, so content alone names only
  the recipe family. The member is chosen jointly across runs: each growth card
  goes to one run, card numbers rise with run time inside a series, and a card
  saved seconds before a run started (a third of them are) wins a tie. Measured
  on HA1P01 with each run's tag hidden and every other tag kept: 241 of 243
  growth runs and 107 of 108 cleanings named correctly.

- **A selected run shows the detected runcard under its tag box** — with a
  *Use* button when it is untagged, and a check of the existing tag when it is
  not ("the log looks like CLEANING-2, not HADD54").

- `tag_store.set_runcard_tags`: several tags in one read and one write, so a
  batch save does not rewrite the sidecar datalog_monitor reads a hundred times.

### Changed

- **Renaming is the detector's default because the filename is what survives
  on HA1P01.** Its DATALOG is filled by a one-way robocopy (SulfurSync), which
  also copies the tool PC's own `runcard_tags.json` over the OneDrive one
  whenever that is newer, so a tag saved only in OneDrive lasts until the tool
  PC next saves a tag. The same copy used to put a renamed file's original name
  back beside it (141 duplicates on 2026-09-28); SulfurSync's HA-DataRecord job
  now has `keepRenamed` (see the doc), which stops that. Bulk rename's caption
  says so, and files sharing a start time with a tagged run are listed as copies
  and not suggested for -- on top of v5.5.0's duplicate hiding, which the
  detector now starts from.

- `modules/datalog` now imports `modules.runcard.io.parser` (in
  `io/runcard_index.py`), so a recipe is parsed one way. The dependency runs one
  direction; `docs/Summary.md` records it.

### Notes

- No existing number moves. The run list, charts, tolerances and both renamers
  behave as before; the only new writes are the tags an operator chooses to save.

## [5.6.0] - 2026-09-21

### Removed

- **The per-wafer adaptive threshold, and with it the second ruler.** Every
  wafer is segmented on the preset's `abs_threshold` pair again, as it was
  before v4.5.0. The derivation was introduced to follow a 3L boundary that
  drifts between wafers, and on the HA 202609 set it did move 34 of 76 wafers
  off the base pair - but `crossing`, the weaker of its two evidence rules,
  produced 42 of those cuts and 28 of them landed on the 2 sigma noise floor
  exactly. A cut at the floor is not a boundary the fit found; it is the
  guardrail refusing to go closer, recorded as though it were a measurement.
  HADH57 is the worked example: its stored pair of -6.0/+2.754 read 87.3 %
  bilayer against 95.6 % on the base pair, an 8-point swing across the 95 %
  spec line, and re-deriving it from the same nine frames with the same code
  returns the base pair instead - the mixture component that decides it sits
  0.04 % from its own clamp. A number that flips on that is not a measurement
  either.

  Coverage is a comparison - wafer against spec, wafer against wafer - so it
  needs one ruler. The genuine finding behind the derivation stands: on wafers
  like HADH51 the fixed +4.25 % cut undercounts 3L, and the wafer's own
  histogram puts the boundary at +2.46 %. That is a known, consistent bias
  across every wafer, which is usable; a per-wafer correction that fires
  wrongly two times in three is not.

- `modules/optical/processing/adaptive.py` and `tests/unit/test_adaptive_threshold.py`
  are deleted; `OpticalParams.adaptive_threshold` and `adaptive_enabled` are
  gone, and a stored preset still carrying `adaptive_threshold` loads with the
  key dropped rather than rejected. The `optical_adaptive` progress stage is
  removed and its weight redistributed.

### Changed

- **`OM_Stats` carries two threshold columns instead of seven.**
  `Threshold_Below_pct` and `Threshold_Above_pct` hold the pair that segmented
  the wafer; `Threshold_Base_*`, `Threshold_*_Source` and `Noise_Sigma_pct`
  described a derivation that no longer happens. The summary page's note under
  the OM table now reads `preset contrast pair -6.00 / +4.25 %`, and its
  NOT MEASURABLE flag line is gone with the rule that raised it.

## [5.5.0] - 2026-09-21

### Added

- **A run stored twice is listed once, and Duplicate cleanup deletes the spare
  copy.** The analysis folder is fed by a copier that matches on filename and
  only ever adds, so renaming a run makes its source look absent on the next
  pass and the recorder's original `DDHHMMSS.csv` name is copied straight back
  in — HA1P01's 2026_09 folder was holding fourteen such pairs, every one
  byte-identical to the renamed file beside it. `io.duplicates` answers it in
  two parts, and which is primary matters: the copier runs on the tool PC on a
  roughly three-minute cycle, so *deleting* a stale copy is a race this side
  cannot win, while not *showing* one is a promise it can always keep.
  `without_duplicates` filters the run list, and Duplicate cleanup reclaims the
  disk space on demand by way of the Recycle Bin, never an unrecoverable
  delete. Both read one `stale_copies` rule, so the preview an operator
  confirms names exactly the files the table stopped showing them.

  A file is a candidate only when another file *in the same directory* already
  carries the canonical name for that run, and only on identical bytes: a
  group with no canonically-named member is not a rename artifact and is left
  alone, and a truncated or half-copied near-duplicate stays on disk *and*
  stays visible, which is where someone will notice it. The same run under two
  month folders is an archiving decision and is never touched. Grouping on the
  `(directory, start, end, row count)` the folder scan has already read is what
  makes it affordable — bytes are read only for files that already agree on all
  four, which on the real 1392-run folder is ten files and 0.03 s, and the
  digests are cached against mtime and size so the 60-second tick costs nothing.

### Fixed

- **`tag_store.clear_runcard_tag` deletes a tag entry without creating a
  sidecar to say so.** `set_runcard_tag(..., "")` reaches the same result but
  rewrites `runcard_tags.json` either way, so clearing a tag that was never
  stored wrote the file into a folder that had never been tagged. The new
  function returns without writing when the key is absent, matching what
  `move_runcard_tag` already did for the same reason. Nothing else changes: the
  on-disk format and the empty-tag-means-delete rule are untouched, and
  `set_runcard_tag` keeps its behaviour for the tag editor that calls it.

## [5.4.1] - 2026-09-21

### Fixed

- **A QC Report page title too long for the page now shrinks instead of
  running off it.** The header is one line drawn at a fixed position and a
  fixed 24pt, and matplotlib clips figure text to nothing, so every fit-grid
  page — `Raman — fitted spectra (9 points)` and its PL twin — lost the tail
  of its own subtitle. It went unnoticed because the summary page's title,
  which the layout was set up against, carries no subtitle and fits at full
  size. `_fit_title_size` measures the string with the renderer-free
  `_text_width_pt` the table fitter already uses, and scales the size down to
  the rule's width inset at the right by as much as at the left; width is
  linear in font size, so one division is exact and no search is needed. A
  fit-grid title lands near 20pt, the summary page keeps its 24, and the
  shrink stops at `_FS_TITLE_MIN` (14pt) — below that the header stops reading
  as a title, and a string that long wants shortening at the source. The
  figure header only; no reported number changes.

## [5.4.0] - 2026-09-21

### Fixed

- **Running the auto-workflow twice ran it on its own output, so an edited
  preset was applied to the residual of the old one.** Stage 2 reads
  `processed_data`, and nothing reset it, so a second run despiked an
  already-despiked array and ALS-baselined an already-baselined one. Reported
  as "we changed the MoS2 preset and the spectra didn't follow". Three
  symptoms, one cause, all fixed by `execute_auto_workflow` calling
  `reset_to_raw()` before stage 1:
  - **X-range could only ever narrow.** Stage 1 overwrote `raw_data` with the
    crop and then cropped *that* next time, so widening 370-420 to 340-450 left
    the data at 370-420 — reporting success, with no warning. The full trace was
    never lost (`original_data` holds it), but only the sidebar's Reset to Raw
    restored it.
  - **Switching the baseline to "None (Skip)" changed nothing.** Skipping
    declines to subtract a *new* baseline; it cannot un-subtract the previous
    run's, which stayed.
  - **Even an unedited preset drifted.** Three runs of the committed MoS2 block
    moved the A1g area 1388 -> 1344 -> 1314, about -5%.

  A re-run now equals a fresh load of the file, bit for bit. Operators who
  learned to press Reset to Raw first no longer need to, and pressing it is
  still harmless. The reset runs *after* the technique-block check, so picking
  a material that has no block for the loaded file's technique still fails
  without discarding the fit already on screen.

- **MoS2 could never appear in the QC Report's Raman quality figure.**
  `PANEL_COLUMNS` names WSe2's peaks (`E2g+A1g`, `2LA`, `B2g`, and the `LA`/`C`
  ratios); MoS2 fits `E2g` and `A1g` as separate peaks, which match none of
  them, so all six panels read "no E2g+A1g fits" however good the fits were —
  on every run, so no preset edit could change it. `QualityFigureSpec.columns_for`
  now falls back to FWHM and centre columns built from the peaks the sample
  actually fitted. The test is "would this render empty", not "is this WSe2": a
  material sharing even one inherited peak keeps the inherited columns, spec
  lines and ratio column included, and the WSe2 and PL figures are byte-identical
  to v5.3.0. Fallback panels carry **no spec lines and no ratio column** — the
  four spec values in this lineage are inherited literals tied to WSe2's peaks,
  and there is no measured tolerance for another material's.

### Added

- **The Spectra page says when the fit on screen predates the preset beside
  it.** The QC Report has compared preset fingerprints since v5.0.0; the
  Spectra page compared nothing, so editing a preset and returning left a stale
  fit displayed with no indication. It now records the technique-block
  fingerprint each file was fitted with and warns above the Run button when the
  selected preset no longer matches, naming the other material when the
  selection itself changed. A warning, not an automatic refit: fitting is the
  operator's action, and the previous fit stays exportable until they take it.
  `fit_stale` already existed but covered despike and baseline only, and
  nothing rendered it.

### Changed

- **`fit_voigt_peaks` no longer branches on `shape < 0.5`.** Both arms of the
  if/else were byte-identical — the split is linear in `shape` across the whole
  range — so the branch only suggested the halves were treated differently. No
  numbers move.

## [5.3.0] - 2026-09-19

### Added

- **The Runcard table carries a `Modified` column, and lists newest first.** A
  recipe's own clock starts at zero and the file records no date inside itself,
  so "which of these did I write last" could only be answered by leaving the
  app. The column is the CSV's filesystem modification time in local time —
  the same stat `load_runcard` already takes for its cache key, now exported as
  `parser.file_mtime` — and the table is sorted on it descending by default.
  Any header still re-sorts on click, and the row selection is by position in
  the sorted data, so ticking a row to chart it is unaffected. A file that
  cannot be stat'ed shows an empty cell and sorts last, rather than reading as
  1970.

## [5.2.0] - 2026-09-19

### Changed

- **The fit iteration budget is a preset field, not a sidebar slider.**
  `TechniquePreset.max_iterations` (default 2000, range [500, 20000]) is edited
  on the Material Presets page next to the baseline and peak settings, and
  `execute_auto_workflow` / `run_sample_batch` no longer take a
  `max_iterations` argument — the block for the spectrum's own mode supplies
  it. The number that produced a fit is now written down in `materials.json`
  with everything else the fit used, so a result is reproducible from its
  preset alone; previously it lived in session state and vanished with the
  browser tab. It is per technique because a noisy PL doublet and a two-peak
  Raman spectrum converge at very different speeds. Existing `materials.json`
  entries need no edit: an absent key reads as the 2000 that was the old
  default. Because block fingerprints hash `to_dict()`, changing it now
  correctly invalidates a stale QC figure.

- **The first navigation group is named "Analysis".** Spectra, QC Report and
  Material Presets were unlabelled from v4.0.0; they are now headed by what
  they have in common — they read measurements taken after a run, where
  "Process" reads the tool's own logs and recipes.

## [5.1.1] - 2026-09-19

### Fixed

- **The growth window is exactly the stretch at the peak, not everything within
  5 °C of it.** v5.1.0 started the window as soon as the reconstructed trace
  came within `GROWTH_BAND_C = 5.0` of the run's peak, which on a slow final
  ramp counts minutes of climbing as growth. The band was carried across from an
  older ancestor of the renderer this page was ported from; the current
  reference records it as a defect that had already been fixed once and "must
  not be reintroduced". Both edges are now equality tests against the peak.

- **A commanded ramp-down ends growth, even with the heater still on.** The end
  is now the earlier of heater-off and the first instant the trace steps off the
  peak, so a deliberate step down to a lower temperature closes the window
  instead of being counted as more growth.

- **A window whose end precedes its start is reported as no window.** That is
  what a recipe whose final ramp was still climbing when the heater went off
  produces; it never held at its own peak.

- **A run that never heats has no growth window.** Below a 100 °C floor the
  "peak" is ambient, and the whole run was being reported as growth at room
  temperature.

- **`Pumping` is recognised, not just `Pumping Forward`.** The HAD* family
  writes the bare spelling — 25 of the 39 example recipes — and every one of
  their pump-downs was being dropped without a word. Neither spelling advances
  the clock (their parameter is a target pressure, so the recipe cannot say how
  long pumping takes), but both are now recorded in `Timeline.pump_events`.

- **The cooldown time constant is 25 minutes, not 40.** τ = 2400 s came from the
  older ancestor and stretched every synthesized cooldown tail by two thirds.

- **A heater turned off at t=0 is off, not absent.** The window's cutoff was a
  truthiness test, so `heater_off_t == 0.0` disabled it entirely.

- **Gas markers inside the growth window are suppressed once, in one place.**
  The rule lived in both `stats.gas_events` and `viz.profile`, and the two
  disagreed about the edges — one allowed a tolerance either side and the other
  did not.

### Changed

- **The RTV row is labelled "RTV P" and carries Torr.** v5.1.0 drew it as "RTV"
  with no unit, reasoning that the number matched neither the gauge range nor
  the measured pressure. The reference settles it: the second column of
  `RTV Pressure Ctrl` is the gauge range and the third is the chamber-pressure
  setpoint the recipe commands. A setpoint is allowed to differ from what the
  chamber settles at.

- **The gas palette is the current reference's.** Each species keeps its family
  — H₂Se pink, O₂ green — so old plots still read, but the hues are the current
  values, and both the ASCII and subscript spellings of every species now match.

- **P1 and P2 draw as one trace when they track identically**, which is the
  ordinary case. Two dashed lines at the same height rendered as one line with
  two legend entries claiming it.

- **`docs/reference/` holds vendored specifications**, starting with
  `render_runcard.py`. Excluded from ruff so it stays byte-identical to what was
  handed over, which is what makes it diffable. See its README.

### Notes

- Verified against the reference CLI on all 40 example recipes, both the VBBE
  and HAD* families: total time, peak temperature and growth window agree on
  every one.

## [5.1.0] - 2026-09-19

### Added

- **A Datalog page** (`pages/4_Datalog.py`), ported from datalog_monitor. Points
  at a folder of the deposition tool's 1 Hz process logs and browses them: a
  searchable run list, stacked shared-x charts of whichever PV/SV channels are
  selected, out-of-tolerance violation segments with their duration and worst
  deviation, and per-channel summary statistics. Compare mode aligns runs on the
  heater reaching its final setpoint rather than on wall-clock start, because two
  runs started ten minutes apart are not ten minutes apart in the process. Runs
  can be tagged with the runcard that produced them, and renamed in bulk to
  `<timestamp>~tag.csv`.

- **A Runcard page** (`pages/5_Runcard.py`), likewise ported. Reads a recipe and
  reconstructs the time profile it describes — temperature ramps, the synthesized
  cooldown, auxiliary heaters, gas and pressure events as a gantt stack, and the
  growth window derived from where the heater trace sits within 5 °C of its peak.
  Nothing here reads a datalog: a runcard is the *intended* process, and this page
  draws only what the recipe says.

- **A "Process" section in the navigation**, holding those two. The flat list was
  kept through v5.0.0 on the argument that a technique label goes stale as
  techniques are added — the "Raman & PL" heading had already done so. "Process"
  survives that argument by naming a **data source** rather than a technique:
  these two pages read the tool's own logs and recipes, where the other three read
  measurements taken off a sample afterwards, and no future technique can falsify
  that.

### Changed

- **`use_container_width=True` is now `width="stretch"`** at all twenty call
  sites. Streamlit deprecated the boolean in favour of `width=`, which takes
  `"stretch"` or `"content"`; every site passed `True`, so the rendering is
  unchanged. Done before the new pages landed so they were not written against a
  deprecated flag and then migrated twice.

- **`streamlit>=1.37.0`** replaces the `>=1.28.0` floor, which had drifted from
  reality: 1.63 is what is installed and what the suite is written against, and
  `st.fragment(run_every=)` and `st.dataframe(on_select=)` — both of which the
  Datalog page needs — arrive at 1.37.

- **`statsmodels` is dropped** from requirements. It was pulled in for the Plot
  Explorer's OLS and LOWESS trendlines and nothing has imported it since that page
  was removed at v5.0.0.

### Notes

- **Three on-disk formats are frozen, deliberately.** `runcard_tags.json`,
  `thresholds.json` and the `<timestamp>~tag.csv` filename convention are read by
  datalog_monitor, which stays in use alongside this app. Their shapes are a
  contract, not an implementation detail, and neither application may change them
  alone. The two pages' *own* preferences (`data/datalog.json`,
  `data/runcard.json`) are separate, nexanalyzer-local, and gitignored.

- **The Runcard profile chart is a Plotly rewrite**, not a port. The ancestor
  hand-wrote SVG and rasterized it in the browser; this draws a `go.Figure`, so it
  goes through `core.viz.render.render_plot` like every other chart, exports
  through `core.io.export`, and gains hover. A consequence worth recording: the
  rewrite means output cannot be diffed against the old app, so the parsers,
  timeline and stats carry unit tests instead.

## [5.0.0] - 2026-09-19

### Changed

- **The Sample Report and QC Panel pages are merged into one QC Report page**
  (`pages/2_QC_Report.py`). They read the same sample folder, ran the same scan
  and the same fit, and each produced half of what an operator wanted — so
  running both meant picking the same folder twice and fitting the same spectra
  twice, with two chances for the halves to describe different fits of one
  sample. One folder pick, one `run_sample_batch`, and every artifact derived
  from that single result.

- **Seven numbered figures and one workbook replace the three-slide deck.**
  `_1_Summary`, `_2_OM`, `_3_OM_diagnostic`, `_4_Raman`, `_5_Raman_stats`,
  `_6_PL`, `_7_PL_stats`, plus the `.xlsx`. The numeric prefix is load-bearing:
  alphabetical sort puts "Summary" last and interleaves the two techniques. The
  summary page keeps the old slide 1's content and gains the per-class
  segmentation table beneath the image grid.

- **The workbook carries every table**: `Summary`, `Raman`, `PL`, `OM_Stats`,
  `OM_Points`. The QC Panel's four CSVs are retired; two of them were
  near-duplicates of sheets the workbook already had.

- **`raman_quality.py` is now `peak_quality.py`, and serves both techniques.**
  Everything technique-specific is a `QualityFigureSpec` carrying the panel
  columns, the cleaning rule and the marker statistic together, so a call site
  cannot pair Raman's panels with PL's cleaning. The grid is computed from the
  columns rather than hardcoded: the old `GridSpec(2, 3)` silently dropped a
  third panel in any column.

- **Per-position markers: mean for Raman, median for PL**, each matching its
  own lineage, named in the figure's suptitle because the difference is
  invisible in the mark itself.

- **PL cleaning is PL's own rule**: `FWHM > 5 nm`, `Center > 700 nm`, then the
  widest 5% of each peak's fits dropped across the sample. Raman keeps the
  1.5x IQR cut that matches `aggregate_fit_results`.

- **`WSe2-HA` is folded into `WSe2`; the old `WSe2` entry is gone.** They were
  identical apart from the optical block. The surviving entry carries the
  -6.0/+4.25 pair, which means the adaptive derivation now applies to WSe2 work
  by default — the old nsigma-only `WSe2` had no base pair for it to move.

### Added

- **The QC Report derives the threshold pair per wafer, and records which one
  ran.** The merged page now calls `adaptive.derive_pair` as a pre-pass over
  the wafer's pooled frames before segmenting, as the QC Panel did — without
  it the merge would have silently put every wafer back on the fixed base pair.
  That is not cosmetic: on the 202609 batch the derivation moved the pair on
  21 of 55 wafers, and on HADH57 the difference between the derived and the
  fixed pair was 3.6 points of bilayer coverage.

  The pair and its provenance are now saved, not just shown: `describe()` sits
  under the summary page's OM table, and `OM_Stats` gains
  `Threshold_Below_pct` / `Threshold_Above_pct`, `Threshold_Base_Below_pct` /
  `Threshold_Base_Above_pct`, `Threshold_Below_Source` /
  `Threshold_Above_Source` and `Noise_Sigma_pct`. A run that used the preset
  pair unchanged records `preset` as its source, so it is distinguishable from
  an adaptive run that derived its way back to the base. `progress.py` gains an
  `optical_adaptive` stage, since pooling every frame is a second pass over
  the same images.

  The derivation's noise flags reach the summary page too, not only the screen:
  a flag means even the preset's own cut sits inside this wafer's noise, and a
  saved PNG outlives the warning that said so.

- **PL quality panels** (`PL_PANEL_COLUMNS`), ported from the `WSe2_PL.py`
  ancestor in `angyulu/wse2_optical_analysis`: FWHM and centre for Exciton and
  Trion, plus the Exciton/Trion **intensity** ratio. (The ancestor's fit table
  calls that column "Amplitude", but it holds peak height, which is what this
  codebase calls intensity — see CLAUDE.md.) The ratio column holds one panel
  where the others hold two, and that gap is deliberate: the lineage defines
  exactly one PL ratio.

- **One PL spec line: 35 nm FWHM**, on both the Exciton and Trion panels. There
  is deliberately **no PL centre spec and no PL ratio spec**: the ancestor
  gates its only PL reference line on `if col == "FWHM"`, and the 770/800 nm in
  the material preset are fit-initialisation guesses, not tolerances anyone
  measured.

- **A pre-run inventory** above the Run button, distinguishing "no PL files in
  this folder" (a naming problem) from "this material defines no PL peaks" (a
  preset problem) — they are fixed in different places.

- **A quality-gate verdict per technique**, and no combined pass/fail: PL
  carries one spec line, so an overall grade would assert a judgment the data
  cannot support.

- **`core.report.models.OpticalClassStat`** — the optical counterpart of
  `PeakStat`, so the summary figure can print the segmentation's class table
  without `core` importing `modules.optical`.

### Removed

- **`core/report/pptx.py` and `core/report/slides.py`**, and with them
  `python-pptx` and `pywin32` from `requirements.txt`. Nothing needs Microsoft
  Office installed any more. `_hex_to_rgb` moved to
  `core/report/summary_figure.py`, so the `#RRGGBB`-only rule in
  `modules/spectra/viz/palette.py` still has teeth.

- **The Plot Explorer**, which never reached this branch's history. It exists
  on branch `qc-report-v5` if it is ever wanted back.

- **`modules/optical/io/frame_csv.py`** — replaced by `frame_tables.py`, which
  returns rows instead of CSV text.

## [4.6.1] - 2026-09-15

### Fixed

- **The OM grid's wafer map is detected from the point numbers, not the frame
  count.** `build_om_grid_figure`'s default `points` was the sorted presence
  list, so a ten-point wafer missing one frame handed over nine points and
  fell back to the nine-point 3x3 layout -- silently shifting every point
  after the gap one cell over. The default is now `1..max(point)`: a
  ten-point wafer missing P7 still draws the 2-3-3-2 map with P7 an empty
  "(no frame)" cell, and the archive's sparse wafers (positions 4-6 only)
  put their frames in the rows they belong to instead of the top row. No
  numbers move; complete wafers render exactly as in v4.6.0.

## [4.6.0] - 2026-09-15

### Changed

- **Adaptive is now the default threshold mode wherever the abs pair is set.**
  `OpticalParams.adaptive_threshold` flips from opt-in to opt-out: unset means
  the QC Panel derives the pair per wafer, and only an explicit `false` pins
  the fixed pair (read `OpticalParams.adaptive_enabled`, not the raw field).
  The Material Presets checkbox now stores `False` or absent -- never `True` --
  and `WSe2-HA`'s now-redundant `adaptive_threshold: true` is dropped from
  `data/materials.json` (a legacy `true` still loads, and saving normalises
  it away). Rationale: the derivation degrades to the base pair exactly when
  the evidence for moving it is weak, so on-by-default costs nothing on clean
  wafers -- the HA 202609 validation had 30 of 52 keep the pair
  byte-for-byte -- and opting *out* is the decision worth writing down.
  Numbers move only for a wafer whose pooled histogram carries strong
  evidence the fixed pair missed; `nsigma`-only presets (no abs pair) are
  untouched.

- **Ten-point wafers draw as their 2-3-3-2 measurement map.** `build_om_grid_figure`
  laid positions three per row and silently truncated to nine while the title
  and the coverage summary still counted every frame -- a ten-point wafer's
  P10 (HADH41_RECHECK, for one) was segmented, averaged, and missing from the
  panels. Ten positions now draw as centred rows of 2-3-3-2, mirroring where
  the points sit on the wafer, and the truncation is gone for every count.
  Nine-point figures are pixel-identical to before; no numbers move.

### Fixed

- **`core/version.py` said 4.4.0 through two releases.** The v4.5.0 commit
  bumped the changelog but not the module; both now say 4.6.0.

## [4.5.0] - 2026-09-15

### Added

- **Adaptive per-wafer threshold** -- the third threshold method, after
  `nsigma` and `abs`. `OpticalParams.adaptive_threshold: true` makes the QC
  Panel derive the abs pair per wafer from its own pooled frames
  (`modules/optical/processing/adaptive.py`), with the preset's
  `abs_threshold_below` / `abs_threshold_above` as the base pair that only
  strong evidence can move: an empirical valley in the pooled density
  (preferred -- it is what a manual threshold sweep finds by eye), else a
  posterior crossing against a mixture population that is compact
  (sigma <= 1.5x the film's) and substantial (>= 3 % weight). A cut never
  sits closer to the mode than 2x the robust noise sigma (MAD of lag-4 pixel
  differences -- the estimate domain content cannot inflate), and a side
  whose *base* cut fails that gate is reported NOT MEASURABLE before any
  percentage is produced, which is the summary CSV's threshold-to-sigma rule
  applied up front instead of after the fact.

  Validated against the HA 202609 set (52 wafers): 21 moved a cut, 30 kept
  the preset pair byte-for-byte, 7 flagged not measurable -- a superset of
  the QC summary's re-image list. HADH51 is the motivating case: its fixed
  +4.25 cut reported 2.0 % "Above 2L"; the derived +2.46 -- within 0.04 of
  the +2.5 the operator had found by hand in that wafer's threshold sweep --
  reports 10.8 %. HADG38 and HADH26, where the fixed pair was already right,
  reproduce it exactly. The classifier itself is untouched: adaptive resolves
  to a concrete `abs_threshold` pair before `analyse_frame` runs, so
  `contrast.py` is byte-for-byte as it was and the locked numbers in
  `tests/unit/test_om_contrast.py` keep meaning what they meant.

- **`WSe2-HA` carries `adaptive_threshold: true`** on its `2L` optical block:
  its wafers are the ones whose trilayer valley drifts per wafer. `WSe2`
  still runs plain `nsigma`.

## [4.4.0] - 2026-09-15

### Added

- **Optical segmentation can threshold on a fixed contrast instead of `nsigma`.**
  `analyse_frame(..., abs_threshold=...)` takes either a scalar percent or a
  `(below, above)` pair, and the preset carries it as `abs_threshold_below` /
  `abs_threshold_above` on `OpticalParams`. Unset -- the default -- leaves the
  `nsigma` path byte-for-byte as it was, which is what keeps the locked numbers
  in `tests/unit/test_om_contrast.py` meaningful.

  The reason is a feedback trap in `classify()`. Its threshold is
  `mode +- nsigma * min(sigma_l, sigma_r)`, and `min()` was chosen because a
  domain population grows a shoulder that inflates *its own* half-width. That
  defence assumes only one side is contaminated. A film whose domains are small
  and pervasive rather than few and large widens **both** halves equally, the
  minimum stops being a noise width, and the threshold it sets climbs above a
  full layer step -- so the population hides itself. HADH51 is the worked
  example: nine frames whose `sigma_l` and `sigma_r` agree to twelve decimal
  places, 4 sigma landing at +7.1 % against a ~5 % layer step, and 0.14 % of a
  trilayer-bearing film reported as "Above 2L". Stated as a contrast, the
  threshold cannot be moved by the thing it measures.

  The pair is asymmetric on purpose. 2L->3L is one layer step up, but
  2L->substrate can be two or more steps down, so the natural boundaries across
  the HA 202609 set sit near +4 % and -6 % and are not mirror images. Cutting
  symmetrically puts the low threshold inside the film's own noise and inflates
  the "Below" class.

- **`WSe2-HA` material preset**, carrying `abs_threshold_below` 6.0 and
  `abs_threshold_above` 4.25 on its `2L` optical block. A separate material
  rather than a change to `WSe2`, because the pair is calibrated for the HA
  tool's noisier frames and measurably degrades the TSM260803 reference: its
  "Above 2L" falls 3.60 % -> 1.66 % and the class contrast rises +5.44 % ->
  +7.17 %, i.e. back above one layer step. TSM260803's noise sigma is 0.65 % of
  the green mode, so its natural 4-sigma cut is +2.6 %; a fixed +4.25 % clips
  its tail. `WSe2` is unchanged and still runs `nsigma=4`.

### Notes

- A fixed contrast threshold is only meaningful when it clears the frame noise.
  Across the HA 202609 set, 8 of 47 samples have a green-mode noise sigma of
  3.2-8.1 %, which puts a +4.25 % cut at 0.5-1.3 sigma -- inside the bilayer
  distribution. Those frames cannot be segmented at any threshold and need
  re-imaging; under `nsigma` they read ~0 % trilayer and under a fixed contrast
  8-31 %, and neither number is real. Check the threshold-to-sigma ratio before
  trusting a coverage figure.

## [4.3.0] - 2026-09-14

### Added

- **The QC Panel saves its numbers, not just its pictures.** One Save dialog now
  writes four CSVs beside the three PNGs, off the same chosen stem: `_OM_stats`
  and `_Raman_stats` hold the per-class and per-peak mean +/- std the figures
  summarise, `_OM_points` and `_Raman_points` hold the rows those averages were
  taken over -- one per segmented frame, one per fitted peak per grid position.

  The point of shipping both halves is that they agree: averaging a column of a
  `_points` file lands on the number in the matching `_stats` file. Keeping that
  true is why the Raman detail table holds the fits that survived the
  R-squared gate rather than every fit attempted -- the gate's survivors are
  what the figure and the statistics were built from, and a detail table with
  extra rows in it would quietly average to a different answer. The excluded
  count is still reported on screen.

  Both tables are built during the run, from the very `FrameResult` list and
  `(point, FitResult)` pairs the figures were drawn from, and held in session
  state next to the PNGs. A CSV saved beside an image therefore cannot describe
  a different segmentation or a different set of fits than the image it sits
  next to, and a preset edit that invalidates one technique's figure drops that
  technique's CSVs with it.

  New: `modules/optical/io/frame_csv.py` (`export_frame_stats_csv`,
  `export_frame_points_csv`) and two additions to
  `modules/spectra/io/results_csv.py` (`export_point_fits_csv`,
  `export_peak_stats_csv`, the latter taking the `PeakStat` list the report
  tables already use). `contrast.contrast_summary()` joins `class_summary()`,
  both now sharing one ddof rule.

  No PL pair is written. This page fits Raman only -- it calls
  `run_sample_batch(..., pl_preset=None)` -- so no PL measurement exists here to
  tabulate, and the page now says so where the files are listed rather than
  leaving the absence to be noticed. The Sample Report page is where both
  techniques are fitted and written out, to one `.xlsx`.

### Changed

- **QC Panel: "Save Images" is now "Save Results".** The button reads
  "Save Images & Data As...". Same one dialog, same stem-plus-suffix naming, same
  pre-filled `<sample>_QC.png`; the extension the dialog collects is used for
  the images and ignored for the CSVs.

- **CSVs are written as UTF-8 with a BOM.** Excel reads a bare UTF-8 CSV as the
  system codepage and mangles any non-ASCII peak label; the BOM is what stops
  that. Applies to the files this page writes to disk, not to the download
  buttons elsewhere in the app.

## [4.1.0] - 2026-09-11

### Added

- **"FWHM (v1)" comparison column.** `FittedPeak` now also carries
  `width_fwhm_v1` — `2.355 x sigma`, the Gaussian-only width the app reported
  before v3.4.0, computed alongside the correct Voigt width at fit time. The
  Spectra page's Fit Results table always shows it next to `FWHM`, for
  comparing a fit against how it would have been reported pre-v3.4.0. It is
  never the value any other calculation reads.
  On the Sample Report page, a new "Include legacy FWHM (v1) column" checkbox
  (off by default) additionally adds it to the generated .pptx's summary
  tables and the .xlsx's per-point and Summary sheets — `PeakStat` gained
  matching `fwhm_v1_mean`/`fwhm_v1_std` fields, aggregated the same way as
  `fwhm_mean`/`fwhm_std`. The "Raw" row (PL's unfitted empirical stats) has no
  Voigt fit to compute it from, so it's always dashed out. Every reporting
  surface's default (correct) `FWHM` column is unaffected either way.

## [4.0.5] - 2026-09-11

### Changed

- **Image 1's panel gaps are now pixel-exact.** The previous tightening
  (v4.0.4) only reduced the raw/analyzed `wspace` fraction, and a `wspace` is
  relative to the column it divides -- shrinking the fraction only slightly
  shrank the pixel gap, since the nested pair's columns are wider than the
  top-level grid's. `_PAIR_WSPACE` and the new `_POSITION_WSPACE` are now
  back-solved from this module's fixed `figsize` width and `dpi=160` to land
  on exactly 10 px between one position's raw and analyzed panels and 40 px
  between one position and the next -- verified by rendering and measuring
  pixel columns directly, at both 3 and 9 positions. Both figures assume a
  panel near the ~4:3 aspect a camera frame crops to; a frame letterboxed
  inside its panel widens the visible gap beyond these figures, since the
  fraction targets the panel box, not the pixels drawn inside it.

## [4.0.4] - 2026-09-11

### Changed

- **Tighter raw/analyzed pairing in Image 1.** Each position's raw and
  analyzed panels sat exactly as far apart as one position sits from the
  next, because both gaps were the same top-level GridSpec `wspace`. The pair
  now sits on its own nested GridSpec with a smaller `wspace`, so the two
  panels read as a pair and the gap between positions still reads as a
  separator.

## [4.0.3] - 2026-09-11

### Changed

- **OM panel labelling.** Image 1's Original panel used to name the frame's
  detected aperture shape — "P1 (rectangular)" or "P1 (circular)" — which read
  as a claim about the photo rather than what it actually was: the raw,
  unsegmented frame. It now reads "P1 (raw)" for every panel regardless of
  aperture shape; `FrameResult.frame_type` itself is unchanged; only this
  display label moved. The overlay panel's coverage line now reads
  "Analyzed: 2.0 % / 93.9 % / 4.1 %" rather than the bare percentages.

## [4.0.2] - 2026-09-11

### Fixed

- **The OM panels no longer carry a dark grey border.** `mask_margin` excludes
  an outer band of each frame from segmentation, and the QC Panel's Image 1
  showed that band dimmed rather than dropped, so every one of the eighteen
  Original/overlay panels displayed a thick grey frame around a smaller live
  image. `build_om_grid_figure` now crops each panel to the bounding box of
  its `valid` mask before drawing, so a rectangular frame's excluded margin is
  gone entirely. A circular frame's `valid` is an eroded disc, so its bounding
  box still has invalid corners; those are still dimmed, just over a much
  smaller area than before.

## [4.0.1] - 2026-09-11

### Fixed

- **A bad fit is a bad fit, not a crash.** R-squared is `1 - SS_res/SS_tot`,
  which is unbounded below: any model fitting worse than a horizontal line
  through the mean scores negative. `FitResult` validated it into `[0, 1]` and
  raised, so the ordinary result of running a preset against the wrong material
  arrived as `Peak fitting failed: r_squared must be in [0, 1] (got
  -0.1036...)`, an internal invariant surfacing as a user-facing error whose
  suggested remedy — check the peak initial guesses in the preset — pointed away
  from the actual problem. The validation was inconsistent as well: MoS₂'s
  383/408 cm⁻¹ peaks on WSe₂ data collapse to ~zero area and score about -0.11,
  which raised, while the Silicon preset on the *same* data scores +0.00008 and
  passed silently. Both are the same mistake, and both now reach
  `peak_metrics.R_SQUARED_MIN`, the 0.5 quality gate built to drop them.
  Only R-squared > 1 is rejected. Numbers for correctly-matched presets do not
  move; TSM260803 against WSe₂ still fits 225/225 at R² 0.944–0.967.

### Changed

- **The quality gate now says what it did, and keeps saying it.** Spectra
  dropped at `R_SQUARED_MIN` are not fit failures, so they never appeared in
  the "failed to fit" list; they simply left the pipeline and made `n` smaller,
  which reads as a sample with fewer measurements. The QC Panel and the Sample
  Report each report how many were excluded. When *every* spectrum is dropped
  there is no Raman figure either, and the QC Panel now says so in those terms
  and names the selected material as the likeliest cause, rather than leaving a
  bare exclusion count under an empty space where the figure should be. That
  verdict is rendered from persisted state: anything written inside the **Run
  Analysis** block survives exactly one rerun and is wiped by the operator's
  next interaction, which is why the first version of this message was not
  there when it was wanted.
- **`qc_panel_state` derived keys are guarded by a test.** Every key in the
  state dict must be either an operator selection or a member of one of the
  reset tuples. A derived key absent from both outlives the run that produced
  it — the stale-artifact failure the module was written to prevent, and one
  the new verdict would otherwise repeat against the wrong material.

## [4.0.0] - 2026-09-11

Breaking: `data/materials.json` changes shape. A preset is now keyed by
**material alone**, not by material-and-technique, and holds nested `raman`,
`pl` and `optical` blocks. The committed store is migrated in this same change;
a local uncommitted v1 file is migrated on load by a shim that can be deleted
once no one has one.

### Fixed

- **The preset's centre tolerance is honoured.** `fit_voigt_peaks()` re-ran
  `calculate_auto_bounds()` unconditionally, overwriting each peak's
  `center_tolerance` with the mode default of ±5 cm⁻¹. Five of WSe₂'s seven
  peaks were fitted against a tolerance they never asked for; LA, which asks for
  ±10, railed against the ±5 wall it was given and reported 130.011 ± 0.026 — a
  standard deviation of 0.026 across 225 fits being the signature of a peak
  pinned to a bound rather than a peak that was measured. The tolerance now
  travels on `PeakDefinition.center_tolerance` and survives every recalculation.
  This contradicted CLAUDE.md's "the preset owns position and width" and moves
  every WSe₂ number; see **Re-baselined** below.
- **A peak placed outside the spectrum no longer reports a fictional position.**
  Clamping the tolerance window to the data range inverts it when the peak lies
  outside the data entirely — WSe₂'s "center" template sits at 0 cm⁻¹ while the
  same preset crops to x_min=6, which produced `center_min=6.674 >
  center_max=3.000`. lmfit takes an inverted bound without complaint, silently
  **swaps** the two, and fits inside the window that swap invents: the reported
  centre, 6.405 before this change, belonged to neither the preset nor the data.
  `calculate_auto_bounds` now pins such a peak to the nearest edge, and
  `fit_voigt_peaks` expresses that as a fixed parameter rather than a degenerate
  range, which lmfit rejects outright.
- **`Raman_1.txt` is read as Raman by both filename rules.** `sample_scanner`
  accepted `RAMAN` and `RM`; `detect_mode_from_filename` knew only `RM`, so
  VABD38's naming was collected by the scanner and unlabelled by the detector.
  Both now read one shared `RAMAN_PREFIXES` / `PL_PREFIXES` in `parser.py`.
- **A preset can no longer relabel a folder of Raman spectra as PL.** The
  sidebar rewrote *every* loaded file's mode to the selected preset's, Raman
  files included. Technique now comes from each file's own filename.
- **`enabled` is a real control.** `pages/3_Material_Presets.py` hardcoded
  `True` on save, so nothing could write `False` and editing a hand-disabled
  preset silently re-enabled it. It has a checkbox now.

### Added

- **Optical tuning lives in the preset.** `nsigma`, `minpx`, `mask_margin` and
  `ff_divisor` were module constants in `contrast.py`, so the tuning
  docs/OM_Contrast_Algo.md tells an operator to do ("5 if the operator reports
  over-count; 3 if faint domains are missed") meant editing source. They are now
  an `optical` block on the material, **split by layer** — a monolayer film's
  optical contrast against SiO₂/Si differs from a bilayer's, while a Raman or PL
  spectrum does not: the signal says which peaks it has.
- **Two OM images.** The QC Panel produces a clean segmentation grid for a
  report and a diagnostic copy carrying the green-channel histograms for
  internal review, from one segmentation — the ~30 s of analysis happens once
  and only the ~5 s render repeats. Saving writes `_OM`, `_OM_diagnostic` and
  `_Raman` from one dialog. The histograms are what make a saturated frame
  legible *as* one, which is exactly what a customer reading a report does not
  need.
- **Stale figures are cleared per block.** Editing a Raman peak no longer
  discards a 30-second OM segmentation. `preset_staleness` fingerprints the
  `raman` and `optical` blocks separately, and the QC Panel drops only the
  figures whose source block changed.
- **The sidebar says what the filenames said.** Now that technique comes from
  the filename and `detect_mode_from_filename` falls back to "Raman" for a name
  it doesn't recognise, the sidebar shows the mix (`3 Raman, 1 PL, 2 undetected →
  Raman`) and "Run All Files" skips the undetected ones. A guess that nothing
  distinguishes from a reading is worse than no reading.
- **WSe₂ carries a bilayer optical block**, seeded with the algorithm's own
  defaults so it is visible and editable without changing any number. Its
  monolayer block stays absent until it is tuned against VABD38; the QC Panel
  says which it is running.

### Changed

- **`data/materials.json` is v2**, an object with `schema_version` and
  `materials` rather than a bare array — the discriminator is the JSON *type*,
  so a half-migrated file is detected rather than guessed at. `migrate_legacy`
  is a pure function (dicts in, dicts out) and raises rather than guessing on an
  entry carrying both shapes, on a material appearing in both halves, and on a
  duplicate material in the new shape. Unknown `optical` layer keys round-trip
  untouched and are reported by `validate()`: a loader that silently drops part
  of a file turns "I loaded it" into "I edited it".
- **`MaterialPreset` splits into `MaterialPreset` + `TechniquePreset` +
  `OpticalParams`.** `mode` leaves the preset entirely. `OpticalParams` fields
  default to `None` meaning "use `contrast.py`'s default", so the numbers live in
  one place and a material with no optical block produces
  `analyse_frame(**{})` — byte-identical to v3.10.0, which is what keeps
  `tests/unit/test_om_contrast.py`'s locked numbers meaningful.
- **The Material Presets page is one expander per material**, with Raman, PL and
  side-by-side optical layer sections. Layers are stored as `"1L"`/`"2L"` and
  rendered through `contrast.layer_word()`; the stored form is never the word,
  since that helper is one-way presentation and passes unknown values through.
- **The QC Panel's section 3 is "Layer"**, not "Reference Layer", because it now
  selects a tuning block as well as the class labels. Class names are unchanged:
  Below 2L / Bilayer / Above 2L.
- **`contrast.flatfield` takes `ff_divisor`**, plumbed through `analyse` and
  `analyse_frame`. It is a divisor, not a sigma: the blur runs at
  max(H, W) / ff_divisor, so a larger number means a *smaller* sigma.
  `mask_margin` and `ff_divisor` apply to both frame types — whether a frame is
  circular is decided per image at runtime by `is_circular()`, so a preset has no
  way to address one of them, and a name implying otherwise would promise a
  precision that cannot exist.

### Re-baselined

TSM260803, 225 spectra, before → after the centre-tolerance fixes:

| Peak | Centre | FWHM |
| --- | --- | --- |
| E₂g+A₁g | 249.951 → 249.952 | 7.066 → 7.102 |
| 2LA | 260.292 → 260.327 | 5.797 → 5.622 |
| B2g | 308.510 → 308.467 | 4.234 → 4.348 |
| LB | 28.100 → 27.899 | 4.278 → 4.539 |
| LA | **130.011 ± 0.026 → 125.543 ± 0.835** | 36.778 → 36.831 |
| C | 16.620 → 16.385 | 8.283 → 8.853 |
| center | **6.405 → 6.674 (pinned)** | 8.400 → 7.619 |

LA/E₂g+A₁g 0.1503 → 0.1519; B2g/E₂g+A₁g 0.1040 → 0.1031. The schema move itself
changed no fitted value: the post-migration run is bit-identical to the
post-fix one.

### Known issues

- **LA is still cornered, now against the preset's own walls.** 130 of 225 fits
  sit at its ±10 lower bound (125.0), and its Voigt FWHM is railed at 36.83
  against a ceiling of 36.844 — `width_max = 3 × width_fwhm = 45` in σ and γ,
  which is voigt_fwhm(σ_max, γ_max) = 36.844. Both need preset numbers raised,
  which is a decision about the material rather than about the code.
- **TSM260803 remains out of spec on both Raman indicators**: E₂g+A₁g FWHM 7.10
  vs 7.0, defect ratio 0.152 vs 0.13.
- **The deck still shows raw OM frames on slide 1.** The clean image is the
  obvious replacement once it has been used on a few wafers.

## [3.10.0] - 2026-09-11

### Added
- **QC Panel, a fourth section.** Point it at a sample folder, pick the
  reference layer, press Run: it produces an OM layer-segmentation figure and a
  Raman quality figure, and skips either one when the sample has no data for it.
  A sample like `TSM260803` — 50x optical frames and Raman, no PL — produces two
  images and no empty PL placeholder.
- **Optical-microscopy layer segmentation** (`modules/optical/`), a new
  technique module alongside `modules/spectra/`. The algorithm classifies each
  pixel as darker than the film, the film, or brighter, from the green channel's
  own noise width; it is vendored from the `tmd_contrast.py` research script and
  its reasoning is in [docs/OM_Contrast_Algo.md](docs/OM_Contrast_Algo.md).
  Reproduces that script's output on nine real 50x frames to within rounding.
- **Classes are named ordinally** — "Below 2L" / "Bilayer" / "Above 2L" — rather
  than "Substrate" / "Bilayer" / "Multilayer". On a bilayer film, darker than
  the film could be monolayer or bare substrate, and a single frame cannot tell
  them apart; the old naming asserted more than the measurement supports.
- **The OM figure carries a diagnostics row**: the nine green-channel histograms
  with each frame's mode and both thresholds marked. Coverage percentages alone
  cannot show whether a frame segmented sensibly — the predecessor algorithm's
  characteristic failure was every position reading 99 %+ of one class, which in
  an overlay looks like a clean wafer rather than a broken measurement.
- **matplotlib** is now a dependency, used only for these two figures.

### Fixed
- **Multi-spectrum files are fitted instead of silently skipped.** A sample
  folder's `Raman_N.txt` is often a map — one X column plus 25 or 100 intensity
  columns — and the Sample Report parsed it with the two-column
  `parse_spectrum()`, which failed, so every such point was excluded and the
  report's Raman half came out empty. `parse_spectrum_multi()` already existed
  and was simply not wired in. `TSM260803` goes from 0 fitted spectra to 225.
  Documented as Known Issue #4 in docs/Summary.md since v2.x.
- **The whole integration test suite was dead.** Streamlit 1.63 resolves a
  relative `AppTest.from_file` path against the file that calls it rather than
  the working directory, so all 15 integration tests failed with
  `FileNotFoundError` before executing a single page. They now resolve through
  `core.paths.PROJECT_ROOT`.
- **The fitting progress bar no longer freezes** when a file yields more
  spectra than the caller estimated. `sub_callback`'s `totals` is now a floor it
  raises when a label reports more, instead of a fixed denominator that a
  25-spectrum file overshoots 25-fold on its first tick.
- **The progress bar is proportional on multi-spectrum samples.** The stage
  weights were measured on a sample holding one spectrum per point, and fitting
  is the only stage whose cost scales with that count — measured on TSM260803 it
  took 77 % of the run against a declared 21.5 %, so the bar crawled through its
  first fifth and then jumped to done. `stages_for()` now takes `fit_spectra`
  and scales that one weight; `parser.count_spectra()` reads a single row to
  supply it, about 2 ms per file.

### Changed
- **Fit quality is gated and outliers are cut, on every surface.** Fits with
  R-squared at or below 0.5 are dropped, then values outside 1.5x Tukey fences
  *within their own grid position* are excluded from each metric. Cleaning per
  position rather than per sample is deliberate: position-to-position variation
  is the signal the report exists to show, and pooled cleaning would delete it.
  Single-spectrum samples are unaffected — one value per position has no spread
  to judge an outlier against.
- **`aggregate_fit_results()` takes `(point, FitResult)` pairs**, not bare fits,
  since per-position cleaning needs to know the positions.
- **The B2g / E2g+A1g intensity ratio is replaced by C / LB**, reported as the
  "stacking ratio", in the .pptx table, both CSVs and the Excel summary. C and
  LB are the shear and layer-breathing modes — both interlayer vibrations, so
  their ratio speaks to how the two layers sit on each other, which is what a
  bilayer wafer is judged on. This matches the inherited WSe2 analysis, so the
  QC Panel's figure and the report's table cannot disagree.
- **The Sample Report's fitted-spectra grid shows each position's best fit** by
  R-squared. With many spectra per position the previous `dict()` kept whichever
  parsed last, which was arbitrary and silently so.

## [3.9.1] - 2026-09-07

### Fixed
- **Every PNG render crashed under Plotly 7.** `export_figure_png()` asked for
  `to_image(..., engine='kaleido')`; Plotly 6 deprecated that argument and
  Plotly 7 removed it, so the call raised `TypeError: to_image() got an
  unexpected keyword argument 'engine'`, which the wrapper re-raised as
  `RuntimeError: PNG export failed ... Make sure kaleido is installed` — a
  misleading message, since kaleido was installed and working. It took down
  every Sample Report build at the first figure column and the Spectra page's
  Quick Export with it. The argument is simply gone now: Plotly selects kaleido
  on its own when it is installed, on 5.x as well as 7.x.
- **A failed render now chains its cause** (`raise ... from e`), so the
  underlying error keeps its own traceback instead of only surviving as text
  inside the `RuntimeError` message.

## [3.9.0] - 2026-08-24

### Added
- **Saving a Sample Report now writes an Excel workbook of the numbers beside
  the deck.** `💾 Save Report As...` produces `<name>.xlsx` next to
  `<name>.pptx` and its page images — a `Summary` sheet, then a `Raman` and a
  `PL` sheet carrying **one row per fitted peak per point**: center, Intensity,
  FWHM, each one's stderr, shape, R², χ², convergence time, and the source
  file the row came from.

  The .pptx reports one mean ± std over nine points. Anyone asking which point
  was the outlier, or wanting to plot a peak's position across the grid, had to
  refit the sample file by file on the Analysis page and export nine CSVs. The
  workbook is that table, written in the same click as the report.

- **`Summary` mirrors the slide's tables**, built from the very `PeakStat`s the
  .pptx tables are built from (`modules/spectra/io/results_excel.py` takes them
  as an argument rather than recomputing), so the two artifacts written side by
  side cannot disagree about a number. It carries the sample/material/date
  block, each technique's per-peak mean and std, the LA/E2g+A1g and
  B2g/E2g+A1g medians with their MADs, and PL's leading empirical `Raw` row.

- **Mean and std are separate numeric columns**, not one `"248.8 ± 0.2"`
  string, and every cell holds the unrounded value — the number formats only
  decide how many decimals Excel *shows*. A workbook exists so the next person
  can compute with it; a pre-formatted string is a picture of a number.

- **Excluded points are named, not just missing.** A point that failed to fit
  gets a row in the Summary sheet's excluded block with its error. The page has
  always listed them on screen, but a workbook that silently drops nine rows
  reads as a complete record of a sample that was never measured that way.

- Per-point sheets freeze the header row and carry an autofilter, which is how
  you pull one peak's row out of all nine points; a technique that produced no
  fits gets **no sheet at all**, since an empty sheet named `PL` claims a
  technique nobody measured.

### Changed
- **The report's spectra pages now read in the material preset's colors.** The
  peak components on slides 2 and 3 always carried their preset hex
  (`PeakTemplate.color` → `FittedPeak.color` → the plotted line), but they were
  the least visible thing on the slide: drawn thinner than the black total fit
  and under a dense cloud of blue data markers. The components are now the
  heaviest line in the panel, as they already are on the Spectra page, and the
  data is drawn in the color that page draws it.

- **The data trace on every exported figure is the *processed* series' color,
  purple, not the raw file's blue** (`palette.PROCESSED_COLOR`). It always was
  the processed series — `processed_data`, after de-spiking and baseline
  removal, the same numbers the Spectra page shows as "Baseline-corrected" and
  draws purple — so painting it blue made the report disagree with the screen
  about a series they both draw, which is exactly what v3.7.0's shared palette
  was introduced to stop.

  On WSe2 it did more than that: the preset gives `C` and `center` #3276EC, so
  the data and two of the peaks were the same hue and the preset colors stopped
  reading as preset colors. `palette.DATA_COLOR` is now `RAW_COLOR`, named for
  the one layer that uses it — the Spectra page's raw series, which the report
  never draws.

- The Sample Report's save step now reports every file it wrote, the workbook
  included, and the section's caption says what a save will produce.
- `openpyxl>=3.1.0` is now an explicit runtime dependency (it was previously
  only present as a pandas extra). `start.bat` installs it on the next launch.

### Fixed
- **The Spectra page's "Baseline-corrected" layer was purple by luck.** It is
  drawn `mode="markers"`, and its color was set on `line` only — which reaches
  the dots as a Plotly fallback, not as an instruction. It rendered #800080
  because plotly.js defaults a marker's color to its line's; one default away
  from silently becoming a cycled color that no longer matched the report. The
  color is now set on the marker that actually renders, and a test asserts it.

### Notes
- **Intensity in the workbook is the fitted curve's maximum**, via
  `peak_metrics.peak_intensity_and_stderr` — never `FittedPeak.area`. That
  makes it the fifth surface reporting the same quantity under the same name as
  the on-screen table, both CSVs and the .pptx, and a guard test asserts the
  column is an intensity rather than an area (they differ by ~FWHM x 1.064,
  which is the bug v3.3.0 shipped).
- A `Raw` row reports 0 counts for a flat spectrum but leaves its FWHM cell
  **empty**: 0 counts is a measurement, a width with no half-maximum crossing
  is not. Same split the master CSV and the report's Raw row already make.

## [3.8.1] - 2026-08-24

### Fixed
- **The Spectra page paid a 1.5 s image render on every rerun.** The Quick
  Export PNG feeds `st.download_button`, which needs its bytes at render time,
  so the kaleido rasterization ran on every checkbox toggle and file switch
  whether or not anyone ever clicked Download — measured at 1316 ms per rerun.
  It is now memoized: 1510 ms on the first render, 21 ms on every rerun after,
  **71x faster**, and the download button behaves exactly as before.
- **The cache key is the plotly figure itself, hashed by content** (measured
  4.3 ms), not a hand-rolled fingerprint. A fingerprint was written first and
  adversarially reviewed; it had five holes, one of them reachable —
  `st.cache_data` hashes only the decorated function's own source, never its
  callees, so editing `palette.py` moved 25,264 pixels while the digest stayed
  identical and the stale PNG was served until the process restarted. Hashing
  the figure closes that and the other four for free, because every one of
  those inputs is baked into the traces. Tests pin that changed data, a changed
  peak label or colour, changed residuals and a changed palette constant all
  miss the cache.
- **`max_entries=16` and `show_spinner=False`.** The default cache is unbounded
  and each PNG is 165-300 KB, so every refit would add one forever; the default
  spinner would render "Running _export_png_cached(...)" inside the narrow Quick
  Export column. A failing render is not cached, so a broken kaleido install
  still re-pays it every rerun — an error path, not a slow path.
- **"Run All Files" had a progress bar that read one file ahead of reality.**
  It advanced to `(idx + 1) / total` *before* running that file's fit, so with
  two files it showed 50% before any fitting began, and on the last iteration it
  read 100% for the whole of the final — usually slowest — fit before being torn
  down. The fraction is now work actually finished while the label names the
  file currently running, and both live in one `st.progress(text=...)` instead of
  two widgets each carrying half the truth.
- **The batch report no longer vanishes.** `st.rerun()` immediately after the
  loop discarded the success/warning summary and the entire list of failed
  files, so a 12-file batch ended with no record of what happened. It is gone;
  the `show_fit`/`show_components` writes it was there for still take effect,
  because their checkboxes are instantiated further down the same function.
- **The failure list is reachable at all now.** It sat in a collapsed
  `st.expander`, and clicking to open one reruns the script with the button
  False — so the block, and the expander with it, ceased to exist. It renders
  expanded and outside the status (`st.status` is expander-like and Streamlit
  forbids nesting them).
- A batch failure whose message was empty rendered as `**file**:` with nothing
  after it; `.get(k, default)` could not fire because the key is always present.

### Added
- An autouse fixture clearing `st.cache_data` between tests. Streamlit's data
  cache is process-wide and outlives an `AppTest`, surviving across instances and
  across test files in one pytest process — so a test asserting "the PNG has
  bytes" could pass on a payload another test cached, with the render path
  broken.

## [3.8.0] - 2026-08-24

### Added
- **Generate Report now reports progress for the whole build, not just the
  fitting.** A single `st.status` names the stage in flight while a bar tracks
  the measured position through it, so a 25-second build no longer looks like a
  crash.

  The old bar covered only `run_sample_batch`, then called `.empty()` on itself
  and left the remaining ~78% of the wall time completely silent — figure
  rendering, optical-image loading, .pptx assembly and the PowerPoint preview.
  Filling a bar to 100%, deleting it, and then working for another twenty
  seconds is a worse signal than showing nothing at all.

- **The stage weights are measured, not guessed** (`core/report/progress.py`).
  Profiled on a real 9-point sample (`Example/HADG06`: 9 Raman + 9 PL
  2000-point spectra, 9x 2240x1680 BMP images), total 24.5 s:

  | Stage | Time | Share |
  | --- | --- | --- |
  | Fitting spectra | 5.28 s | 21.5% |
  | Rendering Raman figures | 3.98 s | 16.2% |
  | Rendering PL figures | 4.66 s | 19.0% |
  | Loading optical images | 5.37 s | 21.9% |
  | Assembling the report | 2.09 s | 8.5% |
  | Rendering preview in PowerPoint | 3.17 s | 12.9% |

  Equal weights would put the bar at 50% with 78% of the time still to run —
  the same lie in a different shape. Aggregation is excluded deliberately: at
  4 ms a step for it would only flicker.

- **The bar keeps moving through the long stages.** Fitting reports per point
  (18 updates) off the callback `run_sample_batch` already had; each figure
  column and each optical image reports as it completes. 43 updates across the
  build, with the longest motionless stretch 3.17 s — the PowerPoint preview,
  which is one opaque call and is now labelled as such, with the status spinner
  animating throughout.

- **Stages absent from a run are dropped and the rest renormalize.** A
  Raman-only sample does not reserve 19% of the bar for PL figures it will
  never render, and an images-only folder skips fitting entirely instead of
  raising.

### Fixed
- **The fitting stage would have sat still through the entire second
  technique.** `run_sample_batch` fits Raman then PL, restarting its count for
  each, so Raman's 9/9 filled the stage and PL's 1/9 computed a lower fraction
  that the monotonic guard then pinned in place — the bar frozen for 2.9 s of a
  5.3 s stage. The adapter now sums against the combined total, so all 18
  points advance it. Caught by replaying the page's real call sequence against
  the measured timings rather than by reading the code.
- **A build that fails mid-way no longer leaves the status spinning forever.**
  The `with st.status(...)` form resolves to `error` on the way out; a bare
  handle leaves it at `running`, which is indistinguishable from the hang the
  progress exists to rule out. An integration test asserts the errored state.
- **The failed-points list is no longer trapped inside the collapsed status.**
  The status collapses itself on completion, which is no place for the list of
  points that were excluded from the report.

## [3.7.1] - 2026-08-24

### Fixed
- **Clicking the launcher while an older NexAnalyzer was still running could
  serve either version, at random.** `start.bat` pulls the new code, then starts
  a server — but Streamlit does not reload modules an existing server already
  imported, and on Windows a second Streamlit binds the same port *without
  error*. Both servers then listen on 8501 and incoming requests are split
  between them unpredictably: measured directly, three requests to 8501 were
  answered by both PIDs. The browser could show the new version, the old one, or
  flip between them on refresh, and a stale server is also what raises
  `ImportError` on a renamed symbol.
- **The launcher now frees the port before starting.** It finds whatever is
  listening on 8501, and:
  - if it is a Python/Streamlit process, explains that it is running the old
    version and offers to stop it — defaulting to yes after 15 seconds, so the
    double-click path resolves correctly on its own;
  - if the answer is no, it does **not** start a second server, since that is the
    broken state; it points at the running copy and says it may be older;
  - if the owner is not a Python process, it refuses to touch it and suggests
    another port;
  - after stopping, it waits for the port to actually be released before
    launching, and gives up with instructions rather than starting into a
    contested port.
- The port is defined once as `APP_PORT` and passed to `streamlit run`
  explicitly, so the check, the URL printed to the user, and the server can no
  longer disagree.

## [3.7.0] - 2026-08-24

### Added
- **The Sample Report's PL table now leads with the empirical measurement.** A
  "Raw" row carrying center, Intensity and FWHM — read straight off each
  processed spectrum's tallest point, no fit involved — sits above the fitted
  peaks, as mean ± std across the sample's points like every other row. The
  on-screen Fit Results table and the master CSV already showed a "Raw" row for
  PL; the .pptx was the one surface missing it, and now all three agree.
  `peak_metrics.aggregate_raw_peak_stats()` does the aggregation, returning a
  `PeakStat` labelled "Raw" so the existing table renderer needed no new
  plumbing.
- Measured off `processed_data` — after de-spiking and baseline correction, the
  same layer the fit sees — so the empirical intensity is directly comparable to
  the fitted intensities below it rather than being inflated by a baseline
  offset. On a synthetic 9-point sample the two agree to well under a percent,
  which is what makes the row a useful check on the fit.

### Fixed
- **The Sample Report's spectra didn't look like the spectra on screen.** The two
  plotters each picked their own colors, so a fit drawn black and dashed over
  blue points on the Spectra page came out as a solid orange line over
  muted-blue points in the .pptx, and components swapped dashed for solid. The
  report now draws what the screen draws: blue data points, a black dashed total
  fit, solid semi-transparent components in their preset colors, green
  residuals.
- **The colors live in one place, `modules/spectra/viz/palette.py`**, imported by
  both plotters, because two independent definitions is how they drifted apart.
  Only the traces both surfaces draw are shared; the Spectra page's own layers
  (de-spiked, baseline-corrected, live previews) stay its own. A test asserts
  neither plotter hardcodes a shared trace color again, and that both agree
  trace by trace.
- Colors are hex rather than CSS names, because the .pptx legend parses them
  with `_hex_to_rgb`, which falls back to black for anything unreadable — a
  `"blue"` there would have plotted in blue and drawn a black swatch beside it.
- Marker and line *weights* still differ deliberately: a panel shrunk into a 3x3
  grid needs heavier strokes than the full-width on-screen plot, or it reads as
  blank on a projector. Only color and dash are shared.
- Y-axis unchanged: each panel is still normalized to its own tallest peak, which
  is what keeps the nine points comparable on one shared axis.

### Changed
- **A table carrying the empirical row is no longer captioned a "fit summary".**
  The PL table now reads `PL summary (empirical + fitted, mean ± std)`; Raman,
  which has no such row, keeps `Raman fit summary (...)`. The renderer decides
  from the rows it is handed, so the caption cannot drift from the contents.
  `RAW_STAT_LABEL` moved to `core/report/models.py` alongside `PeakStat` to make
  that possible without `core` importing from `modules` — the one rule.
- **`PeakStat.fwhm_mean` / `fwhm_std` are now `Optional[float]`**, and the report
  renders "—" when they are None. A flat or non-positive spectrum has no
  half-maximum crossing, so its empirical width genuinely cannot be measured;
  printing "0.0 ± 0.0" there would read as a measurement. A width is also
  withheld when only some of the sample's points could be measured, since
  averaging that subset while `n` reports the full count would describe a width
  the sample never had. Fitted peaks always carry a width, so their rows are
  unaffected.

## [3.6.0] - 2026-08-23

### Changed
- **One name per quantity, everywhere: `intensity` for the peak's maximum,
  `area` for the integral.** "Amplitude" is gone from the codebase and from
  every surface, because it was the one word that had meant both. The fitted
  curve's maximum — the number a spectroscopist reads off a plot — is
  **intensity**; the area under the peak, which is what lmfit solves for and
  what lmfit itself calls "amplitude", is **area**. `amplitude` now appears
  only where lmfit's own parameter is addressed by name.

  | Old | New |
  | --- | --- |
  | `peak_height_and_stderr()`, `peak_height()` | `peak_intensity_and_stderr()`, `peak_intensity()` |
  | `compute_peak_height_ratio()` | `compute_peak_intensity_ratio()` |
  | `PeakStat.height_mean` / `height_std` | `PeakStat.intensity_mean` / `intensity_std` |
  | `PeakDefinition.height_max` | `PeakDefinition.intensity_max` |
  | `PeakDefinition.amplitude` | `PeakDefinition.intensity` |
  | CSV columns `Amplitude`, `Amplitude_Stderr` | `Intensity`, `Intensity_Stderr` |
  | Sample Report table heading `Amplitude` | `Intensity` |

  The on-screen Fit Results table already said "Intensity" and is unchanged,
  as are `FittedPeak.area` / `area_stderr` and `RawPeakStats.intensity`.
- No numbers change: intensities, widths and the LA/E2g+A1g and B2g/E2g+A1g
  ratios are identical either side of the rename. Only labels and identifiers
  moved.

### Breaking
- **The fit-params and master CSVs now head that column `Intensity` /
  `Intensity_Stderr`.** Anything downstream reading `Amplitude` by name needs
  updating; the values in the column are the same as before.
- **`PeakDefinition`'s serialized keys `amplitude` and `height_max` are now
  `intensity` and `intensity_max`**, and `from_dict()` has no shim for the old
  spelling. In practice nothing in the app writes them: project save/load is not
  reachable from the UI, and the shared `data/materials.json` stores
  `PeakTemplate` rows, which carry neither key — so **material presets are
  unaffected and need no migration**. Only code calling the model API directly is
  affected.

### Documentation
- **`CLAUDE.md` added**, carrying the intensity/area rule, the lmfit caveat, and
  the handful of invariants that are easy to violate and expensive to get wrong.
  The rule previously lived only in a module docstring.
- **`Fitting_Algo.md` §2 documented a bug as current behaviour.** Its "Extract
  Fitted Parameters" listing still showed `width_fwhm_fit = 2.355 * sigma_fit`,
  the Gaussian width that v3.4.0 replaced with `voigt_fwhm()`. Corrected, with a
  note on why it changed. §1-§4 were checked against the code; §5-§8 are now
  labelled as history, whose listings quote code as it stood at the time.
- **47 dead `src/...` file links across the two algo docs repointed** to
  `modules/spectra/...` and verified to resolve. Line-number anchors were dropped
  rather than left to rot on the next edit. `Baseline_Algo.md` now says plainly
  that its algorithm content has not been re-verified since v2.9.0.

## [3.5.0] - 2026-08-23

### Added
- **B2g / E2g+A1g alongside LA / E2g+A1g in the Raman fit summary.** The table
  takes a list of ratios rather than a single one, so further pairs are a
  one-line change to `_RAMAN_RATIO_PAIRS`. Any pair whose peaks aren't both
  present in a fit is dropped automatically, so the list stays safe for
  materials that don't have those modes.

### Changed
- **The two summary tables now size themselves from their row counts.** They
  are stacked, and both heights were fixed, so a second ratio row pushed the
  Raman table straight through the PL one. The Raman height is now derived and
  the PL table placed below whatever it needs.
- **Ratio rows are labelled `LA / E2g+A1g`, with the statistic named in the
  caption** — "peaks mean ± std, ratios median ± MAD" — rather than "(median)"
  on each row. The longer labels wrapped inside the Peak column, and a wrapped
  cell doubles its row height, which is what overran the table below even after
  the heights were derived. The Peak column is also wider now.

## [3.4.2] - 2026-08-22

### Changed
- **`PeakDefinition.amplitude_max` is now `height_max`.** It holds
  `5 × max(Y)` — a ceiling in height units — and only becomes an area when
  `fitting.py` multiplies it by FWHM × 1.064 on the way into lmfit. The old
  name read as lmfit's "amplitude", which is an area, so it named the one
  thing it wasn't. The two local conversions are now `area_guess` and
  `area_max` for the same reason. With this, "amplitude" no longer appears
  anywhere in the codebase meaning something other than peak height.
- No numbers change: heights, widths and the LA/E2g+A1g ratio are identical
  either side of the rename.

## [3.4.1] - 2026-08-22

### Changed
- **The integrated quantity is now called `area`.** `FittedPeak.amplitude` and
  `amplitude_stderr` become `FittedPeak.area` and `area_stderr`. "Amplitude"
  now means peak height consistently — in the UI, the CSV export and the
  Sample Report — while the area under the peak, which is what lmfit solves
  for and what lmfit itself calls "amplitude", says so in its name. Reporting
  one under the other's heading is what made the .pptx disagree with the CSV
  in 3.3.0; nothing on the model carries the ambiguous name any more, and
  tests assert that both `FittedPeak.amplitude` and `PeakStat.amplitude_mean`
  stay gone.
- No numbers change: heights, widths and the LA/E2g+A1g ratio are identical
  either side of the rename.

## [3.4.0] - 2026-08-22

### Fixed
- **Reported FWHM was the Gaussian width, not the Voigt width.** `fitting.py`
  computed `width_fwhm = 2.355 * sigma`, ignoring the Lorentzian `gamma`
  entirely, so every width in the app — the Fit Results table, the CSV export
  and the Sample Report — understated the peak actually drawn by 59-108%. On
  VBBA14, E2g+A1g was quoted as 3.88 where its fitted curve is 8.06 wide, and
  LA as 22.14 where it is 36.50. `docs/Fitting_Algo.md` already documented the
  correct Olivero & Longbothum formula; only the implementation was missing it.
  Widths are now verified against the measured half-maximum of the fitted
  component curves, agreeing to within a few tenths of a percent.
- **FWHM uncertainty propagated only sigma's error.** `2.355 * sigma_stderr`
  describes the Gaussian component rather than the width, and sigma and gamma
  trade off strongly against each other (lmfit reports correlations of -0.82 to
  -0.97 here), so each is poorly determined alone while their combination is
  not. The uncertainty now propagates from both parameters and uses lmfit's
  fitted correlation, falling back to quadrature — an upper bound — when the
  covariance is unavailable.

Peak heights, and therefore the LA/E2g+A1g ratio, are unaffected: they come
from the maximum of the component curve, not from any width parameter.

## [3.3.0] - 2026-08-22

### Fixed
- **The Sample Report's "Amplitude" column reported a different quantity from
  every other surface in the app.** The CSV export and the on-screen Fit
  Results table report peak *height*; the .pptx summary table and its
  LA/E2g+A1g ratio reported lmfit's integrated intensity (area = height x FWHM
  x 1.064) under the same heading. Because WSe2's LA mode is ~6x broader than
  E2g+A1g, their area ratio is ~4.3x their height ratio, so a report could
  never be reconciled against the CSV it came from. The report now uses height
  throughout. On VBBA14, E2g+A1g reads 299.6 rather than 3440.9 and the ratio
  0.114 rather than 0.527.

### Changed
- **The peak ratio is now the median of the per-point ratios**, with the median
  absolute deviation as its spread, rather than the mean and standard
  deviation. A ratio of two fitted quantities is where one badly fitted point
  drags a 9-point mean somewhere no measurement supports. The row is labelled
  "(median)" since the peak rows above it are means, and it prints three
  decimals — at ratios around 0.1, two rounded away the variation the row
  exists to show.
- `compute_peak_amplitude_ratio` is now `compute_peak_height_ratio` and returns
  `(median, MAD, n)`; `PeakStat.amplitude_*` is now `PeakStat.height_*`. Both
  renamed rather than redefined so callers fail loudly instead of silently
  reporting a different quantity.

## [3.2.2] - 2026-08-22

### Changed
- **Report columns drop the Y scale and stack completely flush.** Every panel
  is normalized to its own peak at 1.0, so a Y axis repeated the same three
  numbers nine times across a slide. Removing the tick labels, tick marks and
  horizontal grid also removes the only reason the panels needed a gap and the
  figures needed a wide left margin, so the panels now touch and the spectra
  take the width back. The rotated "Normalized intensity" title on the slide
  still says what the vertical axis is.

## [3.2.1] - 2026-08-22

### Changed
- **The three panels of a Sample Report column now stack flush.** The gap
  between them drops to a hairline, and each point's number moves from a title
  band above its panel to a label inside the panel's top-left corner, where it
  costs no height at all. With the tightened figure margins, column gutter and
  grid bounds, each spectrum gets **16% more height and 20% more area** than in
  3.2.0 — the nine panels read as three continuous stacks rather than nine
  boxes.

## [3.2.0] - 2026-08-22

### Changed
- **Sample Report grids are now three columns, not nine cells.** Each column
  (points 1/4/7, 2/5/8, 3/6/9) is a single figure whose three panels share one
  X-axis, so only the bottom panel carries tick labels. A column is one
  vertical line across the wafer and now reads as one measurement, with peaks
  lining up down it; the two rows of tick labels that saves is most of the
  extra height the spectra get.
- **Every panel is normalized to its own tallest peak, which plots at 1.0.**
  Peak shape and position are directly comparable across the wafer. This
  deliberately discards intensity — a weak point and a strong one now reach
  the same height by construction, unlike 3.1.0's shared raw scale. The Y-axis
  title says "Normalized intensity" accordingly.
- `peak_normalization_scale()` divides by the fitted curve's maximum rather
  than the raw maximum: a surviving cosmic ray or the Rayleigh edge routinely
  tops the raw data, and dividing by that would squash the real peaks.

## [3.1.0] - 2026-08-22

### Changed
- **Sample Report, slides 2 and 3: all nine points now share one pair of axes.**
  Each point's spectrum was autoscaled independently, so every point's strongest
  peak filled its own frame and a weak point looked exactly like a strong one —
  the grid showed nine spectra but said nothing about uniformity across the
  wafer. `shared_axis_ranges()` derives one X and Y range covering every point
  (data and fit curve, with 5% headroom on Y), and every cell is drawn on it.
- **One legend per slide instead of nine.** The legend is drawn on the slide as
  PowerPoint shapes above the grid, not rendered into each cell image. Peak
  components now take their color from the peak's Material Preset rather than
  Plotly's automatic cycling, so a peak is the same color in every cell and one
  key describes them all.
- **The axis titles moved to the slide too** — the Y title rotated in the left
  gutter, the X title centered under the grid — so the two labels appear once
  rather than eighteen times.
- **The spectra are substantially larger and legible.** Beyond the space the
  legend and axis titles gave back, the cell text was badly undersized: a
  figure rendered ~460px tall and placed in a 1.9-inch cell puts one figure
  pixel at about a third of a point, so Plotly's defaults were landing near
  3pt on the slide. Compact figures now set their fonts and stroke weights for
  the size they are actually displayed at, and render at 2x scale.

## [3.0.0] - 2026-08-21

Renamed to **NexAnalyzer** and restructured from a single-purpose spectrum fitter into a
platform: a technique-agnostic `core/` plus one module per measurement technique, with today's
Raman/PL code as the first module (`modules/spectra/`). No analysis behavior changed — same
pipeline, same fit results, same report layout.

### Changed
- **Renamed to NexAnalyzer** (`core/version.py` is now the single source of truth for name and
  version; the UI, launchers, and export filenames read from it instead of hardcoding a string
  in five places).
- **App promoted to the repo root.** `SpectralFit/app.py` is now `app.py`, so a clone is
  `git clone && cd nexanalyzer && start.bat` with no nested folder. The launchers' auto-update
  step no longer reaches one directory up for `.git`.
- **`src/` split into `core/` + `modules/spectra/`** along one rule: `core` knows nothing about
  peaks or spectra, and never imports `modules`. Platform pieces that were filed as
  spectroscopy code moved to `core/` — the PPTX builder (`core/report/pptx.py`), slide
  rasterization (`core/report/slides.py`), native dialogs and figure export (`core/io/`),
  width-aware plot rendering (`core/viz/render.py`).
- **Clearer module names**: `visualization/plotter.py` → `modules/spectra/viz/fit_plot.py`
  (static figures for export/reports), `visualization/unified_plot.py` →
  `modules/spectra/viz/live_plot.py` (the interactive session-state plot),
  `processing/peak_stats.py` → `processing/peak_metrics.py`.
- **Runtime data moved out of the package tree** to `data/` — `materials.json` is committed and
  shared, `report_settings.json` is per-installation and gitignored. Paths resolve through
  `core/paths.py` instead of counting `parent.parent` hops.
- **Pages renamed and reordered** for navigation: Spectra, Sample Report, Material Presets.
- **Sample Report**: the LA/E2g+A1g amplitude ratio is now a bolded final row of the Raman
  fit-summary table instead of a separate text line below it, and the per-point fitted-spectrum
  grids no longer include the residuals strip (unreadable at grid-cell size, and it stole
  height from the spectrum). `plot_composite()` gained `show_residuals`.
- Added `LICENSE` (proprietary, Nexstrom internal) and a root `.gitignore` covering caches,
  local Claude settings, and per-installation data.

### Removed
- **Project save/load** (`src/io/project_io.py`, `src/models/project.py`, 430 lines with tests).
  Its UI was removed in v2.11.0 and never replaced, leaving the module unreachable.
- **`StylingPreferences`** — written into session state on every launch, never read by any plot
  code, along with `get_styling()`/`update_styling()`/`get_mode()`.
- **`add_x_range_indicators()`** and `plot_composite()`'s `x_range_enabled`/`x_min`/`x_max`
  parameters. Unreachable since v2.2: `execute_auto_workflow()` crops the arrays and then resets
  `x_range_enabled` to False, so the branch could never fire. Also dropped the unused
  `width_preset` parameter.
- Orphaned helpers with no call sites: `estimate_peak_bounds()` (a wrapper around
  `PeakDefinition.calculate_auto_bounds()`), `export_single_spectrum_csv()`,
  `detect_negative_x()`, `estimate_baseline_degree()`, and eight unused imports.

### Fixed
- **One rule, one place.** Peak height (`max` of the component curve, with its stderr rescaled
  from lmfit's integrated amplitude) was implemented three times — in the master CSV, in the
  sidebar's Quick Export, and in the on-screen Fit Results table. The PL "Raw row" measurement
  was implemented twice. Both now live in `modules/spectra/processing/peak_metrics.py`
  (`peak_height_and_stderr()`, `raw_peak_stats()`) and are covered by tests; CSV output is
  unchanged.
- Fit-results CSV building moved out of the UI into `modules/spectra/io/results_csv.py`, so
  `sidebar.py` no longer assembles DataFrames inline.
- `_WIDTH_MAP` had lost its only definition when a neighbouring dead function was removed;
  `render_plot()` now lives with it in `core/viz/render.py`.

### Known limitations
- Sample folders where each point file is itself a multi-spectrum hyperspectral file aren't
  fitted yet — carried over from v2.12.0, unchanged.
- The Sample Report's summary tables and its LA/E2g+A1g ratio report lmfit's *integrated*
  amplitude, while the CSVs and the on-screen table report peak *height*. Both conventions are
  now documented in `peak_metrics.py`, but they are still different numbers under the same
  "Amplitude" label.

## [2.12.0] - 2026-08-21

### Added
- **Sample Report page** (`pages/sample_report.py`): pick a sample folder containing a 9-point OM + Raman + PL measurement grid, fit every Raman/PL file against one material's presets, and generate a three-slide PPTX report — no manual plotting or copy-pasting required.
  - **Slide 1 (overview)**: 3x3 OM image grid (magnification selectable, e.g. `100x`/`10x`) plus Raman and PL fit-summary tables (mean ± std of center/amplitude/FWHM per peak label, across the 9 points). For WSe2 Raman specifically, the Raman table carries an extra bolded row with the LA/E2g+A1g amplitude ratio (`compute_peak_amplitude_ratio()` in `src/processing/peak_stats.py`) — omitted automatically for materials without both peak labels.
  - **Slides 2 & 3**: a 3x3 grid of each point's individually fitted spectrum for Raman and PL respectively, rendered with `plot_composite(show_residuals=False)` — the same data+fit+components view used elsewhere in the app, minus the residuals strip, which is unreadable at grid-cell size and only steals height from the spectrum.
  - Missing content (no image, no fit, no stats — e.g. a technique entirely absent from a sample) renders as an empty black-outlined box rather than an error or a gray "N/A" placeholder, keeping partial reports clean.
  - After generating, the page renders the actual .pptx's slides to on-screen preview images (not a separate approximation of the layout) via PowerPoint COM automation (`src/io/slide_render.py`), and saves them alongside the .pptx as `_page1.png`/`_page2.png`/`_page3.png` when you save.
  - Folder discovery (`src/processing/sample_scanner.py`) matches `RM`/`Raman`/`rm`/`raman` (Raman) and `PL`/`pl` prefixes with either `-` or `_` before the point number (e.g. `RM_1.txt`, `RM-8.txt`, `rm-9.txt` all resolve); files that don't match a recognizable pattern are listed as ignored rather than guessed at. A "default material" selection persists across restarts (`src/presets/report_settings.json`, separate from `materials.json`).
  - New dependencies: `python-pptx`, `Pillow`, and (Windows-only, via a `sys_platform` marker) `pywin32` for the PowerPoint COM automation.
- New `tests/integration/test_sample_report_flow.py` drives the actual page via `streamlit.testing.v1.AppTest`, closing the coverage gap Summary.md previously flagged as "not practically unit-testable" for UI-workflow integration.

### Known limitations
- Sample folders where each point file is itself a multi-spectrum hyperspectral file (e.g. ~100 sub-acquisitions per grid point, seen in some real customer QC data) aren't fitted yet — those files currently fail to parse per point and are excluded gracefully (shown in an errors panel) rather than averaged or fit individually. Deferred; `parse_spectrum_multi()` in `src/processing/parser.py` is the building block for when this is picked back up.

## [2.11.0] - 2026-08-12

### Added
- **Multi-page app.** `app.py` is now a thin `st.navigation()` entrypoint grouping pages under a "Spectral Fit" section: **Analysis** (`pages/analysis.py`, today's workflow — unchanged in behavior, just relocated out of `app.py`'s top level) and **Material Presets** (`pages/material_presets.py`, new). Grouping under an explicit section means a future page (e.g. an OM Analyzer) can be added as its own sibling section without reworking these two.
- **Material Presets page**: a full in-app editor (create/edit/delete) for the materials used by Run Auto-Workflow / Run All Files — despike threshold, baseline algorithm + parameters, X-range, exclusion ranges, and peak templates (editable as a table via `st.data_editor`, add/remove rows freely). Replaces hand-editing an Excel file.
- `src/io/preset_store.py`: JSON load/save for `src/presets/materials.json`, the new preset store (replaces `src/io/preset_parser.py`'s Excel parsing).

### Changed
- **Material presets are now embedded in the app, not loaded from an Excel file.** The sidebar's Material Presets section is now just a "Select Material" dropdown reading live from `src/presets/materials.json` — the Browse Preset File / Reload Presets / clear (✖️) buttons and the Excel file-path caption are gone. Add or edit materials on the new Material Presets page instead.
- **View Options moved to the very bottom of the sidebar** (after Loaded Files), instead of sitting between Batch Export and Reset to Raw.
- The 4 existing presets (WSe2_PL, WSe2_Raman, MoS2_Raman, Silicon_Raman) were migrated as-is into `src/presets/materials.json`.

### Removed
- **`presets/` folder removed**: `material_presets.xlsx`, `create_template.py`, `README.md`. **`src/io/preset_parser.py`** (Excel parsing) and its tests (`tests/unit/test_preset_parser.py`) removed along with it; `tests/conftest.py`'s now-unused `tmp_preset_xlsx` fixture removed too.
- **`PeakTemplate.amplitude` field dropped.** Verified dead: `src/processing/fitting.py` auto-estimates amplitude from the actual spectrum data at fit time and never consults the preset's value (this was already documented on `PeakDefinition.amplitude`). `to_peak_definition()` now passes a fixed placeholder instead.
- **`MaterialPreset.to_processing_settings()` removed** — zero call sites anywhere in `src/` or `tests/`; dead code.
- **`PresetLibrary` class removed** from `src/models/preset.py`. It existed to wrap Excel's sheet-name-based lookup (`get_preset()` parsed a `"Material_Mode"` string back into two fields via `parse_sheet_name()`); JSON presets already have `material_name`/`mode` as separate fields, so callers now use a plain `dict[(material_name, mode), MaterialPreset]` directly — no parsing round-trip, no wrapper class. `PresetLibrary.last_loaded` (set on construction, never read anywhere) is gone with it.
- `validate_preset_schema()` and `parse_sheet_name()` (both Excel-specific, in the deleted `preset_parser.py`) — superseded by `MaterialPreset.validate()`, reused directly by the new editor's save action. `parse_exclusion_ranges()` was *not* dead (still used by `auto_workflow.py` for the exclusion-ranges feature) — it moved to `src/models/preset.py` instead of being deleted, with its pandas `NaN` check simplified since JSON never produces one.
- `get_sheet_count()` — repurposed as a "N material(s) configured" caption on the new Material Presets page rather than deleted outright.

## [2.10.0] - 2026-08-12

### Removed
- **Project save/load removed.** The sidebar's "Project" section (Save Project button and the "Load Project" `st.file_uploader`, including its `_loaded_project_file_id` re-run guard) is gone from `src/ui/sidebar.py`. `src/io/project_io.py` (`save_project()`/`load_project()`) is untouched and still covered by `tests/unit/test_project_io.py` — only the sidebar UI call sites were removed.
- **Plot preview removed from Export.** The composite plot no longer renders on screen before download. `plot_composite()` is still built internally so PNG/HTML Quick Export keeps working — it's just never passed to `render_plot()`.
- **Right-hand control panel removed.** `src/ui/control_panel/` (View Options, Export, Reset to Raw) is deleted; `app.py` no longer splits into `col_center`/`col_right` — the plot now takes the full width next to the sidebar.

### Changed
- **Quick Export, Batch Export, View Options, and Reset to Raw moved into the sidebar** (`src/ui/sidebar.py`), placed right after the Material Presets section (below the Run Auto-Workflow / Run All Files buttons), so the whole workflow — presets, run, export, view/reset, load/manage files — lives in one place. The pre-fit placeholder shortened to "Run Auto-Workflow to enable export." since it now sits directly under those buttons.
- "Load Spectra" (Browse Spectrum Files) and "Loaded Files" (current-file info, Remove File / Delete All) are unchanged and remain in the sidebar, now below the export/view/reset block.

## [2.9.0] - 2026-08-12

### Removed
- **Manual step-by-step processing UI removed.** The accordion's Processing Range, De-spiking, Baseline Correction, and Peak Fitting sections (`src/ui/control_panel/processing_range.py`, `despike.py`, `baseline.py`, `peak_fit.py`, `shared.py`) are gone. Processing a spectrum is now done exclusively through the sidebar's Material Preset **Run Auto-Workflow** / **Run All Files** buttons (`execute_auto_workflow()` in `src/processing/auto_workflow.py`), which already existed and needed no changes. The control panel now only shows View Options, Export, and Reset to Raw.
- **Export section's "Advanced Options" (detailed per-point CSV) dropped.** `export_single_spectrum_csv()` itself is untouched in `src/io/export.py` (and still covered by `tests/unit/test_export.py`) — only its UI call site was removed. Quick Export (PNG/HTML/CSV fit parameters) and Batch Export (master CSV across files) are unchanged.

### Changed
- The Export section is no longer step "5️⃣" of an accordion — `render_export_section()` lost its `is_expanded` parameter and outer `st.expander(...)` wrapper, since it's now the panel's only content.
- Sidebar now expands by default (`initial_sidebar_state="expanded"`) since it's the only place processing can be started from.
- `tests/unit/test_control_panel_baseline.py` removed (tested the deleted `control_panel/baseline.py` UI module; `tests/unit/test_baseline.py`, which tests the underlying `src/processing/baseline.py` algorithms used by `auto_workflow.py`, is unaffected).
- `USER_GUIDE.md`, `README.md`, `Summary.md` updated to describe the preset/auto-workflow-only flow; the now-inapplicable "Using SpectralFit (Manual Workflow)" section was removed from `USER_GUIDE.md`.

## [2.8.0] - 2026-08-12

A maintainability and correctness pass: no user-facing features were added, but several real bugs were fixed (including two that could crash or hang the app), the codebase gained its first automated test suite, and the largest UI module was split apart for maintainability.

### Fixed
- **"Load Project" crashed on every use** (`src/ui/sidebar.py`): `load_project()` returns a `(files, plot_width_preset)` tuple, but the call site assigned the whole tuple to `st.session_state["files"]` without unpacking it, so the plot immediately crashed with `AttributeError: 'tuple' object has no attribute 'keys'`.
- **Fixing the above exposed an infinite-rerun loop**: `st.file_uploader`'s value stays truthy across reruns until the file is removed or replaced. The handler had no guard, so it re-processed the same upload — including its own `st.rerun()` — on every rerun, forever, and the app never rendered past the sidebar. Fixed by tracking the uploaded file's stable `.file_id` and only processing it once.
- **Whitespace-delimited spectrum files were completely broken** (`src/processing/parser.py`): the whitespace-delimiter fallback used `pd.read_csv(..., delim_whitespace=True)`, a kwarg removed in the installed pandas version, so it raised `TypeError` on every attempt — silently swallowed by the surrounding delimiter-sniffing loop.
- **Missing spectrum files reported the wrong error**: because of the same swallowed-exception loop, a genuinely missing file surfaced as a generic "File must have at least 2 columns" `ValueError` instead of `FileNotFoundError`. Fixed by checking file existence explicitly before the delimiter loop.
- **Non-functional mobile-detection code removed** (`app.py`, `control_panel/__init__.py`): the injected JavaScript viewport-detection block could never actually communicate back to Python (Streamlit doesn't support that callback), so `is_mobile` was permanently `False` and an entire unreachable mobile-layout code path existed. Removed; the app now has a single (the only reachable) desktop layout.
- **Broken footer links** (`app.py`): "Documentation" pointed at a file deleted in an earlier commit; "Report Issues" pointed at a `your-repo` placeholder URL. Both now point at the real repository.
- **Confusing peak-count logic simplified** (`src/processing/fitting.py`): `auto_find_peaks()`'s peak-selection formula looked like it enforced a `min_peaks` floor but never actually could (Python slicing silently truncates) — simplified to what it actually computes, with output unchanged.

### Added
- **Automated test suite**: `pytest` configured via `pyproject.toml`; 119 unit tests added under `tests/unit/` covering despiking, all 5 baseline algorithms, Voigt fitting, spectrum/preset parsing, CSV/project export and import, and stale-fit detection. Previously the project had zero automated tests.

### Changed
- **`src/ui/control_panel.py` (2,687 lines) split into a package**, `src/ui/control_panel/`, one module per accordion section (`processing_range.py`, `despike.py`, `baseline.py`, `peak_fit.py`, `export.py`) plus `shared.py` for cross-section helpers. The public interface (`render_control_panel()`) is unchanged.
  - Deduplicated the baseline algorithm dispatch, which was previously implemented three times (parameter widgets, live preview, and the real run) with drift risk between preview and applied results — now a single `_run_baseline_algorithm()` helper used by both preview and run paths.
  - Consolidated the repeated `show_raw`/`show_despiked`/`show_corrected`/`show_fit`/`show_components`/`show_residuals` session-state rewrite (previously duplicated near-verbatim across all 5 sections) into a single `_set_view_stage()` helper.
  - `compute_preprocessing_hash()` / `mark_fit_stale_if_needed()` moved to a new `src/utils/fit_staleness.py`, fixing a layering violation where `src/processing/auto_workflow.py` (a processing-layer module) had to import from the UI layer to reach them.
- **Dead code removed**: 6 unused UI modules (`file_panel.py`, `control_panel_old.py`, `export_tab.py`, `fit_tab.py`, `preprocess_tab.py`, `components.py`, ~1,300 lines) plus 3 unreachable functions in `src/visualization/plotter.py` (`plot_preview`, `plot_with_baseline`, `apply_plot_width`) that had no callers anywhere in the repo.
- **Documentation consolidated**: `FITTING_IMPROVEMENTS.md` merged into `Fitting_Algo.md` as a dated implementation-history section (it was a near-duplicate changelog for work already described there); the version-history content that had been triplicated across `README.md`, `Summary.md`, and this file is now only here; `Summary.md` trimmed to focus on architecture/data-model/decisions (its stale project-structure tree and data-model code samples were also corrected to match the current code).

## [2.7.1] - 2026-05-31

### Added
- **Auto-update on launch**: `start.bat` (Windows) and `start.sh` (macOS/Linux) now pull the latest version from GitHub each time they run, so anyone who cloned the repo always launches the newest release. The check runs `git pull --ff-only` from the repo root, reinstalls any changed dependencies, then starts the app.
- **Never blocks**: if Git isn't installed, the copy isn't a git checkout (e.g. a ZIP download), or the network/GitHub is unavailable, the launcher prints a short notice and starts the version you already have.

### Changed
- README install instructions now recommend `git clone` (for auto-updates) with a manual-setup fallback, and note that ZIP downloads don't auto-update.

### Added
- **"Save Master CSV to folder" button** in the Export section's Batch Export block. Opens a native OS Save-As dialog **pre-pointed at the folder the raw `.txt` data was loaded from**, with an **editable filename**, and writes the master CSV directly there — no more browser-Downloads detour. The existing in-browser "Download Master CSV" button is retained as a fallback.
- New `source_dir` field on `SpectrumFile` records the folder each spectrum was loaded from (captured by the Browse picker). Backward-compatible: old project JSON without this field loads fine (defaults to `None`).
- New reusable `prompt_save_path()` helper in `src/io/export.py` wrapping `tkinter.filedialog.asksaveasfilename` in a subprocess (same pattern as the Browse-files dialog).

## [2.6.0] - 2026-05-30

### Added
- **"Delete All Files" button** in the sidebar's "Loaded Files" section. A single click clears every loaded spectrum at once (wired to the existing `clear_all_files()` helper), instead of removing files one by one. Placed side-by-side with the existing "Remove File" button via a two-column layout.

## [2.5.0] - 2026-05-16

### Added
- **Multi-select file picker** for spectrum input: the sidebar now exposes a **"Browse Spectrum Files"** button that opens a native OS multi-select dialog (`tkinter.filedialog.askopenfilenames`), filtered to `.txt` with an "All files" fallback. Ctrl-click / Cmd-click to pick multiple files in one dialog session.
- Last-picked directory is remembered as the next dialog's starting location (session-state key `'last_picked_dir'`).
- Pop-based reload guard via a transient `'pending_files_to_load'` session-state queue — picked files are parsed exactly once on the next rerun; no per-rerun re-parsing.

### Changed
- **Sidebar "Load Spectra" block fully replaced**: the "Folder Path" text input and "Browse File Folder" button are removed. The new picker preserves all downstream behavior (multi-Y `__1`/`__2` splitting, `detect_mode_from_filename` auto-detection, per-file duplicate skipping, per-file error isolation).
- Subprocess output for the new picker is **JSON-serialized** (rather than bare `print()`) so picked paths with spaces, commas, or unicode round-trip safely.
- **PL "Raw" summary row** in the fit-results table is now rendered **at the top** of the table instead of the bottom — applies to both the in-app table ([src/visualization/unified_plot.py](src/visualization/unified_plot.py)) and the exported master CSV ([src/io/export.py](src/io/export.py)). Easier raw-vs-fit comparison.
- Sidebar success message reads `"Loaded N file(s)"` (dropped the "from folder" suffix).

### Removed
- Session-state keys `'last_folder_path'` and `'loaded_folder_path'` (replaced by `'last_picked_dir'` and `'pending_files_to_load'`). These keys were never written to project JSON, so existing saved projects load unchanged.

## [2.4.1] - 2026-02-02

### Changed
- **Removed Display Settings UI** from sidebar - plot width now defaults to Full (100%) everywhere
- **Moved Fit Results table** from right-side control panel to below the spectrum plot in center column
- **Removed "Residuals" subplot title** that overlapped with x-axis labels (y-axis label retained)

### Fixed
- All `plot_width_preset` defaults updated from "Standard" to "Full" across codebase

## [2.3.0] - 2026-01-08

### Added
- **Material Preset System**: Excel-driven, one-click auto-workflow (X-range → Despike → Baseline → Fitting) across a full pipeline. Sheet-per-material design (e.g. "Graphene_Raman", "MoS2_Raman"), auto-discovered from sheet names; mode validation prevents applying a Raman preset to a PL file. No code changes needed to add a new material — see [presets/README.md](presets/README.md) for the Excel schema. Batch-processing 10+ files with identical parameters now takes seconds instead of minutes.

### Changed
- **Auto-Workflow rewritten** to replicate the exact manual step-by-step workflow instead of taking shortcuts. Previously it used `original_data` (instead of `raw_data`) as the X-range source, didn't reset flags or clear previews correctly, and didn't mark fits stale after preprocessing changes. It now updates both `raw_data` and `processed_data`, resets flags, clears previews, marks fits stale, and updates view options at every stage — producing identical results to manual processing.

### Fixed
- **Despike tuple-unpacking bug** in auto-workflow: `remove_spikes()` returns `(y_clean, spike_mask)`, but the whole tuple was being assigned to the Y array, which numpy then coerced into an invalid 2D array. Fixed by unpacking both return values.
- **ALS baseline parameter name mismatch**: auto-workflow called the ALS functions with `lam=` instead of the actual keyword `lambda_=`, silently halting the pipeline at the baseline stage.
- **Sparse matrix format error** ("spsolve requires A be CSC or CSR matrix format"): added `A = A.tocsc()` before all three `spsolve()` calls in `baseline.py`, fixing ALS, Rolling Ball, and airPLS.
- **Preset file not found**: the default preset path was relative and broke depending on the working directory the app was launched from. `get_default_preset_path()` now resolves an absolute path via `Path(__file__).resolve()`.
- **X-range validation crash** ("StreamlitValueBelowMinError"): a saved `x_min` from a preset could be slightly below the actual data minimum (floating-point precision). Added clamping so X-range inputs always stay within the current data's bounds.

## [2.2.1] - 2025-12-23

### Added
- **Real-time preview for baseline correction** - See red dashed baseline preview before applying
- **Real-time preview for de-spiking** - See orange dashed preview of spike removal
- **X-range processing** - Crop spectrum to specific region before processing
- **Improved peak fitting algorithm** with critical fixes:
  - Fixed amplitude initialization (convert peak height to integrated intensity)
  - Shape-aware width initialization using Gaussian/Lorentzian mixing
  - Adaptive parameter bounds (wider tolerance for broader peaks)
  - Improved auto-find FWHM estimation with curvature-based fallback
  - Peak overlap detection with actionable warnings
- **Enhanced UI/UX**:
  - Simplified View Options with organized checkbox groups
  - Auto-managed plot layer visibility at each processing stage
  - Removed left file panel - plot now takes 70% width
  - File navigation dropdown with left/right buttons at top of plot
  - Scrollable control panel (800px height)
  - Reordered workflow sections for better flow
- **Algorithm documentation**:
  - Comprehensive Baseline_Algo.md (algorithm analysis)
  - Comprehensive Fitting_Algo.md (algorithm deep dive)
  - FITTING_IMPROVEMENTS.md (v2.2.1 enhancement summary)

### Changed
- **Plot visibility behavior** (auto-managed):
  - X-range stage: Show only "Raw" curve
  - Despike stage: Show "Raw" AND "De-spiked" for comparison
  - Baseline stage: Show "De-spiked" AND "Preview baseline" (red dashed)
  - Peak fit stage: Show "Corrected", "Fit Total", and optionally "Components"
- **Removed "Preview Corrected" green curve** from baseline preview (user request)
- **UI section order**: Processing Range → De-spiking → Baseline → Peak Fitting → Export → Reset to Raw → View Options
- **Amplitude bounds**: Increased from 2× to 5× max intensity (accounts for sharp peaks)

### Fixed
- **Critical crash fixes**:
  - Peak deletion IndexError when deleting non-consecutive rows (P0)
  - Peak addition TypeError when clicking "+" button (P0)
- **Behavioral fixes**:
  - Reset to Raw now restores original data (before X-range cropping)
  - Plot layers automatically clear when advancing to next processing stage
  - Preview states cleared after fitting runs (no stray preview curves)
- **Parameter fixes**:
  - Despike sensitivity range extended to 30.0 (was 15.0)
- **Fitting algorithm improvements**:
  - Fixed amplitude initialization (critical: lmfit expects integrated intensity, not peak height)
  - Fixed parameter extraction to handle both Parameter objects and floats
  - Fixed component curve evaluation (use kwargs not params dict)
  - Fixed FitResult validation (allow empty peaks when failed)
  - Fixed baseline parameter naming (lambda_ not lam)

### Quality Improvements
- **Expected R² improvement**: 0.85-0.92 → 0.95-0.99 (for well-behaved spectra)
- **Convergence rate improvement**: 60-70% → 90-95%
- **Bound-hitting issues**: Common → Rare
- **Auto-find quality**: Poor → Good

## [2.2.0] - 2025-12-20

### Added
- **Single-page, three-panel layout** (files/plot/controls)
- **X-range cropping** with data masking
- **Real-time preview** for despike and baseline operations
- **Full peak fitting** with Voigt models
- **Sequential workflow** with auto-expand accordion
- **Unified multi-layer plot** visualization
- **Processing Range section** with X-min/X-max controls
- **De-spiking section** with threshold slider
- **Baseline Correction section** with algorithm selection (Polynomial, ALS, Rolling Ball, Spline, airPLS)
- **Peak Fitting section** with auto-find and manual peak management
- **View Options section** with layer visibility controls
- **Status tracking** with progress badges in file cards
- **Stale fit detection** using hash-based preprocessing change tracking

### Changed
- Migrated from multi-tab layout to single-page accordion layout
- Desktop layout: 70% plot width, 30% control panel
- Mobile layout: Stacked vertical (controls → plot)

## [2.1.0] - 2025-12-19

### Added
- Real-time baseline preview with instant parameter feedback
- Auto mode detection from filename patterns (RM*/PL*)
- Plot width control (Compact/Standard/Wide/Full presets)
- Negative Y value support with automatic shifting
- Enhanced export with new metadata columns

## [2.0.0] - 2025-12-18

### Added
- Initial release of SpectralFit v2.0
- Raman and Photoluminescence spectrum analysis
- Cosmic-ray spike removal (modified Z-score)
- Baseline correction (Polynomial, ALS)
- Multi-peak Voigt profile fitting
- Interactive Plotly visualizations
- Batch processing support
- Project save/load (JSON)
- CSV export for fit results

---

## Version History Summary

- **v2.9.0** (2026-08-12): Removed the manual step-by-step processing UI — spectra are now processed exclusively via the sidebar's Material Preset Run Auto-Workflow; the control panel shows only View Options, Export, and Reset
- **v2.8.0** (2026-08-12): Bug-fix and maintainability pass — fixed a crash and an infinite-loop bug in project loading, fixed broken whitespace-delimited file parsing, added a 119-test pytest suite, split `control_panel.py` into a package, removed dead code, consolidated documentation
- **v2.7.1** (2026-05-31): Launchers auto-update from GitHub on each run (with offline/ZIP fallback)
- **v2.7.0** (2026-05-30): "Save Master CSV to folder" — native Save-As dialog writes the master CSV into the raw-data folder with a user-typed filename
- **v2.6.0** (2026-05-30): "Delete All Files" button in the sidebar to clear all loaded spectra at once
- **v2.5.0** (2026-05-16): Multi-select file picker replaces folder picker; PL Raw row moved to top of fit-results table
- **v2.4.1** (2026-02-02): Display Settings removed (plot width defaults to Full); Fit Results moved below plot
- **v2.4.0** (2026-01-XX): Batch auto-workflow ("Run All Files") + smart file navigation
- **v2.3.0** (2026-01-08): Material Preset System (Excel-based auto-workflow)
- **v2.2.1** (2025-12-23): Critical fitting algorithm improvements + UI refinements
- **v2.2.0** (2025-12-20): Single-page accordion layout + real-time previews
- **v2.1.0** (2025-12-19): Real-time baseline preview + auto mode detection
- **v2.0.0** (2025-12-18): Initial release
