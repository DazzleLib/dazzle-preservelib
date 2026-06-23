"""Round-trip + conservation tests for the dazzlelink bridge (P3 5b).

Guards the meshing DWP's acceptance checks
(private/claude/2026-06-23__...dazzlelink-bridge-meshing-with-linklib.md):
- AC-B1: create_dazzlelink writes a .dazzlelink that reads back with the same
  original/target/mode (via the lib's import_link); restore returns the original.
- AC-B3: the bridge gates on is_available() (the [dazzlelink] extra).
- AC-B5: the bridge writes records via `export_link`, NOT the lib's `create_link`
  (which makes an OS symlink -- the same-name collision).
"""
import inspect
import sys

from pathlib import Path

import pytest

from dazzle_preservelib import dazzlelink as bridge

pytestmark = pytest.mark.skipif(
    not bridge.is_available(),
    reason="dazzle-linklib ([dazzlelink] extra) not installed",
)


def test_create_and_read_back_roundtrip(tmp_path):
    src = tmp_path / "orig" / "photo.png"
    src.parent.mkdir(parents=True)
    src.write_bytes(b"pixels")
    dest = tmp_path / "preserved" / "photo.png"
    dest.parent.mkdir(parents=True)
    dest.write_bytes(b"pixels")
    dl_dir = tmp_path / "links"

    dl_path = bridge.create_dazzlelink(
        src, dest, dazzlelink_dir=dl_dir, path_style="flat", mode="info"
    )
    assert dl_path is not None and Path(dl_path).exists()

    # Read it back through the lib: original/target/mode survived the write.
    from dazzle_linklib import import_link
    rec = import_link(str(dl_path))
    assert rec.get_original_path() == str(src)
    assert rec.get_target_path() == str(dest)
    assert rec.get_default_mode() == "info"

    # restore_from_dazzlelink returns the ORIGINAL path (documented contract).
    restored = bridge.restore_from_dazzlelink(dl_path)
    assert Path(restored) == src


def test_find_dazzlelinks_in_dir(tmp_path):
    src = tmp_path / "f.txt"
    src.write_text("x")
    dest = tmp_path / "d.txt"
    dest.write_text("x")
    dl_dir = tmp_path / "links"
    bridge.create_dazzlelink(src, dest, dazzlelink_dir=dl_dir, path_style="flat", mode="info")

    found = bridge.find_dazzlelinks_in_dir(dl_dir, recursive=True, pattern="*.dazzlelink")
    assert any(str(p).endswith(".dazzlelink") for p in found)


def test_manifest_to_and_from_dazzlelinks_roundtrip(tmp_path):
    out = tmp_path / "out"
    manifest = {
        "files": {
            "f0": {
                "source_path": str(tmp_path / "s.txt"),
                "destination_path": str(tmp_path / "d.txt"),
                "timestamps": {"created": 1.0, "modified": 2.0, "accessed": 3.0},
            }
        }
    }
    created = bridge.manifest_to_dazzlelinks(manifest, out, make_executable=False)
    assert len(created) == 1 and created[0].exists()

    back = bridge.dazzlelink_to_manifest(created)
    assert back["files"]  # at least one file recovered into a manifest


def test_bridge_uses_export_link_not_create_link():
    """AC-B5: guard the same-name collision -- the bridge writes via export_link,
    never the lib's create_link (which makes an OS symlink)."""
    src = inspect.getsource(sys.modules["dazzle_preservelib.dazzlelink.core"])
    assert "export_link(" in src
    code_lines = [l for l in src.splitlines() if not l.lstrip().startswith("#")]
    assert not any("create_link(" in l for l in code_lines), (
        "bridge must write via export_link, never the lib's create_link (OS symlink)"
    )
