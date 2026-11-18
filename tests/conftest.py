"""Shared test fixtures for dazzle-preservelib."""

import pytest
from pathlib import Path

import dazzle_preservelib

# The package under test must be THIS checkout's copy. preservelib is often
# installed editable from another checkout, so a run from a git worktree with
# a missing or mis-set PYTHONPATH would silently test that other copy.
_REPO_ROOT = Path(__file__).resolve().parents[1]
_IMPORTED = Path(dazzle_preservelib.__file__).resolve()
if _REPO_ROOT not in _IMPORTED.parents:
    raise pytest.UsageError(
        f"dazzle_preservelib imported from {_IMPORTED}, not from this checkout "
        f"({_REPO_ROOT}). Run with PYTHONPATH={_REPO_ROOT}."
    )


@pytest.fixture
def tmp_project(tmp_path):
    """Create a temporary project directory for testing."""
    project_dir = tmp_path / "test_project"
    project_dir.mkdir()
    return project_dir


@pytest.fixture
def sample_data(tmp_path):
    """Create sample data files for testing."""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    return data_dir
