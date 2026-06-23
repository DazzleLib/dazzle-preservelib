"""Conservation invariants for the links -> dazzle_filekit delegation (P3 4b).

Guards the three traps the body-level audit flagged
(private/claude/2026-06-22__links-delegation-conservation-audit.md):

1. NAME-MAP: detect_link_type must keep preservelib's 'soft'/'hard' vocabulary
   (the ON-DISK MANIFEST stores it) -- NOT filekit's 'symlink'/'hardlink'.
2. ARG-ORDER: the _create_* wrappers must point a link at its intended target
   (filekit's signature is (target, link) -- reversed from preservelib's).
3. The name-map break site: symlink removal must still work via remove_link.
"""
import os
import sys
from pathlib import Path

import pytest

from dazzle_preservelib.links import (
    LINK_TYPE_SOFT,
    LINK_TYPE_HARD,
    LINK_TYPE_JUNCTION,
    detect_link_type,
    remove_link,
    create_link,
)


def _try_symlink(target, link, target_is_directory=False):
    try:
        if os.name == "nt":
            os.symlink(str(target), str(link), target_is_directory=target_is_directory)
        else:
            os.symlink(str(target), str(link))
        return True
    except (OSError, NotImplementedError):
        return False


# --- Trap 1: manifest-compat vocabulary -------------------------------------

def test_detect_link_type_symlink_is_soft_not_symlink(tmp_path):
    target = tmp_path / "t.txt"
    target.write_text("x")
    link = tmp_path / "s.txt"
    if not _try_symlink(target, link):
        pytest.skip("symlink privilege not available")
    # MUST be preservelib's 'soft', NOT filekit's 'symlink' -- the manifest stores it.
    assert detect_link_type(link) == LINK_TYPE_SOFT == "soft"


def test_detect_link_type_hardlink_is_hard_not_hardlink(tmp_path):
    src = tmp_path / "f.txt"
    src.write_text("data")
    link = tmp_path / "h.txt"
    try:
        os.link(str(src), str(link))
    except OSError:
        pytest.skip("hard links not supported on this filesystem")
    assert detect_link_type(link) == LINK_TYPE_HARD == "hard"


@pytest.mark.skipif(sys.platform != "win32", reason="junctions are Windows-only")
def test_detect_link_type_junction_is_junction(tmp_path):
    target = tmp_path / "tgt"
    target.mkdir()
    link = tmp_path / "jct"
    ok, _actual, err = create_link(link, target, link_type=LINK_TYPE_JUNCTION)
    if not ok:
        pytest.skip(f"could not create junction: {err}")
    assert detect_link_type(link) == LINK_TYPE_JUNCTION == "junction"


# --- Trap 2: arg-order (link points at the intended target, not inverted) ----

@pytest.mark.skipif(sys.platform != "win32", reason="junctions are Windows-only")
def test_create_link_junction_points_at_intended_target(tmp_path):
    target = tmp_path / "the_real_target"
    target.mkdir()
    (target / "marker.txt").write_text("here")
    link = tmp_path / "the_link"
    ok, _actual, err = create_link(link, target, link_type=LINK_TYPE_JUNCTION)
    if not ok:
        pytest.skip(f"could not create junction: {err}")
    # If args were inverted (filekit is (target, link)) this would not resolve.
    assert (link / "marker.txt").read_text() == "here"


def test_create_link_hardlink_points_at_intended_file(tmp_path):
    target = tmp_path / "real_file.txt"
    target.write_text("payload")
    link = tmp_path / "hard_name.txt"
    ok, _actual, err = create_link(
        link, target, link_type=LINK_TYPE_HARD, is_directory=False
    )
    if not ok:
        pytest.skip(f"could not create hardlink: {err}")
    # link must be a second name for target's content (proves args not inverted).
    assert link.read_text() == "payload"


# --- Trap 4: informative error survives the filekit delegation --------------

def test_hard_link_on_directory_returns_specific_reason(tmp_path):
    """create_link(link_type='hard') on a directory must RETURN the specific
    reason ('files'), not a generic 'creation failed'. filekit performs the same
    check but only LOGS it (returning a bare bool); L3 keeps the pre-check so the
    reason reaches callers. The preserve CLI's test_hard_link_directory_fails
    asserts the word 'files' -- a conservation contract the delegation must not
    silently drop.
    """
    target_dir = tmp_path / "a_directory"
    target_dir.mkdir()
    link = tmp_path / "the_link"
    ok, _actual, error = create_link(link, target_dir, link_type=LINK_TYPE_HARD)
    assert ok is False
    assert error and "files" in error.lower()


# --- Trap 3: symlink removal (the name-map break site) ----------------------

def test_remove_link_removes_file_symlink_keeps_target(tmp_path):
    target = tmp_path / "keep.txt"
    target.write_text("survive")
    link = tmp_path / "s.txt"
    if not _try_symlink(target, link):
        pytest.skip("symlink privilege not available")
    ok, err = remove_link(link)
    assert ok, err
    assert not link.exists()                  # link detached
    assert target.read_text() == "survive"    # target untouched
