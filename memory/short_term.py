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
from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Deque, Dict, List, Optional, Any

from utils.logging_setup import get_logger

log = get_logger("memory.stm")


# ---------------------------------------------------------------------------
# Message entry
# ---------------------------------------------------------------------------

@dataclass
class STMEntry:
    role: str                       # "user" | "assistant"
    content: str
    timestamp: datetime = field(default_factory=datetime.now)
    emotion: str = "neutral"
    topic: str = "general"
    importance: float = 0.5

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["timestamp"] = self.timestamp.isoformat()
        return d

    def to_chat_message(self) -> Dict[str, str]:
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
        self._window: Deque[STMEntry] = deque(maxlen=max_messages)
        self._lock = asyncio.Lock()
        self._turn_count = 0

    async def add(
        self,
        role: str,
        content: str,
        emotion: str = "neutral",
        topic: str = "general",
        importance: float = 0.5,
    ) -> None:
        """Add a message to the rolling window."""
        async with self._lock:
            entry = STMEntry(
                role=role,
                content=content,
                emotion=emotion,
                topic=topic,
                importance=importance,
            )
            self._window.append(entry)
            if role == "user":
                self._turn_count += 1
            log.debug(f"[STM] Added [{role}] len={len(self._window)}")

    async def get_recent(self, n: Optional[int] = None) -> List[STMEntry]:
        """Get the N most recent entries (or all if n=None)."""
        async with self._lock:
            entries = list(self._window)
            if n is not None:
                entries = entries[-n:]
            return entries

    async def get_chat_messages(self, n: Optional[int] = None) -> List[Dict[str, str]]:
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
