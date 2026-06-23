"""Clean-venv packaging verification for dazzle-preservelib (P3 step 8).

Verifies the RELEASE ARTIFACT in isolation from the dev environment's editable
installs -- the failure this guards against is a wheel that imports only because
our editable checkouts are on sys.path. Two modes:

    python tests/one-offs/preflight_clean_venv.py --wheel    # build+install local wheel (pre-publish)
    python tests/one-offs/preflight_clean_venv.py --pypi 0.8.0  # install from PyPI (post-publish)

Either way it: creates a throwaway venv, installs dazzle-preservelib (deps
resolved from PyPI), then -- from a NEUTRAL cwd so the installed package is what
imports, never the repo source -- checks the version, the public surface, the
preserve-CLI consumer submodule imports, and the [dazzlelink] bridge round-trip.
"""
import os
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]

# Embedded verification: runs INSIDE the venv, from a neutral cwd. Imports the
# installed dazzle_preservelib and asserts the artifact is whole.
VERIFY = r'''
import importlib, sys
import dazzle_preservelib as d
loc = d.__file__
assert "site-packages" in loc, f"NOT the installed wheel: {loc}"
from dazzle_preservelib._version import PIP_VERSION
assert PIP_VERSION == "0.8.0", f"version {PIP_VERSION} != 0.8.0"

# Public surface (curated __all__ highlights)
for s in ["PreserveManifest","next_manifest_path","describe_manifest",
          "find_available_manifests","copy_operation","scan_destination",
          "find_and_verify_manifest","collect_file_metadata"]:
    assert hasattr(d, s), f"missing package export {s}"

# Consumer (preserve CLI) submodule contract
checks = {
 "dazzle_preservelib.manifest": ["PreserveManifest","find_available_manifests","next_manifest_path","extract_source_from_manifest"],
 "dazzle_preservelib.operations": ["InsufficientSpaceError","PermissionCheckError","detect_path_cycles_deep","OperationResult"],
 "dazzle_preservelib.verification": ["find_and_verify_manifest","verify_three_way","ThreeWayVerificationResult","select_manifest"],
 "dazzle_preservelib.links": ["LinkHandlingMode","LinkAction","analyze_link","decide_link_action","remove_link","LinkInfo"],
 "dazzle_preservelib.destination": ["scan_destination","compare_files","compute_destination_path","format_scan_report"],
 "dazzle_preservelib.path_warnings": ["check_path_mode_warnings","prompt_path_warning","PathWarning"],
 "dazzle_preservelib.metadata": ["collect_file_metadata","apply_file_metadata"],
 "dazzle_preservelib.dazzlelink": ["is_available"],
}
for mod, syms in checks.items():
    m = importlib.import_module(mod)
    for s in syms:
        assert hasattr(m, s), f"missing {mod}.{s}"

print("BASE OK: version 0.8.0, public surface + consumer contract import clean from the wheel")
'''

VERIFY_BRIDGE = r'''
import tempfile, pathlib
from dazzle_preservelib import dazzlelink as bridge
assert bridge.is_available(), "[dazzlelink] extra not active after install"
with tempfile.TemporaryDirectory() as d:
    dd = pathlib.Path(d)
    src = dd/"orig"/"f.png"; src.parent.mkdir(parents=True); src.write_bytes(b"x")
    dest = dd/"kept"/"f.png"; dest.parent.mkdir(parents=True); dest.write_bytes(b"x")
    dl = bridge.create_dazzlelink(src, dest, dazzlelink_dir=dd/"links", path_style="flat", mode="info")
    assert dl and pathlib.Path(dl).exists(), "create_dazzlelink produced nothing"
    restored = bridge.restore_from_dazzlelink(dl)
    assert pathlib.Path(restored) == src, f"round-trip mismatch: {restored} != {src}"
print("EXTRA OK: [dazzlelink] bridge round-trips against the installed wheel + dazzle-linklib from PyPI")
'''


def run(cmd, **kw):
    print("  $", " ".join(str(c) for c in cmd))
    return subprocess.run(cmd, check=True, **kw)


def main() -> int:
    mode_wheel = "--wheel" in sys.argv
    pypi_ver = None
    if "--pypi" in sys.argv:
        pypi_ver = sys.argv[sys.argv.index("--pypi") + 1]
    if not mode_wheel and not pypi_ver:
        print("usage: preflight_clean_venv.py (--wheel | --pypi VERSION)")
        return 2

    work = Path(tempfile.mkdtemp(prefix="dpl_preflight_"))
    venv = work / "venv"
    print(f"[setup] work dir: {work}")
    run([sys.executable, "-m", "venv", str(venv)])
    py = venv / ("Scripts" if os.name == "nt" else "bin") / ("python.exe" if os.name == "nt" else "python")
    run([str(py), "-m", "pip", "install", "-q", "--upgrade", "pip"])

    if mode_wheel:
        dist = work / "dist"
        run([sys.executable, "-m", "build", "--wheel", "--outdir", str(dist)], cwd=str(REPO))
        wheels = list(dist.glob("dazzle_preservelib-*.whl"))
        assert wheels, "no wheel built"
        wheel = wheels[0]
        print(f"[built] {wheel.name}")
        base_target = str(wheel)
        extra_target = f"{wheel}[dazzlelink]"
    else:
        base_target = f"dazzle-preservelib=={pypi_ver}"
        extra_target = f"dazzle-preservelib[dazzlelink]=={pypi_ver}"

    # Base install (deps resolve from PyPI) + verify from a NEUTRAL cwd.
    run([str(py), "-m", "pip", "install", base_target])
    run([str(py), "-c", VERIFY], cwd=str(work))

    # Add the [dazzlelink] extra + verify the bridge round-trip.
    run([str(py), "-m", "pip", "install", extra_target])
    run([str(py), "-c", VERIFY_BRIDGE], cwd=str(work))

    print(f"\n=== PRE-FLIGHT PASS -- clean-venv artifact verification ({'wheel' if mode_wheel else 'PyPI ' + pypi_ver}) ===")
    print(f"(throwaway venv left at {work} -- safe to delete)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
