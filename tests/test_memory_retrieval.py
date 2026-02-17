"""Tests for keyword-based memory retrieval."""

import asyncio
from uuid import uuid4
import pytest

from miniverse.memory import (
    ImportanceWeightedMemory,
    SimpleMemoryStream,
    BM25MemoryStrategy,
    tokenize,
    compute_bm25_scores,
)
from miniverse.persistence import InMemoryPersistence


@pytest.mark.asyncio
async def test_keyword_retrieval():
    persistence = InMemoryPersistence()
    await persistence.initialize()
    memory = SimpleMemoryStream(persistence)

    run_id = uuid4()
    agent_id = "alpha"

    await memory.add_memory(
        run_id,
        agent_id,
        tick=1,
        memory_type="observation",
        content="Agent inspected the oxygen recycler",
        importance=5,
        tags=["systems"],
    )
    await memory.add_memory(
        run_id,
        agent_id,
        tick=2,
        memory_type="observation",
        content="Team discussed revenue targets",
        importance=5,
    )
    await memory.add_memory(
        run_id,
        agent_id,
        tick=3,
        memory_type="observation",
        content="Agent repaired the recycler filters",
        importance=7,
        tags=["systems"],
    )

    results = await memory.get_relevant_memories(run_id, agent_id, query="recycler", limit=2)
    assert len(results) == 2
    assert "recycler" in results[0]

    await persistence.close()


@pytest.mark.asyncio
async def test_importance_weighted_memory_balances_scores():
    persistence = InMemoryPersistence()
    await persistence.initialize()
    memory = ImportanceWeightedMemory(
        persistence,
        recency_weight=0.2,
        importance_weight=0.8,
        window=10,
    )

    run_id = uuid4()
    agent_id = "alpha"

    # Older but critical memory should outrank fresher, low-importance noise.
    await memory.add_memory(
        run_id,
        agent_id,
        tick=1,
        memory_type="observation",
        content="Logged safety protocol deviation",
        importance=9,
    )
    await memory.add_memory(
        run_id,
        agent_id,
        tick=3,
        memory_type="observation",
        content="Filed routine shift report",
        importance=3,
    )

    results = await memory.get_relevant_memories(run_id, agent_id, query="", limit=2)
    assert results[0] == "Logged safety protocol deviation"

    await persistence.close()


def test_tokenize_basic():
    """Test basic tokenization."""
    tokens = tokenize("The quick brown fox jumps over the lazy dog")
    assert "quick" in tokens
    assert "fox" in tokens
    # Single-letter tokens filtered out
    assert "a" not in tokens
    # Check lowercase
    assert all(t == t.lower() for t in tokens)


def test_tokenize_special_chars():
    """Test tokenization with special characters."""
    tokens = tokenize("BM25-based retrieval (with scores!)")
    assert "bm25" in tokens
    assert "based" in tokens
    assert "retrieval" in tokens
    assert "scores" in tokens


def test_bm25_scores_basic():
    """Test BM25 scoring ranks relevant documents higher."""
    documents = [
        ("The cat sat on the mat", tokenize("The cat sat on the mat")),
        ("The dog ran in the park", tokenize("The dog ran in the park")),
        ("A cat and a dog are friends", tokenize("A cat and a dog are friends")),
    ]
    query_tokens = tokenize("cat")
    results = compute_bm25_scores(query_tokens, documents)

    # Two docs contain "cat"
    assert len(results) == 2
    assert "cat" in results[0][0].lower()


def test_bm25_scores_idf():
    """Test that rare terms boost scores more than common terms."""
    # Create corpus where 'the' appears in all docs but 'unicorn' appears in one
    documents = [
        ("The unicorn appeared", tokenize("The unicorn appeared")),
        ("The dog appeared", tokenize("The dog appeared")),
        ("The cat appeared", tokenize("The cat appeared")),
    ]

    # Query for rare term 'unicorn'
    unicorn_results = compute_bm25_scores(tokenize("unicorn"), documents)
    # Query for common term 'appeared'
    appeared_results = compute_bm25_scores(tokenize("appeared"), documents)

    # Unicorn query should give higher score to matching doc
    # (rare term = higher IDF)
    assert len(unicorn_results) == 1
    assert unicorn_results[0][1] > 0

    # All docs match 'appeared' with similar scores
    assert len(appeared_results) == 3


def test_bm25_empty_query():
    """Test BM25 with empty query returns empty."""
    documents = [
        ("Some content", tokenize("Some content")),
    ]
    results = compute_bm25_scores([], documents)
    assert results == []


@pytest.mark.asyncio
async def test_bm25_memory_strategy_retrieval():
    """Test BM25MemoryStrategy ranks by term relevance."""
    persistence = InMemoryPersistence()
    await persistence.initialize()
    memory = BM25MemoryStrategy(persistence)

    run_id = uuid4()
    agent_id = "alpha"

    # Add diverse memories
    await memory.add_memory(
        run_id,
        agent_id,
        tick=1,
        memory_type="observation",
        content="The reactor core temperature is stable at 350 degrees",
        importance=5,
    )
    await memory.add_memory(
        run_id,
        agent_id,
        tick=2,
        memory_type="observation",
        content="Team discussed quarterly budget allocation",
        importance=5,
    )
    await memory.add_memory(
        run_id,
        agent_id,
        tick=3,
        memory_type="observation",
        content="Reactor coolant levels checked and nominal",
        importance=6,
    )
    await memory.add_memory(
        run_id,
        agent_id,
        tick=4,
        memory_type="observation",
        content="Attended meeting about performance reviews",
        importance=4,
    )

    # Query for reactor-related content
    results = await memory.get_relevant_memories(
        run_id, agent_id, query="reactor temperature", limit=2
    )

    # Both reactor memories should be retrieved
    assert len(results) == 2
    assert all("reactor" in r.lower() for r in results)
    # The one with "temperature" should rank first (more query terms match)
    assert "temperature" in results[0].lower()

    await persistence.close()


@pytest.mark.asyncio
async def test_bm25_memory_strategy_respects_importance():
    """Test BM25MemoryStrategy includes importance in scoring."""
    persistence = InMemoryPersistence()
    await persistence.initialize()
    memory = BM25MemoryStrategy(
        persistence,
        bm25_weight=0.4,
        recency_weight=0.2,
        importance_weight=0.4,  # High importance weight
    )

    run_id = uuid4()
    agent_id = "alpha"

    # Add two similar memories with different importance
    await memory.add_memory(
        run_id,
        agent_id,
        tick=1,
        memory_type="observation",
        content="Security alert triggered in sector A",
        importance=9,  # High importance
    )
    await memory.add_memory(
        run_id,
        agent_id,
        tick=2,
        memory_type="observation",
        content="Security check completed in sector B",
        importance=3,  # Low importance
    )

    results = await memory.get_relevant_memories(
        run_id, agent_id, query="security sector", limit=2
    )

    # Higher importance memory should rank first despite being older
    assert len(results) == 2
    assert "sector A" in results[0]

    await persistence.close()


@pytest.mark.asyncio
async def test_bm25_memory_fallback_without_matches():
    """Test BM25MemoryStrategy falls back when no BM25 matches."""
    persistence = InMemoryPersistence()
    await persistence.initialize()
    memory = BM25MemoryStrategy(persistence)

    run_id = uuid4()
    agent_id = "alpha"

    await memory.add_memory(
        run_id,
        agent_id,
        tick=1,
        memory_type="observation",
        content="Morning shift started",
        importance=5,
    )
    await memory.add_memory(
        run_id,
        agent_id,
        tick=2,
        memory_type="observation",
        content="Lunch break taken",
        importance=5,
    )

    # Query with no matching terms - should fall back to recency/importance
    results = await memory.get_relevant_memories(
        run_id, agent_id, query="xyz123nonexistent", limit=2
    )

    # Should return results (fallback behavior)
    assert len(results) == 2
    # Most recent should be first in fallback
    assert "Lunch" in results[0]

    await persistence.close()
