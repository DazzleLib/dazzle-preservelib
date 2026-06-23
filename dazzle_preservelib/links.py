"""
Link operations for preserve.

This module provides cross-platform functionality for creating, detecting,
and managing filesystem links (junctions, symlinks, hard links) as part
of the preserve MOVE operation with link creation.

Supported link types:
- junction: Windows NTFS directory junction (no admin required)
- soft: Symbolic link (cross-platform, may need admin on Windows)
- hard: Hard link (cross-platform, same filesystem only, files only)
- auto: Platform-appropriate default (junction on Windows, soft elsewhere)

Link handling modes (for MOVE operations with existing links in source):
- block: (default) Block operation if cycle-creating links found
- skip: Skip links, only move non-link content
- unlink: Remove source links that point to destination (consolidation)
- recreate: Recreate links at destination with adjusted targets (Phase 2)
- ask: Interactive prompt for each link (Phase 2)
"""

import os
import logging
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

# L1 delegation: the intrinsic link MECHANICS live in dazzle-filekit. preservelib
# keeps the L3 POLICY (LinkHandlingMode, the relational LinkInfo, decide_link_action,
# create_link orchestration) and thin wrappers that preserve preservelib's INTERFACE
# -- the 'soft'/'hard' vocabulary the on-disk manifest stores, and the (bool, error)
# return shapes -- over filekit's primitives (which return 'symlink'/'hardlink' + bool).
# See private/claude/2026-06-22__links-delegation-conservation-audit.md.
from dazzle_filekit.links import (
    detect_link_type as _fk_detect_link_type,
    read_link_target as _fk_read_link_target,
    create_junction as _fk_create_junction,
    create_hardlink as _fk_create_hardlink,
    remove_link as _fk_remove_link,
    LINK_SYMLINK as _FK_SYMLINK,
    LINK_HARDLINK as _FK_HARDLINK,
)
from dazzle_filekit.utils.validation import is_junction as _fk_is_junction
from dazzle_filekit.operations import create_symlink as _fk_create_symlink

logger = logging.getLogger(__name__)

# Link type constants
LINK_TYPE_JUNCTION = 'junction'
LINK_TYPE_SOFT = 'soft'
LINK_TYPE_HARD = 'hard'
LINK_TYPE_AUTO = 'auto'
LINK_TYPE_DAZZLE = 'dazzle'  # Future: .dazzlelink metadata file

VALID_LINK_TYPES = [LINK_TYPE_JUNCTION, LINK_TYPE_SOFT, LINK_TYPE_HARD, LINK_TYPE_AUTO, LINK_TYPE_DAZZLE]


# ==============================================================================
# Link Handling Enums and Data Structures
# ==============================================================================


class LinkHandlingMode(Enum):
    """
    How to handle links discovered in the source tree during MOVE operations.

    Used with the --link-handling CLI flag to control behavior when links
    are found that would otherwise block the operation due to cycle detection.
    """
    BLOCK = "block"        # Default: block if cycle-creating links found
    SKIP = "skip"          # Skip links, only move non-link content
    UNLINK = "unlink"      # Remove source links that point to destination
    RECREATE = "recreate"  # Recreate links at destination (Phase 2)
    ASK = "ask"            # Interactive prompt for each link (Phase 2)

    @classmethod
    def from_string(cls, value: str) -> "LinkHandlingMode":
        """Convert string to LinkHandlingMode, with helpful error message."""
        try:
            return cls(value.lower())
        except ValueError:
            valid = ", ".join(m.value for m in cls)
            raise ValueError(f"Invalid link handling mode: '{value}'. Valid modes: {valid}")


class LinkAction(Enum):
    """
    Action to take for a specific link during traversal.

    This is the per-link decision made based on LinkHandlingMode and link analysis.
    """
    FOLLOW = "follow"      # Follow the link (descend into it during traversal)
    SKIP = "skip"          # Skip this link entirely
    UNLINK = "unlink"      # Remove this link from source (consolidation)
    RECREATE = "recreate"  # Recreate this link at destination
    BLOCK = "block"        # Block the entire operation due to this link


@dataclass
class LinkInfo:
    """
    Information about a discovered link in the source tree.

    Captures all details needed to make handling decisions and report to user.
    """
    # Path to the link itself
    link_path: Path

    # Link type (junction, soft, hard)
    link_type: Optional[str] = None

    # Raw target (as stored in the link)
    raw_target: Optional[str] = None

    # Resolved target (absolute path after resolution)
    resolved_target: Optional[Path] = None

    # Relationship to destination
    target_is_destination: bool = False  # Target == destination
    target_inside_destination: bool = False  # Target is child of destination
    target_contains_destination: bool = False  # Destination is child of target

    # Link health
    is_broken: bool = False  # Target doesn't exist
    is_circular: bool = False  # Part of a circular link chain

    # Decision tracking
    action: Optional[LinkAction] = None
    action_result: Optional[str] = None  # Success/error message

    # Additional context
    metadata: Dict[str, Any] = field(default_factory=dict)

    def creates_cycle_with(self, dest_path: Path) -> bool:
        """Check if this link would create a cycle with the given destination."""
        return (
            self.target_is_destination or
            self.target_inside_destination or
            self.target_contains_destination
        )

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization and reporting."""
        return {
            "link_path": str(self.link_path),
            "link_type": self.link_type,
            "raw_target": self.raw_target,
            "resolved_target": str(self.resolved_target) if self.resolved_target else None,
            "target_is_destination": self.target_is_destination,
            "target_inside_destination": self.target_inside_destination,
            "target_contains_destination": self.target_contains_destination,
            "is_broken": self.is_broken,
            "is_circular": self.is_circular,
            "action": self.action.value if self.action else None,
            "action_result": self.action_result,
        }


def analyze_link(
    link_path: Union[str, Path],
    dest_path: Union[str, Path]
) -> LinkInfo:
    """
    Analyze a link and its relationship to the destination.

    Args:
        link_path: Path to the link
        dest_path: Destination path for the MOVE operation

    Returns:
        LinkInfo with all analysis results
    """
    link_path = Path(link_path)
    dest_path = Path(dest_path).resolve()

    info = LinkInfo(link_path=link_path)

    # Detect link type
    info.link_type = detect_link_type(link_path)

    # Get raw target
    info.raw_target = get_link_target(link_path)

    if info.raw_target is None:
        info.is_broken = True
        return info

    # Resolve target
    try:
        # Handle relative targets
        if not Path(info.raw_target).is_absolute():
            resolved = (link_path.parent / info.raw_target).resolve()
        else:
            resolved = Path(info.raw_target).resolve()

        info.resolved_target = resolved

        # Check if target exists
        if not resolved.exists():
            info.is_broken = True

    except Exception as e:
        logger.debug(f"Error resolving link target {info.raw_target}: {e}")
        info.is_broken = True
        return info

    # Analyze relationship to destination
    if info.resolved_target:
        try:
            # Target IS destination
            if os.path.samefile(info.resolved_target, dest_path):
                info.target_is_destination = True

            # Target is inside destination
            elif info.resolved_target.is_relative_to(dest_path):
                info.target_inside_destination = True

            # Destination is inside target
            elif dest_path.is_relative_to(info.resolved_target):
                info.target_contains_destination = True

        except (OSError, ValueError):
            # samefile can fail, is_relative_to can raise ValueError
            pass

    return info


def decide_link_action(
    link_info: LinkInfo,
    mode: LinkHandlingMode,
    dest_path: Path
) -> LinkAction:
    """
    Decide what action to take for a link based on handling mode.

    Args:
        link_info: Analyzed link information
        mode: Link handling mode from CLI
        dest_path: Destination path

    Returns:
        LinkAction to take for this link
    """
    creates_cycle = link_info.creates_cycle_with(dest_path)

    if mode == LinkHandlingMode.BLOCK:
        if creates_cycle:
            return LinkAction.BLOCK
        else:
            return LinkAction.FOLLOW

    elif mode == LinkHandlingMode.SKIP:
        # Always skip links in skip mode
        return LinkAction.SKIP

    elif mode == LinkHandlingMode.UNLINK:
        if creates_cycle:
            # Unlink links that point to/inside destination (consolidation)
            return LinkAction.UNLINK
        else:
            # Links pointing elsewhere - skip them
            return LinkAction.SKIP

    elif mode == LinkHandlingMode.RECREATE:
        # Phase 2: recreate all links at destination
        raise NotImplementedError(
            "Link handling mode 'recreate' is not yet implemented. "
            "Use 'skip' or 'unlink' for now. See issue #48 for progress."
        )

    elif mode == LinkHandlingMode.ASK:
        # Phase 2: interactive prompt
        raise NotImplementedError(
            "Link handling mode 'ask' is not yet implemented. "
            "Use 'skip' or 'unlink' for now. See issue #48 for progress."
        )

    # Fallback - shouldn't reach here
    return LinkAction.BLOCK


def is_link(path: Union[str, Path]) -> bool:
    """
    Check if a path is any type of link (junction, symlink, etc.).

    Args:
        path: Path to check

    Returns:
        True if path is a link of any type
    """
    path = Path(path)

    if not path.exists() and not path.is_symlink():
        return False

    # Check for symlink first (works cross-platform)
    if path.is_symlink():
        return True

    # On Windows, check for junction (reparse point)
    if os.name == 'nt':
        return is_junction(path)

    return False


def is_junction(path: Union[str, Path]) -> bool:
    """
    Check if a path is a Windows NTFS junction.

    Delegates to dazzle_filekit (L1). filekit reads the reparse TAG via
    DeviceIoControl, so it correctly distinguishes a junction from a directory
    symlink -- preservelib's old attribute-only check could not (V7 fix).
    """
    return _fk_is_junction(path)


def is_symlink(path: Union[str, Path]) -> bool:
    """
    Check if a path is a symbolic link.

    Args:
        path: Path to check

    Returns:
        True if path is a symlink
    """
    return Path(path).is_symlink()


def detect_link_type(path: Union[str, Path]) -> Optional[str]:
    """
    Detect what type of link a path is, in preservelib's vocabulary.

    The detection LOGIC (including the reparse-tag junction fix) comes from
    dazzle_filekit; we translate filekit's 'symlink'/'hardlink' back to
    preservelib's 'soft'/'hard' so the on-disk manifest format and every
    downstream link-type comparison (create_link, remove_link, decide_link_action)
    keep working unchanged.

    Returns: 'junction' / 'soft' / 'hard', or None if not a link.
    """
    kind = _fk_detect_link_type(path)
    # junction + None pass through unchanged; symlink->soft, hardlink->hard.
    return {_FK_SYMLINK: LINK_TYPE_SOFT, _FK_HARDLINK: LINK_TYPE_HARD}.get(kind, kind)


def get_link_target(path: Union[str, Path]) -> Optional[str]:
    """
    Get the target of a link (symlink or junction), or None.

    Delegates to dazzle_filekit.read_link_target -- reads junction targets via the
    DeviceIoControl reparse buffer and strips the Windows extended-length prefix,
    replacing preservelib's banned `cmd /c dir /al` parse (which mismatched on
    substring names and mangled UNC / extended-length targets).
    """
    return _fk_read_link_target(path)


def create_link(
    link_path: Union[str, Path],
    target_path: Union[str, Path],
    link_type: str = LINK_TYPE_AUTO,
    is_directory: bool = True
) -> Tuple[bool, str, Optional[str]]:
    """
    Create a filesystem link from link_path pointing to target_path.

    Args:
        link_path: Where to create the link (the source location after move)
        target_path: What the link should point to (the destination after move)
        link_type: Type of link to create ('junction', 'soft', 'hard', 'auto')
        is_directory: Whether the target is a directory (for symlinks)

    Returns:
        Tuple of (success, actual_link_type, error_message)
    """
    link_path = Path(link_path)
    target_path = Path(target_path)

    # Validate link type
    if link_type not in VALID_LINK_TYPES:
        return False, link_type, f"Invalid link type: {link_type}"

    # Handle 'dazzle' type - not yet implemented
    if link_type == LINK_TYPE_DAZZLE:
        return False, link_type, "Dazzle link type not yet implemented"

    # Resolve 'auto' to platform-appropriate type
    if link_type == LINK_TYPE_AUTO:
        if os.name == 'nt' and is_directory:
            link_type = LINK_TYPE_JUNCTION
        else:
            link_type = LINK_TYPE_SOFT
        logger.debug(f"Auto-selected link type: {link_type}")

    # Ensure link_path doesn't exist (or is empty directory we can remove)
    if link_path.exists():
        if link_path.is_dir() and not any(link_path.iterdir()):
            try:
                link_path.rmdir()
            except Exception as e:
                return False, link_type, f"Cannot remove empty directory at link path: {e}"
        else:
            return False, link_type, f"Link path already exists and is not empty: {link_path}"

    # Ensure parent directory exists
    link_path.parent.mkdir(parents=True, exist_ok=True)

    # Create the appropriate link type
    try:
        if link_type == LINK_TYPE_JUNCTION:
            success, error = _create_junction(link_path, target_path)
        elif link_type == LINK_TYPE_SOFT:
            success, error = _create_symlink(link_path, target_path, is_directory)
        elif link_type == LINK_TYPE_HARD:
            success, error = _create_hard_link(link_path, target_path)
        else:
            return False, link_type, f"Unsupported link type: {link_type}"

        if success:
            logger.info(f"Created {link_type} link: {link_path} -> {target_path}")
            return True, link_type, None
        else:
            return False, link_type, error

    except Exception as e:
        return False, link_type, str(e)


def _create_junction(link_path: Path, target_path: Path) -> Tuple[bool, Optional[str]]:
    """
    Create a Windows NTFS junction at link_path pointing to target_path.

    Delegates to dazzle_filekit.create_junction (PowerShell New-Item, replacing
    preservelib's banned cmd mklink /j). NOTE filekit's signature is
    (target, link) -- the arguments are swapped here.
    """
    ok = _fk_create_junction(target_path, link_path)
    return (True, None) if ok else (False, "Junction creation failed (see logs)")


def _create_symlink(link_path: Path, target_path: Path, is_directory: bool) -> Tuple[bool, Optional[str]]:
    """
    Create a symbolic link at link_path pointing to target_path.

    Delegates to dazzle_filekit.create_symlink, which adds an elevation/UAC
    fallback chain on Windows (os.symlink -> win32 unprivileged flag -> mklink ->
    PowerShell elevation). NOTE filekit's signature is (target, link, ...) --
    arguments swapped here. The elevation guidance message is synthesized on
    failure (filekit logs it rather than returning it).
    """
    ok = _fk_create_symlink(target_path, link_path, target_is_directory=is_directory)
    if ok:
        return True, None
    return False, (
        "Symlink creation failed -- on Windows this may require administrator "
        "privileges or Developer Mode (see logs)"
    )


def _create_hard_link(link_path: Path, target_path: Path) -> Tuple[bool, Optional[str]]:
    """
    Create a hard link at link_path pointing to the file target_path.

    Delegates the os.link mechanics to dazzle_filekit.create_hardlink (same
    os.link + EXDEV handling, plus a safer errno check). NOTE filekit's signature
    is (target, link) -- arguments swapped here.

    L3 keeps the directory pre-check so the RETURNED error names the specific
    reason. filekit performs the same check but only logs it (returning a bare
    bool); preservelib's contract -- and its consumers (the preserve CLI's
    create_link test asserts the word "files") -- expect the reason in the
    return value. This is a user-facing POLICY message, not reimplemented
    mechanics (the actual os.link stays delegated).
    """
    if Path(target_path).is_dir():
        return False, "Hard links can only be created for files, not directories"
    ok = _fk_create_hardlink(target_path, link_path)
    return (True, None) if ok else (False, "Hard link creation failed (see logs)")


def remove_link(path: Union[str, Path]) -> Tuple[bool, Optional[str]]:
    """
    Safely remove a link without deleting the target content.

    For junctions and directory symlinks, this removes the link only.
    For file symlinks and hard links, this removes the link only.

    Args:
        path: Path to the link to remove

    Returns:
        Tuple of (success, error_message)
    """
    path = Path(path)

    if not is_link(path):
        return False, f"Path is not a link: {path}"

    link_type = detect_link_type(path)
    if _fk_remove_link(path):
        logger.info(f"Removed {link_type} link: {path}")
        return True, None
    return False, f"Failed to remove link: {path} (see logs)"


def verify_link(path: Union[str, Path], expected_target: Union[str, Path]) -> Tuple[bool, Optional[str]]:
    """
    Verify that a link exists and points to the expected target.

    Args:
        path: Path to the link
        expected_target: Expected target path

    Returns:
        Tuple of (matches, actual_target_or_error)
    """
    path = Path(path)
    expected_target = Path(expected_target)

    if not is_link(path):
        return False, "Path is not a link"

    actual_target = get_link_target(path)

    if actual_target is None:
        return False, "Could not determine link target"

    # Normalize paths for comparison
    try:
        actual_normalized = Path(actual_target).resolve()
        expected_normalized = expected_target.resolve()

        if actual_normalized == expected_normalized:
            return True, str(actual_target)
        else:
            return False, str(actual_target)
    except Exception:
        # Fall back to string comparison
        if str(actual_target) == str(expected_target):
            return True, str(actual_target)
        return False, str(actual_target)


def check_for_links_at_sources(manifest, preserved_dir: Union[str, Path]) -> Dict:
    """
    Check if any original source paths are now links.

    This is used during RESTORE to detect links that need to be removed
    before files can be restored to their original locations.

    Args:
        manifest: PreserveManifest object
        preserved_dir: Directory containing preserved files

    Returns:
        Dictionary with:
        - has_links: bool
        - links: list of link info dicts
    """
    from .manifest import extract_source_from_manifest

    links_found = []

    # First check manifest's link_result (if we created the link)
    for op in manifest.get_all_operations():
        link_result = op.get('link_result')
        if link_result:
            link_path = link_result.get('link_path')
            if link_path and is_link(link_path):
                links_found.append({
                    'path': link_path,
                    'type': link_result.get('type'),
                    'target': link_result.get('target_path'),
                    'tracked': True  # We created this link
                })

    # Also check filesystem for untracked links (safety)
    source_base = extract_source_from_manifest(manifest)
    if source_base and is_link(source_base):
        # Check if we already found this link
        if not any(l['path'] == str(source_base) for l in links_found):
            links_found.append({
                'path': str(source_base),
                'type': detect_link_type(source_base),
                'target': get_link_target(source_base),
                'tracked': False  # Not in manifest - warn user
            })

    return {
        'has_links': len(links_found) > 0,
        'links': links_found
    }
