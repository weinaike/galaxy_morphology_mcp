"""Shared test configuration and fixtures."""

import sys
from pathlib import Path

import pytest

# Add src to path for all tests
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))


@pytest.fixture
def test_data_dir():
    """Path to the tests/test_data/ directory."""
    return Path(__file__).parent / "test_data"


@pytest.fixture
def test_image(test_data_dir):
    """Path to residual image.png in test data."""
    return str(test_data_dir / "NGC1097_comparison.png")


@pytest.fixture
def test_summary_file(test_data_dir):
    """Path to summary file.md in test data."""
    return str(test_data_dir / "NGC1097_summary.md")
