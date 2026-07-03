"""
Sorachio-STS Cognitive Gateway (LLM #1)
Fast decision layer using Qwen3-0.6B.

This is NOT a chatbot.
It ONLY produces structured JSON decisions.

Features:
  - Qwen3 no_think mode
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
    "confidence": 0.5,
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

SYSTEM_PROMPT = """/no_think

You are a cognitive routing layer for an AI companion named Sorachio.
You are NOT a chatbot.
You MUST output ONLY valid minified JSON.

Do NOT:
- explain
- think aloud
- use markdown
- use code blocks
- add extra text

Required JSON schema:
{"respond": boolean, "interrupt": boolean, "priority": string, "speech_type": string,
 "social_attention": float, "addressed_to_ai": boolean, "store_memory": boolean,
 "importance": float, "emotion": string, "topic": string, "memory_queries": list,
 "confidence": float}

Rules:
- speech_type must be one of: direct_address, background, filler, ambient, conversation
- priority must be one of: low, medium, high
- social_attention must be 0.0-1.0 (how much attention the user is paying to you)
- respond=false for background speech, TV/music, filler words (um, uh), or noise
- respond=true for greetings, questions, commands, or intentional interaction
- interrupt=true ONLY if the user is urgently telling you to stop or changing the subject forcefully
- addressed_to_ai=true if user directly speaks to Sorachio
- store_memory=true for personal facts, goals, preferences, or important events
- importance must be 0.0-1.0
- confidence must be 0.0-1.0
- memory_queries maximum length is 3
- topic should be a short label
- emotion must be one of: neutral, happy, sad, anxious, frustrated, excited, confused, tired

Example Input: "Hey, I've been really stressed about my exams this week."
Example Output:
{"respond": true, "interrupt": false, "priority": "high", "speech_type": "direct_address",
 "social_attention": 0.9, "addressed_to_ai": true, "store_memory": true, "importance": 0.8,
 "emotion": "anxious", "topic": "exams", "memory_queries": ["exams", "stress"],
 "confidence": 0.9}

More Examples:
Input: "Hey Mom! Can you turn off that TV please?"
Output: {"respond":false,"interrupt":false,"priority":"low","speech_type":"background","social_attention":0.1,"addressed_to_ai":false,"store_memory":false,"importance":0.1,"emotion":"neutral","topic":"social_interaction","memory_queries":[],"confidence":0.98}

Input: "Stop talking, I need to focus."
Output: {"respond":true,"interrupt":true,"priority":"high","speech_type":"direct_address","social_attention":1.0,"addressed_to_ai":true,"store_memory":false,"importance":0.5,"emotion":"frustrated","topic":"focus","memory_queries":[],"confidence":0.99}

Input: "Who made you?"
Output: {"respond":true,"interrupt":false,"priority":"medium","speech_type":"direct_address","social_attention":1.0,"addressed_to_ai":true,"store_memory":false,"importance":0.2,"emotion":"curious","topic":"origin","memory_queries":[],"confidence":0.95}

Input: "Umm... wait..."
Output: {"respond":false,"interrupt":false,"priority":"low","speech_type":"filler","social_attention":0.8,"addressed_to_ai":true,"store_memory":false,"importance":0.1,"emotion":"neutral","topic":"general","memory_queries":[],"confidence":0.9}

Analyze the user's input and fill the JSON with appropriate values. Output ONLY valid JSON.
"""


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
                "confidence": 0.0,
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
            "confidence",
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
                elif key == "speech_type" and value not in ("direct_address", "background", "filler", "ambient", "conversation"):
                    value = "direct_address"
                
                result[key] = value

        # If the model explicitly set respond=False, trust it unless it's a high priority direct address
        if not result.get("respond"):
            if result.get("speech_type") == "direct_address" and result.get("priority") == "high":
                result["respond"] = True

        # Prevent storing trivial interactions in LTM
        trivial_topics = {"hello", "hi", "greeting", "greetings", "general", "smalltalk"}
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