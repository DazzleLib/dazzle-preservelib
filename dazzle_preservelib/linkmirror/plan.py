"""Diff a source LinkManifest against a mirrored destination tree and apply
the missing links.

Design invariants (see the 2026-07-20 linkmirror DWP):

- ADDITIVE ONLY by default: the only writes are new link nodes, their own
  timestamps, and restoring the timestamps of parent directories dirtied by
  link creation. Existing files/links are NEVER modified or replaced; any
  mismatch is reported as a conflict and skipped.
- VERBATIM by default: targets are recreated exactly as ``os.readlink``
  returned them from the source (relative unresolved, broken unrepaired,
  ``\\\\?\\`` prefixes kept). Target rewriting is an explicit, pluggable
  policy (:func:`make_prefix_rewrite_policy`).
- IDEMPOTENT: re-running produces a plan of all-satisfied and applies as a
  no-op.
- Hardlink reconciliation is the ONE destructive capability (replacing a
  duplicated file with a hardlink to its group canonical). It is opt-in
  (``hardlink_mode='recreate'``), refuses any group whose duplicate content
  hashes differ, and swaps atomically (temp link + ``os.replace``).
"""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional

from dazzle_filekit import apply_file_metadata
from dazzle_filekit.links import create_junction_raw
from dazzle_filekit.operations import create_symlink

from .records import (
    KIND_HARDLINK,
    KIND_JUNCTION,
    KIND_OTHER_REPARSE,
    KIND_SYMLINK,
    LinkManifest,
    LinkRecord,
)
from .scan import extended_path

_IS_WINDOWS = os.name == "nt"
_FILE_ATTRIBUTE_REPARSE_POINT = 0x400

# Plan actions
ACTION_CREATE = "create"
ACTION_SATISFIED = "satisfied"
ACTION_CONFLICT = "conflict"
ACTION_PARENT_MISSING = "parent_missing"
ACTION_EXCLUDED = "excluded"
ACTION_HARDLINK_REPORT = "hardlink_report"
ACTION_HARDLINK_LINK = "hardlink_link"

TargetPolicy = Callable[[str], str]


def verbatim_policy(target: str) -> str:
    """The default: recreate the source's raw target bytes unchanged."""
    return target


def make_prefix_rewrite_policy(old_prefix: str, new_prefix: str) -> TargetPolicy:
    """Rewrite absolute targets beginning with ``old_prefix`` to
    ``new_prefix`` (e.g. ``D:\\`` -> ``B:\\``), transparently handling the
    ``\\\\?\\`` / ``\\??\\`` forms ``os.readlink`` renders for absolute
    targets. Relative targets and non-matching prefixes pass through
    verbatim. Matching is case-insensitive (Windows path semantics)."""

    def _policy(target: str) -> str:
        for nt_prefix in ("\\\\?\\", "\\??\\"):
            if target.startswith(nt_prefix):
                body = target[len(nt_prefix):]
                if body.lower().startswith(old_prefix.lower()):
                    return nt_prefix + new_prefix + body[len(old_prefix):]
                return target
        if target.lower().startswith(old_prefix.lower()):
            return new_prefix + target[len(old_prefix):]
        return target

    return _policy


@dataclass
class PlanItem:
    action: str
    record: LinkRecord
    dest_path: str  # absolute, unprefixed
    detail: str = ""
    #: policy-translated target for CREATE/SATISFIED/CONFLICT items; the
    #: canonical member's ABSOLUTE dest path for HARDLINK_LINK items.
    target: str = ""


@dataclass
class MirrorPlan:
    source_root: str
    dest_root: str
    items: List[PlanItem] = field(default_factory=list)
    hardlink_mode: str = "report"

    def by_action(self, action: str) -> List[PlanItem]:
        return [i for i in self.items if i.action == action]

    def counts(self) -> Dict[str, int]:
        c: Dict[str, int] = {}
        for i in self.items:
            c[i.action] = c.get(i.action, 0) + 1
        return c


@dataclass
class MirrorResult:
    dry_run: bool
    created: List[str] = field(default_factory=list)
    hardlinked: List[str] = field(default_factory=list)
    skipped_satisfied: int = 0
    conflicts: List[str] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)
    parents_restored: List[str] = field(default_factory=list)


def _lstat_or_none(path: str) -> Optional[os.stat_result]:
    try:
        return os.lstat(extended_path(path))
    except OSError:
        return None


def _is_link_st(st: os.stat_result) -> bool:
    if _IS_WINDOWS:
        return bool(
            getattr(st, "st_file_attributes", 0) & _FILE_ATTRIBUTE_REPARSE_POINT
        )
    import stat as stat_mod
    return stat_mod.S_ISLNK(st.st_mode)


def build_plan(
    manifest: LinkManifest,
    dest_root: str,
    target_policy: Optional[TargetPolicy] = None,
    hardlink_mode: str = "report",
) -> MirrorPlan:
    """Diff ``manifest`` (scanned from the source) against ``dest_root``."""
    if hardlink_mode not in ("report", "recreate"):
        raise ValueError(f"unknown hardlink_mode: {hardlink_mode!r}")
    policy = target_policy or verbatim_policy
    dest_root = os.path.abspath(dest_root)
    plan = MirrorPlan(
        source_root=manifest.root, dest_root=dest_root,
        hardlink_mode=hardlink_mode,
    )

    for rec in manifest.records:
        if rec.kind == KIND_HARDLINK:
            continue  # grouped below
        dest_path = os.path.join(dest_root, rec.rel_path)
        expected_target = policy(rec.target)
        st = _lstat_or_none(dest_path)

        if rec.kind == KIND_OTHER_REPARSE:
            plan.items.append(PlanItem(
                ACTION_EXCLUDED, rec, dest_path,
                f"reparse tag 0x{rec.reparse_tag:08X} is not mirrored",
            ))
            continue

        if st is None:
            parent = os.path.dirname(dest_path)
            if not os.path.isdir(extended_path(parent)):
                plan.items.append(PlanItem(
                    ACTION_PARENT_MISSING, rec, dest_path,
                    f"destination parent missing: {parent}",
                    target=expected_target,
                ))
            else:
                plan.items.append(PlanItem(
                    ACTION_CREATE, rec, dest_path, target=expected_target,
                ))
            continue

        if not _is_link_st(st):
            plan.items.append(PlanItem(
                ACTION_CONFLICT, rec, dest_path,
                "destination exists and is not a link",
            ))
            continue

        try:
            dest_target = os.readlink(extended_path(dest_path))
        except OSError as e:
            plan.items.append(PlanItem(
                ACTION_CONFLICT, rec, dest_path, f"readlink failed: {e}"
            ))
            continue
        if dest_target == expected_target:
            plan.items.append(PlanItem(
                ACTION_SATISFIED, rec, dest_path, target=expected_target,
            ))
        else:
            plan.items.append(PlanItem(
                ACTION_CONFLICT, rec, dest_path,
                f"existing link target {dest_target!r} != expected "
                f"{expected_target!r}",
                target=expected_target,
            ))

    # Hardlink groups: one report item per group; in recreate mode also one
    # link item per non-canonical name whose destination file exists.
    for group_id, members in manifest.hardlink_groups().items():
        names = sorted(m.rel_path for m in members)
        rec0 = members[0]
        detail = f"group {group_id}: {len(names)} names: " + "; ".join(names)
        plan.items.append(PlanItem(
            ACTION_HARDLINK_REPORT, rec0,
            os.path.join(dest_root, names[0]), detail,
        ))
        if hardlink_mode == "recreate":
            canonical = os.path.join(dest_root, names[0])
            for name in names[1:]:
                dest_path = os.path.join(dest_root, name)
                rec = next(m for m in members if m.rel_path == name)
                plan.items.append(PlanItem(
                    ACTION_HARDLINK_LINK, rec, dest_path,
                    f"link to canonical {names[0]}",
                    target=canonical,
                ))
    return plan


def _sha256(path: str, chunk: int = 1024 * 1024) -> str:
    h = hashlib.sha256()
    with open(extended_path(path), "rb") as f:
        while True:
            b = f.read(chunk)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def _timestamps_dict(rec: LinkRecord) -> dict:
    # *_ns keys drive filekit's exact 100ns FILETIME path; the float keys are
    # the documented fallback if that path is unavailable.
    return {
        "created": rec.created_ns / 1e9,
        "modified": rec.modified_ns / 1e9,
        "accessed": rec.accessed_ns / 1e9,
        "created_ns": rec.created_ns,
        "modified_ns": rec.modified_ns,
        "accessed_ns": rec.accessed_ns,
    }


def apply_plan(plan: MirrorPlan, dry_run: bool = True) -> MirrorResult:
    """Execute a plan. ``dry_run=True`` (default) reports without writing.

    Parent-directory timestamps: every directory that receives a new link
    node has its (creation, modified, accessed) times snapshotted before the
    first write and restored after the last, so the mirror stays
    byte-honest about "nothing but the links changed".
    """
    result = MirrorResult(dry_run=dry_run)
    result.skipped_satisfied = len(plan.by_action(ACTION_SATISFIED))
    result.conflicts = [
        f"{i.dest_path}: {i.detail}"
        for i in plan.items
        if i.action in (ACTION_CONFLICT, ACTION_PARENT_MISSING)
    ]

    creates = plan.by_action(ACTION_CREATE)
    hardlinks = (
        plan.by_action(ACTION_HARDLINK_LINK)
        if plan.hardlink_mode == "recreate" else []
    )

    if dry_run:
        result.created = [i.dest_path for i in creates]
        result.hardlinked = [i.dest_path for i in hardlinks]
        return result

    # Snapshot parent-dir (accessed, modified) before any write. Creating a
    # child updates the parent's mtime but never its creation time, so an
    # exact os.utime(ns=...) restore afterwards is sufficient.
    parent_snapshots: Dict[str, tuple] = {}
    for item in creates + hardlinks:
        parent = os.path.dirname(item.dest_path)
        if parent not in parent_snapshots:
            st = _lstat_or_none(parent)
            if st is not None:
                parent_snapshots[parent] = (st.st_atime_ns, st.st_mtime_ns)

    for item in creates:
        rec = item.record
        target = item.target  # policy-translated at build time
        try:
            if rec.kind == KIND_SYMLINK:
                ok = create_symlink(
                    target, item.dest_path,
                    target_is_directory=rec.is_dir,
                )
            elif rec.kind == KIND_JUNCTION:
                ok = create_junction_raw(target, item.dest_path)
            else:
                result.errors.append(
                    f"{item.dest_path}: unexpected kind {rec.kind}"
                )
                continue
            if not ok:
                result.errors.append(f"{item.dest_path}: creation failed")
                continue
            # Fidelity gate: the stored target must round-trip.
            stored = os.readlink(extended_path(item.dest_path))
            if stored != target:
                result.errors.append(
                    f"{item.dest_path}: fidelity check failed: stored "
                    f"{stored!r} != expected {target!r}"
                )
                continue
            if not apply_file_metadata(
                item.dest_path, {"timestamps": _timestamps_dict(rec)}
            ):
                result.errors.append(
                    f"{item.dest_path}: link timestamps not fully applied"
                )
                continue
            result.created.append(item.dest_path)
        except OSError as e:
            result.errors.append(f"{item.dest_path}: {e}")

    for item in hardlinks:
        canonical = item.target  # canonical member's dest path, set at build
        try:
            st_c = _lstat_or_none(canonical)
            st_d = _lstat_or_none(item.dest_path)
            if st_c is None or st_d is None:
                result.errors.append(
                    f"{item.dest_path}: hardlink member missing on destination"
                )
                continue
            if st_c.st_ino == st_d.st_ino and st_c.st_dev == st_d.st_dev:
                result.skipped_satisfied += 1
                continue
            if _sha256(canonical) != _sha256(item.dest_path):
                result.errors.append(
                    f"{item.dest_path}: content differs from canonical "
                    f"{canonical}; refusing to replace"
                )
                continue
            tmp = item.dest_path + ".linkmirror-tmp"
            os.link(extended_path(canonical), extended_path(tmp))
            os.replace(extended_path(tmp), extended_path(item.dest_path))
            result.hardlinked.append(item.dest_path)
        except OSError as e:
            result.errors.append(f"{item.dest_path}: {e}")
            try:
                os.unlink(extended_path(item.dest_path + ".linkmirror-tmp"))
            except OSError:
                pass

    # Restore parent-directory timestamps last (exact ns).
    for parent, (atime_ns, mtime_ns) in parent_snapshots.items():
        try:
            os.utime(extended_path(parent), ns=(atime_ns, mtime_ns))
            result.parents_restored.append(parent)
        except OSError as e:
            result.errors.append(f"{parent}: parent timestamp restore failed: {e}")

    return result
