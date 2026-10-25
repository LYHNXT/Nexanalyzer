# Runcard matching: which recipe produced a datalog

The tool's controller names each datalog by a timestamp and nothing else, so
most runs arrive untagged while the runcard that drove them sits in a folder
beside them. The Datalog page's **Detect runcards** reads both and proposes,
for every untagged run, the card that produced it — `HADH75`, or `CLEANING-4`.
It is implemented in
[modules/datalog/processing/runcard_match.py](../modules/datalog/processing/runcard_match.py)
(pure logic) and
[modules/datalog/io/runcard_index.py](../modules/datalog/io/runcard_index.py)
(file reading), and wired into [pages/4_Datalog.py](../pages/4_Datalog.py).

Status: current as of v5.7.0. Every number below was measured on HA1P01's
DATALOG and `RUNCARD/lcy` on 2026-09-28.

---

## Using it

1. Open **Datalog** and pick the tool's `DATALOG` folder as usual.
2. In the **Source** card, **Detect runcards for untagged runs**. The runcard
   folder is guessed from the datalog folder — `…\HA1P01\RUNCARD\lcy` for
   HA1P01 — and **Change…** points it elsewhere.
3. Choose **Runs from** (default: the newest 60 days of runs) and press
   **Detect runcards**. Every untagged run in that range gets a row: the
   suggested tag, a confidence, the step differences between the log and the
   card, and notes.
4. Untick anything you disagree with, edit a tag if needed, and press
   **Rename N file(s)**: each file becomes `<timestamp>~<tag>.csv`, through the
   same renamer as Bulk rename (collisions and locked files are skipped and
   listed; a file written to in the last 30 minutes is skipped as still
   logging). **Save as tags only** writes `runcard_tags.json` instead and
   renames nothing.

Selecting a single run in the run table shows the same answer inline under its
tag box, with a **Use HADH75** button. For a run that already has a tag it
shows a check instead — "the log matches this tag's runcard", or a warning that
the log looks like a different card.

### Why renaming, and what the sync has to do for it

HA1P01's `DATALOG` is filled by SulfurSync (`Sync-SulfurScience.ps1`, job
`HA-DataRecord`), a one-way robocopy (`/E /XO /FFT`) from the tool PC's Google
Drive backup. Two consequences:

- **The filename is the durable carrier, not the sidecar.** The tool PC keeps
  its own `runcard_tags.json` in the source folder, and robocopy copies it over
  the OneDrive one whenever the tool PC's is newer — so a tag saved only in
  OneDrive lasts until datalog_monitor on the tool PC next saves a tag.
- **A renamed file's original name comes back** unless the sync is told
  otherwise: robocopy copies every source name the destination lacks. That
  happened to 141 files on 2026-09-28 (and is where older pairs such as
  `21090307.csv` / `2026-09-21_064927~HADH76.csv` came from).

Since 2026-09-28 the HA-DataRecord job has `"keepRenamed": true` in
`D:\SulfurSyncMonitor\sync-jobs.json`. For such a job the script passes the
source paths listed in `DATALOG\.sulfursync-exclude.txt` to robocopy as
exclusions, and when a newly copied file has a byte-identical twin in the same
folder under a name the source does not have — the renamed copy — it deletes
the new copy and appends it to that list. A rename therefore costs at most one
re-copy, removed within the minute. Every other job, and every other file,
syncs exactly as before.

### Reading the confidence

| Confidence | Meaning | Save by default? |
| --- | --- | --- |
| high | The log is the card, step for step (cost ≤ 0.6), and nothing else fits as well — or the card was saved as the run started, or a tagged cleaning log of the same week ran exactly these steps. | yes |
| medium | Up to ~2 differing steps, or an exact match with other identical cards still free (the choice then rests on run order and save times). Stopped runs are capped here. | yes |
| low | Several steps differ from every card. Usually an operator changed the recipe on the tool, or the card file was edited after the run. Worth a look. | no |

A growth run that stopped before the recipe's `End` is suggested as
`<CARD>_FAIL`, following the existing `HADF05_FAIL` convention.

---

## How it works

### 1. Both sides become blocks on a wait clock

A **block** is the set of setpoint changes applied together, the wait that
follows, and the heater SV at the end of that wait.

- A **runcard** yields blocks by replaying its commands from the log's own
  starting state: `MFC/PC` → MFC-n or PC-n, `Accumulation PC1/2` → P1/P2,
  `Stage Rot`, `RTV Pressure Ctrl`'s gauge range, `Heater Ramp` (a background
  ramp), `Pulse`. A command that sets a channel to the value it already has is
  a no-op on both sides.
- A **datalog** yields blocks from its SV columns: every change of `MFC-1..8
  SV`, `P1..P3 SV`, `Stage Rot` and the `651C Gauge` range label, grouped when
  they land within 3 s of each other.

The clock advances **only on `Wait` rows**. Pumping, Check Status, Check PC and
Stage Pos last as long as the chamber takes, which no card says; counting only
waits on both sides is what lets a card line up against a log without any time
warping. This is deliberately *not* `modules.runcard`'s timeline, which charges
Check Status a nominal 10 s — right for drawing a recipe, wrong here, where the
log shows the same step taking a second.

### 2. Blocks are aligned, not compared by position

A small dynamic program aligns the log's blocks with the card's, with five
moves: substitute, log-only block, card-only block, and two blocks on one side
read as one on the other (a no-op set on one side splits or joins blocks).
Costs are in units of "about one differing step":

| Difference | Cost |
| --- | --- |
| A step on one side only | 1.0 per channel + 0.5 |
| A changed value on a shared step | 0.2 + 0.5·min(1, 3·relative difference) — at most 0.7 |
| A wait off by more than max(6 s, 2 %) | 0.2 + 0.5·min(1, relative difference); half for the last wait |
| Heater SV at the end of a wait off by more than 6 °C | 0.2 + 0.5·min(1, ΔT/50) |

The asymmetry — structure costs more than values — keeps an edited recipe
closer to its own older version than to a different recipe. An aborted run is
not charged for the card's blocks after its last one, nor for its last wait
being *shorter* than the card's (the stop cut it short) nor for its heater SV
there. That final log block lines up with whichever card block the run was
stopped in — usually a middle one — so "last" means the log's last block, not
the pair of last blocks; until v5.23.0 it meant the pair, and the forgiveness
never fired for a run stopped mid-card (HADH79 cost 0.58 for "wait: log 43 s,
card 180 s"; now 0). A last wait *longer* than the card's is still charged.

The first version compared the two as time series. It could not tell HADH70's
card, whose only difference from the log was the first purge wait (60 s on the
card, 301 s on the tool), from a card that differed everywhere — that one early
shift displaced every later step. Alignment costs it one mismatch (0.6).

### 3. Content narrows it to a family; order and exclusivity pick the member

Consecutive growth cards are routinely identical — HADH75 to HADH79 differ in no
step — so content alone cannot choose. `suggest_runcards` adds:

- **Candidates**: every file at the runcard folder's top level (CLEANING-1~5,
  O2-annealing, …) and cleaning-type names inside month folders are *common*
  and match any number of runs. Every other card is a *growth* card, a
  candidate only when saved within 14 days before to 7 days after the run
  started. Explorer copies (`HADH84 - 複製.csv`) and files that are not recipes
  are skipped.
- **Save-time prior** (content-cost units): −1.5 when the card was saved in the
  6 minutes before the run started (31 % of tagged runs), −0.5 when saved
  during the run, 0.01·h + 0.0002·h² for a card saved h ≤ 48 hours earlier
  (convex, so a batch prepared the night before pairs with the morning's runs
  in order), then 0.94 + 0.004 per further hour; edited after the run: 0.5 +
  0.004 per hour.
- **Exclusivity**: a growth card belongs to one run, solved jointly for all runs
  as a sparse min-cost bipartite matching. Cards already tagged on other runs
  are taken — except a card tagged only on a failed attempt (`_FAIL`), which
  stays free for 12 hours for its rerun.
- **Order**: within a wafer series (HADH, HADG, …; serial names by year-month)
  card numbers never went backwards in time across the 200 tagged runs, so
  interchangeable cards are swapped back into run order, and a card whose
  number falls outside the tagged runs just before and after the run costs
  +1.0.
- **Aborted attempts** take the card of the run that follows within 3 hours.

### 4. Cleaning recipes are edited in place

CLEANING-2 was edited on 2026-08-14 and CLEANING-5 on 2026-09-24, so a July
cleaning matches today's CLEANING-2 with one step off and a September 22
CLEANING-5 run differs from today's file in H2Se (6 vs 9 sccm). When the best
card is a cleaning, the run is also aligned against **tagged cleaning logs** of
the same ±30 days whose tag names a number (`Cleaning-2`, `cleanin-4`, …). A log
within 0.3 of one of them is that recipe, and gets its normalised name
(`CLEANING-3`).

### 5. What the log cannot show

- A card's 0.01 sccm is below some MFCs' resolution and logs as 0. Which MFCs
  is read off each log (`RunSteps.coarse`): a channel never logged strictly
  between 0 and 0.05 sccm in that run has the card's 0.01 replayed as 0. It
  was a fixed list (MFC-3 and MFC-8 fine, the rest coarse) until v5.21.0;
  from 2026-10 the controller logs MFC-8's 0.01 as 0 as well, and the fixed
  list charged every October growth run a flat 2.0 for its two MFC-8 steps.
- Every run ends with MFC-4 and MFC-6 at 500 sccm (vent and refill), and the
  next run often begins with an operator switching them off before the first
  command. A change to 0 from ≥ 300 sccm in the first 120 s is taken as the
  starting state, not a step.
- The controller logs no pressure setpoint, and `651C Pre` reads 50 whenever the
  gauge sits on its 1000 Torr range, so the `RTV Pressure Ctrl` setpoint is not
  compared — only its gauge range, which the `651C Gauge` label does show.
- A `Pulse` toggles its gas many times; it counts as one event and the toggles
  are skipped.

---

## Validation

Measured on HA1P01, runs from 2026-06 to 2026-09-26, with duplicate files
(same start time) counted once:

| Test | Growth runs | Other (cleanings, O2-annealing) |
| --- | --- | --- |
| Leave-one-out: each tagged run's tag hidden, every other tag kept (the situation in the page) | 241 / 243 | 107 / 108 |
| All runs at once, no tags used | 233 / 243 | 108 / 108 |

Both leave-one-out growth "misses" come from a re-synced typo copy
(`2026-09-08_173336~HADF28.csv`, whose run is HADH28 — the matcher says HADH28).
The one miss among the others is the 2026-09-01 cleaning tagged `cleanin-4`: it
matches today's CLEANING-3 better, because CLEANING-4 was edited the next day.

The run that drove the design, 2026-09-28: 108 untagged July–September files
were identified — 102 matched, 6 stopped during the initial pumping and have
nothing to compare — and four existing tags were found to disagree with their
logs (a `HADD54` file that is a CLEANING-2 run, the actual HADD54 run filed as
`PM-Throttle Valve Oring`, an aborted HADH71 attempt filed as `Cleaning`, and
the `cleanin-4` typo).

---

## Limits

- **A card edited after its run** matches worse than it should. The save-time
  prior and the order rule usually still pick it, at lower confidence.
- **Continuous daily logs** (the tool's format before April 2025) hold several
  runs per file. The page skips files spanning more than 12 hours: one tag
  cannot name them all.
- **Runs stopped in the first minutes** have fewer than two blocks or two
  minutes of waits and get no suggestion.
- **Identical cards across series** (HADD51 and HADE01 are the same recipe) are
  told apart only by save times and the tagged runs around them. With no tags
  nearby, such a pair can come out swapped; it shows as medium confidence with
  the other card listed under "also fits".
