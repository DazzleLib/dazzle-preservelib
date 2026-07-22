"""End-to-end engine tests for dazzle_preservelib.linkmirror.

Fixture: a source tree containing every link shape the D:->B: migration must
survive (relative/absolute/broken file+dir symlinks, healthy+broken
junctions, a hardlink group), and a destination tree holding ONLY the
regular files/dirs -- exactly what a robocopy/Beyond Compare mirror leaves
behind (measured on the real drives, 2026-07-20: links silently dropped,
hardlinks materialized as independent duplicate files).

Acceptance checks covered (2026-07-20 DWP, Stage 4):
  1  round-trip fidelity incl. broken + relative targets
  2  junction timestamps land on the junction (via filekit fix)
  3  broken DIRECTORY symlink keeps directory kind
  4  no-touch guarantee (only new links + restored parents)
  5  idempotency (second run all-satisfied, applies as no-op)
  6  conflict safety (wrong link / plain file in the way -> reported, kept)
  7  verify honesty (ctime caveat note present)
  9  hardlink 'report' default changes nothing; 'recreate' hash-guards
"""

import os
import platform

import pytest

from dazzle_preservelib.linkmirror import (
    ACTION_CONFLICT,
    ACTION_CREATE,
    ACTION_HARDLINK_REPORT,
    ACTION_SATISFIED,
    KIND_HARDLINK,
    KIND_JUNCTION,
    KIND_SYMLINK,
    apply_plan,
    build_plan,
    make_prefix_rewrite_policy,
    verbatim_policy,
    verify_mirror,
    walk_scan,
)

IS_WINDOWS = platform.system() == "Windows"
SEP = "\\" if IS_WINDOWS else "/"

EPOCH_2021 = 1609459200.0  # 2021-01-01


def _symlinks_available(tmp_path):
    t = tmp_path / "_probe_t.txt"
    t.write_text("x", encoding="utf-8")
    try:
        os.symlink(str(t), str(tmp_path / "_probe_l"))
    except (OSError, NotImplementedError):
        return False
    os.unlink(str(tmp_path / "_probe_l"))
    return True


def _rel(*parts):
    return SEP.join(parts)


@pytest.fixture()
def trees(tmp_path):
    """Build source (with links) and dest (files only) trees."""
    if not _symlinks_available(tmp_path):
        pytest.skip("symlink creation not permitted")
    src = tmp_path / "src"
    dst = tmp_path / "dst"
    for base in (src, dst):
        (base / "data" / "real_dir").mkdir(parents=True)
        (base / "data" / "real_dir" / "inner.txt").write_text("inner", encoding="utf-8")
        (base / "data" / "file2.txt").write_text("payload2", encoding="utf-8")
        (base / "links").mkdir()
        (base / "hl").mkdir()

    # --- links on the SOURCE only ---
    links = src / "links"
    os.symlink(_rel("..", "data", "file2.txt"), str(links / "rel_file_link"))
    os.symlink(
        _rel("..", "data", "real_dir"), str(links / "rel_dir_link"),
        target_is_directory=True,
    )
    os.symlink(
        _rel("data", "missing.txt"), str(links / "broken_file_link")
    )
    os.symlink(
        _rel("no", "such", "dir"), str(links / "broken_dir_link"),
        target_is_directory=True,
    )
    os.symlink(str(src / "data" / "file2.txt"), str(links / "abs_file_link"))

    if IS_WINDOWS:
        from dazzle_filekit.links import create_junction_raw
        (src / "junc").mkdir()
        (dst / "junc").mkdir()
        assert create_junction_raw(
            str(src / "data" / "real_dir"), str(src / "junc" / "junc_ok")
        )
        assert create_junction_raw(
            str(src / "data" / "gone_dir"), str(src / "junc" / "junc_broken")
        )

    # hardlink group on source; independent duplicate files on dest
    (src / "hl" / "a.txt").write_text("hardlinked-content", encoding="utf-8")
    os.link(str(src / "hl" / "a.txt"), str(src / "hl" / "b.txt"))
    (dst / "hl" / "a.txt").write_text("hardlinked-content", encoding="utf-8")
    (dst / "hl" / "b.txt").write_text("hardlinked-content", encoding="utf-8")

    return src, dst


def _snapshot_tree(root):
    """path -> (is_dir, size, mtime_ns) for every pre-existing entry.

    Note: a directory's st_size is NTFS index bookkeeping and legitimately
    grows when link nodes are added inside it; the no-touch guarantee is
    about FILE content/size and everyone's timestamps.
    """
    snap = {}
    for dirpath, dirnames, filenames in os.walk(root):
        for name in filenames + dirnames:
            p = os.path.join(dirpath, name)
            st = os.lstat(p)
            snap[os.path.relpath(p, root)] = (
                name in dirnames, st.st_size, st.st_mtime_ns
            )
    return snap


def test_scan_finds_every_link_kind(trees):
    src, _ = trees
    m = walk_scan(str(src))
    kinds = {}
    for r in m.records:
        kinds.setdefault(r.kind, []).append(r.rel_path)
    assert len(kinds.get(KIND_SYMLINK, [])) == 5
    assert len(kinds.get(KIND_HARDLINK, [])) == 2
    if IS_WINDOWS:
        assert len(kinds.get(KIND_JUNCTION, [])) == 2
    # verbatim targets: broken relative preserved unresolved
    broken = next(r for r in m.records
                  if r.rel_path == _rel("links", "broken_file_link"))
    assert broken.target == _rel("data", "missing.txt")
    # dir-kind captured from the link NODE's attributes, not from the
    # (missing) target -- POSIX symlinks are kindless, so False there
    broken_dir = next(r for r in m.records
                      if r.rel_path == _rel("links", "broken_dir_link"))
    assert broken_dir.is_dir == IS_WINDOWS
    assert not m.errors


def test_dry_run_changes_nothing(trees):
    src, dst = trees
    before = _snapshot_tree(str(dst))
    m = walk_scan(str(src))
    plan = build_plan(m, str(dst))
    result = apply_plan(plan, dry_run=True)
    assert result.dry_run is True
    assert len(result.created) >= 5
    assert _snapshot_tree(str(dst)) == before


def test_apply_recreates_links_faithfully_and_restores_parents(trees):
    src, dst = trees
    before = _snapshot_tree(str(dst))
    m = walk_scan(str(src))
    plan = build_plan(m, str(dst))
    result = apply_plan(plan, dry_run=False)
    assert result.errors == []

    # Check 1: byte-identical readlink round-trip for every symlink/junction
    for rec in m.records:
        if rec.kind not in (KIND_SYMLINK, KIND_JUNCTION):
            continue
        dest = os.path.join(str(dst), rec.rel_path)
        assert os.path.lexists(dest), rec.rel_path
        assert os.readlink(dest) == rec.target, rec.rel_path
        # link-own timestamps restored (10us tolerance: float/pywintypes path)
        st = os.lstat(dest)
        assert abs(st.st_mtime_ns - rec.modified_ns) < 10_000_000, rec.rel_path

    # Check 3: broken DIRECTORY symlink kept directory kind (Windows)
    if IS_WINDOWS:
        attrs = os.lstat(
            os.path.join(str(dst), _rel("links", "broken_dir_link"))
        ).st_file_attributes
        assert attrs & 0x10  # FILE_ATTRIBUTE_DIRECTORY

    # Check 4: nothing pre-existing changed (parents restored to original).
    # File sizes must be identical; directory sizes are NTFS index
    # bookkeeping and may grow when links are added inside; every
    # pre-existing entry's mtime must be back to its original value.
    after = _snapshot_tree(str(dst))
    for rel, (is_dir, size, mtime) in before.items():
        assert rel in after
        a_is_dir, a_size, a_mtime = after[rel]
        assert a_is_dir == is_dir, f"{rel} kind changed"
        if not is_dir:
            assert a_size == size, f"{rel} size changed"
        assert a_mtime == mtime, f"{rel} mtime changed"

    # Check 9: hardlink report mode changed nothing about the dupes
    st_a = os.lstat(os.path.join(str(dst), "hl", "a.txt"))
    st_b = os.lstat(os.path.join(str(dst), "hl", "b.txt"))
    assert st_a.st_ino != st_b.st_ino  # still independent files


def test_idempotent_second_run(trees):
    src, dst = trees
    m = walk_scan(str(src))
    apply_plan(build_plan(m, str(dst)), dry_run=False)
    # second pass: everything satisfied, nothing created
    plan2 = build_plan(walk_scan(str(src)), str(dst))
    counts = plan2.counts()
    assert counts.get(ACTION_CREATE, 0) == 0
    assert counts.get(ACTION_CONFLICT, 0) == 0
    result2 = apply_plan(plan2, dry_run=False)
    assert result2.created == []
    assert result2.errors == []


def test_conflicts_are_reported_and_untouched(trees):
    src, dst = trees
    # Pre-place a WRONG link and a plain file where links should go
    os.symlink("wrong-target", str(dst / "links" / "rel_file_link"))
    (dst / "links" / "broken_file_link").write_text("i am a file", encoding="utf-8")

    m = walk_scan(str(src))
    plan = build_plan(m, str(dst))
    conflicts = {os.path.basename(i.dest_path)
                 for i in plan.by_action(ACTION_CONFLICT)}
    assert conflicts == {"rel_file_link", "broken_file_link"}

    result = apply_plan(plan, dry_run=False)
    assert len(result.conflicts) == 2
    # untouched:
    assert os.readlink(str(dst / "links" / "rel_file_link")) == "wrong-target"
    assert (dst / "links" / "broken_file_link").read_text(encoding="utf-8") == "i am a file"


def test_hardlink_recreate_merges_dupes_and_hash_guards(trees):
    src, dst = trees
    m = walk_scan(str(src))
    plan = build_plan(m, str(dst), hardlink_mode="recreate")
    result = apply_plan(plan, dry_run=False)
    assert result.errors == []
    st_a = os.lstat(os.path.join(str(dst), "hl", "a.txt"))
    st_b = os.lstat(os.path.join(str(dst), "hl", "b.txt"))
    assert (st_a.st_dev, st_a.st_ino) == (st_b.st_dev, st_b.st_ino)
    assert (dst / "hl" / "b.txt").read_text(encoding="utf-8") == "hardlinked-content"

    # hash guard: content mismatch refuses replacement
    os.unlink(str(dst / "hl" / "b.txt"))
    (dst / "hl" / "b.txt").write_text("DIFFERENT", encoding="utf-8")
    plan2 = build_plan(walk_scan(str(src)), str(dst), hardlink_mode="recreate")
    result2 = apply_plan(plan2, dry_run=False)
    assert any("refusing to replace" in e for e in result2.errors)
    assert (dst / "hl" / "b.txt").read_text(encoding="utf-8") == "DIFFERENT"


def test_verify_mirror_reports_and_notes(trees):
    src, dst = trees
    m = walk_scan(str(src))
    # before apply: verify must flag all links missing
    report0 = verify_mirror(m, str(dst))
    assert not report0.ok
    assert any("change-time" in n for n in report0.notes)  # check 7

    apply_plan(build_plan(m, str(dst)), dry_run=False)
    report1 = verify_mirror(m, str(dst))
    # hardlink topology still un-merged in report mode -> exactly 1 issue
    hardlink_issues = [i for i in report1.issues if i.problem == "hardlink"]
    other_issues = [i for i in report1.issues if i.problem != "hardlink"]
    assert other_issues == []
    assert len(hardlink_issues) == 1

    apply_plan(
        build_plan(m, str(dst), hardlink_mode="recreate"), dry_run=False
    )
    report2 = verify_mirror(m, str(dst))
    assert report2.ok, [f"{i.rel_path}: {i.problem} {i.detail}"
                        for i in report2.issues]


def test_prefix_rewrite_policy_forms():
    p = make_prefix_rewrite_policy("D:\\", "B:\\")
    assert p(r"D:\M\x.txt") == r"B:\M\x.txt"
    assert p("\\\\?\\D:\\M\\x.txt") == "\\\\?\\B:\\M\\x.txt"
    assert p("\\??\\D:\\M\\x.txt") == "\\??\\B:\\M\\x.txt"
    assert p(r"d:\lower.txt") == r"B:\lower.txt"
    assert p(r"C:\other.txt") == r"C:\other.txt"      # non-matching kept
    assert p(r"..\relative.txt") == r"..\relative.txt"  # relative kept
    assert verbatim_policy(r"any\thing") == r"any\thing"
