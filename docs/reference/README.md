# Vendored references

Source that is a **specification**, not code this project runs. Nothing here is
imported by the app, collected by the test suite, or linted — `pyproject.toml`
excludes this directory from ruff precisely so it can stay byte-identical to
what was handed over, which is the only thing that makes it worth keeping.

Treat these files as read-only. Their value is that they can be diffed against
and run side by side with our implementation; tidying an unused local or
reflowing a comment destroys that.

## `render_runcard.py`

The standalone CLI that draws a runcard's recipe profile as SVG. It is the
authority on recipe semantics for `modules/runcard/`:

- which commands advance the clock, and which are instantaneous;
- how a `Heater Ramp` interacts with the clock it does not spend;
- how the growth window is defined — **exactly** the stretch at the peak, ended
  by whichever comes first of heater-off and a commanded ramp-down;
- the cooldown model (exponential, τ = 1500 s) and the 25 °C ambient;
- that `RTV Pressure Ctrl`'s second column is a gauge range and its third is the
  commanded chamber-pressure setpoint, in Torr;
- that PC-1, PC-2 and every other PC channel stay separate and are never merged;
- the per-species gas palette, and which events are worth marking.

Four things it records that cost this app a bug when they were not carried
across, all noted here so they are not lost again:

1. **The growth window is not a 5 °C band.** The file says so in its own words —
   "not a 'within 5°C' threshold estimate — that was a previously-fixed bug and
   must not be reintroduced". This app reintroduced it by porting an older
   ancestor, and shipped it in v5.1.0 before this file arrived.
2. **Pressure channels hold their last commanded setpoint to the end of the
   run.** An earlier version inferred an end-of-run switch to vacuum from the
   first low `Pumping Forward` value, which also fires during ordinary
   pre-growth evacuation seconds into a run, and that falsely truncated PC-1 and
   PC-2's final segment.
3. **A zero is a setpoint too.** Every commanded PC, RTV and spin value is
   drawn, 0 included — PC as a "0" bar, RTV below 1 Torr as "vac", spin as
   "0 rpm" — and only a zero-*length* segment is dropped. The app applied the
   gas rows' "strictly above zero" floor to these rows, so `MFC/PC,PC-2,0`
   ended PC-2's row instead of holding it at 0 to the end of the run; 324 of
   2,349 real cards lost a segment that way until v5.23.0. MFC gas rows keep
   their floor.
4. **Nothing commanded after heater-off reaches the temperature trace.** The
   reference's evaluator reads only keypoints at or before heater-off, so a
   `Heater Ramp` issued after it never acted. The app laid every ramp down
   whole and decayed from the last one's target, so its trace could run
   backwards in time and hold a temperature the model had already cooled
   from (fixed v5.23.0; see departure 4 below for a ramp still running).

Four places where `modules/runcard/` deliberately departs from this file, all
because the recipes outgrew it (1 and 2 in v5.21.0, 3 and 4 in v5.23.0):

1. **`Pulse H2Se,N,half` advances the clock.** The reference knows only `Wait`
   and `Check Status`, and every recipe written since mid-2025 grows by
   pulsing: `Pulse H2Se,72,5` is 72 pulses of 5 s on, 5 s off. Ignoring it put
   heater-off on the same second the peak was reached, so 44 of 2,349 real
   recipes reconstructed a zero-length growth window. The app charges
   `N * 2 * half` plus a measured 1.5 s per pulse of controller switching
   (`growth_window.pulse_duration_seconds`). `Check PC-*` and `Stage Pos`
   remain off the clock, like `Pumping`, for the same reason.
2. **The tube-group series is read past the `NS` prefix.** Production cards
   are named `NSHA1P01...`/`NSVA1P01...`; the reference keys its inner/outer
   plumbing on `run_name[:2]`, which is always `NS`, prints its fallback note
   and uses HA for every file. The app skips the `NS` and reads `HA`/`VA`, so
   VB1P01's 577 cards get the VA/VB plumbing (MFC 1-4 / 5-8) instead of HA's.
3. **Only `Heater Soak,0` is heater-off.** The reference reads every first
   `Heater Soak` as off, whatever its first parameter. On the tool, a nonzero
   one steps the heater setpoint and keeps controlling: the HA1P01 datalogs
   show `Heater Soak,600,--` taking the SV to 600 and holding it, and the
   `NSHA1N012506029`-`036` family grows 20 min at 900 °C, soaks at 600 for
   15 min, ramps back to 900 for a second 20 min, then ends on
   `Heater Soak,0,--`. The app treats a nonzero soak as an instantaneous step
   to that temperature that does not set heater-off
   (`growth_window.soak_setpoint`); a zero, or an unreadable first parameter,
   is off as before. The growth window is unchanged by this on those cards —
   the first stretch at the peak, ended by the step down — but the trace now
   holds 600 °C where the reference decays from 900, and the cooldown starts
   at the final `Heater Soak,0`. 60 of 2,349 real cards carry a nonzero soak.
4. **A ramp still running at heater-off stops at the temperature it reached.**
   The reference pins the ramp's *target* at the heater-off instant, which
   draws the ramp as if it had finished the moment the heater was cut, and
   decays from there. The app interpolates along the ramp instead, so the
   peak and the cooldown start from what the heater actually reached. One
   real card does this — `VB1P01/RUNCARD/LYH/NSVA1P012512040.csv`, cut at
   2100 s 56 % of the way from 200 to 850 °C — and it now peaks at 564 °C
   where the reference says 850. The growth window agrees with the
   reference's: zero-length, on heater-off.

Run it against a recipe to compare:

```
python docs/reference/render_runcard.py <runcard.csv> --out-dir <somewhere>
```

It prints a summary — total time, peak, growth window, per-channel setpoints —
which is what `modules/runcard/` is checked against. At the time of writing the
two agree on total time, peak temperature and growth window for all 40 example
recipes, across both the VBBE and HAD* families.
