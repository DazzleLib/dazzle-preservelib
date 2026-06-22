# API Stability

`dazzle-preservelib` is **L3** of the DazzleLib stack. The `preserve` CLI,
safedel, ghtraf, and csb's recorded-fidelity work all build on its manifest +
operations API, so its public surface **locks at the first functional release
(0.8.0)**. The canary test `tests/test_import_stability.py` enumerates the
locked symbols and fails if any disappears or moves.

## Policy

1. **Locked symbols never vanish silently.** Removing/renaming one follows the
   stack's shim policy (STACK-MAP Rule 6): a temporary NOISY shim
   (`DeprecationWarning` naming the new home + removal version), registered in
   the alias register, removed on schedule.
2. **Manifest schema only gains keys.** The `PreserveManifest` JSON schema
   evolves by addition + migration; removing/re-typing a field is a breaking
   change requiring a schema-version bump + a CHANGELOG migration note.
3. **Additions follow the rule of two.**
4. **Boundary discipline (STACK-MAP D6):** this library does not reimplement
   filesystem mechanics (L1 `dazzle-filekit`), UNC identity (L0 `unctools`), or
   the `.dazzlelink` record (L2 `dazzle-linklib`). Pulling one of those into L3
   is an architecture change, not a code-review comment.

## Locked surface (scaffold)

| Module | Symbols |
|---|---|
| `dazzle_preservelib` (re-exports) | `__version__`, `__app_name__`, `PIP_VERSION` |

The manifest/operations/verification/destination surface (`PreserveManifest`,
`copy_operation` / `move_operation` / `restore_operation` / `verify_operation`,
`scan_destination`, the verification types, link **policy**) joins this table at
the **0.8.0** extraction release.

## Upstream dependencies

- `dazzle-lib` (B): `Serializable`, `DazzleDataMixin`, `PreserveError`.
- `dazzle-filekit` (L1): link create/detect/read/**remove**, metadata
  collect/apply, hashing/verification, disk-space (the OS mechanics L3 delegates
  to).
- `dazzle-linklib` (L2): the `.dazzlelink` record, via the optional
  `[dazzlelink]` extra (hard named error when absent).

## Known consumers

| Consumer | Since |
|---|---|
| preserve CLI (DazzleTools) | stack phase P3 |
| safedel (dazzlecmd) | stack phase P4 |
| ghtraf | stack phase P4 |
| csb (recorded-fidelity / Track C) | after 0.8.0 |
