"""
Sorachio-STS Cognitive Gateway (LLM #1)
Fast decision layer — model-agnostic structured JSON router.

This is NOT a chatbot.
It ONLY produces structured JSON decisions.

Features:
  - Model-agnostic (works with any instruction-following LLM)
  - Thinking/reasoning disabled at server level (--reasoning off)
  - Robust JSON repair
  - Defensive parsing
  - Stable low-latency behavior
  - Fault-tolerant validation
"""

from __future__ import annotations

import json
import re
from typing import Any

from llm.llama_client import LlamaClient
from utils.logging_setup import get_logger

log = get_logger("cognition.gateway")


# ---------------------------------------------------------------------------
# Default fallback decision
# ---------------------------------------------------------------------------

DEFAULT_DECISION: dict[str, Any] = {
    "respond": True,
    "interrupt": False,
    "priority": "medium",
    "speech_type": "direct_address",
    "store_memory": False,
    "emotion": "neutral",
    "topic": "general",
    "social_attention": 0.5,
    # Legacy fields preserved for backward compatibility
    "addressed_to_ai": True,
    "importance": 0.3,
    "memory_queries": [],
}


# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """You are a cognitive routing layer for an AI companion named Sorachio.
Output ONLY valid minified JSON. No explanations, no markdown.

Schema: {"respond": boolean, "topic": string, "emotion": string,
"store_memory": boolean, "importance": float, "memory_queries": list}

Rules:
- respond=false: background noise, other people talking, filler words (um, wait, hmm).
- respond=true: greetings, questions, commands, or direct speech addressed to you.
- topic: short label (e.g. greeting, focus, origin, general).
  Use "visual_analysis" if user asks to look/see/watch something.
- emotion: neutral|happy|sad|anxious|frustrated|excited|confused|tired
- store_memory=true ONLY for important personal facts, preferences,
  goals about the USER. False for small talk, greetings, questions about Sorachio.
- importance: 0.0–1.0
- memory_queries: up to 2 search keywords, empty list if not needed.

Examples:
"Hey, stressed about exams."
→ {"respond":true,"topic":"exams","emotion":"anxious",
   "store_memory":true,"importance":0.8,"memory_queries":["exams","stress"]}
"Hey Mom, turn off the TV."
→ {"respond":false,"topic":"general","emotion":"neutral",
   "store_memory":false,"importance":0.1,"memory_queries":[]}
"Who made you?"
→ {"respond":true,"topic":"origin","emotion":"neutral",
   "store_memory":false,"importance":0.2,"memory_queries":[]}
"Look at this, what is it?"
→ {"respond":true,"topic":"visual_analysis","emotion":"curious",
   "store_memory":false,"importance":0.5,"memory_queries":[]}
"Umm... wait..."
→ {"respond":false,"topic":"general","emotion":"neutral",
   "store_memory":false,"importance":0.1,"memory_queries":[]}

Output ONLY valid JSON."""


# ---------------------------------------------------------------------------
# CognitiveGateway
# ---------------------------------------------------------------------------

class CognitiveGateway:
    """
    Fast cognitive filtering + routing layer.
    """

    def __init__(
        self,
        client: LlamaClient,
        temperature: float = 0.1,
        max_tokens: int = 256,
    ):
        self.client = client
        self.temperature = temperature
        self.max_tokens = max_tokens

    async def analyze(
        self,
        transcript: str,
        conversation_context: str | None = None,
    ) -> dict[str, Any]:
        """
        Analyze transcript and return structured decision.
        """

        transcript = transcript.strip()

        if not transcript:

            log.debug("[Gateway] Empty transcript")

            return {
                **DEFAULT_DECISION,
                "respond": False,
            }

        # -------------------------------------------------------------------
        # Build prompt
        # -------------------------------------------------------------------

        user_content = f"Input: {transcript}"

        if conversation_context:

            user_content = (
                f"Context:\n{conversation_context}\n\n"
                f"{user_content}"
            )

        messages = [
            {
                "role": "system",
                "content": SYSTEM_PROMPT,
            },
            {
                "role": "user",
                "content": user_content,
            },
        ]

        # -------------------------------------------------------------------
        # Inference
        # -------------------------------------------------------------------

        try:

            raw = await self.client.complete(
                messages=messages,
                temperature=self.temperature,
                max_tokens=self.max_tokens,
                extra_params={"response_format": {"type": "json_object"}},
            )

            if not isinstance(raw, str):
                raw = str(raw)

            log.debug(f"[Gateway Raw] {raw!r}")

            decision = self._parse_json(raw)
            decision = self._validate_decision(decision)

            log.info(
                f"[Gateway] "
                f"respond={decision['respond']} "
                f"emotion={decision['emotion']} "
                f"topic={decision['topic']} "
                f"importance={decision['importance']:.2f}"
            )

            return decision

        except Exception as e:

            log.error(
                f"[Gateway] Analysis failed: {e}",
                exc_info=True,
            )

            return {**DEFAULT_DECISION}

    # -----------------------------------------------------------------------
    # JSON parsing + repair
    # -----------------------------------------------------------------------

    def _parse_json(self, raw: str) -> dict[str, Any]:
        """
        Parse and repair malformed JSON from model output.

        Uses an iterative strip-and-retry approach to handle all truncation
        patterns: cut mid-key, cut mid-value, cut mid-scalar, cut mid-array,
        and missing closing braces. Falls back to brute-force right-trim
        if pattern matching cannot fix the output.
        """

        if not raw:
            return {}

        raw = raw.strip()

        # -------------------------------------------------------------------
        # Remove markdown/code blocks
        # -------------------------------------------------------------------

        raw = re.sub(r"```(?:json)?\s*", "", raw)
        raw = re.sub(r"```", "", raw)
        raw = raw.strip()

        # -------------------------------------------------------------------
        # Extract probable JSON region
        # -------------------------------------------------------------------

        start = raw.find("{")

        if start >= 0:
            raw = raw[start:]

        # -------------------------------------------------------------------
        # Quick path: already valid JSON
        # -------------------------------------------------------------------

        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            pass

        # -------------------------------------------------------------------
        # Helpers
        # -------------------------------------------------------------------

        def _close(s: str) -> str:
            """Add missing closing brackets and braces."""
            s = re.sub(r",\s*}", "}", s)
            s = re.sub(r",\s*]", "]", s)
            ob = s.count("[")
            cb = s.count("]")
            if cb < ob:
                s += "]" * (ob - cb)
            ob = s.count("{")
            cb = s.count("}")
            if cb < ob:
                s += "}" * (ob - cb)
            return s

        def _strip_one(s: str) -> str:
            """Strip one likely-incomplete tail pattern."""
            patterns = [
                r',?\s*"[^"]*$',                                    # unterminated string (key or array elem)
                r',?\s*"[^"]+"\s*:\s*"[^"]*$',                     # unterminated string value
                r',?\s*"[^"]+"\s*:\s*[^"{}\[\],\s][^,{}\[\]]*$',  # partial scalar value (bool/number)
                r',?\s*"[^"]+"\s*:\s*$',                            # key with no value
            ]
            for p in patterns:
                new_s = re.sub(p, '', s)
                new_s = re.sub(r",\s*}", "}", new_s)
                new_s = re.sub(r",\s*]", "]", new_s)
                if new_s != s:
                    return new_s
            return s

        # -------------------------------------------------------------------
        # Iterative repair: strip one bad tail per iteration, retry parse
        # -------------------------------------------------------------------

        repaired = raw
        for _ in range(20):
            candidate = _close(repaired)
            try:
                parsed = json.loads(candidate)
                log.warning("[Gateway] JSON repaired successfully")
                return parsed
            except json.JSONDecodeError:
                pass

            new_repaired = _strip_one(repaired)
            if new_repaired == repaired:
                break  # no further progress from pattern stripping
            repaired = new_repaired

        # -------------------------------------------------------------------
        # Last resort: brute-force trim from right until parseable
        # -------------------------------------------------------------------

        for i in range(len(raw), 0, -1):
            candidate = _close(raw[:i])
            try:
                parsed = json.loads(candidate)
                log.warning("[Gateway] JSON repaired via brute-force trim")
                return parsed
            except json.JSONDecodeError:
                pass

        log.warning(
            f"[Gateway] JSON parse failed entirely\n"
            f"Raw: {raw!r}"
        )

        return {}

    # -----------------------------------------------------------------------
    # Validation + normalization
    # -----------------------------------------------------------------------

    def _validate_decision(
        self,
        decision: dict[str, Any],
    ) -> dict[str, Any]:
        """
        Validate and normalize decision output.
        """

        result = {**DEFAULT_DECISION}

        # -------------------------------------------------------------------
        # Boolean fields
        # -------------------------------------------------------------------

        for key in (
            "respond",
            "interrupt",
            "addressed_to_ai",
            "store_memory",
        ):

            if key in decision:
                result[key] = bool(decision[key])

        # -------------------------------------------------------------------
        # Float fields
        # -------------------------------------------------------------------

        for key in (
            "importance",
            "social_attention",
        ):

            if key not in decision:
                continue

            try:

                value = float(decision[key])

                result[key] = max(
                    0.0,
                    min(1.0, value),
                )

            except (TypeError, ValueError):
                pass

        # -------------------------------------------------------------------
        # String fields
        # -------------------------------------------------------------------

        for key in (
            "emotion",
            "topic",
            "priority",
            "speech_type",
        ):

            value = decision.get(key)

            if not isinstance(value, str):
                continue

            value = value.lower().strip()

            # Remove weird characters
            value = re.sub(r"[^a-z0-9_\-\s]", "", value)

            # Limit length
            value = value[:32]

            if value:
                # Basic enum validation
                if key == "priority" and value not in ("low", "medium", "high"):
                    value = "medium"
                elif key == "speech_type" and value not in (
                    "direct_address", "background", "filler",
                    "ambient", "conversation",
                ):
                    value = "direct_address"

                result[key] = value

        # If the model explicitly set respond=False, trust it unless it's a high priority direct address
        if not result.get("respond"):
            if result.get("speech_type") == "direct_address" and result.get("priority") == "high":
                result["respond"] = True

        # Prevent storing trivial interactions in LTM
        trivial_topics = {
            "hello", "hi", "greeting", "greetings", "general", "smalltalk",
            "introduction", "identity", "self_introduction", "origin", "greeting_response"
        }
        if result.get("topic") in trivial_topics or result.get("speech_type") in ("filler", "ambient", "background"):
            result["store_memory"] = False
            if result.get("importance", 0.0) > 0.4:
                result["importance"] = 0.2

        # -------------------------------------------------------------------
        # Memory queries
        # -------------------------------------------------------------------

        queries = decision.get("memory_queries")

        if isinstance(queries, list):

            cleaned_queries = []

            for q in queries[:5]:

                q = str(q).strip()

                if not q:
                    continue

                # Remove weird chars
                q = re.sub(
                    r"[^a-zA-Z0-9_\-\s]",
                    "",
                    q,
                )

                # Limit length
                q = q[:64]

                if q:
                    cleaned_queries.append(q)

            result["memory_queries"] = cleaned_queries

        return result
