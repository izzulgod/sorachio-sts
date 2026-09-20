"""
Sorachio-STS Action Dispatcher.
Decouples LLM1 action planning decisions and dispatches them to PersonalityCore, RobotController,
WebSearchEngine, Vision, and Memory.
"""

from __future__ import annotations

import asyncio
from typing import Any

from actuators.robot_controller import RobotController
from utils.logging_setup import get_logger
from utils.web_search import WebSearchEngine

log = get_logger("cognition.dispatcher")


class ActionDispatcher:
    """
    Action Dispatcher Engine.
    Executes JSON action decisions produced by CognitiveGateway (LLM1).
    """

    def __init__(
        self,
        personality_core: Any,
        robot_controller: RobotController | None = None,
        web_search: WebSearchEngine | None = None,
        context_manager: Any | None = None,
    ):
        self._personality = personality_core
        self._robot = robot_controller
        self._web_search = web_search
        self._context = context_manager

    async def _generate_response_turn(
        self,
        transcript: str,
        decision: dict[str, Any],
        image_b64: str | None = None,
    ) -> str:
        """Helper to build context prompt and stream PersonalityCore response."""
        if not self._context or not self._personality:
            return ""
        messages = await self._context.build_prompt(
            user_input=transcript,
            cognitive_decision=decision,
            image_b64=image_b64,
        )
        return await self._personality.generate_streaming(messages)

    async def dispatch(
        self,
        decision: dict[str, Any],
        transcript: str,
        detected_language: str = "auto",
        stt_latency_ms: float = 0.0,
        image_b64: str | None = None,
    ) -> str:
        """
        Main entry point: dispatch action decision.

        Args:
            decision: Parsed JSON decision dictionary from LLM1.
            transcript: Transcribed user speech text.
            detected_language: Language code from STT ("id", "en", etc.).
            stt_latency_ms: Time taken by STT processing.
            image_b64: Optional base64 image data.

        Returns:
            Generated response string from PersonalityCore.
        """
        action = decision.get("action", "conversation")
        log.info(f"[Dispatcher] Dispatching action='{action}' for utterance: '{transcript}'")

        try:
            if action == "move":
                return await self._handle_move(decision, transcript, image_b64)
            elif action == "look":
                return await self._handle_look(decision, transcript, image_b64)
            elif action == "remember":
                return await self._handle_remember(decision, transcript, image_b64)
            elif action == "search":
                return await self._handle_search(decision, transcript, image_b64)
            elif action == "multi":
                return await self._handle_multi(decision, transcript, image_b64)
            else:
                # Default conversation action
                return await self._handle_conversation(decision, transcript, image_b64)
        except Exception as e:
            log.error(f"[Dispatcher] Action execution error ({action}): {e}. Falling back to conversation.")
            return await self._handle_conversation(decision, transcript, image_b64)

    async def _handle_conversation(
        self,
        decision: dict[str, Any],
        transcript: str,
        image_b64: str | None = None,
    ) -> str:
        """Standard conversation turn with PersonalityCore (LLM2)."""
        return await self._generate_response_turn(transcript, decision, image_b64)

    async def _handle_move(
        self,
        decision: dict[str, Any],
        transcript: str,
        image_b64: str | None = None,
    ) -> str:
        """Physical robot movement action + verbal confirmation."""
        robot_params = decision.get("robot_params") or {}
        direction = robot_params.get("direction", "forward")
        speed = float(robot_params.get("speed", 0.5))
        duration_s = float(robot_params.get("duration_s", 1.0))
        angle_deg = float(robot_params.get("angle_deg", 0.0))

        # 1. Trigger verbal response task concurrently
        chat_task = asyncio.create_task(
            self._generate_response_turn(transcript, decision, image_b64)
        )

        # 2. Execute actuator movement
        if self._robot:
            try:
                if angle_deg != 0.0:
                    await self._robot.rotate(angle_deg=angle_deg, speed=speed)
                elif direction == "stop":
                    await self._robot.stop()
                else:
                    await self._robot.move(direction=direction, speed=speed, duration_s=duration_s)
            except Exception as e:
                log.error(f"[Dispatcher] Robot execution error: {e}")

        return await chat_task

    async def _handle_look(
        self,
        decision: dict[str, Any],
        transcript: str,
        image_b64: str | None = None,
    ) -> str:
        """Visual inspection / camera action + LLM description."""
        decision["topic"] = "visual_analysis"
        return await self._generate_response_turn(transcript, decision, image_b64)

    async def _handle_remember(
        self,
        decision: dict[str, Any],
        transcript: str,
        image_b64: str | None = None,
    ) -> str:
        """Explicit long-term memory storage action."""
        decision["store_memory"] = True
        decision["importance"] = max(0.8, float(decision.get("importance", 0.8)))
        return await self._generate_response_turn(transcript, decision, image_b64)

    async def _handle_search(
        self,
        decision: dict[str, Any],
        transcript: str,
        image_b64: str | None = None,
    ) -> str:
        """Live web search action."""
        from core.events import EventType, get_bus
        search_params = decision.get("search_params") or {}
        query = search_params.get("query") or transcript

        # Notify CLI that search is starting (for spinner/indicator)
        await get_bus().emit(
            EventType.WEB_SEARCHING,
            data={"query": query},
            source="dispatcher",
        )

        search_text = ""
        if self._web_search:
            search_text = await self._web_search.search(query=query)

        # Attach search results to decision payload so context builder injects it
        decision["web_search_results"] = search_text
        log.info("[Dispatcher] Search complete. Injecting results into PersonalityCore prompt.")

        return await self._generate_response_turn(transcript, decision, image_b64)

    async def _handle_multi(
        self,
        decision: dict[str, Any],
        transcript: str,
        image_b64: str | None = None,
    ) -> str:
        """Execute multi-action chains sequentially."""
        subactions = decision.get("subactions") or []
        if not subactions:
            return await self._handle_conversation(decision, transcript, image_b64)

        last_resp = ""
        for sub in subactions:
            if isinstance(sub, dict):
                sub_action = sub.get("action", "conversation")
                log.info(f"[Dispatcher] Executing subaction: {sub_action}")
                last_resp = await self.dispatch(sub, transcript, image_b64=image_b64)
        return last_resp
