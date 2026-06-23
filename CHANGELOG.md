# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to a PEP 440 versioning scheme (see `_version.py`).

Status: **pre-release (P3 extraction in progress).** The first functional
release is **0.8.0** (continuing the preserve lineage; supersedes the
0.4.0-snapshot and 0.7.3-embedded lineages). The public surface locks at that
release (`docs/api-stability.md`).

## [Unreleased]

### Added
- Project scaffold: MIT license, `dazzle_preservelib` package, charter
  docstring, day-one guards (`docs/api-stability.md` + import canary).

### Changed (P3 extraction -- toward 0.8.0)
- Imported the canonical `preservelib` verbatim (conservation snapshot;
  collapses the three drifting copies preserve/ghtraf/safedel into one home),
  validated through the real `preserve` CLI, then began delegating its
  primitives DOWN to `dazzle-filekit` (L1):
  - **`metadata` -> re-export shim over `dazzle_filekit.metadata`** (V6). The
    ~665-line standalone implementation is removed; filekit's verified superset
    (ported from this code, then extended) is the single home. The safedel embed
    already ran this exact delegation in production.
  - **`links` intrinsic mechanics -> `dazzle_filekit`** (V7, R7). `is_junction`
    (the reparse-tag fix), `detect_link_type`, `read_link_target` (kills the
    banned `cmd /c dir /al`), the junction/symlink/hardlink creation primitives,
    and `remove_link` now delegate down (`links.py` -146 net lines). preservelib
    keeps the L3 link POLICY (`LinkHandlingMode`, the relational `LinkInfo`,
    `decide_link_action`, the `create_link` orchestrator, `verify_link`) and a
    TRANSLATING `detect_link_type` shim that preserves the on-disk manifest's
    `'soft'`/`'hard'` vocabulary. Both delegations were verified by a body-level
    `/move-code` conservation audit; red-green tests guard the name-map /
    arg-order / return-shape adapters the audit surfaced.
  - **hashing (`calculate_file_hash` / `verify_file_hash`) -> `dazzle_filekit.verification`.**
    Body-audited identical/superset (filekit's is "the core implementation used
    by both"); preservelib keeps thin wrappers preserving its exact signature
    (incl. the no-op `manifest`/`progress_callback` stubs -- verified unused by
    any consumer) and delegates the computation down. Dead `hashlib` import removed.
  - **disk-space STAYS at L3 (not delegated).** A body-audit revised the planned
    delegation: preservelib's `check_disk_space` (3-state `OK`/`SOFT_WARNING`/
    `HARD_FAIL` logic) and `InsufficientSpaceError` (attr `destination`, which the
    CLI's error handler reads) are preserve-specific, NOT filekit duplicates --
    delegating would have lost the smart logic and broken the CLI. Kept verbatim.
  - **Removed the `sys.path.insert`/`append` dev-fallback hacks** in
    `operations.py` + `restore.py` (V5): `dazzle-filekit` is a declared dependency,
    so its primitives import directly and a missing dep fails loud instead of
    silently degrading to `None`.
  - **Removed the dead `from preserve.output import get_formatter`** upward CLI
    coupling in `operations.py` (it was unreachable -- nested in a contradictory
    `if formatter is None` inside `if formatter:`).
  - **Union `__init__` surface (step 5a).** Widened the package's public exports
    to the union of the canonical/ghtraf/safedel copies (30 symbols) -- adds the
    destination-awareness (`FileCategory`/`ConflictResolution`/`scan_destination`/...)
    and verification (`VerificationStatus`/`find_and_verify_manifest`/...) surfaces
    so no consumer loses a package-level symbol. Fixed the stale `preservelib.*`
    logger names.
  - **`[dazzlelink]` bridge rewired to `dazzle_linklib` (step 5b).** Per the meshing
    DWP, the bridge is a thin adapter over the lib's RECORD API: it builds
    `DazzleLinkData` records and writes/reads them via `export_link`/`import_link`/
    `find_dazzlelinks` -- NOT the lib's `create_link` (which makes an OS symlink;
    a same-name collision with the dazzlelink tool's old API). Deleted
    `SimpleDazzleLinkData` (a subset of `DazzleLinkData`), the multi-API `hasattr`
    shimming, and the last `sys.path.insert` fallback (V5). All preserve-domain
    path-layout/mode/timestamp logic is preserved verbatim. The `[dazzlelink]`
    extra now hard-requires `dazzle-linklib` (D2). core.py -239 net lines; new
    round-trip tests guard the write/read and the `export_link`-not-`create_link`
    invariant.

### Notes
- The first functional release ships as **0.8.0** (continuing the preserve
  lineage; supersedes the 0.4.0-snapshot and 0.7.3-embedded lineages).
  Roadmap: issue #2.
