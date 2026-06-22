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

### Notes
- The manifest + operations extraction from the `preserve` project (stack phase
  P3) is in progress: three drifting `preservelib` copies (preserve / ghtraf /
  safedel) collapse into this one home; filesystem primitives delegate down to
  `dazzle-filekit` (L1); the `preserve` CLI thins to a consumer. The first
  functional release ships as 0.8.0 (Roadmap, issue #2).
