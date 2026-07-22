"""Tests for the MFT/USN enumeration scanner backend (linkmirror.mft).

These tests exercise the USN_RECORD_V2 parser and path-reconstruction logic
against synthetic byte buffers built with struct.pack -- no elevation and no
live volume access is required for the bulk of this suite. Only the final
"access denied when not elevated" test touches a real volume, and it is
skipped on non-Windows platforms.
"""

import os
import struct
import sys
import unittest

import pytest

from dazzle_preservelib.linkmirror import mft
from dazzle_preservelib.linkmirror.records import (
    IO_REPARSE_TAG_MOUNT_POINT,
    IO_REPARSE_TAG_SYMLINK,
)

# ==============================================================================
# Synthetic USN_RECORD_V2 buffer builder
# ==============================================================================
#
# Independently reproduces the on-disk layout documented for USN_RECORD_V2
# (winioctl.h) rather than importing mft.py's own struct format, so a parsing
# bug in mft.py would actually be caught:
#   DWORD RecordLength; WORD MajorVersion; WORD MinorVersion;
#   DWORDLONG FileReferenceNumber; DWORDLONG ParentFileReferenceNumber;
#   USN Usn; LARGE_INTEGER TimeStamp; DWORD Reason; DWORD SourceInfo;
#   DWORD SecurityId; DWORD FileAttributes;
#   WORD FileNameLength; WORD FileNameOffset; WCHAR FileName[1];

_USN_HEADER_FMT = "<IHHQQqqIIIIHH"
_USN_HEADER_SIZE = struct.calcsize(_USN_HEADER_FMT)

FILE_ATTRIBUTE_DIRECTORY = 0x10
FILE_ATTRIBUTE_REPARSE_POINT = 0x400
FILE_ATTRIBUTE_NORMAL = 0x80


def build_usn_record(frn, parent_frn, name, attrs, major_version=2, minor_version=0,
                      usn=0, timestamp=0, reason=0, source_info=0, security_id=0):
    """Pack one synthetic USN_RECORD_V2, padded to an 8-byte boundary the way
    real Windows output is (RecordLength includes the padding).

    ``name`` may be raw bytes to model filenames that are not valid UTF-16
    (NTFS allows arbitrary 16-bit code units, e.g. unpaired surrogates)."""
    name_bytes = name if isinstance(name, bytes) else name.encode("utf-16-le")
    name_offset = _USN_HEADER_SIZE
    raw_length = name_offset + len(name_bytes)
    padded_length = (raw_length + 7) // 8 * 8
    header = struct.pack(
        _USN_HEADER_FMT,
        padded_length, major_version, minor_version,
        frn, parent_frn, usn, timestamp, reason, source_info, security_id,
        attrs, len(name_bytes), name_offset,
    )
    body = (header + name_bytes).ljust(padded_length, b"\x00")
    assert len(body) == padded_length
    return body


def build_usn_buffer(records, next_start_frn=0):
    """Pack a full FSCTL_ENUM_USN_DATA output buffer: 8-byte next-start FRN
    followed by concatenated record blobs (each already self-padded)."""
    out = struct.pack("<Q", next_start_frn)
    for rec in records:
        out += rec
    return out


# ==============================================================================
# USN_RECORD_V2 parser
# ==============================================================================

class TestParseUsnRecordV2(unittest.TestCase):
    def test_plain_file_record(self):
        rec = build_usn_record(frn=100, parent_frn=10, name="file.txt", attrs=FILE_ATTRIBUTE_NORMAL)
        frn, parent_frn, name, attrs = mft._parse_usn_record_v2(rec)
        self.assertEqual(frn, 100)
        self.assertEqual(parent_frn, 10)
        self.assertEqual(name, "file.txt")
        self.assertEqual(attrs, FILE_ATTRIBUTE_NORMAL)

    def test_plain_directory_record(self):
        rec = build_usn_record(frn=20, parent_frn=5, name="subdir", attrs=FILE_ATTRIBUTE_DIRECTORY)
        frn, parent_frn, name, attrs = mft._parse_usn_record_v2(rec)
        self.assertEqual(name, "subdir")
        self.assertTrue(attrs & FILE_ATTRIBUTE_DIRECTORY)

    def test_symlink_attributed_record(self):
        attrs = FILE_ATTRIBUTE_REPARSE_POINT | FILE_ATTRIBUTE_NORMAL
        rec = build_usn_record(frn=200, parent_frn=10, name="link.txt", attrs=attrs)
        frn, parent_frn, name, parsed_attrs = mft._parse_usn_record_v2(rec)
        self.assertEqual(name, "link.txt")
        self.assertTrue(parsed_attrs & FILE_ATTRIBUTE_REPARSE_POINT)
        self.assertFalse(parsed_attrs & FILE_ATTRIBUTE_DIRECTORY)

    def test_junction_attributed_dir_record(self):
        attrs = FILE_ATTRIBUTE_REPARSE_POINT | FILE_ATTRIBUTE_DIRECTORY
        rec = build_usn_record(frn=300, parent_frn=10, name="junction_dir", attrs=attrs)
        frn, parent_frn, name, parsed_attrs = mft._parse_usn_record_v2(rec)
        self.assertTrue(parsed_attrs & FILE_ATTRIBUTE_REPARSE_POINT)
        self.assertTrue(parsed_attrs & FILE_ATTRIBUTE_DIRECTORY)

    def test_unicode_filename_round_trips(self):
        rec = build_usn_record(frn=400, parent_frn=10, name="café 日本語.txt", attrs=FILE_ATTRIBUTE_NORMAL)
        _frn, _parent, name, _attrs = mft._parse_usn_record_v2(rec)
        self.assertEqual(name, "café 日本語.txt")

    def test_unpaired_surrogate_filename_decodes(self):
        """NTFS names are arbitrary 16-bit units, not guaranteed UTF-16.
        An unpaired surrogate must decode (surrogatepass), not raise --
        a strict decode aborted the first real drive-wide scan (2026-07-22)."""
        # 'a', unpaired high surrogate U+D800, 'b' as raw UTF-16LE units
        raw_name = b"a\x00\x00\xd8b\x00"
        rec = build_usn_record(frn=500, parent_frn=10, name=raw_name,
                               attrs=FILE_ATTRIBUTE_NORMAL)
        _frn, _parent, name, _attrs = mft._parse_usn_record_v2(rec)
        self.assertEqual(name, "a\ud800b")
        # and it must round-trip back to the same on-disk code units
        self.assertEqual(
            name.encode("utf-16-le", errors="surrogatepass"), raw_name
        )

    def test_unsupported_major_version_raises(self):
        rec = build_usn_record(frn=1, parent_frn=1, name="x", attrs=0, major_version=3)
        with self.assertRaises(mft.MftError):
            mft._parse_usn_record_v2(rec)


class TestParseUsnBuffer(unittest.TestCase):
    """Multi-record buffer walking (RecordLength-based offset advancement)."""

    def test_multiple_records_in_one_buffer(self):
        recs = [
            build_usn_record(frn=1, parent_frn=5, name="a.txt", attrs=FILE_ATTRIBUTE_NORMAL),
            build_usn_record(frn=2, parent_frn=5, name="bb.txt", attrs=FILE_ATTRIBUTE_NORMAL),
            build_usn_record(frn=3, parent_frn=5, name="ccc", attrs=FILE_ATTRIBUTE_DIRECTORY),
        ]
        buf = build_usn_buffer(recs, next_start_frn=999)
        next_start, parsed = mft._parse_usn_buffer(buf)
        self.assertEqual(next_start, 999)
        self.assertEqual(len(parsed), 3)
        self.assertEqual([p[0] for p in parsed], [1, 2, 3])
        self.assertEqual(parsed[0][2], "a.txt")
        self.assertEqual(parsed[1][2], "bb.txt")
        self.assertEqual(parsed[2][2], "ccc")

    def test_empty_buffer_yields_no_records(self):
        buf = build_usn_buffer([], next_start_frn=42)
        next_start, parsed = mft._parse_usn_buffer(buf)
        self.assertEqual(next_start, 42)
        self.assertEqual(parsed, [])

    def test_hardlinked_pair_two_records_same_frn(self):
        """Models the assumed MFT/USN representation for a hardlinked file:
        one USN record per name, both sharing FileReferenceNumber, each with
        its own ParentFileReferenceNumber/FileName."""
        recs = [
            build_usn_record(frn=77, parent_frn=5, name="name_one.txt", attrs=FILE_ATTRIBUTE_NORMAL),
            build_usn_record(frn=77, parent_frn=6, name="name_two.txt", attrs=FILE_ATTRIBUTE_NORMAL),
        ]
        buf = build_usn_buffer(recs, next_start_frn=1000)
        _next_start, parsed = mft._parse_usn_buffer(buf)
        self.assertEqual(len(parsed), 2)
        self.assertEqual(parsed[0][0], parsed[1][0])  # same FRN
        self.assertNotEqual(parsed[0][1], parsed[1][1])  # different parents
        self.assertNotEqual(parsed[0][2], parsed[1][2])  # different names


# ==============================================================================
# frn_map accumulation (bare tuple -> list upgrade on hardlink)
# ==============================================================================

class TestFrnMapAccumulation(unittest.TestCase):
    def test_single_name_stored_as_bare_tuple(self):
        frn_map = {}
        mft._add_usn_record(frn_map, frn=1, parent_frn=5, name="a.txt", attrs=FILE_ATTRIBUTE_NORMAL)
        self.assertIsInstance(frn_map[1], tuple)

    def test_second_name_upgrades_to_list(self):
        frn_map = {}
        mft._add_usn_record(frn_map, frn=1, parent_frn=5, name="a.txt", attrs=FILE_ATTRIBUTE_NORMAL)
        mft._add_usn_record(frn_map, frn=1, parent_frn=6, name="b.txt", attrs=FILE_ATTRIBUTE_NORMAL)
        self.assertIsInstance(frn_map[1], list)
        self.assertEqual(len(frn_map[1]), 2)

    def test_entries_for_normalizes_both_shapes(self):
        frn_map = {}
        mft._add_usn_record(frn_map, frn=1, parent_frn=5, name="a.txt", attrs=0)
        self.assertEqual(mft._entries_for(frn_map, 1), [(5, "a.txt", 0)])
        mft._add_usn_record(frn_map, frn=1, parent_frn=6, name="b.txt", attrs=0)
        entries = mft._entries_for(frn_map, 1)
        self.assertEqual(len(entries), 2)

    def test_entries_for_missing_frn_is_none(self):
        self.assertIsNone(mft._entries_for({}, 12345))


# ==============================================================================
# Path reconstruction (_walk_to_root / _rel_path_for_name)
# ==============================================================================

def _mk_frn_map(edges):
    """edges: list of (frn, parent_frn, name, attrs) -> build a frn_map."""
    frn_map = {}
    for frn, parent_frn, name, attrs in edges:
        mft._add_usn_record(frn_map, frn, parent_frn, name, attrs)
    return frn_map


class TestPathReconstruction(unittest.TestCase):
    def test_simple_chain_to_root(self):
        # root_frn=5 (volume root) -> 10 "M" -> 20 "Books" -> file 30 "book.txt"
        frn_map = _mk_frn_map([
            (10, 5, "M", FILE_ATTRIBUTE_DIRECTORY),
            (20, 10, "Books", FILE_ATTRIBUTE_DIRECTORY),
        ])
        cache = {}
        errors = []
        rel = mft._rel_path_for_name("book.txt", 20, frn_map, root_frn=5, cache=cache, errors=errors)
        self.assertEqual(rel, os.sep.join(["M", "Books", "book.txt"]))
        self.assertEqual(errors, [])

    def test_root_is_direct_parent(self):
        frn_map = _mk_frn_map([])
        cache = {}
        errors = []
        rel = mft._rel_path_for_name("book.txt", 5, frn_map, root_frn=5, cache=cache, errors=errors)
        self.assertEqual(rel, "book.txt")
        self.assertEqual(errors, [])

    def test_subpath_root_filters_out_sibling_subtree(self):
        # Volume root 5 has two children: 10 "M" (our scan root) and 11 "Other".
        # Files under "Other" must NOT resolve when root_frn == 10. The real
        # volume root record is self-referential (parent == self); include it
        # so the walk can recognize "reached the true FS root, never matched
        # our scan root" instead of mistaking it for an orphan.
        frn_map = _mk_frn_map([
            (5, 5, "$Volume", FILE_ATTRIBUTE_DIRECTORY),
            (10, 5, "M", FILE_ATTRIBUTE_DIRECTORY),
            (11, 5, "Other", FILE_ATTRIBUTE_DIRECTORY),
            (20, 10, "Books", FILE_ATTRIBUTE_DIRECTORY),
            (21, 11, "Stuff", FILE_ATTRIBUTE_DIRECTORY),
        ])
        cache = {}
        errors = []

        under_root = mft._rel_path_for_name("book.txt", 20, frn_map, root_frn=10, cache=cache, errors=errors)
        self.assertEqual(under_root, os.sep.join(["Books", "book.txt"]))

        outside_root = mft._rel_path_for_name("other.txt", 21, frn_map, root_frn=10, cache=cache, errors=errors)
        self.assertIsNone(outside_root)
        # Not being under root is not itself an error condition.
        self.assertEqual(errors, [])

    def test_volume_root_scan_includes_everything(self):
        # When root_frn IS the volume root (5), both subtrees resolve.
        frn_map = _mk_frn_map([
            (10, 5, "M", FILE_ATTRIBUTE_DIRECTORY),
            (11, 5, "Other", FILE_ATTRIBUTE_DIRECTORY),
            (20, 10, "Books", FILE_ATTRIBUTE_DIRECTORY),
        ])
        cache = {}
        errors = []
        rel1 = mft._rel_path_for_name("book.txt", 20, frn_map, root_frn=5, cache=cache, errors=errors)
        rel2 = mft._rel_path_for_name("thing.txt", 11, frn_map, root_frn=5, cache=cache, errors=errors)
        self.assertEqual(rel1, os.sep.join(["M", "Books", "book.txt"]))
        self.assertEqual(rel2, os.sep.join(["Other", "thing.txt"]))

    def test_orphaned_parent_reports_error_and_returns_none(self):
        # Parent FRN 999 is referenced but never itself enumerated.
        frn_map = _mk_frn_map([
            (20, 999, "Books", FILE_ATTRIBUTE_DIRECTORY),
        ])
        cache = {}
        errors = []
        rel = mft._rel_path_for_name("book.txt", 20, frn_map, root_frn=5, cache=cache, errors=errors)
        self.assertIsNone(rel)
        self.assertEqual(len(errors), 1)
        self.assertIn("orphaned", errors[0])

    def test_cycle_is_detected_and_bailed(self):
        # 10 -> 20 -> 10 -> ... (parent chain never reaches root_frn=5)
        frn_map = _mk_frn_map([
            (10, 20, "a", FILE_ATTRIBUTE_DIRECTORY),
            (20, 10, "b", FILE_ATTRIBUTE_DIRECTORY),
        ])
        cache = {}
        errors = []
        rel = mft._rel_path_for_name("file.txt", 10, frn_map, root_frn=5, cache=cache, errors=errors)
        self.assertIsNone(rel)
        self.assertTrue(any("cycle" in e for e in errors))

    def test_self_referential_true_root_without_matching_scan_root(self):
        # Entry 999 is its own parent (the true volume root record), but the
        # scan root_frn (10) is never reached along this chain -- outside scope.
        frn_map = _mk_frn_map([
            (999, 999, "$Volume", FILE_ATTRIBUTE_DIRECTORY),
            (20, 999, "Elsewhere", FILE_ATTRIBUTE_DIRECTORY),
        ])
        cache = {}
        errors = []
        rel = mft._rel_path_for_name("file.txt", 20, frn_map, root_frn=10, cache=cache, errors=errors)
        self.assertIsNone(rel)
        self.assertEqual(errors, [])  # simply outside root, not an error

    def test_long_path_reconstructed_over_260_chars(self):
        # Build ~10 nested directories of 30 chars each -> well over MAX_PATH.
        edges = []
        parent = 5
        for i in range(10):
            frn = 100 + i
            name = "component_%02d_%s" % (i, "x" * 15)
            edges.append((frn, parent, name, FILE_ATTRIBUTE_DIRECTORY))
            parent = frn
        frn_map = _mk_frn_map(edges)
        cache = {}
        errors = []
        rel = mft._rel_path_for_name("deep_file.txt", parent, frn_map, root_frn=5, cache=cache, errors=errors)
        self.assertIsNotNone(rel)
        self.assertGreater(len(rel), 260)
        self.assertTrue(rel.endswith("deep_file.txt"))
        self.assertEqual(errors, [])

    def test_hardlinked_names_resolve_independently(self):
        # Same FRN (77), two names in two different directories under root.
        frn_map = _mk_frn_map([
            (10, 5, "DirA", FILE_ATTRIBUTE_DIRECTORY),
            (11, 5, "DirB", FILE_ATTRIBUTE_DIRECTORY),
            (77, 10, "name_one.txt", FILE_ATTRIBUTE_NORMAL),
            (77, 11, "name_two.txt", FILE_ATTRIBUTE_NORMAL),
        ])
        entries = mft._entries_for(frn_map, 77)
        self.assertEqual(len(entries), 2)
        cache = {}
        errors = []
        rels = sorted(
            mft._rel_path_for_name(name, parent_frn, frn_map, root_frn=5, cache=cache, errors=errors)
            for parent_frn, name, _attrs in entries
        )
        self.assertEqual(rels, sorted([
            os.sep.join(["DirA", "name_one.txt"]),
            os.sep.join(["DirB", "name_two.txt"]),
        ]))

    def test_ambiguous_directory_ancestor_multiple_names_logs_and_uses_first(self):
        # Defensive case: a "directory" FRN unexpectedly has 2 name records
        # (should never happen on real NTFS -- dirs can't be hardlinked).
        frn_map = _mk_frn_map([
            (10, 5, "FirstName", FILE_ATTRIBUTE_DIRECTORY),
            (10, 5, "SecondName", FILE_ATTRIBUTE_DIRECTORY),
        ])
        cache = {}
        errors = []
        rel = mft._rel_path_for_name("file.txt", 10, frn_map, root_frn=5, cache=cache, errors=errors)
        self.assertEqual(rel, os.sep.join(["FirstName", "file.txt"]))
        self.assertTrue(any("expected to be a single-name directory ancestor" in e for e in errors))


# ==============================================================================
# Small path helpers
# ==============================================================================

@unittest.skipUnless(
    os.name == "nt",
    "drive-letter helpers depend on ntpath splitdrive/abspath semantics; "
    "the product code guards them behind the Windows-only call path",
)
class TestPathHelpers(unittest.TestCase):
    def test_normalize_root_bare_drive_letter(self):
        result = mft._normalize_root("D:")
        self.assertTrue(result.upper().startswith("D:\\"))

    def test_split_volume(self):
        self.assertEqual(mft._split_volume(r"D:\M\Books"), "D:")
        self.assertEqual(mft._split_volume(r"D:\\"), "D:")

    def test_split_volume_no_drive_raises(self):
        with self.assertRaises(mft.MftError):
            mft._split_volume(r"\\some\unc\path")

    def test_to_extended_path_adds_prefix(self):
        result = mft._to_extended_path(r"D:\M\Books")
        self.assertTrue(result.startswith("\\\\?\\"))

    def test_to_extended_path_idempotent(self):
        already = "\\\\?\\D:\\M\\Books"
        self.assertEqual(mft._to_extended_path(already), already)

    def test_to_extended_path_unc(self):
        result = mft._to_extended_path(r"\\server\share\file.txt")
        self.assertTrue(result.startswith("\\\\?\\UNC\\"))


# ==============================================================================
# stat_confirm: real (unelevated) hardlink creation via os.link
# ==============================================================================

class TestStatConfirmHardlinkGroup(unittest.TestCase):
    """os.link() and os.stat() don't require elevation, unlike raw MFT access,
    so this exercises the *actual* filesystem behavior our stat_confirm
    fallback relies on -- not just the MFT-representation assumption."""

    def setUp(self):
        import tempfile
        self.tmp = tempfile.mkdtemp(prefix="test_mft_hardlink_")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    @unittest.skipIf(sys.platform != "win32", "hardlink creation semantics tested on Windows target platform")
    def test_confirms_real_hardlink_group(self):
        original = os.path.join(self.tmp, "original.txt")
        linked = os.path.join(self.tmp, "linked.txt")
        with open(original, "w") as f:
            f.write("shared content")
        os.link(original, linked)

        confirmed, group_id = mft._confirm_hardlink_group(
            self.tmp, ["original.txt", "linked.txt"], errors=[]
        )
        self.assertTrue(confirmed)
        self.assertIsNotNone(group_id)

        st_a = os.stat(original)
        st_b = os.stat(linked)
        self.assertEqual(st_a.st_ino, st_b.st_ino)
        self.assertGreater(st_a.st_nlink, 1)
        self.assertEqual(group_id, st_a.st_ino)

    @unittest.skipIf(sys.platform != "win32", "hardlink creation semantics tested on Windows target platform")
    def test_rejects_non_hardlinked_files(self):
        a = os.path.join(self.tmp, "a.txt")
        b = os.path.join(self.tmp, "b.txt")
        with open(a, "w") as f:
            f.write("one")
        with open(b, "w") as f:
            f.write("two")

        errors = []
        confirmed, group_id = mft._confirm_hardlink_group(self.tmp, ["a.txt", "b.txt"], errors)
        self.assertFalse(confirmed)
        self.assertIsNone(group_id)
        self.assertTrue(errors)

    def test_missing_file_reports_error(self):
        errors = []
        confirmed, group_id = mft._confirm_hardlink_group(self.tmp, ["does_not_exist.txt"], errors)
        self.assertFalse(confirmed)
        self.assertIsNone(group_id)
        self.assertTrue(errors)


# ==============================================================================
# Elevation / platform guarding
# ==============================================================================

class TestNotWindowsGuard(unittest.TestCase):
    def test_mft_scan_raises_when_not_windows(self):
        original = mft._IS_WINDOWS
        mft._IS_WINDOWS = False
        try:
            with self.assertRaises(mft.MftNotSupportedError):
                mft.mft_scan("D:\\whatever")
        finally:
            mft._IS_WINDOWS = original

    def test_is_mft_available_false_when_not_windows(self):
        original = mft._IS_WINDOWS
        mft._IS_WINDOWS = False
        try:
            self.assertFalse(mft.is_mft_available("D:"))
        finally:
            mft._IS_WINDOWS = original

    def test_import_does_not_crash_module_reference(self):
        # The module already imported successfully at collection time (this
        # file's own import statement) -- if guarding at import time were
        # broken, pytest collection itself would have failed already.
        self.assertIn(mft._IS_WINDOWS, (True, False))


@pytest.mark.skipif(sys.platform != "win32", reason="raw volume access is Windows-only")
def test_mft_access_denied_unelevated():
    """The whole point of this test: on a normal (non-elevated) developer
    session, opening the raw volume device must fail cleanly with
    MftAccessDenied rather than crashing or hanging -- callers use this to
    fall back to the directory-walk backend."""
    is_admin = False
    try:
        import ctypes
        is_admin = bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        is_admin = False

    if is_admin:
        pytest.skip("running elevated -- access-denied path cannot be exercised here")

    drive = os.path.splitdrive(os.path.abspath(__file__))[0]  # e.g. "C:"

    with pytest.raises(mft.MftAccessDenied):
        mft._open_volume_device(drive)

    # is_mft_available() must swallow the same failure and report False.
    assert mft.is_mft_available(drive) is False


if __name__ == "__main__":
    unittest.main()
