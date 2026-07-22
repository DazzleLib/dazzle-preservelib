# Platform Support

`dazzle-preservelib` is pure-Python orchestration. Platform-specific behavior
(junction vs symlink semantics, NTFS metadata/ACLs, UNC paths) is delegated
down the stack to `dazzle-filekit` (L1) and `unctools` (L0), which carry their
own platform matrices.

| Platform | Status |
|----------|--------|
| Windows 10/11 | Tested (primary development target) |
| Linux | Tested |
| macOS | Expected to work |

Python: **3.9+**.

The manifest format, operations, conflict resolution, and verification are
platform-independent; the link/metadata mechanics they invoke inherit
filekit's platform support.

## linkmirror specifics

The `linkmirror` engine (0.9.0) is cross-platform in its walk/plan/verify
logic (POSIX mirrors symlinks and hardlink groups; validated on Linux --
the engine suite runs green under WSL/ubuntu in addition to Windows).
Timestamp semantics differ by platform: link atime/mtime restore uses
`os.utime(follow_symlinks=False)` where supported, while **creation time is
capturable and restorable only on Windows** -- Linux's `st_ctime` is inode
change time, so `verify_mirror` compares only `modified` on POSIX (and says
so in its notes). Two pieces are Windows-only:

- **Junction handling** (scan classification via reparse tags, recreation via
  filekit's `create_junction_raw`) -- junctions do not exist elsewhere.
- **`linkmirror.mft`** (MFT/USN volume enumeration) -- NTFS-only and requires
  an **elevated** process to open the volume device; unelevated callers get
  `MftAccessDenied` and fall back to the portable walk scanner. Memory note:
  the FRN map holds the whole volume's records in RAM (roughly 260-300
  bytes/record; ~13-15 GB at 50M records).
