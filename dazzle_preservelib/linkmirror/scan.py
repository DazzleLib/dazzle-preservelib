"""Directory-walk scanner backend (portable, unelevated).

Walks a tree with ``os.scandir`` and reduces it to a :class:`LinkManifest`.
Never descends into a reparse-point directory (junction/dir-symlink loops are
structurally impossible). Records unreadable directories into
``manifest.errors`` and continues.

Windows notes measured during design (2026-07-21):

- ``DirEntry.stat(follow_symlinks=False)`` is served from find-data and
  carries ``st_file_attributes``/``st_reparse_tag`` (reparse classification
  is FREE) but ``st_nlink`` is always 0 -- hardlink detection requires a real
  ``os.stat`` (handle open) per file, so it is a switchable cost
  (``include_hardlinks``).
- Long paths: the walk uses a ``\\\\?\\``-prefixed root internally so >260
  char trees scan correctly; ``rel_path`` values are stored unprefixed.
"""

from __future__ import annotations

import os
from typing import Callable, FrozenSet, Optional

from .records import (
    KIND_HARDLINK,
    KIND_JUNCTION,
    KIND_OTHER_REPARSE,
    KIND_SYMLINK,
    IO_REPARSE_TAG_MOUNT_POINT,
    IO_REPARSE_TAG_SYMLINK,
    LinkManifest,
    LinkRecord,
)

_IS_WINDOWS = os.name == "nt"
_FILE_ATTRIBUTE_REPARSE_POINT = 0x400
_EXT_PREFIX = "\\\\?\\"


def extended_path(path: str) -> str:
    """Return a ``\\\\?\\``-prefixed absolute form for Windows I/O (long-path
    safe). POSIX and already-prefixed paths pass through unchanged."""
    if not _IS_WINDOWS or path.startswith(_EXT_PREFIX):
        return path
    p = os.path.abspath(path)
    if p.startswith("\\\\"):  # UNC -> \\?\UNC\server\share\...
        return _EXT_PREFIX + "UNC" + p[1:]
    return _EXT_PREFIX + p


def unextended_path(path: str) -> str:
    """Strip the ``\\\\?\\`` (or ``\\\\?\\UNC``) prefix for display/storage."""
    if path.startswith(_EXT_PREFIX + "UNC"):
        return "\\" + path[len(_EXT_PREFIX) + 3:]
    if path.startswith(_EXT_PREFIX):
        return path[len(_EXT_PREFIX):]
    return path


def _birthtime_ns(st: os.stat_result) -> int:
    """Best-available creation time. Windows: real birthtime. macOS/BSD:
    st_birthtime when Python exposes the ns variant. Linux: falls back to
    st_ctime_ns, which is inode CHANGE time, not creation -- captured for
    completeness but neither restorable nor compared by verify on POSIX."""
    bt = getattr(st, "st_birthtime_ns", None)
    if bt is not None:
        return bt
    return st.st_ctime_ns


def _classify_reparse(st: os.stat_result) -> str:
    tag = getattr(st, "st_reparse_tag", 0)
    if tag == IO_REPARSE_TAG_SYMLINK:
        return KIND_SYMLINK
    if tag == IO_REPARSE_TAG_MOUNT_POINT:
        return KIND_JUNCTION
    return KIND_OTHER_REPARSE


def walk_scan(
    root: str,
    include_hardlinks: bool = True,
    skip_rel_dirs: FrozenSet[str] = frozenset(),
    progress: Optional[Callable[[int], None]] = None,
) -> LinkManifest:
    """Scan ``root`` for link objects and return a :class:`LinkManifest`.

    Args:
        root: tree to scan (drive root or any directory).
        include_hardlinks: pay one real ``os.stat`` per file to detect
            hardlink groups (``st_nlink > 1``). Off = reparse points only.
        skip_rel_dirs: root-relative directory paths (OS separators,
            case-insensitive on Windows) whose subtrees are pruned.
        progress: optional callback receiving the running entry count
            (called about every 100k entries).
    """
    display_root = os.path.abspath(root)
    walk_root = extended_path(display_root)
    manifest = LinkManifest(root=display_root, backend="walk")

    def _rel(path: str) -> str:
        rel = path[len(walk_root):]
        return rel.lstrip("\\/")

    skip_norm = {s.strip("\\/").lower() for s in skip_rel_dirs}

    stack = [walk_root]
    while stack:
        d = stack.pop()
        try:
            it = os.scandir(d)
        except OSError as e:
            manifest.errors.append(f"scandir {unextended_path(d)}: {e}")
            continue
        with it:
            for entry in it:
                try:
                    st = entry.stat(follow_symlinks=False)
                    manifest.entries_scanned += 1
                    is_reparse = (
                        entry.is_symlink()
                        if not _IS_WINDOWS
                        else bool(
                            getattr(st, "st_file_attributes", 0)
                            & _FILE_ATTRIBUTE_REPARSE_POINT
                        )
                    )
                    if is_reparse:
                        kind = (
                            _classify_reparse(st)
                            if _IS_WINDOWS
                            else KIND_SYMLINK
                        )
                        target = ""
                        if kind in (KIND_SYMLINK, KIND_JUNCTION):
                            try:
                                target = os.readlink(entry.path)
                            except OSError as e:
                                manifest.errors.append(
                                    f"readlink {unextended_path(entry.path)}: {e}"
                                )
                        # Directory-ness of the link NODE comes from its own
                        # FILE_ATTRIBUTE_DIRECTORY bit -- is_dir(follow_symlinks
                        # =False) reports False for directory SYMLINKS, and a
                        # broken link cannot be probed via its target. This bit
                        # is what lets a broken directory symlink be recreated
                        # with the correct kind.
                        if _IS_WINDOWS:
                            node_is_dir = bool(
                                getattr(st, "st_file_attributes", 0) & 0x10
                            )
                        else:
                            node_is_dir = False  # POSIX symlinks are kindless
                        manifest.records.append(LinkRecord(
                            kind=kind,
                            rel_path=_rel(entry.path),
                            target=target,
                            is_dir=node_is_dir,
                            reparse_tag=getattr(st, "st_reparse_tag", 0),
                            created_ns=_birthtime_ns(st),
                            modified_ns=st.st_mtime_ns,
                            accessed_ns=st.st_atime_ns,
                        ))
                        continue  # never descend into reparse dirs
                    if entry.is_dir(follow_symlinks=False):
                        if skip_norm and _rel(entry.path).lower() in skip_norm:
                            continue
                        stack.append(entry.path)
                    else:
                        if include_hardlinks:
                            # find-data st_nlink is 0; a real stat is required
                            st2 = os.stat(entry.path, follow_symlinks=False)
                            if st2.st_nlink > 1:
                                manifest.records.append(LinkRecord(
                                    kind=KIND_HARDLINK,
                                    rel_path=_rel(entry.path),
                                    target="",
                                    is_dir=False,
                                    created_ns=_birthtime_ns(st2),
                                    modified_ns=st2.st_mtime_ns,
                                    accessed_ns=st2.st_atime_ns,
                                    group_id=st2.st_ino,
                                    nlink=st2.st_nlink,
                                ))
                except OSError as e:
                    manifest.errors.append(
                        f"stat {unextended_path(entry.path)}: {e}"
                    )
        if progress and manifest.entries_scanned % 100000 < 64:
            progress(manifest.entries_scanned)
    return manifest
