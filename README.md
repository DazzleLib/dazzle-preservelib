# dazzle-preservelib

[![PyPI](https://img.shields.io/pypi/v/dazzle-preservelib?color=green)](https://pypi.org/project/dazzle-preservelib/)
[![Release Date](https://img.shields.io/github/release-date/DazzleLib/dazzle-preservelib?color=green)](https://github.com/DazzleLib/dazzle-preservelib/releases)
[![Python 3.9+](https://img.shields.io/badge/python-3.9+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![Platform](https://img.shields.io/badge/platform-Windows%20%7C%20Linux%20%7C%20macOS%20%7C%20BSD-lightgrey.svg)](docs/platform-support.md)

**Manifest + operations library** -- the **L3** orchestration layer of the
[DazzleLib stack](https://github.com/DazzleLib/.github/blob/main/docs/STACK-MAP.md).

It owns the *preserve* domain: the `PreserveManifest` (a content-hash record of
what was copied, with DAG lineage), transactional **copy / move / restore /
verify** operations, destination-conflict resolution, link-handling **policy**,
and **link-mirror reconciliation** (`linkmirror`). It delegates the actual
filesystem mechanics down the stack.

## What this owns (and what it delegates)

| Concern | Layer |
|---|---|
| Manifest, operations, conflict resolution, link **policy**, verification | **dazzle-preservelib (L3, this lib)** |
| File/link mechanics (create/detect/read/remove, copy, hash, metadata, disk-space) | `dazzle-filekit` (L1) |
| UNC <-> drive identity | `unctools` (L0) |
| Shared Protocols / TypedDicts / exception root | `dazzle-lib` (B) |
| `.dazzlelink` record bridge (optional `[dazzlelink]` extra) | `dazzle-linklib` (L2) |

The orchestration and policy live here; the primitives they stand on belong to
the layers below. This library is the destination of the **P3 extraction** that
collapses three drifting `preservelib` copies (preserve, ghtraf, safedel) into
one canonical home.

## linkmirror -- reconcile links onto a mirrored tree

File-level mirrors (robocopy, Beyond Compare, `preserve COPY`) carry the files
but frequently drop the **links**: symlinks and junctions vanish, hardlink
groups arrive as independent duplicates. `dazzle_preservelib.linkmirror` is the
mirror-scoped implementation of `LinkHandlingMode.RECREATE`
([preserve#48](https://github.com/DazzleTools/preserve/issues/48) Phase 2,
narrowed to the case that needs no lineage tracking: the destination already
holds the copied files, so link identity is *same relative path*).

```python
from dazzle_preservelib.linkmirror import (
    walk_scan, build_plan, apply_plan, verify_mirror,
)

manifest = walk_scan(r"D:\data")                # or mft.mft_scan (elevated, fast)
plan = build_plan(manifest, r"B:\data")         # diff against the mirror
result = apply_plan(plan, dry_run=False)        # additive-only: creates links,
                                                # restores link + parent times
report = verify_mirror(manifest, r"B:\data")    # byte/tick parity proof
```

Fidelity contract: targets are recreated **verbatim** (relative targets
unresolved, intentionally-broken targets unrepaired, `\\?\` forms kept) with
the link's own timestamps at 100ns precision; existing destination entries are
never modified (mismatches are reported as conflicts); re-runs are idempotent.
Target rewriting (e.g. `D:\` -> `B:\` for drive retirement) is an explicit
pluggable policy (`make_prefix_rewrite_policy`). Hardlink reconciliation is
opt-in and sha256-guarded. The `dz link-mirror` tool (dazzlecmd) is the thin
CLI over this engine.

Scanner backends: a portable `os.scandir` walk, and a Windows MFT/USN
enumeration (`linkmirror.mft`, requires elevation) that inventories
multi-million-record volumes in minutes -- field-proven mirroring 2,644 links
off a failing 51M-record drive with nanosecond-identical timestamps and zero
additional wear.

## The stack

| Layer | Library | Role |
|---|---|---|
| B | [dazzle-lib](https://github.com/DazzleLib/dazzle-lib) | bedrock contracts |
| L0 | [unctools](https://github.com/DazzleLib/UNCtools) | path identity |
| L1 | [dazzle-filekit](https://github.com/DazzleLib/dazzle-filekit) | filesystem primitives |
| L2 | [dazzle-linklib](https://github.com/DazzleLib/dazzle-linklib) | link record + resolver |
| L3 | **dazzle-preservelib** (this) | manifest + operations |
| ⊥ | [dazzle-treelib](https://github.com/DazzleLib/dazzle-tree-lib) | traversal engine |

## Status

**Functional (0.8.0 shipped the P3 extraction; 0.9.0 added `linkmirror`).**
The manifest + operations API is locked (see
[docs/api-stability.md](docs/api-stability.md)); the `preserve` CLI consumes
this library. The `linkmirror` package is **provisional** in 0.9.x (API may
still refine; locks at 0.10). See the
[Roadmap](https://github.com/DazzleLib/dazzle-preservelib/issues/2).

## Installation

```bash
pip install dazzle-preservelib
```

### From source

```bash
git clone https://github.com/DazzleLib/dazzle-preservelib.git
cd dazzle-preservelib
pip install -e ".[dev]"
```

## Development

```bash
python -m venv .venv
source .venv/bin/activate   # or .venv\Scripts\activate on Windows
pip install -e ".[dev]"

# Run tests
python -m pytest tests/ -v

# Install git hooks
bash scripts/install-hooks.sh
```

## License

MIT. See [LICENSE](LICENSE) for details. The whole DazzleLib stack is MIT
(STACK-MAP D11).
