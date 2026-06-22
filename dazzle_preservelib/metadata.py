"""dazzle_preservelib.metadata -- re-export shim delegating to dazzle_filekit.metadata.

This module used to contain a full ~665-line standalone implementation of file
metadata collection and application (timestamps, ctime restoration, Windows
attributes + SDDL ACL round-trip, Unix uid/gid + xattrs). As of the P3
extraction it delegates to ``dazzle_filekit.metadata`` -- the canonical L1 home
for these primitives. filekit's implementation is a verified SUPERSET of the
original: it was ported FROM this preservelib code, then gained
``restore_windows_creation_time`` / ``is_win32_available`` and the Unix xattr
helpers.

Dependency direction: ``dazzle_preservelib -> dazzle_filekit`` (one-way, never
the reverse). This removes the metadata duplication the stack's V6 violation
flagged (three drifting copies); the safedel embed already ran this exact
delegation in production. Behavior is preserved (the CLI COPY/VERIFY round-trip
+ the vendored verification tests are the regression gate).
"""

from dazzle_filekit.metadata import (
    # Public API -- the surface preservelib exposed.
    collect_file_metadata,
    apply_file_metadata,
    compare_metadata,
    get_metadata_summary,
    metadata_to_json,
    collect_timestamp_info,
    apply_timestamp_strategy,
    # Additions filekit made when it absorbed this code (available here too now).
    restore_windows_creation_time,
    is_win32_available,
    # Private helpers -- re-exported because consumers historically imported
    # these directly (conservation; matches the safedel shim).
    _collect_windows_metadata,
    _apply_windows_metadata,
    _apply_unix_metadata,
    _collect_unix_xattrs,
    _apply_unix_xattrs,
)

__all__ = [
    "collect_file_metadata",
    "apply_file_metadata",
    "compare_metadata",
    "get_metadata_summary",
    "metadata_to_json",
    "collect_timestamp_info",
    "apply_timestamp_strategy",
    "restore_windows_creation_time",
    "is_win32_available",
]
