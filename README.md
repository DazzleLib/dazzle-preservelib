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
verify** operations, destination-conflict resolution, and link-handling
**policy**. It delegates the actual filesystem mechanics down the stack.

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

**Pre-release (P3 extraction in progress).** This repository is being populated
by the P3 extraction of `preservelib` from the `preserve` project: the manifest
+ operations move here as a standalone library, the filesystem primitives
delegate down to `dazzle-filekit`, and the `preserve` CLI thins to a consumer.
The first functional release will be **0.8.0** (continuing the preserve lineage).
See the [Roadmap](https://github.com/DazzleLib/dazzle-preservelib/issues/2).

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
