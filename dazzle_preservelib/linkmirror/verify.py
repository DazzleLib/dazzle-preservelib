"""Source/destination link parity verification.

Given a source :class:`LinkManifest` and a destination root, prove (or
disprove) that every source link exists on the destination with the same
kind, the policy-expected target bytes, and link-own timestamps within
tolerance.

Honesty note carried from the migration postmortem: the NTFS *change* time
(the POSIX-style ctime) is not settable by any user-mode tool and is NOT
compared here. What IS compared -- and what Windows tooling means by
"ctime" -- is CREATION time. Restored link timestamps travel through
float-seconds / pywintypes (microsecond resolution), so the default
tolerance is 10 microseconds; untouched objects should match far tighter.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import List, Optional

from .plan import TargetPolicy, verbatim_policy, _is_link_st, _lstat_or_none
from .records import (
    KIND_HARDLINK,
    KIND_JUNCTION,
    KIND_OTHER_REPARSE,
    KIND_SYMLINK,
    IO_REPARSE_TAG_MOUNT_POINT,
    IO_REPARSE_TAG_SYMLINK,
    LinkManifest,
)
from .scan import extended_path, _birthtime_ns

DEFAULT_TOLERANCE_NS = 10_000  # 10 microseconds


@dataclass
class VerifyIssue:
    rel_path: str
    kind: str
    problem: str  # 'missing' | 'kind' | 'target' | 'timestamps' | 'hardlink'
    detail: str = ""


@dataclass
class VerifyReport:
    source_root: str
    dest_root: str
    checked: int = 0
    satisfied: int = 0
    excluded: int = 0
    issues: List[VerifyIssue] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.issues


def _expected_kind_matches(rec_kind: str, tag: int, is_windows: bool) -> bool:
    if not is_windows:
        return rec_kind == KIND_SYMLINK
    if rec_kind == KIND_SYMLINK:
        return tag == IO_REPARSE_TAG_SYMLINK
    if rec_kind == KIND_JUNCTION:
        return tag == IO_REPARSE_TAG_MOUNT_POINT
    return False


def verify_mirror(
    manifest: LinkManifest,
    dest_root: str,
    target_policy: Optional[TargetPolicy] = None,
    timestamp_tolerance_ns: int = DEFAULT_TOLERANCE_NS,
    check_timestamps: bool = True,
) -> VerifyReport:
    policy = target_policy or verbatim_policy
    dest_root = os.path.abspath(dest_root)
    is_windows = os.name == "nt"
    report = VerifyReport(source_root=manifest.root, dest_root=dest_root)
    report.notes.append(
        "NTFS change-time (POSIX ctime) is not user-settable and is not "
        "compared; 'created' below is NTFS creation time."
    )
    if not is_windows:
        report.notes.append(
            "POSIX: creation time is not settable (and Linux st_ctime is "
            "inode change time), so only 'modified' is compared here."
        )

    groups: dict = {}
    for rec in manifest.records:
        if rec.kind == KIND_OTHER_REPARSE:
            report.excluded += 1
            continue
        if rec.kind == KIND_HARDLINK:
            groups.setdefault(rec.group_id, []).append(rec)
            continue

        report.checked += 1
        dest_path = os.path.join(dest_root, rec.rel_path)
        st = _lstat_or_none(dest_path)
        if st is None:
            report.issues.append(VerifyIssue(
                rec.rel_path, rec.kind, "missing", "no destination entry"
            ))
            continue
        if not _is_link_st(st):
            report.issues.append(VerifyIssue(
                rec.rel_path, rec.kind, "kind",
                "destination is not a link node",
            ))
            continue
        tag = getattr(st, "st_reparse_tag", 0)
        if not _expected_kind_matches(rec.kind, tag, is_windows):
            report.issues.append(VerifyIssue(
                rec.rel_path, rec.kind, "kind",
                f"destination reparse tag 0x{tag:08X}",
            ))
            continue
        expected = policy(rec.target)
        try:
            actual = os.readlink(extended_path(dest_path))
        except OSError as e:
            report.issues.append(VerifyIssue(
                rec.rel_path, rec.kind, "target", f"readlink failed: {e}"
            ))
            continue
        if actual != expected:
            report.issues.append(VerifyIssue(
                rec.rel_path, rec.kind, "target",
                f"stored {actual!r} != expected {expected!r}",
            ))
            continue
        if check_timestamps:
            deltas = {
                "modified": abs(st.st_mtime_ns - rec.modified_ns),
            }
            if is_windows:
                # Creation time is capturable AND restorable only on Windows.
                # POSIX has no settable birthtime, and Linux's st_ctime is
                # inode CHANGE time -- comparing it would falsely flag every
                # recreated link.
                deltas["created"] = abs(_birthtime_ns(st) - rec.created_ns)
            bad = {k: v for k, v in deltas.items()
                   if v > timestamp_tolerance_ns}
            if bad:
                report.issues.append(VerifyIssue(
                    rec.rel_path, rec.kind, "timestamps",
                    "; ".join(f"{k} off by {v}ns" for k, v in bad.items()),
                ))
                continue
        report.satisfied += 1

    # Hardlink groups: verified only for topology (shared identity on dest).
    for group_id, members in groups.items():
        report.checked += 1
        inos = set()
        missing = []
        for rec in members:
            st = _lstat_or_none(os.path.join(dest_root, rec.rel_path))
            if st is None:
                missing.append(rec.rel_path)
            else:
                inos.add((st.st_dev, st.st_ino))
        if missing:
            report.issues.append(VerifyIssue(
                members[0].rel_path, KIND_HARDLINK, "hardlink",
                f"group {group_id}: missing on destination: "
                + ", ".join(sorted(missing)),
            ))
        elif len(inos) > 1:
            report.issues.append(VerifyIssue(
                members[0].rel_path, KIND_HARDLINK, "hardlink",
                f"group {group_id}: destination names are "
                f"{len(inos)} independent files (not hardlinked)",
            ))
        else:
            report.satisfied += 1
    return report
