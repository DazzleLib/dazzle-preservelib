# Roadmap

The living roadmap is tracked in **[Issue #2](https://github.com/DazzleLib/dazzle-preservelib/issues/2)**.

`dazzle-preservelib` is **L3** of the [DazzleLib stack](https://github.com/DazzleLib/.github/blob/main/docs/STACK-MAP.md):
the manifest + operations orchestration library.

| Phase | Theme | Status |
|---|---|---|
| Scaffold | Repo, MIT license, charter, day-one guards | done |
| P3 extraction | Import canonical `preservelib`; delegate links/metadata/hashing/disk-space down to `dazzle-filekit`; kill `sys.path` hacks (V5); collapse the 3 copies (V6); fix the `is_junction` bug (V7); ship 0.8.0 | next |
| P3 (same window) | `preserve` CLI thins to a consumer of this library | planned |
| P4 | ghtraf / safedel / csb migrate to the published package (V13/V14) | planned |

See the architecture contract (STACK-MAP) for the frozen L3 boundary and the
P3 design (extraction DWP, decisions R7/D-DL/D-ERR).
