"""
Sorachio-STS Vector Store
ChromaDB-based vector similarity search for long-term memory retrieval.

Replaces keyword matching with semantic embedding search for more
relevant memory recall. Uses sentence-transformers for embeddings.

Features:
  - ChromaDB persistent storage
  - Sentence-transformers embeddings (all-MiniLM-L6-v2)
  - Semantic similarity search
  - Automatic embedding on store
  - Graceful fallback if ChromaDB unavailable
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from utils.logging_setup import get_logger

log = get_logger("memory.vector")


class VectorStore:
    """
    ChromaDB-backed vector store for semantic memory retrieval.
    """

    def __init__(
        self,
        storage_path: str = "data/memory/chroma",
        embedding_model: str = "all-MiniLM-L6-v2",
    ):
        self.storage_path = Path(storage_path)
        self.embedding_model = embedding_model
        self._collection = None
        self._embedding_fn = None
        self._available = False

    async def initialize(self) -> bool:
        """Initialize ChromaDB and sentence-transformers."""
        loop = asyncio.get_event_loop()
        ok = await loop.run_in_executor(None, self._init_sync)
        return ok

    def _init_sync(self) -> bool:
        """Synchronous initialization (runs in executor)."""
        try:
            import chromadb  # type: ignore[import-untyped]
            from chromadb.config import Settings as ChromaSettings  # type: ignore[import-untyped]

            self.storage_path.mkdir(parents=True, exist_ok=True)

            self._client = chromadb.PersistentClient(
                path=str(self.storage_path),
                settings=ChromaSettings(anonymized_telemetry=False),
            )

            self._collection = self._client.get_or_create_collection(
                name="memories",
                metadata={"hnsw:space": "cosine"},
            )

            log.info(
                f"[VectorStore] ChromaDB initialized — "
                f"collection size: {self._collection.count()}"
            )
            self._available = True
            return True

        except ImportError:
            log.warning(
                "[VectorStore] chromadb not installed. "
                "Install with: pip install chromadb"
            )
            return False
        except Exception as e:
            log.error(f"[VectorStore] Init failed: {e}")
            return False

    @property
    def available(self) -> bool:
        return self._available

    async def add(
        self,
        entry_id: str,
        content: str,
        metadata: dict[str, Any] | None = None,
    ) -> bool:
        """Add a memory entry with embedding."""
        if not self._available:
            return False

        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None, self._add_sync, entry_id, content, metadata or {}
        )

    def _add_sync(
        self,
        entry_id: str,
        content: str,
        metadata: dict[str, Any],
    ) -> bool:
        """Synchronous add (runs in executor)."""
        try:
            if not self._collection:
                return False

            # Convert metadata values to strings for ChromaDB
            chroma_metadata = {}
            for k, v in metadata.items():
                if isinstance(v, (str, int, float, bool)):
                    chroma_metadata[k] = str(v)
                else:
                    chroma_metadata[k] = str(v)

            self._collection.upsert(
                ids=[entry_id],
                documents=[content],
                metadatas=[chroma_metadata],
            )
            log.debug(f"[VectorStore] Added entry {entry_id}")
            return True

        except Exception as e:
            log.error(f"[VectorStore] Add failed: {e}")
            return False

    async def query(
        self,
        query_text: str,
        n_results: int = 5,
        where: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """Query similar memories by semantic search."""
        if not self._available:
            return []

        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None, self._query_sync, query_text, n_results, where
        )

    def _query_sync(
        self,
        query_text: str,
        n_results: int,
        where: dict[str, Any] | None,
    ) -> list[dict[str, Any]]:
        """Synchronous query (runs in executor)."""
        try:
            if not self._collection:
                return []

            kwargs: dict[str, Any] = {
                "query_texts": [query_text],
                "n_results": n_results,
            }
            if where:
                kwargs["where"] = where

            results = self._collection.query(**kwargs)

            entries = []
            if results and results["ids"] and results["ids"][0]:
                for i, entry_id in enumerate(results["ids"][0]):
                    entry = {
                        "id": entry_id,
                        "content": results["documents"][0][i] if results["documents"] else "",
                        "metadata": results["metadatas"][0][i] if results["metadatas"] else {},
                        "distance": results["distances"][0][i] if results["distances"] else 0.0,
                    }
                    entries.append(entry)

            log.debug(f"[VectorStore] Query returned {len(entries)} results")
            return entries

        except Exception as e:
            log.error(f"[VectorStore] Query failed: {e}")
            return []

    async def delete(self, entry_id: str) -> bool:
        """Delete a memory entry."""
        if not self._available:
            return False

        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._delete_sync, entry_id)

    def _delete_sync(self, entry_id: str) -> bool:
        """Synchronous delete (runs in executor)."""
        try:
            if not self._collection:
                return False

            self._collection.delete(ids=[entry_id])
            log.debug(f"[VectorStore] Deleted entry {entry_id}")
            return True
        except Exception as e:
            log.error(f"[VectorStore] Delete failed: {e}")
            return False

    async def count(self) -> int:
        """Return number of entries in the store."""
        if not self._available or not self._collection:
            return 0
        return self._collection.count()
