"""linkmirror -- reconcile NTFS links between a source tree and a mirrored copy.

Implements the mirror-scoped portion of ``LinkHandlingMode.RECREATE``
(DazzleTools/preserve#48 Phase 2): the destination tree already holds the
regular files (copied by robocopy/Beyond Compare/preserve); this package
finds every link on the source, diffs against the destination, and recreates
missing links identically -- same kind, verbatim target, link-own timestamps
-- without touching any existing file.

Layout:
    records  -- LinkRecord/LinkManifest (scanner output contract)
    scan     -- directory-walk scanner backend (portable, unelevated)
    mft      -- MFT/USN enumeration backend (Windows, elevated, fast)
    plan     -- diff -> MirrorPlan; apply -> MirrorResult
    verify   -- source/destination link parity report
"""

from .records import (  # noqa: F401
    KIND_HARDLINK,
    KIND_JUNCTION,
    KIND_OTHER_REPARSE,
    KIND_SYMLINK,
    LinkManifest,
    LinkRecord,
)
from .scan import walk_scan  # noqa: F401
from .plan import (  # noqa: F401
    ACTION_CONFLICT,
    ACTION_CREATE,
    ACTION_EXCLUDED,
    ACTION_HARDLINK_LINK,
    ACTION_HARDLINK_REPORT,
    ACTION_PARENT_MISSING,
    ACTION_SATISFIED,
    MirrorPlan,
    MirrorResult,
    PlanItem,
    apply_plan,
    build_plan,
    make_prefix_rewrite_policy,
    verbatim_policy,
)
from .verify import VerifyIssue, VerifyReport, verify_mirror  # noqa: F401
