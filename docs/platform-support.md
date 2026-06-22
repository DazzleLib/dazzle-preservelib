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
