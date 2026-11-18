"""Same-name guard (0.9.1): one call never overwrites a destination it just
placed, and a move never deletes a source whose bytes are not at the
destination.

Before 0.9.1, `dz f-mv -f a/x.txt b/x.txt dest/` (flat style, overwrite)
copied a to dest/x.txt, copied b over it, then deleted BOTH sources: a's
content existed nowhere. Every test here runs in tmp_path, so the deletes a
broken guard would perform can only reach throwaway files.
"""
import pytest

from dazzle_preservelib import operations
from dazzle_preservelib.operations import copy_operation, move_operation


def _two_same_named(tmp_path):
    a = tmp_path / "a" / "x.txt"
    b = tmp_path / "b" / "x.txt"
    for p, data in ((a, b"AAA"), (b, b"BBB")):
        p.parent.mkdir(parents=True)
        p.write_bytes(data)
    dest = tmp_path / "dest"
    dest.mkdir()
    return a, b, dest


def _opts(policy, **extra):
    return {"path_style": "flat", "on_conflict": policy, **extra}


@pytest.mark.parametrize("policy", ["overwrite", "newer", "larger"])
def test_move_refuses_to_overwrite_what_it_just_placed(tmp_path, policy):
    a, b, dest = _two_same_named(tmp_path)
    if policy == "newer":
        import os, time
        later = time.time() + 60
        os.utime(b, (later, later))          # b would win a "newer" contest
    if policy == "larger":
        b.write_bytes(b"BBBB")               # b would win a "larger" contest

    result = move_operation([a, b], dest, options=_opts(policy))

    assert (dest / "x.txt").read_bytes() == b"AAA"
    assert not a.exists()                    # a was moved
    assert b.exists()                        # b refused, kept in place
    assert any(entry[0] == str(b) for entry in result.failed), result.failed


def test_copy_overwrite_keeps_the_first_copy(tmp_path):
    a, b, dest = _two_same_named(tmp_path)

    result = copy_operation([a, b], dest, options=_opts("overwrite"))

    assert (dest / "x.txt").read_bytes() == b"AAA"
    assert a.exists() and b.exists()
    assert result.failed


def test_rename_places_both(tmp_path):
    a, b, dest = _two_same_named(tmp_path)

    move_operation([a, b], dest, options=_opts("rename"))

    assert sorted(p.read_bytes() for p in dest.iterdir()) == [b"AAA", b"BBB"]
    assert not a.exists() and not b.exists()


def test_skip_keeps_the_second_source(tmp_path):
    a, b, dest = _two_same_named(tmp_path)

    move_operation([a, b], dest, options=_opts("skip"))

    assert (dest / "x.txt").read_bytes() == b"AAA"
    assert not a.exists() and b.read_bytes() == b"BBB"


def test_fail_stops_and_keeps_the_second_source(tmp_path):
    a, b, dest = _two_same_named(tmp_path)

    result = move_operation([a, b], dest, options=_opts("fail"))

    assert (dest / "x.txt").read_bytes() == b"AAA"
    assert not a.exists() and b.read_bytes() == b"BBB"
    assert any(entry[0] == str(b) for entry in result.failed), result.failed


def test_overwriting_a_preexisting_file_still_works(tmp_path):
    """-f keeps its documented meaning for files that were there before."""
    a, _b, dest = _two_same_named(tmp_path)
    (dest / "x.txt").write_bytes(b"OLD")

    move_operation([a], dest, options=_opts("overwrite"))

    assert (dest / "x.txt").read_bytes() == b"AAA"
    assert not a.exists()


def test_move_keeps_source_when_destination_changed_after_verify(tmp_path, monkeypatch):
    """The delete gate re-checks the destination's bytes, whatever path led
    to them changing after the copy verified."""
    a, _b, dest = _two_same_named(tmp_path)
    real_copy = operations.copy_operation

    def copy_then_clobber(*args, **kwargs):
        res = real_copy(*args, **kwargs)
        (dest / "x.txt").write_bytes(b"CLOBBERED")
        return res

    monkeypatch.setattr(operations, "copy_operation", copy_then_clobber)

    result = move_operation([a], dest, options=_opts("overwrite"))

    assert a.read_bytes() == b"AAA"
    assert result.retained_sources


def test_move_to_self_is_never_deleted(tmp_path):
    """A source whose computed destination is itself is not removed."""
    src_dir = tmp_path / "d"
    src_dir.mkdir()
    f = src_dir / "x.txt"
    f.write_bytes(b"SELF")

    # The pre-flight check flags moving onto the source's own folder; this
    # test is about what happens past it, so acknowledge the warning.
    move_operation([f], src_dir,
                   options=_opts("overwrite", ignore_space_warning=True))

    assert f.read_bytes() == b"SELF"
