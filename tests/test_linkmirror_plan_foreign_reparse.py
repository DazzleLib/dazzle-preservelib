"""build_plan reports a foreign reparse point at a destination as a conflict (0.9.2).

The plan decided "the destination is a link" from the reparse bit alone, then
read its target with os.readlink catching only OSError. A WSL symlink
(0xA000001D), an app alias or a cloud placeholder makes os.readlink raise
ValueError, which crashed the whole plan. verify_mirror already checked the
reparse TAG against the record's kind first; build_plan now does the same, and
also tolerates the ValueError.

Found 2026-11-23 by a read-only review (another session) after the same fault
was fixed in dazzlecmd-lib. The foreign reparse point is simulated by patching
the destination's lstat, so no WSL link is needed; nothing outside tmp_path is
named.
"""
import os
from types import SimpleNamespace

import pytest

from dazzle_preservelib.linkmirror import plan as plan_mod
from dazzle_preservelib.linkmirror.plan import ACTION_CONFLICT, build_plan
from dazzle_preservelib.linkmirror.records import (
    KIND_SYMLINK, LinkManifest, LinkRecord,
)

TAG_LX_SYMLINK = 0xA000001D


def _manifest(root):
    rec = LinkRecord(rel_path="link", kind=KIND_SYMLINK, target="real.txt", is_dir=False)
    return LinkManifest(root=str(root), backend="walk", records=[rec])


def _foreign_at(monkeypatch, path, tag):
    real = plan_mod._lstat_or_none
    want = os.path.normcase(str(path))

    def fake(p):
        st = real(p)
        if st is not None and os.path.normcase(str(p)) == want:
            return SimpleNamespace(st_mode=st.st_mode, st_file_attributes=0x400,
                                   st_reparse_tag=tag)
        return st
    monkeypatch.setattr(plan_mod, "_lstat_or_none", fake)


def test_a_wsl_link_at_the_destination_is_a_conflict_not_a_crash(tmp_path, monkeypatch):
    dst = tmp_path / "dst"
    dst.mkdir()
    (dst / "link").write_text("stands in for a WSL symlink")
    _foreign_at(monkeypatch, dst / "link", TAG_LX_SYMLINK)
    monkeypatch.setattr(plan_mod, "_IS_WINDOWS", True)

    plan = build_plan(_manifest(tmp_path / "src"), str(dst))

    [item] = plan.by_action(ACTION_CONFLICT)
    assert "0xA000001D" in item.detail


def test_a_readlink_valueerror_is_a_conflict_not_a_crash(tmp_path, monkeypatch):
    """Belt and braces: a tag that matches but a target that cannot be read."""
    dst = tmp_path / "dst"
    dst.mkdir()
    (dst / "link").write_text("x")
    _foreign_at(monkeypatch, dst / "link", 0xA000000C)     # says "symlink"
    monkeypatch.setattr(plan_mod, "_IS_WINDOWS", True)

    def readlink(p, *a, **k):
        raise ValueError("not a symbolic link")
    monkeypatch.setattr(plan_mod.os, "readlink", readlink)

    plan = build_plan(_manifest(tmp_path / "src"), str(dst))

    [item] = plan.by_action(ACTION_CONFLICT)
    assert "readlink failed" in item.detail
