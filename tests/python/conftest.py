"""Pytest fixtures for the scheduler test suite.

Path note: pytest.ini sets `pythonpath = .` (repo root) so `import scheduler`,
`import validator`, `import app` resolve. Shared factories live in helpers.py.
"""
import pytest

from helpers import make_division, make_config


# ── Group-layout fixtures (the four bracket shapes that drive _build_po_structure) ──

@pytest.fixture
def div_3333():
    """12-team symmetric: 4 groups of 3 -> tiered, no 13-16."""
    return make_division("TEST 12", [3, 3, 3, 3])


@pytest.fixture
def div_4333():
    """13-team asymmetric: A=4, B/C/D=3 -> tiered, A4 has no bracket entry."""
    return make_division("TEST 13", [4, 3, 3, 3])


@pytest.fixture
def div_4433():
    """14-team asymmetric: A/B=4, C/D=3 -> tiered + one '13th Place' game."""
    return make_division("TEST 14", [4, 4, 3, 3])


@pytest.fixture
def div_4444():
    """16-team full: 4 groups of 4 -> paired Silver/Bronze + 13-16."""
    return make_division("TEST 16", [4, 4, 4, 4])


@pytest.fixture
def make_division_factory():
    return make_division


@pytest.fixture
def make_config_factory():
    return make_config
