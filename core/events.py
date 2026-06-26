"""
Sorachio-STS Event Bus
Lightweight async event system for cross-component signaling.

Events:
  - INTERRUPT: stop TTS and clear queue
  - USER_SPEECH_START: user has begun speaking
  - USER_SPEECH_END: user has finished speaking
  - PIPELINE_IDLE: pipeline is ready for next input
  - SHUTDOWN: graceful shutdown signal
  - STT_RESULT: transcribed text available
  - COGNITIVE_RESULT: cognitive gateway JSON decision
  - RESPONSE_START: LLM #2 started streaming
  - RESPONSE_END: LLM #2 finished streaming
  - TTS_CHUNK_READY: audio chunk ready for playback
  - PLAYBACK_STARTED: audio playback began
  - PLAYBACK_FINISHED: audio playback completed
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Callable, Dict, List, Optional
from datetime import datetime

from utils.logging_setup import get_logger

log = get_logger("events")


# ---------------------------------------------------------------------------
# Event Types
# ---------------------------------------------------------------------------

class EventType(Enum):
    # Lifecycle
    STARTUP = auto()
    SHUTDOWN = auto()
    PIPELINE_IDLE = auto()

    # Audio / VAD
    USER_SPEECH_START = auto()
    USER_SPEECH_END = auto()
    INTERRUPT = auto()

    # STT
    STT_RESULT = auto()

    # Cognitive
    COGNITIVE_RESULT = auto()

    # LLM #2
    RESPONSE_START = auto()
    RESPONSE_TOKEN = auto()
    RESPONSE_END = auto()

    # TTS
    TTS_CHUNK_READY = auto()

    # Playback
    PLAYBACK_STARTED = auto()
    PLAYBACK_FINISHED = auto()

    # Memory
    MEMORY_STORED = auto()

    # Error
    ERROR = auto()


# ---------------------------------------------------------------------------
# Event dataclass
# ---------------------------------------------------------------------------

@dataclass
class Event:
    type: EventType
    data: Any = None
    source: str = "unknown"
    timestamp: datetime = field(default_factory=datetime.now)

    def __repr__(self) -> str:
        data_repr = str(self.data)[:80] if self.data else "None"
        return f"Event({self.type.name}, src={self.source}, data={data_repr!r})"


# ---------------------------------------------------------------------------
# Event Bus
# ---------------------------------------------------------------------------

HandlerFn = Callable[[Event], Any]


class EventBus:
    """
    Simple async publish/subscribe event bus.

    Components subscribe to event types and publish events.
    All handlers are called asynchronously (as asyncio tasks).
    """

    def __init__(self) -> None:
        self._handlers: Dict[EventType, List[HandlerFn]] = {}
        self._global_handlers: List[HandlerFn] = []

    def subscribe(self, event_type: EventType, handler: HandlerFn) -> None:
        """Register a handler for a specific event type."""
        if event_type not in self._handlers:
            self._handlers[event_type] = []
        self._handlers[event_type].append(handler)
        log.debug(f"Subscribed {handler.__name__} to {event_type.name}")

    def subscribe_all(self, handler: HandlerFn) -> None:
        """Register a handler for ALL event types."""
        self._global_handlers.append(handler)

    def unsubscribe(self, event_type: EventType, handler: HandlerFn) -> None:
        """Remove a handler."""
        if event_type in self._handlers:
            self._handlers[event_type] = [
                h for h in self._handlers[event_type] if h != handler
            ]

    async def publish(self, event: Event) -> None:
        """Publish an event. All handlers called as async tasks."""
        log.debug(f"Publishing: {event}")

        handlers = self._handlers.get(event.type, []) + self._global_handlers

        for handler in handlers:
            try:
                result = handler(event)
                if asyncio.iscoroutine(result):
                    asyncio.create_task(result)
            except Exception as e:
                log.error(f"Handler {handler.__name__} failed: {e}", exc_info=True)

    async def emit(
        self,
        event_type: EventType,
        data: Any = None,
        source: str = "unknown",
    ) -> None:
        """Shorthand to create and publish an event."""
        await self.publish(Event(type=event_type, data=data, source=source))


# ---------------------------------------------------------------------------
# Global bus singleton
# ---------------------------------------------------------------------------

_bus: Optional[EventBus] = None


def get_bus() -> EventBus:
    """Get the global event bus singleton."""
    global _bus
    if _bus is None:
        _bus = EventBus()
    return _bus


def reset_bus() -> EventBus:
    """Reset and return a fresh event bus (for testing)."""
    global _bus
    _bus = EventBus()
    return _bus
