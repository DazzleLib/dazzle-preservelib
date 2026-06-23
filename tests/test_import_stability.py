"""Import-stability canary (see docs/api-stability.md).

Every symbol listed here is part of the locked public API. If this test
fails, a consumer somewhere breaks: do NOT silently fix the test -- follow
the api-stability.md process (deprecate with a noisy shim, register it,
slate removal).

The package-level public surface (curated `__all__`) is locked as of the
0.8.x extraction completion. The SUBMODULE-level contract the preserve CLI
actually imports against is locked separately and more precisely in
`tests/test_consumer_import_surface.py` (the CLI imports
`from preservelib.manifest import ...`, which `__all__` alone does not cover).
"""

import importlib

LOCKED_SURFACE = {
    "dazzle_preservelib": [
        # Version
        "__version__",
        "__app_name__",
        "PIP_VERSION",
        # Logging
        "configure_logging",
        "enable_verbose_logging",
        # Manifest (incl. the step-6 lifecycle pull-down)
        "PreserveManifest",
        "calculate_file_hash",
        "verify_file_hash",
        "create_manifest_for_path",
        "read_manifest",
        "find_available_manifests",
        "next_manifest_path",
        "describe_manifest",
        # Operations
        "copy_operation",
        "move_operation",
        "verify_operation",
        "restore_operation",
        # Metadata
        "collect_file_metadata",
        "apply_file_metadata",
        "compare_metadata",
        # Restore
        "restore_file_to_original",
        "restore_files_from_manifest",
        "find_restoreable_files",
        # Destination awareness
        "FileCategory",
        "ConflictResolution",
        "FileComparison",
        "DestinationScanResult",
        "compare_files",
        "scan_destination",
        # Verification
        "VerificationStatus",
        "FileVerificationResult",
        "VerificationResult",
        "verify_file_against_manifest",
        "verify_files_against_manifest",
        "find_and_verify_manifest",
    ],
}


def test_locked_surface_importable():
    missing = []
    for module_name, symbols in LOCKED_SURFACE.items():
        module = importlib.import_module(module_name)
        for symbol in symbols:
            if not hasattr(module, symbol):
                missing.append(f"{module_name}.{symbol}")
    assert not missing, (
        f"Locked API symbols missing: {missing} -- see docs/api-stability.md "
        f"before changing the public surface."
    )


def test_package_is_importable():
    """The package imports cleanly with no side effects at import time."""
    mod = importlib.import_module("dazzle_preservelib")
    assert isinstance(mod.__version__, str)
    assert mod.__app_name__ == "dazzle-preservelib"
