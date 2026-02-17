"""
MemoryStrategy interface for agent memory systems.

This module provides the abstract base class for implementing how agents
remember and recall past experiences. Based on Stanford Generative Agents
research on memory streams.

Key responsibilities:
- Store agent observations, actions, and reflections
- Retrieve relevant memories based on recency, importance, relevance
- Manage memory capacity (forgetting old/unimportant memories)
- Support different memory architectures

Design principle: Start simple (FIFO), enable sophisticated (weighted retrieval).
"""

from abc import ABC, abstractmethod
from collections import Counter
import math
import re
from typing import Any, Dict, List, Optional, Tuple
from uuid import UUID
from datetime import datetime

from miniverse.schemas import AgentMemory


# ---------------------------------------------------------------------------
# BM25 Scoring Utilities
# ---------------------------------------------------------------------------

def tokenize(text: str) -> List[str]:
    """Simple tokenizer: lowercase, split on non-alphanumeric, filter short tokens."""
    return [t for t in re.split(r'[^a-z0-9]+', text.lower()) if len(t) > 1]


def compute_bm25_scores(
    query_tokens: List[str],
    documents: List[Tuple[str, List[str]]],  # (content, tokens)
    k1: float = 1.5,
    b: float = 0.75,
) -> List[Tuple[str, float]]:
    """
    Compute BM25 scores for documents given a query.

    Returns list of (content, score) tuples sorted by score descending.
    """
    if not documents or not query_tokens:
        return []

    # Calculate corpus statistics
    corpus_size = len(documents)
    doc_lengths = [len(tokens) for _, tokens in documents]
    avgdl = sum(doc_lengths) / corpus_size if corpus_size > 0 else 1.0

    # Calculate document frequencies for IDF
    doc_freqs: Dict[str, int] = Counter()
    for _, tokens in documents:
        unique_terms = set(tokens)
        for term in unique_terms:
            doc_freqs[term] += 1

    # Calculate IDF for query terms
    idf: Dict[str, float] = {}
    for term in query_tokens:
        df = doc_freqs.get(term, 0)
        # BM25 Okapi IDF formula with floor at 0
        idf[term] = max(0, math.log((corpus_size - df + 0.5) / (df + 0.5) + 1))

    # Score each document
    scored: List[Tuple[str, float]] = []
    for i, (content, tokens) in enumerate(documents):
        if not tokens:
            continue

        doc_len = doc_lengths[i]
        term_freqs = Counter(tokens)
        score = 0.0

        for term in query_tokens:
            if term not in term_freqs:
                continue
            tf = term_freqs[term]
            term_idf = idf.get(term, 0)
            # BM25 Okapi TF formula
            numerator = tf * (k1 + 1)
            denominator = tf + k1 * (1 - b + b * doc_len / avgdl)
            score += term_idf * (numerator / denominator)

        if score > 0:
            scored.append((content, score))

    # Sort by score descending
    scored.sort(key=lambda x: x[1], reverse=True)
    return scored


class MemoryStrategy(ABC):
    """
    Abstract base class for agent memory systems.

    This interface allows different memory architectures:
    - SimpleMemoryStream: FIFO queue (recent memories only)
    - ImportanceWeightedMemory: Weight by recency + importance
    - RelevanceMemory: Semantic search for relevant memories
    - ReflectionMemory: Periodic higher-level summaries

    Based on Stanford Generative Agents (2023) memory architecture:
    - Memory Stream: Sequential record of observations
    - Retrieval: Recency + importance + relevance scoring
    - Reflection: Periodic summarization of memories
    """

    @abstractmethod
    async def initialize(self) -> None:
        """
        Initialize the memory backend.

        Called once before simulation starts. Used to set up connections,
        allocate resources, load data, etc.

        Raises:
            Exception: If initialization fails
        """
        pass

    @abstractmethod
    async def close(self) -> None:
        """
        Close the memory backend.

        Called once after simulation completes. Used to close connections,
        flush buffers, cleanup resources, etc.

        Raises:
            Exception: If cleanup fails
        """
        pass

    @abstractmethod
    async def add_memory(
        self,
        run_id: UUID,
        agent_id: str,
        tick: int,
        memory_type: str,
        content: str,
        importance: int = 5,
        *,
        tags: Optional[List[str]] = None,
        metadata: Optional[Dict[str, Any]] = None,
        embedding_key: Optional[str] = None,
        branch_id: Optional[str] = None,
    ) -> AgentMemory:
        """
        Add a new memory for an agent.

        Args:
            run_id: Simulation run identifier
            agent_id: Agent who owns this memory
            tick: Tick when memory was created
            memory_type: Type (observation, action, communication, reflection)
            content: Memory content (natural language)
            importance: Importance score 1-10 (5 = neutral)
            tags: Optional labels that retrieval engines can use for
                filtering/boosting (e.g., topics, entities)
            metadata: Structured payload for advanced retrieval engines
            embedding_key: Optional pointer to an external embedding entry
            branch_id: Optional branching timeline identifier

        Returns:
            The created AgentMemory object

        Raises:
            Exception: If memory cannot be stored
        """
        pass

    @abstractmethod
    async def get_recent_memories(
        self, run_id: UUID, agent_id: str, limit: int = 10
    ) -> List[str]:
        """
        Retrieve recent memories for an agent as strings.

        Used to build agent perception (recent_observations field).
        Returns natural language strings, not full AgentMemory objects.

        Args:
            run_id: Simulation run identifier
            agent_id: Agent identifier
            limit: Maximum number of memories to return

        Returns:
            List of memory content strings (most recent first)

        Raises:
            Exception: If retrieval fails
        """
        pass

    @abstractmethod
    async def get_relevant_memories(
        self,
        run_id: UUID,
        agent_id: str,
        query: str,
        limit: int = 5,
    ) -> List[str]:
        """
        Retrieve memories relevant to a query.

        Used for context-aware memory retrieval. Advanced implementations
        can use semantic similarity, keyword matching, etc.

        Args:
            run_id: Simulation run identifier
            agent_id: Agent identifier
            query: Query string to find relevant memories
            limit: Maximum number of memories to return

        Returns:
            List of relevant memory content strings

        Raises:
            Exception: If retrieval fails
        """
        pass

    @abstractmethod
    async def clear_agent_memories(self, run_id: UUID, agent_id: str) -> None:
        """
        Clear all memories for an agent.

        Used for testing or resetting agent state.

        Args:
            run_id: Simulation run identifier
            agent_id: Agent identifier

        Raises:
            Exception: If clearing fails
        """
        pass


class SimpleMemoryStream(MemoryStrategy):
    """
    Simple FIFO memory stream implementation.

    Stores all memories and returns the N most recent when queried.
    No importance weighting, no semantic search, no reflection.

    Good for:
    - Initial prototyping
    - Short simulations (<100 ticks)
    - Testing basic agent behavior

    Limitations:
    - No importance-based retrieval
    - No semantic relevance
    - Memory grows unbounded (should add capacity limit)
    - Delegates storage to persistence layer
    """

    def __init__(self, persistence):
        """
        Initialize memory stream with persistence backend.

        Args:
            persistence: PersistenceStrategy instance for storing memories
        """
        self.persistence = persistence

    async def initialize(self) -> None:
        """
        Initialize memory backend.

        For SimpleMemoryStream, this is a no-op since we delegate
        to the persistence layer which handles its own initialization.
        """
        pass

    async def close(self) -> None:
        """
        Close memory backend.

        For SimpleMemoryStream, this is a no-op since we delegate
        to the persistence layer which handles its own cleanup.
        """
        pass

    async def add_memory(
        self,
        run_id: UUID,
        agent_id: str,
        tick: int,
        memory_type: str,
        content: str,
        importance: int = 5,
        *,
        tags: Optional[List[str]] = None,
        metadata: Optional[Dict[str, Any]] = None,
        embedding_key: Optional[str] = None,
        branch_id: Optional[str] = None,
    ) -> AgentMemory:
        """
        Add a memory to the stream.

        Stores via persistence layer. Importance is recorded but not
        used for retrieval in this simple implementation.

        Args:
            run_id: Simulation run identifier
            agent_id: Agent who owns this memory
            tick: Tick when memory was created
            memory_type: Type of memory
            content: Memory content
            importance: Importance score (recorded but not used)
            tags: Optional labels for future retrieval engines
            metadata: Arbitrary key/value payload for retrievers
            embedding_key: Pointer into external embedding store (optional)
            branch_id: Timeline identifier for branching simulations (optional)

        Returns:
            The created AgentMemory object
        """
        import uuid

        memory = AgentMemory(
            id=uuid.uuid4(),
            run_id=run_id,
            agent_id=agent_id,
            tick=tick,
            memory_type=memory_type,
            content=content,
            importance=importance,
            tags=tags or [],
            metadata=metadata or {},
            embedding_key=embedding_key,
            branch_id=branch_id,
            created_at=datetime.now(),
        )

        await self.persistence.save_memory(run_id, memory)
        return memory

    async def get_recent_memories(
        self, run_id: UUID, agent_id: str, limit: int = 10
    ) -> List[str]:
        """
        Get N most recent memories as strings.

        Simple FIFO retrieval: just get the most recent N memories
        by tick number, regardless of importance or relevance.

        Args:
            run_id: Simulation run identifier
            agent_id: Agent identifier
            limit: Maximum memories to return

        Returns:
            List of memory content strings (most recent first)
        """
        memories = await self.persistence.get_recent_memories(run_id, agent_id, limit)
        return [m.content for m in memories]

    async def get_relevant_memories(
        self,
        run_id: UUID,
        agent_id: str,
        query: str,
        limit: int = 5,
    ) -> List[str]:
        """
        Get relevant memories (simple implementation: just recent).

        This simple implementation doesn't do semantic search,
        just returns recent memories. Advanced implementations
        would use embeddings, keyword matching, etc.

        Args:
            run_id: Simulation run identifier
            agent_id: Agent identifier
            query: Query string (unused in simple implementation)
            limit: Maximum memories to return

        Returns:
            List of recent memory content strings
        """
        query = query.lower().strip()
        if not query:
            return await self.get_recent_memories(run_id, agent_id, limit)

        terms = [term for term in query.replace(",", " ").split() if term]
        if not terms:
            return await self.get_recent_memories(run_id, agent_id, limit)

        # Fetch a broader window so we can compute a lightweight score.
        candidate_memories = await self.persistence.get_recent_memories(
            run_id, agent_id, max(limit * 5, limit)
        )
        if not candidate_memories:
            return []

        most_recent_tick = candidate_memories[0].tick
        scores: List[tuple[float, str]] = []

        for mem in candidate_memories:
            text = mem.content.lower()
            tag_text = " ".join(mem.tags).lower()
            score = 0.0

            for term in terms:
                if term in text:
                    score += 2.0
                if term in tag_text:
                    score += 1.0

            if score <= 0.0:
                continue

            # Favor fresher memories without ignoring high-importance items.
            recency_delta = max(most_recent_tick - mem.tick, 0)
            recency_boost = 1.0 / (1.0 + recency_delta)
            score += recency_boost

            # Importance gives a gentle push so high-salience items stay near the top.
            score += mem.importance * 0.1

            scores.append((score, mem.content))

        if not scores:
            return await self.get_recent_memories(run_id, agent_id, limit)

        scores.sort(key=lambda item: item[0], reverse=True)
        return [content for _, content in scores[:limit]]

    async def clear_agent_memories(self, run_id: UUID, agent_id: str) -> None:
        """
        Clear all memories for an agent.

        Delegates to persistence layer which handles the actual deletion.

        Args:
            run_id: Simulation run identifier
            agent_id: Agent identifier
        """
        await self.persistence.clear_agent_memories(run_id, agent_id)


class ImportanceWeightedMemory(MemoryStrategy):
    """Memory retrieval weighted by both recency and importance."""

    def __init__(
        self,
        persistence,
        *,
        recency_weight: float = 0.65,
        importance_weight: float = 0.35,
        window: int = 100,
    ) -> None:
        self.persistence = persistence
        self.recency_weight = recency_weight
        self.importance_weight = importance_weight
        self.window = max(window, 1)

    async def initialize(self) -> None:  # pragma: no cover - delegate to persistence
        pass

    async def close(self) -> None:  # pragma: no cover - delegate to persistence
        pass

    async def add_memory(
        self,
        run_id: UUID,
        agent_id: str,
        tick: int,
        memory_type: str,
        content: str,
        importance: int = 5,
        *,
        tags: Optional[List[str]] = None,
        metadata: Optional[Dict[str, Any]] = None,
        embedding_key: Optional[str] = None,
        branch_id: Optional[str] = None,
    ) -> AgentMemory:
        import uuid

        memory = AgentMemory(
            id=uuid.uuid4(),
            run_id=run_id,
            agent_id=agent_id,
            tick=tick,
            memory_type=memory_type,
            content=content,
            importance=importance,
            tags=tags or [],
            metadata=metadata or {},
            embedding_key=embedding_key,
            branch_id=branch_id,
            created_at=datetime.now(),
        )

        await self.persistence.save_memory(run_id, memory)
        return memory

    async def get_recent_memories(
        self, run_id: UUID, agent_id: str, limit: int = 10
    ) -> List[str]:
        memories = await self.persistence.get_recent_memories(run_id, agent_id, limit)
        return [m.content for m in memories]

    async def get_relevant_memories(
        self,
        run_id: UUID,
        agent_id: str,
        query: str,
        limit: int = 5,
    ) -> List[str]:
        # Grab a fixed window and score each entry using a convex combination of
        # normalized recency and importance. A non-empty query boosts keyword matches.
        candidate_memories = await self.persistence.get_recent_memories(
            run_id, agent_id, self.window
        )
        if not candidate_memories:
            return []

        most_recent_tick = candidate_memories[0].tick
        normalized_query = query.lower().strip()
        terms = [term for term in normalized_query.replace(",", " ").split() if term]

        scored: List[tuple[float, str]] = []
        for mem in candidate_memories:
            # Recency normalized to [0, 1].
            recency_delta = max(most_recent_tick - mem.tick, 0)
            recency = 1.0 / (1.0 + recency_delta)
            importance = mem.importance / 10.0
            base_score = (
                self.recency_weight * recency
                + self.importance_weight * importance
            )

            if not terms:
                scored.append((base_score, mem.content))
                continue

            text = mem.content.lower()
            tag_blob = " ".join(mem.tags).lower()
            keyword_score = 0.0
            for term in terms:
                if term in text:
                    keyword_score += 1.5
                if term in tag_blob:
                    keyword_score += 0.75

            if keyword_score <= 0.0:
                continue

            scored.append((base_score + keyword_score, mem.content))

        if not scored:
            # Fall back to generic recency/importance ordering if no keyword match.
            scored = [
                (
                    self.recency_weight
                    * (1.0 / (1.0 + max(most_recent_tick - mem.tick, 0)))
                    + self.importance_weight * (mem.importance / 10.0),
                    mem.content,
                )
                for mem in candidate_memories[: limit * 2]
            ]

        scored.sort(key=lambda item: item[0], reverse=True)
        return [content for _, content in scored[:limit]]

    async def clear_agent_memories(self, run_id: UUID, agent_id: str) -> None:
        """
        Clear all memories for an agent.

        Delegates to persistence layer which handles the actual deletion.

        Args:
            run_id: Simulation run identifier
            agent_id: Agent identifier
        """
        await self.persistence.clear_agent_memories(run_id, agent_id)


class BM25MemoryStrategy(MemoryStrategy):
    """
    Memory retrieval using BM25 ranking combined with recency and importance.

    This strategy implements proper information retrieval scoring:
    - BM25 for term relevance (handles term frequency, document frequency, length normalization)
    - Recency decay (fresher memories get a boost)
    - Importance weighting (high-salience memories surface)

    Good for:
    - Medium to long simulations (>50 ticks)
    - Scenarios where context relevance matters
    - Research requiring realistic memory recall

    Based on:
    - BM25 Okapi (Robertson et al.)
    - Stanford Generative Agents memory architecture
    """

    def __init__(
        self,
        persistence,
        *,
        bm25_weight: float = 0.5,
        recency_weight: float = 0.3,
        importance_weight: float = 0.2,
        window: int = 200,
        k1: float = 1.5,
        b: float = 0.75,
    ) -> None:
        """
        Initialize BM25 memory strategy.

        Args:
            persistence: PersistenceStrategy instance for storing memories
            bm25_weight: Weight for BM25 relevance score (0-1)
            recency_weight: Weight for recency (0-1)
            importance_weight: Weight for importance score (0-1)
            window: Maximum memories to consider for retrieval
            k1: BM25 term frequency saturation parameter
            b: BM25 document length normalization parameter
        """
        self.persistence = persistence
        self.bm25_weight = bm25_weight
        self.recency_weight = recency_weight
        self.importance_weight = importance_weight
        self.window = max(window, 1)
        self.k1 = k1
        self.b = b

    async def initialize(self) -> None:
        pass

    async def close(self) -> None:
        pass

    async def add_memory(
        self,
        run_id: UUID,
        agent_id: str,
        tick: int,
        memory_type: str,
        content: str,
        importance: int = 5,
        *,
        tags: Optional[List[str]] = None,
        metadata: Optional[Dict[str, Any]] = None,
        embedding_key: Optional[str] = None,
        branch_id: Optional[str] = None,
    ) -> AgentMemory:
        import uuid

        memory = AgentMemory(
            id=uuid.uuid4(),
            run_id=run_id,
            agent_id=agent_id,
            tick=tick,
            memory_type=memory_type,
            content=content,
            importance=importance,
            tags=tags or [],
            metadata=metadata or {},
            embedding_key=embedding_key,
            branch_id=branch_id,
            created_at=datetime.now(),
        )

        await self.persistence.save_memory(run_id, memory)
        return memory

    async def get_recent_memories(
        self, run_id: UUID, agent_id: str, limit: int = 10
    ) -> List[str]:
        memories = await self.persistence.get_recent_memories(run_id, agent_id, limit)
        return [m.content for m in memories]

    async def get_relevant_memories(
        self,
        run_id: UUID,
        agent_id: str,
        query: str,
        limit: int = 5,
    ) -> List[str]:
        """
        Retrieve memories relevant to a query using BM25 + recency + importance.

        The scoring formula is:
            final_score = bm25_weight * normalized_bm25
                        + recency_weight * recency_score
                        + importance_weight * normalized_importance

        Args:
            run_id: Simulation run identifier
            agent_id: Agent identifier
            query: Query string to find relevant memories
            limit: Maximum number of memories to return

        Returns:
            List of relevant memory content strings
        """
        # Fetch candidate memories
        candidate_memories = await self.persistence.get_recent_memories(
            run_id, agent_id, self.window
        )
        if not candidate_memories:
            return []

        # Tokenize query
        query_tokens = tokenize(query)
        if not query_tokens:
            # No valid query tokens - fall back to recency + importance
            return await self._score_without_query(candidate_memories, limit)

        # Build document corpus for BM25: (content, tokens)
        documents: List[Tuple[str, List[str]]] = []
        memory_map: Dict[str, AgentMemory] = {}
        for mem in candidate_memories:
            # Include tags in searchable text
            full_text = mem.content + " " + " ".join(mem.tags)
            tokens = tokenize(full_text)
            documents.append((mem.content, tokens))
            memory_map[mem.content] = mem

        # Compute BM25 scores
        bm25_results = compute_bm25_scores(query_tokens, documents, self.k1, self.b)

        if not bm25_results:
            # No BM25 matches - fall back to recency + importance
            return await self._score_without_query(candidate_memories, limit)

        # Normalize BM25 scores to [0, 1]
        max_bm25 = max(score for _, score in bm25_results) if bm25_results else 1.0
        bm25_scores = {content: score / max_bm25 for content, score in bm25_results}

        # Calculate recency and importance for matched documents
        most_recent_tick = candidate_memories[0].tick
        scored: List[Tuple[float, str]] = []

        for content in bm25_scores:
            mem = memory_map[content]

            # Recency: exponential decay from most recent
            recency_delta = max(most_recent_tick - mem.tick, 0)
            recency_score = 1.0 / (1.0 + recency_delta)

            # Importance: normalized to [0, 1]
            importance_score = mem.importance / 10.0

            # BM25 score (already normalized)
            bm25_score = bm25_scores[content]

            # Combined weighted score
            final_score = (
                self.bm25_weight * bm25_score
                + self.recency_weight * recency_score
                + self.importance_weight * importance_score
            )

            scored.append((final_score, content))

        # Sort by final score and return top results
        scored.sort(key=lambda x: x[0], reverse=True)
        return [content for _, content in scored[:limit]]

    async def _score_without_query(
        self, memories: List[AgentMemory], limit: int
    ) -> List[str]:
        """Score memories using only recency and importance when no query matches."""
        if not memories:
            return []

        most_recent_tick = memories[0].tick
        scored: List[Tuple[float, str]] = []

        for mem in memories[:limit * 2]:
            recency_delta = max(most_recent_tick - mem.tick, 0)
            recency_score = 1.0 / (1.0 + recency_delta)
            importance_score = mem.importance / 10.0

            # Adjust weights when no BM25: recency + importance only
            adjusted_recency_weight = self.recency_weight / (
                self.recency_weight + self.importance_weight
            )
            adjusted_importance_weight = self.importance_weight / (
                self.recency_weight + self.importance_weight
            )

            final_score = (
                adjusted_recency_weight * recency_score
                + adjusted_importance_weight * importance_score
            )
            scored.append((final_score, mem.content))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [content for _, content in scored[:limit]]

    async def clear_agent_memories(self, run_id: UUID, agent_id: str) -> None:
        await self.persistence.clear_agent_memories(run_id, agent_id)
