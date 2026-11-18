"""Red-green audit of the 0.9.1 same-name guard.

Reverts each behaviour separately (keeping every signature), runs only
tests/test_same_name_guard.py, prints which tests went red, and restores the
source byte-for-byte (verified by hash). Run from the worktree root with
PYTHONPATH set to it.
"""
import hashlib
import subprocess
import sys
from pathlib import Path

SRC = Path("dazzle_preservelib/operations.py")
REVERTS = {
    "G (claimed-destination guard)": [
        ("            if _dest_key(dest_path) in claimed_dests:", "            if False:", 1),
    ],
    "H (delete-gate re-check)": [
        ("                if not still_same:", "                if False:", 1),
    ],
}

original = SRC.read_bytes()
digest = hashlib.sha256(original).hexdigest()
try:
    for name, edits in REVERTS.items():
        text = original.decode("utf-8")
        for old, new, count in edits:
            assert text.count(old) == count, (name, text.count(old))
            text = text.replace(old, new)
        SRC.write_bytes(text.encode("utf-8"))
        run = subprocess.run(
            [sys.executable, "-m", "pytest", "tests/test_same_name_guard.py",
             "-q", "-p", "no:cacheprovider", "--tb=line", "-rf"],
            capture_output=True, text=True,
        )
        lines = run.stdout.strip().splitlines()
        print(f"== revert {name}: {lines[-1] if lines else run.stderr[-300:]}")
        for line in lines:
            if line.startswith("FAILED"):
                print("   red:", line.split("::", 1)[1].split(" - ")[0])
        SRC.write_bytes(original)
finally:
    SRC.write_bytes(original)
assert hashlib.sha256(SRC.read_bytes()).hexdigest() == digest
print("restored by hash")
