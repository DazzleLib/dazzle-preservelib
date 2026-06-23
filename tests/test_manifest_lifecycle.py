"""Lifecycle tests for the manifest WRITE/READ helpers (P3 step 6).

Guards the manifest-lifecycle pull-down from the preserve CLI's
``get_manifest_path`` (preserve/utils.py) into the library:

- ``next_manifest_path`` -- the sequential-numbering WRITE side (first op,
  second-op migration, gaps, descriptions, and the read-only ``migrate=False``
  predict-without-touching-disk path).
- ``find_available_manifests(include_preserve_subdir=True)`` -- the ``.preserve/``
  fallback that previously lived only in ``verification.select_manifest``.
- ``describe_manifest`` -- a read-only single-manifest summary.

The numbering core is byte-faithful to the CLI: the CLI's args-free delegation
(``next_manifest_path(dest, migrate=not scan_only)``) must reproduce
``get_manifest_path`` exactly. These tests mirror preserve's
``test_manifest_numbering.py`` contract.
"""
import json

from pathlib import Path

import pytest

from dazzle_preservelib.manifest import (
    PreserveManifest,
    find_available_manifests,
    next_manifest_path,
    describe_manifest,
)


# --------------------------------------------------------------------------
# next_manifest_path -- WRITE-side sequential numbering
# --------------------------------------------------------------------------

def test_first_operation_returns_unnumbered(tmp_path):
    """No manifests yet -> the simple unnumbered preserve_manifest.json."""
    result = next_manifest_path(tmp_path)
    assert result == tmp_path / "preserve_manifest.json"
    assert not result.exists()  # path computed, not created


def test_second_operation_migrates_and_returns_002(tmp_path):
    """An unnumbered manifest present -> migrate it to _001, return _002."""
    single = tmp_path / "preserve_manifest.json"
    single.write_text('{"test": "data"}')

    result = next_manifest_path(tmp_path)

    assert result == tmp_path / "preserve_manifest_002.json"
    assert (tmp_path / "preserve_manifest_001.json").exists()
    assert not single.exists()  # the rename happened


def test_migrate_false_predicts_002_without_touching_disk(tmp_path):
    """Read-only callers (scan-only) get the predicted path, no rename."""
    single = tmp_path / "preserve_manifest.json"
    single.write_text('{"test": "data"}')

    result = next_manifest_path(tmp_path, migrate=False)

    assert result == tmp_path / "preserve_manifest_002.json"
    # Crucial: nothing moved -- the unnumbered manifest is untouched.
    assert single.exists()
    assert not (tmp_path / "preserve_manifest_001.json").exists()


def test_sequential_numbering(tmp_path):
    """Multiple numbered manifests -> max + 1."""
    for n in (1, 2, 3):
        (tmp_path / f"preserve_manifest_{n:03d}.json").write_text(f'{{"num": {n}}}')

    result = next_manifest_path(tmp_path)
    assert result == tmp_path / "preserve_manifest_004.json"


def test_numbering_handles_gaps(tmp_path):
    """Gaps are never reused -- always max + 1."""
    for n in (1, 3, 7):
        (tmp_path / f"preserve_manifest_{n:03d}.json").write_text(f'{{"num": {n}}}')

    result = next_manifest_path(tmp_path)
    assert result == tmp_path / "preserve_manifest_008.json"


def test_numbered_manifests_with_descriptions_in_dir(tmp_path):
    """Descriptions embedded in existing filenames are parsed for the number."""
    (tmp_path / "preserve_manifest_001__dataset-A.json").write_text('{"num": 1}')
    (tmp_path / "preserve_manifest_002__training.json").write_text('{"num": 2}')
    (tmp_path / "preserve_manifest_003.json").write_text('{"num": 3}')

    # Default (no description requested) -> bare _004, byte-faithful to the CLI.
    result = next_manifest_path(tmp_path)
    assert result == tmp_path / "preserve_manifest_004.json"


def test_description_param_embeds_label(tmp_path):
    """The additive description= param yields the NNN__<label> grammar."""
    (tmp_path / "preserve_manifest_001.json").write_text('{"num": 1}')

    result = next_manifest_path(tmp_path, description="run2")
    assert result == tmp_path / "preserve_manifest_002__run2.json"


# --------------------------------------------------------------------------
# find_available_manifests -- READ side + .preserve/ fallback
# --------------------------------------------------------------------------

def test_find_default_is_single_dir_only(tmp_path):
    """Default behavior: primary dir only, no .preserve fallback."""
    preserve_sub = tmp_path / ".preserve"
    preserve_sub.mkdir()
    (preserve_sub / "preserve_manifest.json").write_text("{}")

    # Default off -> the .preserve manifest is invisible.
    assert find_available_manifests(tmp_path) == []


def test_find_preserve_subdir_fallback_when_primary_empty(tmp_path):
    """include_preserve_subdir=True falls back to .preserve/ only if empty."""
    preserve_sub = tmp_path / ".preserve"
    preserve_sub.mkdir()
    (preserve_sub / "preserve_manifest.json").write_text("{}")

    found = find_available_manifests(tmp_path, include_preserve_subdir=True)
    assert len(found) == 1
    assert found[0][0] == 0
    assert found[0][1] == preserve_sub / "preserve_manifest.json"


def test_find_primary_wins_over_preserve_subdir(tmp_path):
    """Fallback is only-if-empty: a primary manifest suppresses .preserve."""
    (tmp_path / "preserve_manifest.json").write_text("{}")
    preserve_sub = tmp_path / ".preserve"
    preserve_sub.mkdir()
    (preserve_sub / "preserve_manifest_005.json").write_text("{}")

    found = find_available_manifests(tmp_path, include_preserve_subdir=True)
    assert len(found) == 1
    assert found[0][1] == tmp_path / "preserve_manifest.json"


def test_find_numbered_sorted_with_descriptions(tmp_path):
    """Numbered manifests sort by number; descriptions are parsed."""
    (tmp_path / "preserve_manifest_001.json").write_text("{}")
    (tmp_path / "preserve_manifest_002__backup.json").write_text("{}")

    found = find_available_manifests(tmp_path)
    assert [(n, p.name, d) for n, p, d in found] == [
        (1, "preserve_manifest_001.json", None),
        (2, "preserve_manifest_002__backup.json", "backup"),
    ]


# --------------------------------------------------------------------------
# describe_manifest -- read-only summary
# --------------------------------------------------------------------------

def _write_real_manifest(path: Path, n_files: int = 2) -> None:
    m = PreserveManifest()
    op = m.add_operation(operation_type="COPY")
    for i in range(n_files):
        m.add_file(
            source_path=f"/src/f{i}.txt",
            destination_path=f"/dst/f{i}.txt",
            operation_id=op,
        )
    m.save(path)


def test_describe_unnumbered_manifest(tmp_path):
    p = tmp_path / "preserve_manifest.json"
    _write_real_manifest(p, n_files=2)

    desc = describe_manifest(p)
    assert desc is not None
    assert desc["number"] == 0
    assert desc["description"] is None
    assert desc["manifest_version"] == 3
    assert desc["file_count"] == 2
    assert desc["operation_count"] == 1
    assert isinstance(desc["manifest_id"], str) and desc["manifest_id"]


def test_describe_numbered_manifest_with_description(tmp_path):
    p = tmp_path / "preserve_manifest_004__nightly.json"
    _write_real_manifest(p, n_files=1)

    desc = describe_manifest(p)
    assert desc is not None
    assert desc["number"] == 4
    assert desc["description"] == "nightly"
    assert desc["file_count"] == 1


def test_describe_missing_manifest_returns_none(tmp_path):
    assert describe_manifest(tmp_path / "does_not_exist.json") is None


def test_describe_invalid_manifest_returns_none(tmp_path):
    bad = tmp_path / "preserve_manifest.json"
    bad.write_text("not json at all {{{")
    assert describe_manifest(bad) is None
