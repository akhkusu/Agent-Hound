# Shared code and local evidence

Keep reusable collectors, CLI code, tests with synthetic fixtures, and general documentation in `src/`, `tests/`, `examples/` and `docs/`. These are the reviewable commit candidates.

Keep tenant-specific scripts, account names, document IDs, chat transcripts, screenshots and live collection output in `.local/`. Git and workspace source/asset discovery ignore that directory. The previous one-off validation work is preserved in `.local/lab/`:

- `tools/`: fixed-account live probes and injection experiments.
- `tests/`: tests that load those machine-specific probes.
- `docs/`: reports describing the actual account and experiments.
- `outputs/` and `copilot_windows_inventory.json`: collected machine data.

Existing evidence journals and screenshots remain directly under `.local/`. This is an archive move: scripts that derive paths from their location may need their lab/evidence roots adjusted before reuse. Run shared tests with `python -m pytest tests`; they do not require these archived files. Run live experiments only within their explicitly authorized scope.

The cleanup moves untracked material only. It does not remove anything from Git history, stage files, or create a commit. Check `git status --short` before committing. Default collector output filenames are ignored; for other filenames, choose `.local/` as the destination.
