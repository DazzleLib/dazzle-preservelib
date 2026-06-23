"""Consumer-contract lock: dazzle_preservelib is a drop-in for the preserve CLI.

These tests replicate the *exact* import statements the preserve CLI issues
against its embedded `preservelib`, but pointed at `dazzle_preservelib`. They
are the real locked surface: the CLI imports at the SUBMODULE level
(`from preservelib.manifest import ...`), so the package-level `__all__` alone
does not protect it. If the lifted library drops or moves any symbol the CLI
uses, one of these imports raises and the test fails -- before the CLI breaks.

Source of the surface (audited from C:\\code\\preserve\\preserve, P3 step 7):
- preserve.py:           from preservelib import operations
                         from preservelib.manifest import find_available_manifests
- handlers/copy.py:      operations.{InsufficientSpaceError,PermissionCheckError},
                         destination.{scan_destination,format_scan_report,ConflictResolution},
                         path_warnings.{check_path_mode_warnings,prompt_path_warning}
- handlers/move.py:      links.{LinkHandlingMode,LinkAction,analyze_link,decide_link_action,remove_link},
                         operations.{...,detect_path_cycles_deep}
- handlers/restore.py:   links, manifest.{PreserveManifest,find_available_manifests}
- handlers/verify.py:    verification.{find_and_verify_manifest,verify_three_way},
                         manifest.{find_available_manifests,read_manifest,
                                   extract_source_from_manifest,PreserveManifest}
- handlers/cleanup.py:   manifest.{read_manifest,find_available_manifests,calculate_file_hash},
                         metadata.{collect_file_metadata,apply_file_metadata}
- utils.py:              from preservelib import dazzlelink

`verify_source_against_manifest` is intentionally NOT here: the CLI imports it
under try/except (ImportError, AttributeError) with a verify_three_way fallback
("Function doesn't exist yet"), so it was never part of the canonical contract.
"""


def test_operations_surface():
    from dazzle_preservelib import operations  # noqa: F401
    from dazzle_preservelib.operations import (  # noqa: F401
        InsufficientSpaceError,
        PermissionCheckError,
        detect_path_cycles_deep,
    )


def test_manifest_surface():
    from dazzle_preservelib.manifest import (  # noqa: F401
        find_available_manifests,
        read_manifest,
        calculate_file_hash,
        extract_source_from_manifest,
        PreserveManifest,
        # lifecycle pulled down in step 6 -- part of the contract going forward
        next_manifest_path,
        describe_manifest,
    )


def test_verification_surface():
    from dazzle_preservelib.verification import (  # noqa: F401
        find_and_verify_manifest,
        verify_three_way,
    )


def test_links_policy_surface():
    from dazzle_preservelib import links  # noqa: F401
    from dazzle_preservelib.links import (  # noqa: F401
        LinkHandlingMode,
        LinkAction,
        analyze_link,
        decide_link_action,
        remove_link,
    )


def test_destination_surface():
    from dazzle_preservelib.destination import (  # noqa: F401
        scan_destination,
        format_scan_report,
        ConflictResolution,
    )


def test_path_warnings_surface():
    from dazzle_preservelib.path_warnings import (  # noqa: F401
        check_path_mode_warnings,
        prompt_path_warning,
    )


def test_metadata_surface():
    from dazzle_preservelib.metadata import (  # noqa: F401
        collect_file_metadata,
        apply_file_metadata,
    )


def test_dazzlelink_gate_surface():
    # is_available() is always importable; the bridge functions live behind the
    # [dazzlelink] extra and are intentionally NOT part of the unconditional lock.
    from dazzle_preservelib import dazzlelink
    assert hasattr(dazzlelink, "is_available")
    assert isinstance(dazzlelink.is_available(), bool)
