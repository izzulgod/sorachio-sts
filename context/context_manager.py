"""
Sorachio-STS Context Manager
Assembles the final prompt for LLM #2 (Personality Core).

Merges:
  1. Personality/system prompt
  2. Retrieved LTM memories (relevant to current query)
  3. Short-term conversation history (rolling window)
  4. Current emotional state
  5. Current user input

This produces a rich, context-aware prompt for natural conversation.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from memory.short_term import ShortTermMemory
from memory.long_term import LongTermMemory
from utils.logging_setup import get_logger

log = get_logger("context.manager")


# ---------------------------------------------------------------------------
# ContextManager
# ---------------------------------------------------------------------------

class ContextManager:
    """
    Assembles the final LLM #2 prompt from all context sources.
    """

    def __init__(
        self,
        stm: ShortTermMemory,
        ltm: LongTermMemory,
        personality_prompt: str,
        companion_name: str = "Sorachio",
        max_stm_in_prompt: int = 10,
        max_ltm_in_prompt: int = 3,
        include_emotional_state: bool = True,
    ):
        self.stm = stm
        self.ltm = ltm
        self.personality_prompt = personality_prompt
        self.companion_name = companion_name
        self.max_stm_in_prompt = max_stm_in_prompt
        self.max_ltm_in_prompt = max_ltm_in_prompt
        self.include_emotional_state = include_emotional_state

    async def build_prompt(
        self,
        user_input: str,
        cognitive_decision: Dict[str, Any],
    ) -> List[Dict[str, str]]:
        """
        Build the full message list for LLM #2.

        Args:
            user_input: The current user speech transcript
            cognitive_decision: JSON dict from Cognitive Gateway

        Returns:
            List of chat messages for the LLM API
        """
        # Extract cognitive metadata
        emotion = cognitive_decision.get("emotion", "neutral")
        topic = cognitive_decision.get("topic", "general")
        memory_queries = cognitive_decision.get("memory_queries", [])

        # Retrieve relevant LTM memories
        ltm_entries = []
        if memory_queries:
            ltm_entries = await self.ltm.retrieve(
                queries=memory_queries,
                top_k=self.max_ltm_in_prompt,
            )

        # Build system prompt
        system_content = self._build_system_prompt(
            emotion=emotion,
            topic=topic,
            ltm_entries_text=self.ltm.format_for_context(ltm_entries),
        )

        # Build message list
        messages: List[Dict[str, str]] = [
            {"role": "system", "content": system_content},
        ]

        # Add STM history
        recent = await self.stm.get_chat_messages(n=self.max_stm_in_prompt)
        messages.extend(recent)

        # Add current user message
        messages.append({"role": "user", "content": user_input})

        log.debug(
            f"[Context] Built prompt: {len(messages)} messages, "
            f"emotion={emotion}, topic={topic}, "
            f"ltm_hits={len(ltm_entries)}"
        )
        return messages

    def _build_system_prompt(
        self,
        emotion: str,
        topic: str,
        ltm_entries_text: str,
    ) -> str:
        """Construct the system prompt with all contextual additions."""
        parts = [self.personality_prompt.strip()]

        # Emotional awareness context
        if self.include_emotional_state and emotion != "neutral":
            parts.append(
                f"\n[Current emotional context: The user seems {emotion}. "
                f"Respond with appropriate empathy and care.]"
            )

        # Topic context
        if topic and topic not in ("general", "unknown"):
            parts.append(
                f"[Current topic: {topic}]"
            )

        # LTM memories
        if ltm_entries_text:
            parts.append(f"\n{ltm_entries_text}")

        parts.append(
            f"\nYou are {self.companion_name}. Respond naturally in 1-3 spoken sentences. "
            "Do NOT use markdown, bullet points, or lists. Speak conversationally."
        )

        return "\n".join(parts)

    async def store_interaction(
        self,
        user_input: str,
        assistant_response: str,
        cognitive_decision: Dict[str, Any],
    ) -> None:
        """
        Store this interaction in STM and optionally LTM.

        Called after a successful response has been generated.
        """
        emotion = cognitive_decision.get("emotion", "neutral")
        topic = cognitive_decision.get("topic", "general")
        importance = cognitive_decision.get("importance", 0.3)
        store_memory = cognitive_decision.get("store_memory", False)

        # Always add to STM
        await self.stm.add(
            role="user",
            content=user_input,
            emotion=emotion,
            topic=topic,
            importance=importance,
        )
        await self.stm.add(
            role="assistant",
            content=assistant_response,
            emotion="neutral",
            topic=topic,
            importance=0.3,
        )

        # Conditionally store in LTM
        if store_memory and importance >= self.ltm.importance_threshold:
            await self.ltm.store(
                content=f"User said: {user_input}",
                topic=topic,
                emotion=emotion,
                importance=importance,
            )
            log.info(f"[Context] Stored in LTM: topic={topic}, importance={importance:.2f}")
