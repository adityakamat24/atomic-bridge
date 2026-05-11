from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from src.core.in_memory_store import InMemoryStore
from src.core.schema_loader import load
from src.planner.name_resolver import NameResolver

REPO_DATA_DIR = Path(__file__).resolve().parents[2] / "data"


@pytest.fixture
def resolver(tmp_path: Path) -> NameResolver:
    target = tmp_path / "data"
    shutil.copytree(REPO_DATA_DIR, target)
    g = load(target / "schema.yaml")
    return NameResolver(InMemoryStore(g, target))


def test_exact_match_returns_max_confidence(resolver: NameResolver) -> None:
    cands = resolver.resolve("John Doe")
    assert cands
    assert cands[0].full_name == "John Doe"
    assert cands[0].confidence == 1.0
    assert cands[0].match_type == "exact"


def test_lowercase_first_name_matches(resolver: NameResolver) -> None:
    """Spec acceptance: 'john' resolves to 'John Doe'."""
    cands = resolver.resolve("john")
    full_names = {c.full_name for c in cands}
    assert "John Doe" in full_names


def test_lowercase_last_name_matches(resolver: NameResolver) -> None:
    """Spec acceptance: 'doe' resolves to 'John Doe'."""
    cands = resolver.resolve("doe")
    full_names = {c.full_name for c in cands}
    assert "John Doe" in full_names


def test_initial_with_period_matches(resolver: NameResolver) -> None:
    """Spec acceptance: 'John D.' resolves to 'John Doe'."""
    cands = resolver.resolve("John D.")
    full_names = {c.full_name for c in cands}
    assert "John Doe" in full_names


def test_typo_matches_via_fuzzy(resolver: NameResolver) -> None:
    cands = resolver.resolve("Jhon")  # one transposition
    full_names = {c.full_name for c in cands}
    assert "John Doe" in full_names
    candidate = next(c for c in cands if c.full_name == "John Doe")
    assert candidate.match_type == "fuzzy"


def test_empty_mention_returns_empty_list(resolver: NameResolver) -> None:
    assert resolver.resolve("") == []
    assert resolver.resolve("   ") == []


def test_unknown_name_returns_empty_list(resolver: NameResolver) -> None:
    assert resolver.resolve("Zelpha Quincelot") == []


def test_top_k_caps_results(resolver: NameResolver) -> None:
    """A common single-letter prefix should match many users; top_k limits."""
    cands = resolver.resolve("J", top_k=2)
    assert len(cands) <= 2


def test_results_sorted_by_confidence_desc(resolver: NameResolver) -> None:
    cands = resolver.resolve("john")
    confs = [c.confidence for c in cands]
    assert confs == sorted(confs, reverse=True)
