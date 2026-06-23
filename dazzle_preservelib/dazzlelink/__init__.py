"""
Dazzlelink integration for preserve.py.

This package provides integration with the dazzlelink library, allowing
preserve to work with dazzlelink files for alternative metadata storage.
"""

import os
import sys
import logging
import importlib.util
from pathlib import Path
from typing import Dict, List, Optional, Union, Set, Tuple, Any

# Set up package-level logger
logger = logging.getLogger(__name__)

# Check if the dazzle-linklib (L2) library is available. It is the optional
# `[dazzlelink]` extra; without it the bridge functions are not exported and a
# consumer should gate on is_available() (never a silent bundled-path fallback --
# V5 removed; D2 hard boundary).
HAVE_DAZZLELINK = False
try:
    import dazzle_linklib
    HAVE_DAZZLELINK = True
    logger.debug("dazzle-linklib found, dazzlelink integration enabled")
except ImportError:
    logger.debug(
        "dazzle-linklib not installed; dazzlelink integration disabled "
        "(install the extra: pip install dazzle-preservelib[dazzlelink])"
    )

def is_available() -> bool:
    """
    Check if dazzlelink integration is available.
    
    Returns:
        True if dazzlelink is available, False otherwise
    """
    return HAVE_DAZZLELINK

# Import functions from the main dazzlelink module if available
if HAVE_DAZZLELINK:
    try:
        from .core import (
            create_dazzlelink,
            find_dazzlelinks_in_dir,
            restore_from_dazzlelink,
            dazzlelink_to_manifest,
            manifest_to_dazzlelinks
        )
        
        # Export the functions
        __all__ = [
            'is_available',
            'create_dazzlelink',
            'find_dazzlelinks_in_dir',
            'restore_from_dazzlelink',
            'dazzlelink_to_manifest',
            'manifest_to_dazzlelinks'
        ]
    except ImportError as e:
        logger.warning(f"Error importing dazzlelink functionality: {e}")
        __all__ = ['is_available']
else:
    __all__ = ['is_available']
