"""MFT/USN enumeration scanner backend for linkmirror.

Sequential-read alternative to the directory-walk backend (``linkmirror.scan``).
Directory walking a ~50M-record NTFS volume over a failing/slow link can take
hours; reading the Master File Table sequentially via ``FSCTL_ENUM_USN_DATA``
takes minutes because it is one large streamed read instead of millions of
small directory-handle opens.

Approach:
    1. Open the raw volume device (``\\\\.\\D:``) -- requires elevation.
    2. Stream the entire volume's MFT via repeated ``DeviceIoControl`` calls
       using ``MFT_ENUM_DATA_V0`` / ``USN_RECORD_V2``, building a
       FRN -> (parent FRN, name, attributes) map. This step touches every
       record on the volume, not just the requested subtree, because the USN
       enumeration API has no path filter -- it can only be scoped by FRN
       range, not by name.
    3. Reconstruct paths by walking ParentFileReferenceNumber chains, then
       filter down to the entries that fall under the requested root.
    4. For anything with the reparse-point attribute, do ONE targeted,
       per-path I/O call (os.lstat + os.readlink) to classify the reparse
       type and read its target -- this is the only per-file I/O in the
       whole backend.
    5. For plain files that share an FRN across multiple name records
       (hardlinks), emit one KIND_HARDLINK record per name, grouped by FRN
       (or by the confirmed st_ino when ``stat_confirm=True``).

Fidelity contract (see records.py): targets are raw and verbatim. Nothing
here resolves, validates, or rewrites a reparse target.

Windows-only. Importing this module on a non-Windows platform is safe (no
ctypes.WinDLL calls happen at import time); calling any Windows-only function
on a non-Windows platform raises MftNotSupportedError.
"""

from __future__ import annotations

import ctypes
import os
import struct
from typing import Callable, Dict, List, Optional, Tuple, Union

from .records import (
    IO_REPARSE_TAG_MOUNT_POINT,
    IO_REPARSE_TAG_SYMLINK,
    KIND_HARDLINK,
    KIND_JUNCTION,
    KIND_OTHER_REPARSE,
    KIND_SYMLINK,
    LinkManifest,
    LinkRecord,
)

_IS_WINDOWS = (os.name == "nt")

# ==============================================================================
# Win32 constants
# ==============================================================================

FSCTL_ENUM_USN_DATA = 0x000900B3

GENERIC_READ = 0x80000000
FILE_SHARE_READ = 0x00000001
FILE_SHARE_WRITE = 0x00000002
FILE_SHARE_DELETE = 0x00000004
OPEN_EXISTING = 3
FILE_FLAG_BACKUP_SEMANTICS = 0x02000000

FILE_ATTRIBUTE_DIRECTORY = 0x00000010
FILE_ATTRIBUTE_REPARSE_POINT = 0x00000400

ERROR_ACCESS_DENIED = 5
ERROR_HANDLE_EOF = 38

# HighUsn value that covers the entire journal range (USN is a signed 64-bit
# quantity; this is the largest positive value it can hold).
_MAX_USN = 0x7FFFFFFFFFFFFFFF

_DEFAULT_BUFFER_SIZE = 1024 * 1024  # 1 MiB per DeviceIoControl call

# INVALID_HANDLE_VALUE is ((HANDLE)-1). Compute it via ctypes so it is correct
# on both 32-bit and 64-bit builds instead of hardcoding a bit width.
INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value


# ==============================================================================
# ctypes structures (plain c_* fields only -- safe to define on any platform,
# no WinDLL binding happens here).
# ==============================================================================

class _MFT_ENUM_DATA_V0(ctypes.Structure):
    _fields_ = [
        ("StartFileReferenceNumber", ctypes.c_uint64),
        ("LowUsn", ctypes.c_int64),
        ("HighUsn", ctypes.c_int64),
    ]


class _FILETIME(ctypes.Structure):
    _fields_ = [
        ("dwLowDateTime", ctypes.c_uint32),
        ("dwHighDateTime", ctypes.c_uint32),
    ]


class _BY_HANDLE_FILE_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("dwFileAttributes", ctypes.c_uint32),
        ("ftCreationTime", _FILETIME),
        ("ftLastAccessTime", _FILETIME),
        ("ftLastWriteTime", _FILETIME),
        ("dwVolumeSerialNumber", ctypes.c_uint32),
        ("nFileSizeHigh", ctypes.c_uint32),
        ("nFileSizeLow", ctypes.c_uint32),
        ("nNumberOfLinks", ctypes.c_uint32),
        ("nFileIndexHigh", ctypes.c_uint32),
        ("nFileIndexLow", ctypes.c_uint32),
    ]


# USN_RECORD_V2 header (fixed-size part, before the variable-length UTF-16LE
# FileName). Field order/sizes match winioctl.h exactly:
#   DWORD     RecordLength;
#   WORD      MajorVersion;
#   WORD      MinorVersion;
#   DWORDLONG FileReferenceNumber;
#   DWORDLONG ParentFileReferenceNumber;
#   USN       Usn;                 (LONGLONG, signed)
#   LARGE_INTEGER TimeStamp;       (signed 64-bit FILETIME)
#   DWORD     Reason;
#   DWORD     SourceInfo;
#   DWORD     SecurityId;
#   DWORD     FileAttributes;
#   WORD      FileNameLength;
#   WORD      FileNameOffset;
# This packs to exactly 60 bytes with no padding (every multi-byte field
# lands on a naturally-aligned offset already), matching the documented
# 60-byte USN_RECORD_V2 header / FileNameOffset == 60 convention.
_USN_RECORD_V2_HEADER = struct.Struct("<IHHQQqqIIIIHH")
USN_RECORD_V2_HEADER_SIZE = _USN_RECORD_V2_HEADER.size  # 60
assert USN_RECORD_V2_HEADER_SIZE == 60

_USN_MAJOR_VERSION_2 = 2


# ==============================================================================
# Win32 bindings (guarded: only touches ctypes.WinDLL under Windows, so import
# never crashes on POSIX).
# ==============================================================================

if _IS_WINDOWS:
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    kernel32.CreateFileW.argtypes = [
        ctypes.c_wchar_p,
        ctypes.c_uint32,
        ctypes.c_uint32,
        ctypes.c_void_p,
        ctypes.c_uint32,
        ctypes.c_uint32,
        ctypes.c_void_p,
    ]
    kernel32.CreateFileW.restype = ctypes.c_void_p

    kernel32.DeviceIoControl.argtypes = [
        ctypes.c_void_p,
        ctypes.c_uint32,
        ctypes.c_void_p,
        ctypes.c_uint32,
        ctypes.c_void_p,
        ctypes.c_uint32,
        ctypes.POINTER(ctypes.c_uint32),
        ctypes.c_void_p,
    ]
    kernel32.DeviceIoControl.restype = ctypes.c_int

    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    kernel32.CloseHandle.restype = ctypes.c_int

    kernel32.GetFileInformationByHandle.argtypes = [
        ctypes.c_void_p,
        ctypes.POINTER(_BY_HANDLE_FILE_INFORMATION),
    ]
    kernel32.GetFileInformationByHandle.restype = ctypes.c_int
else:
    kernel32 = None


# ==============================================================================
# Exceptions
# ==============================================================================

class MftError(Exception):
    """Base class for all MFT backend errors."""


class MftNotSupportedError(MftError):
    """Raised when MFT scanning is attempted on a non-Windows platform."""

    def __init__(self, message: str = "MFT scanning requires Windows (FSCTL_ENUM_USN_DATA is an NTFS/Win32 API)."):
        super().__init__(message)


class MftAccessDenied(MftError):
    """Raised when the raw volume handle cannot be opened.

    This is almost always because the process is not elevated -- reading raw
    MFT data via FSCTL_ENUM_USN_DATA requires administrator rights. Callers
    should catch this and fall back to the directory-walk scanner backend
    (linkmirror.scan).
    """

    def __init__(self, volume: str, winerror: Optional[int] = None):
        self.volume = volume
        self.winerror = winerror
        msg = (
            "Cannot open volume %r for MFT enumeration (access denied). "
            "Reading the raw MFT via FSCTL_ENUM_USN_DATA requires an elevated "
            "(Run as Administrator) process. Re-run elevated, or use the "
            "directory-walk scanner backend instead." % (volume,)
        )
        if winerror is not None:
            msg += " (WinError %d)" % winerror
        super().__init__(msg)


# ==============================================================================
# Small path helpers
# ==============================================================================

def _normalize_root(root: str) -> str:
    """Normalize a scan root, treating a bare drive letter ("D:") as the
    volume root ("D:\\") rather than "current directory on D:" (a Windows
    quirk of os.path.abspath / CreateFile with a drive-relative path).
    """
    if len(root) == 2 and root[1] == ":":
        root = root + "\\"
    return os.path.abspath(root)


def _split_volume(path: str) -> str:
    """Return the drive-letter volume spec ("D:") for an absolute path."""
    drive, _rest = os.path.splitdrive(path)
    if not drive or not drive.endswith(":"):
        raise MftError(
            "Cannot determine an NTFS volume for path %r (no drive letter; "
            "MFT enumeration only supports local drive-letter volumes)" % (path,)
        )
    return drive


def _to_extended_path(path: str) -> str:
    """Convert an absolute path to its \\\\?\\-prefixed form for per-file I/O.

    Reconstructed paths under a deep tree can exceed MAX_PATH (260 chars);
    the extended-length prefix bypasses that limit entirely without needing
    the Windows 10 "long paths" registry opt-in. The prefix is used only for
    internal I/O calls -- manifest rel_path values are never prefixed.
    """
    if path.startswith("\\\\?\\"):
        return path
    if path.startswith("\\\\"):
        return "\\\\?\\UNC\\" + path[2:]
    return "\\\\?\\" + path


# ==============================================================================
# Low-level Win32 calls
# ==============================================================================

def _check_windows() -> None:
    if not _IS_WINDOWS:
        raise MftNotSupportedError()


def _open_volume_device(volume: str):
    """Open the raw volume device (\\\\.\\D:). Requires elevation."""
    _check_windows()
    device_path = "\\\\.\\" + volume.rstrip("\\")
    handle = kernel32.CreateFileW(
        device_path,
        GENERIC_READ,
        FILE_SHARE_READ | FILE_SHARE_WRITE,
        None,
        OPEN_EXISTING,
        0,
        None,
    )
    if handle is None or handle == INVALID_HANDLE_VALUE:
        err = ctypes.get_last_error()
        if err == ERROR_ACCESS_DENIED:
            raise MftAccessDenied(volume, winerror=err)
        raise MftError("CreateFileW failed opening volume %r (WinError %d)" % (volume, err))
    return handle


def is_mft_available(volume: str) -> bool:
    """Return True if the raw volume device can be opened for MFT enumeration.

    Swallows all errors (including "not Windows") and returns False rather
    than raising -- callers use this to decide, cheaply, whether to attempt
    the MFT backend at all before committing to a scan.
    """
    if not _IS_WINDOWS:
        return False
    try:
        handle = _open_volume_device(volume)
    except MftError:
        return False
    kernel32.CloseHandle(handle)
    return True


def _get_frn_for_path(path: str) -> int:
    """Return the 64-bit NTFS file reference number for an existing path.

    Uses an ordinary (non-elevated) CreateFileW query-only open, so this
    works even when the caller is not elevated -- only the raw volume device
    open (_open_volume_device) needs admin rights.
    """
    _check_windows()
    long_path = _to_extended_path(path)
    handle = kernel32.CreateFileW(
        long_path,
        0,  # query metadata only; no read/write access needed
        FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE,
        None,
        OPEN_EXISTING,
        FILE_FLAG_BACKUP_SEMANTICS,  # required to open a directory handle
        None,
    )
    if handle is None or handle == INVALID_HANDLE_VALUE:
        err = ctypes.get_last_error()
        raise MftError("CreateFileW failed opening %r for FRN lookup (WinError %d)" % (path, err))
    try:
        info = _BY_HANDLE_FILE_INFORMATION()
        ok = kernel32.GetFileInformationByHandle(handle, ctypes.byref(info))
        if not ok:
            err = ctypes.get_last_error()
            raise MftError("GetFileInformationByHandle failed for %r (WinError %d)" % (path, err))
        return (info.nFileIndexHigh << 32) | info.nFileIndexLow
    finally:
        kernel32.CloseHandle(handle)


def _resolve_volume_and_root_frn(root: str) -> Tuple[str, int]:
    volume = _split_volume(root)
    frn = _get_frn_for_path(root)
    return volume, frn


# ==============================================================================
# USN_RECORD_V2 parsing
# ==============================================================================

def _parse_usn_record_v2(buf: bytes) -> Tuple[int, int, str, int]:
    """Parse one USN_RECORD_V2 from a byte buffer (buf[0] is RecordLength).

    Returns (file_reference_number, parent_file_reference_number, name, attrs).
    """
    (
        _record_length,
        major_version,
        _minor_version,
        frn,
        parent_frn,
        _usn,
        _timestamp,
        _reason,
        _source_info,
        _security_id,
        attrs,
        name_length,
        name_offset,
    ) = _USN_RECORD_V2_HEADER.unpack_from(buf, 0)

    if major_version != _USN_MAJOR_VERSION_2:
        raise MftError(
            "Unsupported USN record MajorVersion %d (only USN_RECORD_V2 is "
            "handled)" % major_version
        )

    name_bytes = buf[name_offset:name_offset + name_length]
    # NTFS filenames are arbitrary 16-bit code units, NOT guaranteed valid
    # UTF-16 -- unpaired surrogates occur in the wild (first hit: real
    # drive-wide scan, 2026-07-22). surrogatepass mirrors how CPython itself
    # decodes Windows filenames, so the resulting str round-trips through
    # os.path.join/os.lstat/os.readlink on the targeted follow-up I/O.
    name = name_bytes.decode("utf-16-le", errors="surrogatepass")
    return frn, parent_frn, name, attrs


# ==============================================================================
# FRN map
# ==============================================================================
#
# frn_map: Dict[int, Union[Tuple[int, str, int], List[Tuple[int, str, int]]]]
#
# Memory note (the honest trade-off): a ~50M-record volume means ~50M dict
# entries. The overwhelmingly common case is one name per FRN, so the value
# is stored as a BARE (parent_frn, name, attrs) tuple -- no list wrapper --
# and is only "upgraded" to a list on the second observed name for that FRN
# (a hardlink). Rough CPython accounting per single-name record: dict slot
# (~50-70 bytes) + int key object (~28 bytes) + 3-tuple (~56 bytes) + parent
# int (~28 bytes) + attrs int (~28 bytes) + name str (~70-90 bytes for a
# typical filename) totals roughly 260-300 bytes/record. For 50,000,000
# records that is ballpark 13-15 GB resident -- large, but a one-time cost
# for a scan that replaces a directory walk that was taking hours and had to
# be abandoned. If memory becomes the binding constraint on a given box, the
# next optimization step (not implemented here) would be parallel typed
# arrays (e.g. numpy structured dtype: frn/parent/attrs as fixed-width
# columns, names in a separate offset-indexed buffer) or an on-disk index
# (sqlite/lmdb) instead of a plain Python dict.


def _add_usn_record(frn_map: Dict, frn: int, parent_frn: int, name: str, attrs: int) -> None:
    entry = (parent_frn, name, attrs)
    existing = frn_map.get(frn)
    if existing is None:
        frn_map[frn] = entry
    elif isinstance(existing, list):
        existing.append(entry)
    else:
        frn_map[frn] = [existing, entry]


def _entries_for(frn_map: Dict, frn: int) -> Optional[List[Tuple[int, str, int]]]:
    """Return the list of (parent_frn, name, attrs) entries for frn, or None."""
    v = frn_map.get(frn)
    if v is None:
        return None
    if isinstance(v, list):
        return v
    return [v]


def _parse_usn_buffer(data: bytes) -> Tuple[int, List[Tuple[int, int, str, int]]]:
    """Parse one FSCTL_ENUM_USN_DATA output buffer.

    Layout: an 8-byte little-endian "next StartFileReferenceNumber" value,
    followed by zero or more back-to-back USN_RECORD_V2 entries, each
    self-describing its own length via its leading RecordLength DWORD.

    Pure function (no ctypes/WinDLL calls) so it can be exercised directly
    against synthetic buffers in tests without touching any Windows API.

    Returns (next_start_frn, [(frn, parent_frn, name, attrs), ...]).
    """
    n = len(data)
    next_start = struct.unpack_from("<Q", data, 0)[0]
    records = []
    offset = 8
    while offset < n:
        rec_len = struct.unpack_from("<I", data, offset)[0]
        if rec_len == 0:
            break
        record_bytes = data[offset:offset + rec_len]
        records.append(_parse_usn_record_v2(record_bytes))
        offset += rec_len
    return next_start, records


def _enumerate_usn(
    handle,
    frn_map: Dict,
    buffer_size: int = _DEFAULT_BUFFER_SIZE,
    progress_callback: Optional[Callable[[int], None]] = None,
) -> int:
    """Stream FSCTL_ENUM_USN_DATA into frn_map. Returns the record count."""
    enum_data = _MFT_ENUM_DATA_V0(StartFileReferenceNumber=0, LowUsn=0, HighUsn=_MAX_USN)
    out_buf = ctypes.create_string_buffer(buffer_size)
    bytes_returned = ctypes.c_uint32(0)
    count = 0

    while True:
        ok = kernel32.DeviceIoControl(
            handle,
            FSCTL_ENUM_USN_DATA,
            ctypes.byref(enum_data),
            ctypes.sizeof(enum_data),
            out_buf,
            buffer_size,
            ctypes.byref(bytes_returned),
            None,
        )
        if not ok:
            err = ctypes.get_last_error()
            if err == ERROR_HANDLE_EOF:
                break
            raise MftError("FSCTL_ENUM_USN_DATA failed (WinError %d)" % err)

        n = bytes_returned.value
        if n <= 8:
            # Only the next-start-FRN was returned: nothing left to enumerate.
            break

        next_start, records = _parse_usn_buffer(out_buf.raw[:n])
        for frn, parent_frn, name, attrs in records:
            _add_usn_record(frn_map, frn, parent_frn, name, attrs)
        count += len(records)

        if next_start <= enum_data.StartFileReferenceNumber and n > 8:
            # Defensive: the API is documented to always advance; bail rather
            # than spin forever if it somehow doesn't.
            raise MftError("FSCTL_ENUM_USN_DATA did not advance (possible driver anomaly)")
        enum_data.StartFileReferenceNumber = next_start

        if progress_callback is not None:
            progress_callback(count)

    return count


# ==============================================================================
# Path reconstruction
# ==============================================================================

def _walk_to_root(
    frn: int,
    frn_map: Dict,
    root_frn: int,
    cache: Dict[int, Optional[List[str]]],
    errors: List[str],
    max_depth: int = 4096,
) -> Optional[List[str]]:
    """Return the list of path components (root-relative) addressing a
    DIRECTORY frn, or None if frn is not reachable under root_frn.

    Only call this with FRNs that represent directories (i.e. ancestors) --
    a directory can only have one name/parent pair on NTFS, so lookups here
    are unambiguous. File FRNs that may carry multiple hardlink names must
    be resolved via _rel_path_for_name, which supplies the specific
    (parent_frn, name) pair for one name instance directly.
    """
    if frn == root_frn:
        return []
    if frn in cache:
        return cache[frn]

    chain: List[str] = []
    seen = set()
    cur = frn
    result: Optional[List[str]] = None
    depth = 0

    while True:
        if cur in cache:
            cached = cache[cur]
            result = None if cached is None else (cached + list(reversed(chain)))
            break
        if cur in seen:
            errors.append("mft: cycle detected reconstructing path near FRN 0x%X" % cur)
            result = None
            break
        seen.add(cur)
        depth += 1
        if depth > max_depth:
            errors.append(
                "mft: path reconstruction exceeded max depth (%d) near FRN 0x%X "
                "-- probable cycle, bailing out" % (max_depth, cur)
            )
            result = None
            break

        entries = _entries_for(frn_map, cur)
        if not entries:
            errors.append(
                "mft: orphaned parent reference -- FRN 0x%X not found in the "
                "MFT enumeration" % cur
            )
            result = None
            break
        if len(entries) > 1:
            errors.append(
                "mft: FRN 0x%X was expected to be a single-name directory "
                "ancestor but has %d name records; using the first (%r)"
                % (cur, len(entries), entries[0][1])
            )
        parent_frn, name, _attrs = entries[0]

        if parent_frn == cur:
            # Self-referential record: the true filesystem root (MFT record
            # #5 by convention). root_frn was never matched walking up to
            # here, so the original frn lies outside the requested subtree.
            result = None
            break

        chain.append(name)
        if parent_frn == root_frn:
            result = list(reversed(chain))
            break
        cur = parent_frn

    cache[frn] = result
    return result


def _rel_path_for_name(
    name: str,
    parent_frn: int,
    frn_map: Dict,
    root_frn: int,
    cache: Dict[int, Optional[List[str]]],
    errors: List[str],
) -> Optional[str]:
    """Reconstruct the root-relative path for one specific (parent, name)
    record -- the unit of resolution that is unambiguous even for a
    hardlinked file (which may have several (parent, name) pairs sharing an
    FRN, each requiring its own path).
    """
    if parent_frn == root_frn:
        return name
    ancestors = _walk_to_root(parent_frn, frn_map, root_frn, cache, errors)
    if ancestors is None:
        return None
    if not ancestors:
        return name
    return os.sep.join(ancestors + [name])


# ==============================================================================
# Reparse-point classification (the only per-file I/O in this backend)
# ==============================================================================

def _classify_reparse(root: str, rel_path: str, is_dir: bool, errors: List[str]) -> Optional[LinkRecord]:
    abs_path = os.path.join(root, rel_path)
    long_path = _to_extended_path(abs_path)

    try:
        st = os.lstat(long_path)
    except OSError as exc:
        errors.append("mft: lstat failed for reparse candidate %r: %s" % (rel_path, exc))
        return None

    tag = getattr(st, "st_reparse_tag", 0)

    try:
        target = os.readlink(long_path)
    except (OSError, ValueError):
        target = ""

    if tag == IO_REPARSE_TAG_SYMLINK:
        kind = KIND_SYMLINK
    elif tag == IO_REPARSE_TAG_MOUNT_POINT:
        kind = KIND_JUNCTION
    else:
        kind = KIND_OTHER_REPARSE

    created_ns = getattr(st, "st_birthtime_ns", None)
    if created_ns is None:
        created_ns = st.st_ctime_ns

    return LinkRecord(
        kind=kind,
        rel_path=rel_path,
        target=target,
        is_dir=is_dir,
        reparse_tag=tag,
        created_ns=created_ns,
        modified_ns=st.st_mtime_ns,
        accessed_ns=st.st_atime_ns,
    )


def _confirm_hardlink_group(
    root: str,
    rel_paths: List[str],
    errors: List[str],
) -> Tuple[bool, Optional[int]]:
    """os.stat every candidate name and confirm they share one st_ino with
    nlink > 1. Returns (confirmed, group_id) where group_id is the shared
    st_ino when confirmed.
    """
    inos = set()
    nlinks = set()
    for rel_path in rel_paths:
        abs_path = _to_extended_path(os.path.join(root, rel_path))
        try:
            st = os.stat(abs_path)
        except OSError as exc:
            errors.append("mft: stat_confirm failed for %r: %s" % (rel_path, exc))
            return False, None
        inos.add(st.st_ino)
        nlinks.add(st.st_nlink)

    if len(inos) != 1:
        errors.append(
            "mft: stat_confirm -- hardlink group members do not share a "
            "single st_ino: %r" % (rel_paths,)
        )
        return False, None
    if all(n <= 1 for n in nlinks):
        errors.append(
            "mft: stat_confirm -- MFT suggested a hardlink group but nlink "
            "<= 1 on disk for %r (group likely dissolved since enumeration)"
            % (rel_paths,)
        )
        return False, None
    return True, inos.pop()


# ==============================================================================
# Public entry point
# ==============================================================================

def mft_scan(
    root: str,
    stat_confirm: bool = False,
    buffer_size: int = _DEFAULT_BUFFER_SIZE,
    progress_callback: Optional[Callable[[int], None]] = None,
) -> LinkManifest:
    """Scan for NTFS links under root via MFT/USN enumeration.

    root may be a volume root ("D:\\") or any subpath ("D:\\M"). The entire
    volume's MFT is streamed (FSCTL_ENUM_USN_DATA has no path filter), then
    filtered down to entries under root.

    Args:
        root: Directory to scan (volume root or subpath). Must exist.
        stat_confirm: When True, confirm each candidate hardlink group with
            os.stat (nlink > 1, shared st_ino) before emitting KIND_HARDLINK
            records, using the confirmed st_ino as group_id. When False
            (default), hardlink groups are inferred purely from the MFT
            enumeration (see the module-level assumption note in
            linkmirror/mft.py's hardlink handling) and group_id is the FRN.
        buffer_size: Bytes per DeviceIoControl call (default 1 MiB).
        progress_callback: Optional callable(count) invoked periodically
            during enumeration with the running record count.

    Returns:
        LinkManifest with backend="mft".

    Raises:
        MftNotSupportedError: not running on Windows.
        MftAccessDenied: the raw volume device could not be opened (almost
            always: not elevated). Callers should fall back to the
            directory-walk backend.
        MftError: any other MFT/USN enumeration failure.
    """
    _check_windows()

    root = _normalize_root(root)
    if not os.path.isdir(root):
        raise MftError("Scan root does not exist or is not a directory: %r" % (root,))

    volume, root_frn = _resolve_volume_and_root_frn(root)

    handle = _open_volume_device(volume)
    try:
        frn_map: Dict = {}
        entries_scanned = _enumerate_usn(handle, frn_map, buffer_size, progress_callback)
    finally:
        kernel32.CloseHandle(handle)

    manifest = LinkManifest(root=root, backend="mft")
    manifest.entries_scanned = entries_scanned

    cache: Dict[int, Optional[List[str]]] = {}
    reparse_hits: List[Tuple[str, bool]] = []
    hardlink_names: Dict[int, List[str]] = {}

    for frn, value in frn_map.items():
        if frn == root_frn:
            continue  # the scan root itself is never "under" itself
        entries = value if isinstance(value, list) else (value,)
        for parent_frn, name, attrs in entries:
            is_dir = bool(attrs & FILE_ATTRIBUTE_DIRECTORY)
            is_reparse = bool(attrs & FILE_ATTRIBUTE_REPARSE_POINT)

            if not is_reparse and is_dir:
                continue  # plain directories: never reparse, never hardlink groups

            rel_path = _rel_path_for_name(name, parent_frn, frn_map, root_frn, cache, manifest.errors)
            if rel_path is None:
                continue  # not under root, orphaned, or a cycle (already logged)

            if is_reparse:
                reparse_hits.append((rel_path, is_dir))
            elif not is_dir:
                hardlink_names.setdefault(frn, []).append(rel_path)

    for rel_path, is_dir in reparse_hits:
        record = _classify_reparse(root, rel_path, is_dir, manifest.errors)
        if record is not None:
            manifest.records.append(record)

    for frn, names in hardlink_names.items():
        if len(names) < 2:
            continue

        group_id = frn
        if stat_confirm:
            confirmed, ino = _confirm_hardlink_group(root, names, manifest.errors)
            if not confirmed:
                continue
            group_id = ino

        for rel_path in names:
            manifest.records.append(
                LinkRecord(
                    kind=KIND_HARDLINK,
                    rel_path=rel_path,
                    target="",
                    is_dir=False,
                    group_id=group_id,
                    nlink=len(names),
                )
            )

    return manifest
