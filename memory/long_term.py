"""
Sorachio-STS Long-Term Memory (LTM)
JSON-backed persistent memory with keyword retrieval and importance scoring.

Storage format: data/memory/ltm.json

Designed for future vector DB migration (ChromaDB, FAISS, etc.)
Each memory entry has:
  - id, content, topic, emotion
  - importance (0.0–1.0)
  - keywords (for retrieval)
  - created_at, accessed_at, access_count
  - metadata (extensible dict)
"""

from __future__ import annotations

import asyncio
import json
import re
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

import aiofiles

from utils.logging_setup import get_logger

log = get_logger("memory.ltm")


# ---------------------------------------------------------------------------
# LTM Entry
# ---------------------------------------------------------------------------

class LTMEntry:
    def __init__(
        self,
        content: str,
        topic: str = "general",
        emotion: str = "neutral",
        importance: float = 0.5,
        keywords: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
        entry_id: str | None = None,
    ):
        self.id = entry_id or str(uuid.uuid4())[:8]
        self.content = content
        self.topic = topic
        self.emotion = emotion
        self.importance = importance
        self.keywords = keywords or []
        self.metadata = metadata or {}
        self.created_at = datetime.now().isoformat()
        self.accessed_at = self.created_at
        self.access_count = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "content": self.content,
            "topic": self.topic,
            "emotion": self.emotion,
            "importance": self.importance,
            "keywords": self.keywords,
            "metadata": self.metadata,
            "created_at": self.created_at,
            "accessed_at": self.accessed_at,
            "access_count": self.access_count,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> LTMEntry:
        entry = cls(
            content=d["content"],
            topic=d.get("topic", "general"),
            emotion=d.get("emotion", "neutral"),
            importance=d.get("importance", 0.5),
            keywords=d.get("keywords", []),
            metadata=d.get("metadata", {}),
            entry_id=d.get("id"),
        )
        entry.created_at = d.get("created_at", entry.created_at)
        entry.accessed_at = d.get("accessed_at", entry.accessed_at)
        entry.access_count = d.get("access_count", 0)
        return entry

    def relevance_score(self, query_keywords: list[str]) -> float:
        """Compute relevance score given query keywords."""
        if not query_keywords:
            return self.importance

        content_lower = self.content.lower()
        kw_lower = [k.lower() for k in self.keywords]

        matches = 0
        for q in query_keywords:
            q = q.lower()
            if q in content_lower:
                matches += 1
            if q in kw_lower:
                matches += 0.5  # bonus for indexed keyword match

        keyword_score = min(1.0, matches / max(len(query_keywords), 1))

        # Recency factor: more recent = slightly higher
        try:
            created = datetime.fromisoformat(self.created_at)
            age_days = (datetime.now() - created).days
            recency = max(0.0, 1.0 - age_days / 365.0)
        except Exception:
            recency = 0.5

        return (
            keyword_score * 0.5
            + self.importance * 0.3
            + recency * 0.2
        )


# ---------------------------------------------------------------------------
# Long-Term Memory
# ---------------------------------------------------------------------------

class LongTermMemory:
    """
    JSON-backed persistent long-term memory.

    Features:
      - Store memories with importance scoring
      - Keyword-based retrieval
      - Persistence across sessions
      - Access tracking
      - Future vector DB compatibility
    """

    def __init__(
        self,
        storage_path: str = "data/memory/ltm.json",
        max_entries: int = 500,
        importance_threshold: float = 0.5,
        retrieval_top_k: int = 5,
    ):
        self.storage_path = Path(storage_path)
        self.max_entries = max_entries
        self.importance_threshold = importance_threshold
        self.retrieval_top_k = retrieval_top_k
        self._entries: list[LTMEntry] = []
        self._lock = asyncio.Lock()
        self._dirty = False

    async def initialize(self) -> None:
        """Load existing memories from disk."""
        self.storage_path.parent.mkdir(parents=True, exist_ok=True)
        await self._load()
        log.info(f"[LTM] Loaded {len(self._entries)} memories from {self.storage_path}")

    async def store(
        self,
        content: str,
        topic: str = "general",
        emotion: str = "neutral",
        importance: float = 0.5,
        keywords: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> LTMEntry | None:
        """
        Store a new memory if it meets the importance threshold.
        Returns the stored entry or None if skipped.
        """
        if importance < self.importance_threshold:
            log.debug(
                f"[LTM] Skipped (importance {importance:.2f} < threshold "
                f"{self.importance_threshold:.2f}): {content[:50]!r}"
            )
            return None

        # Auto-extract keywords if not provided
        if keywords is None:
            keywords = self._extract_keywords(content)

        entry = LTMEntry(
            content=content,
            topic=topic,
            emotion=emotion,
            importance=importance,
            keywords=keywords,
            metadata=metadata,
        )

        async with self._lock:
            self._entries.append(entry)
            # Prune if over max
            if len(self._entries) > self.max_entries:
                # Remove least important old entries
                self._entries.sort(key=lambda e: (e.importance, e.accessed_at))
                self._entries = self._entries[-(self.max_entries):]
            self._dirty = True

        await self._save()
        log.info(f"[LTM] Stored [{entry.id}] topic={topic} importance={importance:.2f}: {content[:60]!r}")
        return entry

    async def retrieve(
        self,
        queries: list[str],
        top_k: int | None = None,
    ) -> list[LTMEntry]:
        """
        Retrieve top-K most relevant memories for given query keywords.
        """
        k = top_k or self.retrieval_top_k
        if not queries:
            # Return most important recent memories
            async with self._lock:
                sorted_entries = sorted(
                    self._entries,
                    key=lambda e: e.importance,
                    reverse=True,
                )
            return sorted_entries[:k]

        async with self._lock:
            scored = [
                (e, e.relevance_score(queries))
                for e in self._entries
            ]

        scored.sort(key=lambda x: x[1], reverse=True)
        results = [e for e, score in scored[:k] if score > 0.1]

        # Track access
        async with self._lock:
            now = datetime.now().isoformat()
            for entry in results:
                entry.accessed_at = now
                entry.access_count += 1
            if results:
                self._dirty = True

        if results:
            await self._save()

        log.debug(f"[LTM] Retrieved {len(results)} memories for queries: {queries}")
        return results

    def format_for_context(self, entries: list[LTMEntry]) -> str:
        """Format retrieved memories as a context string for LLM."""
        if not entries:
            return ""
        lines = ["[Relevant memories about the user:]"]
        for e in entries:
            lines.append(f"- [{e.topic}] {e.content}")
        return "\n".join(lines)

    async def _load(self) -> None:
        """Load memories from JSON file."""
        if not self.storage_path.exists():
            self._entries = []
            return
        try:
            async with aiofiles.open(self.storage_path, encoding="utf-8") as f:
                raw = await f.read()
            data = json.loads(raw)
            self._entries = [LTMEntry.from_dict(d) for d in data.get("memories", [])]
        except Exception as e:
            log.error(f"[LTM] Failed to load: {e}")
            self._entries = []

    async def _save(self) -> None:
        """Persist memories to JSON file."""
        async with self._lock:
            data = {"memories": [e.to_dict() for e in self._entries]}
            self._dirty = False

        try:
            self.storage_path.parent.mkdir(parents=True, exist_ok=True)
            async with aiofiles.open(self.storage_path, "w", encoding="utf-8") as f:
                await f.write(json.dumps(data, indent=2, ensure_ascii=False))
        except Exception as e:
            log.error(f"[LTM] Failed to save: {e}")

    def _extract_keywords(self, text: str) -> list[str]:
        """Simple keyword extraction (stopword removal)."""
        stopwords = {
            "i", "me", "my", "you", "your", "we", "they", "it", "is", "am",
            "are", "was", "were", "be", "been", "being", "have", "has", "had",
            "do", "does", "did", "will", "would", "could", "should", "may",
            "might", "can", "shall", "a", "an", "the", "and", "but", "or",
            "in", "on", "at", "to", "for", "of", "with", "by", "from",
            "that", "this", "these", "those", "not", "no", "so", "as", "if",
        }
        words = re.findall(r"\b[a-zA-Z]{3,}\b", text.lower())
        keywords = [w for w in words if w not in stopwords]
        # Return unique, most distinctive (longer) words first
        seen = set()
        result = []
        for w in sorted(keywords, key=len, reverse=True):
            if w not in seen:
                seen.add(w)
                result.append(w)
                if len(result) >= 10:
                    break
        return result

    async def get_stats(self) -> dict[str, Any]:
        async with self._lock:
            return {
                "total_memories": len(self._entries),
                "storage_path": str(self.storage_path),
                "importance_threshold": self.importance_threshold,
            }
