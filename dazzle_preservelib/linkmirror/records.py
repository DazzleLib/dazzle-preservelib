"""Link inventory records shared by all linkmirror scanner backends.

A scanner backend (directory walk, MFT enumeration, ...) reduces a source tree
to a list of ``LinkRecord`` entries -- one per NTFS link object (symlink,
junction, other reparse point) plus one per name participating in a hardlink
group. The mirror engine consumes these records; it never re-walks the tree.

Fidelity contract: ``target`` is the RAW target string exactly as stored in
the source reparse point (relative targets unresolved, broken targets kept,
``\\\\?\\`` prefixes preserved if the source stored them). Nothing in this
module resolves, validates, or rewrites targets -- that is policy, applied
later by the plan/apply layer.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import List, Optional

# Record kinds
KIND_SYMLINK = "symlink"
KIND_JUNCTION = "junction"
KIND_HARDLINK = "hardlink"
KIND_OTHER_REPARSE = "other_reparse"  # OneDrive/WOF/appexec/... inventory-only

# Well-known reparse tags (subset; inventory may carry others verbatim)
IO_REPARSE_TAG_SYMLINK = 0xA000000C
IO_REPARSE_TAG_MOUNT_POINT = 0xA0000003


@dataclass(frozen=True)
class LinkRecord:
    """One link object found under a scan root.

    ``rel_path`` is relative to the scan root using OS separators, no leading
    separator. For hardlinks, one record exists per NAME in the group; records
    sharing ``group_id`` (the NTFS file index / file reference number) form
    one group and ``target`` is ''.
    """

    kind: str
    rel_path: str
    target: str
    is_dir: bool
    reparse_tag: int = 0
    created_ns: int = 0
    modified_ns: int = 0
    accessed_ns: int = 0
    group_id: int = 0
    nlink: int = 1

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "LinkRecord":
        return cls(**d)


@dataclass
class LinkManifest:
    """The result of scanning one root with one backend."""

    root: str
    backend: str  # 'walk' | 'mft'
    records: List[LinkRecord] = field(default_factory=list)
    #: directories the scanner could not enter (recorded, not fatal)
    errors: List[str] = field(default_factory=list)
    #: entries scanned (regular dirs+files, for reporting only)
    entries_scanned: int = 0

    def by_kind(self, kind: str) -> List[LinkRecord]:
        return [r for r in self.records if r.kind == kind]

    def hardlink_groups(self) -> dict:
        groups: dict = {}
        for r in self.records:
            if r.kind == KIND_HARDLINK:
                groups.setdefault(r.group_id, []).append(r)
        return groups

    def to_dict(self) -> dict:
        return {
            "root": self.root,
            "backend": self.backend,
            "entries_scanned": self.entries_scanned,
            "errors": list(self.errors),
            "records": [r.to_dict() for r in self.records],
        }

    @classmethod
    def from_dict(cls, d: dict) -> "LinkManifest":
        m = cls(
            root=d["root"],
            backend=d.get("backend", ""),
            entries_scanned=d.get("entries_scanned", 0),
            errors=list(d.get("errors", [])),
        )
        m.records = [LinkRecord.from_dict(r) for r in d.get("records", [])]
        return m
