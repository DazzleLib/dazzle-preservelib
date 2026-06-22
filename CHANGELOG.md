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

### Notes
- The first functional release ships as **0.8.0** (continuing the preserve
  lineage; supersedes the 0.4.0-snapshot and 0.7.3-embedded lineages).
  Roadmap: issue #2.
