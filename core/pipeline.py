"""
Sorachio-STS Master Pipeline
The central async orchestrator connecting all components.

Worker graph:
  [AudioCapture] → stt_queue → [STTWorker] → cognitive_queue
  → [CognitiveWorker] → context_queue → [PersonalityWorker]
  → tts_chunk_queue → [TTSWorker] → audio_queue → [PlaybackWorker]

All workers are independent asyncio tasks communicating via queues.
Interruption flows backwards: VAD → interrupt_event → Personality + TTS + Playback.
"""

from __future__ import annotations

import asyncio

from config.settings import SorachioSettings, resolve_path
from core.events import EventType, get_bus
from utils.logging_setup import get_logger

log = get_logger("core.pipeline")


class SorachioPipeline:
    """
    Master pipeline orchestrator.

    Initializes all components, wires them together via queues and events,
    and runs the real-time speech-to-speech conversation loop.
    """

    def __init__(self, settings: SorachioSettings):
        self.settings = settings
        self.bus = get_bus()

        # --- Shared asyncio primitives ---
        self._interrupt_event = asyncio.Event()
        self._playback_active_event = asyncio.Event()
        self._shutdown_event = asyncio.Event()

        # --- Queues ---
        cfg_q = settings.queues
        self._stt_queue: asyncio.Queue[bytes] = asyncio.Queue(
            maxsize=cfg_q.stt_queue_maxsize
        )
        self._cognitive_queue: asyncio.Queue[str] = asyncio.Queue(
            maxsize=cfg_q.cognitive_queue_maxsize
        )
        self._tts_chunk_queue: asyncio.Queue[str | None] = asyncio.Queue(
            maxsize=cfg_q.tts_chunk_queue_maxsize
        )
        self._audio_queue: asyncio.Queue = asyncio.Queue(
            maxsize=cfg_q.audio_playback_queue_maxsize
        )

        # --- Components (initialized in setup()) ---
        self._stt = None
        self._cognitive = None
        self._stm = None
        self._ltm = None
        self._context = None
        self._personality = None
        self._tts = None
        self._capture = None
        self._playback = None
        self._llm_gateway = None
        self._llm_personality = None

        # --- Tasks ---
        self._tasks: list[asyncio.Task] = []
        self.on_text_response = None

    async def setup(self) -> bool:
        """Initialize all components. Returns False if critical component fails."""
        cfg = self.settings
        root = resolve_path("")

        log.info("=" * 60)
        log.info("  Sorachio-STS Pipeline Initializing")
        log.info("=" * 60)

        # ---- LLM Clients ----
        from llm.llama_client import LlamaClient
        gw_cfg = cfg.llm.cognitive_gateway
        pc_cfg = cfg.llm.personality_core

        self._llm_gateway = LlamaClient(
            base_url=gw_cfg.server_url,
            temperature=gw_cfg.temperature,
            max_tokens=gw_cfg.max_tokens,
            timeout_s=gw_cfg.timeout_s,
        )
        self._llm_personality = LlamaClient(
            base_url=pc_cfg.server_url,
            temperature=pc_cfg.temperature,
            max_tokens=pc_cfg.max_tokens,
            top_p=pc_cfg.top_p,
            repeat_penalty=pc_cfg.repeat_penalty,
            timeout_s=pc_cfg.timeout_s,
        )

        # ---- STT ----
        from stt.whisper_client import WhisperClient
        stt_cfg = cfg.stt
        self._stt = WhisperClient(
            binary_path=str(root / stt_cfg.binary_path),
            model_path=str(root / stt_cfg.model_path),
            language=stt_cfg.language,
            threads=stt_cfg.threads,
            beam_size=stt_cfg.beam_size,
            temperature=stt_cfg.temperature,
            timeout_s=stt_cfg.timeout_s,
        )

        # ---- Cognitive Gateway ----
        from cognition.cognitive_gateway import CognitiveGateway
        self._cognitive = CognitiveGateway(
            client=self._llm_gateway,
            temperature=gw_cfg.temperature,
            max_tokens=gw_cfg.max_tokens,
        )

        # ---- Memory ----
        from memory.long_term import LongTermMemory
        from memory.short_term import ShortTermMemory
        mem_cfg = cfg.memory
        self._stm = ShortTermMemory(
            max_messages=mem_cfg.short_term.max_messages,
            include_emotions=mem_cfg.short_term.include_emotions,
        )
        self._ltm = LongTermMemory(
            storage_path=str(root / mem_cfg.long_term.storage_path),
            max_entries=mem_cfg.long_term.max_entries,
            importance_threshold=mem_cfg.long_term.importance_threshold,
            retrieval_top_k=mem_cfg.long_term.retrieval_top_k,
        )
        await self._ltm.initialize()

        # ---- Context Manager ----
        from context.context_manager import ContextManager
        ctx_cfg = cfg.context
        self._context = ContextManager(
            stm=self._stm,
            ltm=self._ltm,
            personality_prompt=ctx_cfg.personality_prompt,
            companion_name=ctx_cfg.companion_name,
            max_stm_in_prompt=ctx_cfg.max_stm_in_prompt,
            max_ltm_in_prompt=ctx_cfg.max_ltm_in_prompt,
            include_emotional_state=ctx_cfg.include_emotional_state,
        )

        # ---- Personality Core ----
        from personality.personality_core import PersonalityCore
        chunker_cfg = dict(cfg.chunker)
        self._personality = PersonalityCore(
            client=self._llm_personality,
            tts_queue=self._tts_chunk_queue,
            interrupt_event=self._interrupt_event,
            chunker_config=chunker_cfg,
            temperature=pc_cfg.temperature,
            max_tokens=pc_cfg.max_tokens,
        )

        # ---- TTS ----
        from tts.kokoro_client import KokoroTTSClient
        tts_cfg = cfg.tts
        self._tts = KokoroTTSClient(
            audio_queue=self._audio_queue,
            voice=tts_cfg.voice,
            speed=tts_cfg.speed,
            lang=tts_cfg.lang,
            sample_rate=tts_cfg.sample_rate,
        )
        tts_ok = await self._tts.initialize()
        if not tts_ok:
            log.warning("[Pipeline] TTS unavailable — audio output disabled")

        # ---- Audio Capture ----
        from audio.capture import AudioCapture
        from audio.playback import AudioPlayback
        audio_cfg = cfg.audio

        self._capture = AudioCapture(
            stt_queue=self._stt_queue,
            interrupt_callback=self._on_interrupt if cfg.pipeline.enable_interruption else None,
            sample_rate=audio_cfg.capture.sample_rate,
            channels=audio_cfg.capture.channels,
            chunk_duration_ms=audio_cfg.capture.chunk_duration_ms,
            device_index=audio_cfg.capture.device_index,
            silence_timeout_ms=audio_cfg.capture.silence_timeout_ms,
            vad_aggressiveness=audio_cfg.capture.vad_aggressiveness,
            min_speech_duration_ms=audio_cfg.capture.min_speech_duration_ms,
            max_speech_duration_s=audio_cfg.capture.max_speech_duration_s,
            playback_active_event=self._playback_active_event,
            interrupt_event=self._interrupt_event if cfg.pipeline.enable_interruption else None,
        )

        self._playback = AudioPlayback(
            audio_queue=self._audio_queue,
            playback_active_event=self._playback_active_event,
            sample_rate=audio_cfg.playback.sample_rate,
            channels=audio_cfg.playback.channels,
            dtype=audio_cfg.playback.dtype,
            device_index=audio_cfg.playback.device_index,
        )

        log.info("[Pipeline] All components initialized [OK]")
        return True

    async def run(self) -> None:
        """Start all workers and run until shutdown."""
        loop = asyncio.get_event_loop()

        # Start audio capture (uses threads internally)
        self._capture.start(loop)

        # Launch async worker tasks
        self._tasks = [
            asyncio.create_task(self._stt_worker(), name="STTWorker"),
            asyncio.create_task(self._cognitive_worker(), name="CognitiveWorker"),
            asyncio.create_task(self._tts_worker(), name="TTSWorker"),
            asyncio.create_task(self._playback.run(), name="PlaybackWorker"),
        ]

        # Startup greeting
        if self.settings.pipeline.startup_greeting and self._tts._available:
            msg = self.settings.pipeline.startup_message
            log.info(f"[Pipeline] Greeting: {msg!r}")
            await self._tts.speak(msg)

        log.info("[Pipeline] Running — speak into your microphone")
        log.info("[Pipeline] Press Ctrl+C to stop")

        try:
            await self._shutdown_event.wait()
        except asyncio.CancelledError:
            pass
        finally:
            await self.shutdown()

    async def _stt_worker(self) -> None:
        """Worker: consume audio bytes → transcribe → cognitive queue."""
        log.info("[STT Worker] Started")
        while not self._shutdown_event.is_set():
            try:
                audio_bytes = await asyncio.wait_for(
                    self._stt_queue.get(), timeout=1.0
                )
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                break

            transcript = await self._stt.transcribe(audio_bytes)
            self._stt_queue.task_done()

            if transcript:
                await self.bus.emit(
                    EventType.STT_RESULT, data=transcript, source="stt"
                )
                await self._cognitive_queue.put(transcript)

    async def _cognitive_worker(self) -> None:
        """Worker: transcript → cognitive decision → personality pipeline."""
        log.info("[Cognitive Worker] Started")
        while not self._shutdown_event.is_set():
            try:
                transcript = await asyncio.wait_for(
                    self._cognitive_queue.get(), timeout=1.0
                )
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                break

            log.info(f"[Cognitive] Input: {transcript!r}")

            # Cognitive Gateway analysis
            decision = await self._cognitive.analyze(transcript)
            self._cognitive_queue.task_done()

            await self.bus.emit(
                EventType.COGNITIVE_RESULT, data=decision, source="cognitive"
            )

            if not decision.get("respond", True):
                log.info("[Cognitive] Decision: NO RESPONSE (not addressed to AI)")
                continue

            # Clear interrupt for new turn
            self._interrupt_event.clear()

            # Interrupt ongoing playback if needed
            if self._playback_active_event.is_set():
                log.info("[Cognitive] Interrupting current playback for new turn")
                self._playback.interrupt()

            # Build context prompt
            messages = await self._context.build_prompt(
                user_input=transcript,
                cognitive_decision=decision,
            )

            # Generate streaming response
            await self.bus.emit(EventType.RESPONSE_START, source="cognitive")
            response = await self._personality.generate_streaming(messages)
            await self.bus.emit(
                EventType.RESPONSE_END, data=response, source="cognitive"
            )

            # -------------------------------------------------
            # Send response to CLI text mode callback
            # -------------------------------------------------

            if self.on_text_response:
                try:
                    result = self.on_text_response(transcript, decision, response)
                    # Support both sync and async callbacks
                    if asyncio.iscoroutine(result):
                        await result
                except Exception as e:
                    log.warning(f"[Pipeline] CLI callback failed: {e}")

            # End-of-stream sentinel for TTS
            await self._tts_chunk_queue.put(None)

            # Store interaction in memory
            if response:
                await self._context.store_interaction(
                    user_input=transcript,
                    assistant_response=response,
                    cognitive_decision=decision,
                )

    async def _tts_worker(self) -> None:
        """Worker: TTS chunk queue → synthesize → audio queue."""
        log.info("[TTS Worker] Started")
        await self._tts.process_tts_queue(
            tts_chunk_queue=self._tts_chunk_queue,
            interrupt_event=self._interrupt_event,
        )

    async def _on_interrupt(self) -> None:
        """Called when user speaks during playback."""
        log.info("[Pipeline] Interrupt triggered")
        self._interrupt_event.set()
        self._playback.interrupt()

        # Clear TTS chunk queue
        while not self._tts_chunk_queue.empty():
            try:
                self._tts_chunk_queue.get_nowait()
                self._tts_chunk_queue.task_done()
            except asyncio.QueueEmpty:
                break

        await self.bus.emit(EventType.INTERRUPT, source="pipeline")

    async def inject_text(self, text: str) -> None:
        """
        Inject text directly as if it were a speech transcript.
        Used by the CLI in --text mode for testing without microphone.
        """
        await self._cognitive_queue.put(text)

    async def shutdown(self) -> None:
        """Graceful shutdown of all components."""
        log.info("[Pipeline] Shutting down...")
        self._shutdown_event.set()

        # Stop capture
        if self._capture:
            self._capture.stop()

        # Stop playback
        if self._playback:
            self._playback.stop()

        # Cancel tasks
        for task in self._tasks:
            if not task.done():
                task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)

        # Close LLM clients
        if self._llm_gateway:
            await self._llm_gateway.close()
        if self._llm_personality:
            await self._llm_personality.close()

        log.info("[Pipeline] Shutdown complete")

    def request_shutdown(self) -> None:
        """Thread-safe shutdown request."""
        self._shutdown_event.set()
