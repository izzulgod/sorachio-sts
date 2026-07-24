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

from typing import Any

from memory.long_term import LongTermMemory
from memory.short_term import ShortTermMemory
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
        cognitive_decision: dict[str, Any],
        image_b64: str | None = None,
    ) -> list[dict[str, Any]]:
        """
        Build the full message history for the Personality Core.
        Includes system prompt (100% static to maximize KV cache hits), recent STM,
        and the new user input with injected dynamic context (emotion, topic, LTM, interruption).
        """
        emotion = cognitive_decision.get("emotion", "neutral")
        topic = cognitive_decision.get("topic", "general")
        queries = cognitive_decision.get("memory_queries", [])

        # Fetch LTM memories if relevant
        ltm_entries = []
        if queries:
            ltm_entries = await self.ltm.retrieve(queries=queries, top_k=self.max_ltm_in_prompt)

        # Check if the most recent STM entry was an interrupt
        recent_entries = await self.stm.get_recent(1)
        was_interrupted = False
        if recent_entries and recent_entries[-1].metadata.get("interrupted"):
            was_interrupted = True

        # Build 100% static system prompt
        system_content = self._build_system_prompt()

        # Build message list
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": system_content},
        ]

        # Add STM history
        recent = await self.stm.get_chat_messages(n=self.max_stm_in_prompt)
        messages.extend(recent)

        # Build dynamic context block for the current turn
        context_parts = []
        if self.include_emotional_state and emotion != "neutral":
            context_parts.append(
                f"[Current emotional context: The user seems {emotion}. Respond with appropriate empathy and care.]"
            )
        if topic and topic not in ("general", "unknown"):
            context_parts.append(
                f"[Current topic: {topic}]"
            )
        if ltm_entries:
            ltm_text = self.ltm.format_for_context(ltm_entries)
            if ltm_text:
                context_parts.append(ltm_text.strip())
        if was_interrupted:
            context_parts.append(
                "[Context: Your previous response was interrupted by the user. "
                "Acknowledge the interruption if natural, and keep your next response brief.]"
            )

        # Merge dynamic context block into the newest user input
        context_prefix = "\n".join(context_parts)
        final_user_content = user_input
        if context_prefix:
            final_user_content = f"{context_prefix}\n\n{user_input}"

        # Add current user message
        if image_b64:
            # Multi-modal models like Qwen2-VL require special tags in the text block to locate the image features
            multimodal_user_content = f"<|vision_start|><|image_pad|><|vision_end|>\n{final_user_content}"
            messages.append({
                "role": "user",
                "content": [
                    {"type": "text", "text": multimodal_user_content},
                    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_b64}"}}
                ]
            })
        else:
            messages.append({"role": "user", "content": final_user_content})

        log.debug(
            f"[Context] Built prompt: {len(messages)} messages, "
            f"emotion={emotion}, topic={topic}, "
            f"ltm_hits={len(ltm_entries)}, has_image={bool(image_b64)}"
        )
        return messages

    def _build_system_prompt(self) -> str:
        """Construct a static system prompt to maximize KV Cache reuse."""
        parts = [
            self.personality_prompt.strip(),
            f"\nYou are {self.companion_name}. Respond naturally in 1-3 spoken sentences. "
            "Do NOT use markdown, bullet points, or lists. Speak conversationally."
        ]
        return "\n".join(parts)

    async def store_interaction(
        self,
        user_input: str,
        assistant_response: str,
        cognitive_decision: dict[str, Any],
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
