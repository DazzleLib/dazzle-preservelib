"""Import-stability canary (see docs/api-stability.md).

Every symbol listed here is part of the locked public API. If this test
fails, a consumer somewhere breaks: do NOT silently fix the test -- follow
the api-stability.md process (deprecate with a noisy shim, register it,
slate removal).

The locked surface is intentionally minimal at the scaffold stage (version
exports only). The manifest/operations/verification surface joins this canary
at the 0.8.0 extraction release.
"""

import importlib

LOCKED_SURFACE = {
    "dazzle_preservelib": [
        "__version__",
        "__app_name__",
        "PIP_VERSION",
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
