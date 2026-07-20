"""
Sorachio-STS Short-Term Memory (STM)
Rolling conversation window with emotional metadata.

Provides:
  - Append new messages with emotion + timestamps
  - Retrieve recent N messages
  - Conversation window for LLM context
  - Thread-safe asyncio access
"""

from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any

from utils.logging_setup import get_logger

log = get_logger("memory.stm")


# ---------------------------------------------------------------------------
# Message entry
# ---------------------------------------------------------------------------

@dataclass
class STMEntry:
    role: str                       # "user" | "assistant" | "system"
    content: str
    timestamp: datetime = field(default_factory=datetime.now)
    emotion: str = "neutral"
    topic: str = "general"
    importance: float = 0.5
    # Generic metadata bag — supports interrupt markers, sensor events,
    # vision data, and any future structured annotations.
    # Example: {"interrupted": True} or {"vision": "face_detected"}
    metadata: dict = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["timestamp"] = self.timestamp.isoformat()
        return d

    def to_chat_message(self) -> dict[str, str]:
        """Format as LLM chat message."""
        return {"role": self.role, "content": self.content}


# ---------------------------------------------------------------------------
# Short-Term Memory
# ---------------------------------------------------------------------------

class ShortTermMemory:
    """
    Rolling conversation window.

    Stores recent messages with emotional metadata.
    Thread-safe via asyncio lock.
    """

    def __init__(
        self,
        max_messages: int = 20,
        include_emotions: bool = True,
    ):
        self.max_messages = max_messages
        self.include_emotions = include_emotions
        self._window: deque[STMEntry] = deque(maxlen=max_messages)
        self._lock = asyncio.Lock()
        self._turn_count = 0

    async def add(
        self,
        role: str,
        content: str,
        emotion: str = "neutral",
        topic: str = "general",
        importance: float = 0.5,
        metadata: dict | None = None,
    ) -> None:
        """Add a message to the rolling window."""
        async with self._lock:
            entry = STMEntry(
                role=role,
                content=content,
                emotion=emotion,
                topic=topic,
                importance=importance,
                metadata=metadata or {},
            )
            self._window.append(entry)
            if role == "user":
                self._turn_count += 1
            log.debug(f"[STM] Added [{role}] len={len(self._window)}")

    async def get_recent(self, n: int | None = None) -> list[STMEntry]:
        """Get the N most recent entries (or all if n=None)."""
        async with self._lock:
            entries = list(self._window)
            if n is not None:
                entries = entries[-n:]
            return entries

    async def mark_last_interrupted(self) -> None:
        """Mark the most recent assistant message in the window as interrupted."""
        async with self._lock:
            if not self._window:
                return
            # Find the last message (usually assistant) and mark it
            self._window[-1].metadata["interrupted"] = True
            log.debug(f"[STM] Marked last message ({self._window[-1].role}) as interrupted")

    async def get_chat_messages(self, n: int | None = None) -> list[dict[str, str]]:
        """Get recent entries formatted as LLM chat messages."""
        entries = await self.get_recent(n)
        return [e.to_chat_message() for e in entries]

    async def get_emotion_context(self) -> str:
        """Return a brief emotion summary from recent messages."""
        async with self._lock:
            recent = list(self._window)[-5:]
            if not recent:
                return "neutral"
            user_emotions = [e.emotion for e in recent if e.role == "user"]
            if not user_emotions:
                return "neutral"
            # Return the most recent non-neutral emotion, else last emotion
            for emotion in reversed(user_emotions):
                if emotion != "neutral":
                    return emotion
            return user_emotions[-1]

    async def clear(self) -> None:
        """Clear conversation history."""
        async with self._lock:
            self._window.clear()
            self._turn_count = 0

    @property
    def turn_count(self) -> int:
        return self._turn_count

    async def size(self) -> int:
        async with self._lock:
            return len(self._window)
