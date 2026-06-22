"""Functional validation: drive the REAL preserve CLI against our lifted
`dazzle_preservelib` (instead of preserve's embedded `preservelib`).

We alias `preservelib` -> `dazzle_preservelib` in sys.modules BEFORE the CLI
imports it, so every `from preservelib import ...` in the CLI resolves to our
extracted code. Then we run a real COPY -> VERIFY round-trip on temp files.
If the CLI's full operation succeeds against the lifted lib, the lift works.

Run:  python tests/one-offs/validate_against_preserve_cli.py
"""
import io
import sys
import tempfile
import contextlib
from pathlib import Path

DAZZLE_PRESERVELIB = r"C:\code\dazzle-preservelib"
PRESERVE_REPO = r"C:\code\preserve"

# 1. Make both packages importable (lifted lib FIRST).
sys.path.insert(0, DAZZLE_PRESERVELIB)
sys.path.insert(0, PRESERVE_REPO)

# 2. Alias preservelib -> dazzle_preservelib (package + submodules) BEFORE the
#    preserve CLI imports preservelib.
import dazzle_preservelib  # noqa: E402

_SUBS = [
    "manifest", "operations", "metadata", "restore", "verification",
    "links", "destination", "path_warnings", "pathutils",
]
for _s in _SUBS:
    __import__(f"dazzle_preservelib.{_s}")
import dazzle_preservelib.dazzlelink  # noqa: E402

sys.modules["preservelib"] = dazzle_preservelib
for _s in _SUBS:
    sys.modules[f"preservelib.{_s}"] = getattr(dazzle_preservelib, _s)
sys.modules["preservelib.dazzlelink"] = dazzle_preservelib.dazzlelink

assert dazzle_preservelib.__file__.lower().startswith(DAZZLE_PRESERVELIB.lower()), \
    f"alias points at the wrong place: {dazzle_preservelib.__file__}"
print(f"[ALIAS] preservelib -> {dazzle_preservelib.__file__}")

# 3. Now import the real CLI (its `from preservelib import ...` hits our alias).
from preserve.preserve import main  # noqa: E402


def run_cli(argv):
    """Run the preserve CLI with argv; return (exit_code, output)."""
    buf = io.StringIO()
    code = 0
    old = sys.argv
    sys.argv = ["preserve"] + argv
    try:
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
            try:
                main()
            except SystemExit as e:
                code = e.code if isinstance(e.code, int) else (0 if not e.code else 1)
    finally:
        sys.argv = old
    return code, buf.getvalue()


def main_test():
    tmp = Path(tempfile.mkdtemp(prefix="dpl_validate_"))
    src = tmp / "src"
    (src / "sub").mkdir(parents=True)
    (src / "a.txt").write_text("alpha payload")
    (src / "sub" / "b.txt").write_text("bravo payload")
    dest = tmp / "dest"
    dest.mkdir()

    print(f"[SETUP] src={src}  dest={dest}")

    # --- COPY (exercises operations.copy_operation + manifest + metadata + hashing)
    code, out = run_cli([
        "COPY", str(src), "--dst", str(dest),
        "--recursive", "--hash", "SHA256", "--no-path-warning", "--quiet",
    ])
    print(f"[COPY] exit={code}")
    copied = list(dest.rglob("*.txt"))
    print(f"[COPY] dest .txt files: {[p.name for p in copied]}")
    manifests = list(dest.rglob("*manifest*.json"))
    print(f"[COPY] manifest(s): {[str(p.relative_to(dest)) for p in manifests]}")

    # --- VERIFY (exercises verification + manifest read against disk)
    vcode, vout = run_cli([
        "VERIFY", "--dst", str(dest), "--check", "auto", "--quiet",
    ])
    print(f"[VERIFY] exit={vcode}")
    tail = "\n".join(l for l in vout.splitlines() if l.strip())[-600:]
    print(f"[VERIFY] output tail:\n{tail}")

    # --- Assertions
    ok = True
    if code != 0:
        print("FAIL: COPY non-zero exit"); ok = False
    if len(copied) < 2:
        print("FAIL: expected >=2 copied .txt files"); ok = False
    if not manifests:
        print("FAIL: no manifest written"); ok = False
    if vcode != 0:
        print("FAIL: VERIFY non-zero exit"); ok = False
    # content fidelity
    body = {p.name: p.read_text() for p in copied}
    if body.get("a.txt") != "alpha payload" or body.get("b.txt") != "bravo payload":
        print("FAIL: copied content mismatch"); ok = False

    print("\n=== RESULT:", "PASS -- lifted dazzle_preservelib drives the real preserve CLI"
          if ok else "FAIL", "===")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main_test())
