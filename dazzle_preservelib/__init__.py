"""dazzle-preservelib -- the DazzleLib stack's L3 manifest + operations library.

It owns the *preserve* domain: the ``PreserveManifest`` (a content-hash record
of what was copied, with DAG lineage), transactional **copy / move / restore /
verify** operations, destination-conflict resolution, link-handling **policy**
(``LinkHandlingMode`` + destination-relative cycle analysis), and verification.

The boundary (STACK-MAP, layers B/L0/L1/L2/L3): this library owns the
ORCHESTRATION and the POLICY. The primitives it stands on delegate DOWN --
file/link mechanics to ``dazzle-filekit`` (L1), UNC/drive identity to
``unctools`` (L0), the ``.dazzlelink`` record bridge to ``dazzle-linklib`` (L2,
via the optional ``[dazzlelink]`` extra). Contracts come from ``dazzle-lib``
(B). It never reimplements a lower layer's primitive.

Status: **P3 extraction in progress (pre-release).** The manifest + operations
are being imported from the ``preserve`` project (collapsing three drifting
copies into one canonical home) and the filesystem primitives rewired to
delegate to filekit. The first functional release is **0.8.0** (continuing the
preserve lineage). Tracked on the roadmap (issue #2).

License: MIT (whole stack; STACK-MAP D11). Architecture contract:
https://github.com/DazzleLib/.github/blob/main/docs/STACK-MAP.md
"""

from ._version import PIP_VERSION, __app_name__, __version__

__all__ = ["__version__", "__app_name__", "PIP_VERSION"]
