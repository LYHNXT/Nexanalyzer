# NexAnalyzer

Nexstrom's measurement data analyzer. A desktop web app (Streamlit) that turns raw
measurement files and tool logs into fitted results, QC reports and run-by-run
comparisons, driven by per-material presets rather than manual parameter tuning.

The current version is in [`core/version.py`](core/version.py) and every release is
described in [CHANGELOG.md](CHANGELOG.md).

> **Internal, proprietary software.** This repository is private to the Nexstrom
> organisation on GitHub. Access to it is not a licence to use or share it outside
> Nexstrom; see [License](#license).

---

## Quick start

### Windows: one click

Open **[Install-NexAnalyzer.bat](https://github.com/Nexstrom/NexAnalyzer/blob/main/Install-NexAnalyzer.bat)** on GitHub (sign in
first — the repository is private), click the **Download raw file** button at the top right
of the file, and double-click what you downloaded. It installs Python and Git if they are missing,
downloads the app, puts a shortcut on your Desktop, and launches it. Your browser may warn
about keeping a `.bat` file; choose **Keep**.

### Any platform: clone it yourself

**Prerequisites:** Python 3.10+ (tick "Add Python to PATH" on Windows) and
[Git](https://git-scm.com/downloads).

```bash
git clone https://github.com/Nexstrom/NexAnalyzer.git nexanalyzer
cd nexanalyzer
```

- **Windows**: double-click `start.bat`
- **macOS / Linux**: `./start.sh`

The launcher pulls the latest version, sets up a virtual environment, installs
dependencies, and opens the app at `http://localhost:8501`. It updates itself on every
run and never blocks: offline or without Git, it launches the version you already have.

> A ZIP download works too, but it has no Git history, so it **won't update itself**.
> Clone with Git.

> **Moved on 2026-10-28** from `angyulu/NexAnalyzer`. Old clones keep working through
> GitHub's redirect; to point one at the new address:
> `git remote set-url origin https://github.com/Nexstrom/NexAnalyzer.git`

<details>
<summary>Manual setup</summary>

```bash
python -m venv venv
venv\Scripts\activate        # Windows
source venv/bin/activate     # macOS/Linux
pip install -r requirements.txt
streamlit run app.py
```
</details>

---

## Pages

The sidebar groups pages by the data they read.

| Section | Page | Reads | Gives |
|---|---|---|---|
| **Analysis** | **Spectra** | Raman / PL `.txt` files | Despike, baseline and multi-peak Voigt fit from a material preset; one file or a batch; figures and CSV |
| | **QC Report** | One sample folder: 9-point OM + Raman + PL grid | Eight figures and one workbook from a single fitting pass (below) |
| | **Material Presets** | `data/materials.json` | Per-material Raman / PL fit blocks and per-layer optical (OM) settings, edited in-app |
| **Process** | **Datalog** | Tool-controller run logs | One run's channels with setpoints and out-of-tolerance marks, or several runs overlaid |
| | **Runcard** | Recipe CSVs | One row per recipe, and the recipe replayed as a temperature profile and channel gantt |
| | **Split Table** | A tool's run-by-run workbook | Filtered views, trends and correlations of recipe, run and QC columns |
| **Publish** | **OneNote** | Finished results from the pages above | Appended to the wafer's page in the team notebook |
| **Setup** | **Tools** | `data/tool_layouts.json`, `data/tool_roots.json` | Where each deposition tool's folders are on this PC |

### QC Report

Pick a sample folder and a material, click Generate. One folder scan and one fitting pass
produce every artifact, so no two figures can describe different fits of the same sample:

| | Figure |
|---|---|
| 1 | **Summary**: OM grid, its class table, Raman and PL fit-summary tables, domain-size table |
| 2 | **OM**: layer segmentation across the grid positions |
| 3 | **OM diagnostic**: the same, plus the green-channel histograms showing where each threshold landed |
| 4 | **Domain size** (opt-in): flake domains measured in microns, or pixels without a calibration |
| 5 | **Raman**: the nine fitted Raman spectra |
| 6 | **Raman stats**: FWHM, peak centres and diagnostic ratios, with the customer spec lines |
| 7 | **PL**: the nine fitted PL spectra |
| 8 | **PL stats**: the same panels for PL |

plus one `.xlsx` with every number behind them. Missing data is skipped rather than drawn
empty, and the inventory above the Run button says what was found before anything runs.
How the stats figures state the customer spec: [docs/QC_Report_Spec.md](docs/QC_Report_Spec.md).

### Input spectra

Two-column (or multi-Y) `.txt` files:

- Column 1: Raman shift (cm⁻¹) or wavelength (nm); Raman vs PL is taken from the filename
- Column 2+: intensity, one column per acquisition
- Delimiter: tab, comma, semicolon or whitespace (auto-detected)
- Header: optional; text, `#key=value` lines and `[Header]` sections are skipped, and the
  data starts at the first row of two or more numbers
- Encoding: UTF-8, UTF-16 or Windows-1252

---

## Architecture

```
app.py          Composition root: page config, module session state, navigation
pages/          One file per screen
core/           Platform: knows nothing about peaks, frames or runs
modules/        One package per technique or data source
  spectra/        Raman & PL: models, parsing, baseline, fitting, figures
  optical/        OM layer segmentation (contrast)
  qc_report/      The QC Report pipeline
  datalog/        Tool run logs
  runcard/        Recipes, growth window, card-to-log matching
  splittable/     Run-by-run workbook views
  onenote/        Publishing to the team notebook
data/           materials.json, tool layouts, calibrations (committed); local settings (ignored)
tests/          unit/, integration/, golden/ (validated reference cases)
docs/           Design and algorithm notes
```

**The one rule:** `modules/*` may import `core`; `core` never imports `modules`. Adding a
technique means adding a package under `modules/` and registering its pages in `app.py`.

---

## Documentation

| Document | What it covers |
|---|---|
| [USER_GUIDE.md](USER_GUIDE.md) | Step-by-step use of every page |
| [CHANGELOG.md](CHANGELOG.md) | Every release: what changed and which numbers moved |
| [docs/Summary.md](docs/Summary.md) | Architecture, data model, design decisions |
| [docs/Development_Process.md](docs/Development_Process.md) | Branch, pull request, CI and release |
| [docs/QC_Report_Spec.md](docs/QC_Report_Spec.md) | The customer QC spec and how the report states it |
| [docs/Fitting_Algo.md](docs/Fitting_Algo.md) | Peak fitting |
| [docs/Baseline_Algo.md](docs/Baseline_Algo.md) | Baseline correction |
| [docs/OM_Contrast_Algo.md](docs/OM_Contrast_Algo.md) | OM contrast segmentation |
| [docs/Runcard_Matching.md](docs/Runcard_Matching.md) | Matching runcards to datalogs |

---

## Development

```bash
pip install -r requirements-dev.txt
git config core.hooksPath .githooks       # once per clone: golden-fit check on commit and push
python -m pytest                          # ~1,790 tests
python -m ruff check .
python -m tests.golden.run                # validated golden set -> dist/golden/golden_report.md
powershell -File scripts\ship.ps1         # gates, push, open the PR, wait for CI
```

Every commit and push re-fits a frozen golden set of production spectra and must reproduce
its recorded numbers; every pull request repeats it on CI, together with the full test
suite and a guard that refuses an algorithm change without a version bump and CHANGELOG
entry. Read [CLAUDE.md](CLAUDE.md) and
[docs/Development_Process.md](docs/Development_Process.md) before changing anything under
`modules/*/processing/` or `data/materials.json`.

---

## License

Proprietary. Copyright © 2026 NEXSTROM PTE. LTD. All rights reserved. For use by Nexstrom
employees and authorised contractors only; see [LICENSE](LICENSE).

Third-party open-source packages the app depends on (listed in
[requirements.txt](requirements.txt)) remain under their own licences.

## Contact

Ang-Yu Lu, angyu.lu@nexstrom.com
