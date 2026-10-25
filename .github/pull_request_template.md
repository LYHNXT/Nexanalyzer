## What and why

<!-- One or two sentences: what changes for someone using the app, and why. -->

## Numbers

- [ ] **No numbers move** (UI, docs, refactor, tests only), **or**
- [ ] **Numbers move**, and the CHANGELOG entry says which ones, on which data, by how much
- [ ] Algorithm files changed → `core/version.py` bumped and CHANGELOG entry added (CI enforces this)
- [ ] Golden-fit record or a locked test changed → the CHANGELOG says why (CI enforces this)
- [ ] A new fitting behaviour is **opt-in per preset**, so the frozen golden preset doesn't move

## Checked on real data

<!-- Required when numbers move. Name the wafer(s) or folder(s) and what you compared. -->

- [ ] Ran in the app on a recent wafer and the QC Report looks right

## Gates

CI must be green before merging: **Algorithm-change guard**, **Golden fit + test suite**.
