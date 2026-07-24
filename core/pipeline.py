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
import threading

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
        self._rate_limiter = None
        self._emotion_tracker = None

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

        # ---- Rate Limiter ----
        from utils.rate_limiter import RateLimiter
        pipe_cfg = cfg.pipeline
        if pipe_cfg.enable_rate_limiting:
            self._rate_limiter = RateLimiter(
                max_requests=pipe_cfg.rate_limit_max_requests,
                window_seconds=pipe_cfg.rate_limit_window_seconds,
            )
        else:
            self._rate_limiter = None

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
            model_size=stt_cfg.model_size,
            language=stt_cfg.language,
            threads=stt_cfg.threads,
            beam_size=stt_cfg.beam_size,
            temperature=stt_cfg.temperature,
            timeout_s=stt_cfg.timeout_s,
            device=stt_cfg.device,
            compute_type=stt_cfg.compute_type,
            streaming=stt_cfg.streaming,
            chunk_length_s=stt_cfg.chunk_length_s,
        )
        stt_ok = await self._stt.initialize()
        if not stt_ok:
            log.warning("[Pipeline] STT unavailable — speech input disabled")

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
        from memory.vector_store import VectorStore
        mem_cfg = cfg.memory
        self._stm = ShortTermMemory(
            max_messages=mem_cfg.short_term.max_messages,
            include_emotions=mem_cfg.short_term.include_emotions,
        )

        # Initialize vector store if enabled
        vector_store = None
        if mem_cfg.long_term.use_vector_store:
            vector_store = VectorStore(
                storage_path=str(root / mem_cfg.long_term.vector_store_path),
                embedding_model=mem_cfg.long_term.embedding_model,
            )
            vs_ok = await vector_store.initialize()
            if not vs_ok:
                log.warning("[Pipeline] Vector store unavailable — falling back to keyword search")
                vector_store = None

        self._ltm = LongTermMemory(
            storage_path=str(root / mem_cfg.long_term.storage_path),
            max_entries=mem_cfg.long_term.max_entries,
            importance_threshold=mem_cfg.long_term.importance_threshold,
            retrieval_top_k=mem_cfg.long_term.retrieval_top_k,
            vector_store=vector_store,
            vector_weight=mem_cfg.long_term.vector_weight,
        )
        await self._ltm.initialize()

        # ---- Context Manager ----
        from context.context_manager import ContextManager
        from memory.emotion_tracker import EmotionTracker
        ctx_cfg = cfg.context

        # Initialize emotion tracker
        self._emotion_tracker = EmotionTracker(
            history_size=50,
            summary_interval_turns=10,
        )

        self._context = ContextManager(
            stm=self._stm,
            ltm=self._ltm,
            personality_prompt=ctx_cfg.personality_prompt,
            companion_name=ctx_cfg.companion_name,
            max_stm_in_prompt=ctx_cfg.max_stm_in_prompt,
            max_ltm_in_prompt=ctx_cfg.max_ltm_in_prompt,
            include_emotional_state=ctx_cfg.include_emotional_state,
            emotion_tracker=self._emotion_tracker,
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
            models_dir=str(root / tts_cfg.models_dir),
        )
        tts_ok = await self._tts.initialize()
        if not tts_ok:
            log.warning("[Pipeline] TTS unavailable — audio output disabled")

        # ---- Audio Capture ----
        from audio.capture import AudioCapture
        from audio.echo_cancellation import create_aec
        from audio.playback import AudioPlayback
        audio_cfg = cfg.audio

        # Create AEC provider
        aec_cfg = audio_cfg.echo_cancellation
        if aec_cfg.enabled:
            aec_provider = create_aec(
                provider=aec_cfg.provider,
                attenuation_factor=aec_cfg.attenuation_factor,
                sample_rate=audio_cfg.capture.sample_rate,
                # Calibration AEC settings
                calibration_duration_s=aec_cfg.calibration_duration_s,
                lms_filter_length=aec_cfg.lms_filter_length,
                lms_step_size=aec_cfg.lms_step_size,
                wiener_noise_margin=aec_cfg.wiener_noise_margin,
            )
        else:
            aec_provider = create_aec("null")

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
            interruption_debounce_frames=cfg.pipeline.interruption_debounce_frames,
            acoustic_gate_config=audio_cfg.capture.acoustic_gate,
            aec=aec_provider,
        )

        self._playback = AudioPlayback(
            audio_queue=self._audio_queue,
            playback_active_event=self._playback_active_event,
            sample_rate=audio_cfg.playback.sample_rate,
            channels=audio_cfg.playback.channels,
            dtype=audio_cfg.playback.dtype,
            device_index=audio_cfg.playback.device_index,
            aec=aec_provider,
        )

        # ---- Model Warm-up ----
        # Send the ACTUAL system prompts so llama-server pre-fills the KV cache.
        # The first real user message then gets a near-100% cache hit on the system
        # portion, instead of evaluating hundreds of tokens from scratch.
        #
        # IMPORTANT: warm-ups run SEQUENTIALLY (not parallel) to avoid RAM bandwidth
        # contention. Running both at once causes each model to read weights from
        # disk/swap simultaneously, halving effective throughput (3.7 tok/s instead of 7+).
        log.info("[Pipeline] Warming up LLM servers (pre-filling KV cache with system prompts)...")
        from cognition.cognitive_gateway import SYSTEM_PROMPT as GW_SYSTEM_PROMPT
        gw_system_prompt = GW_SYSTEM_PROMPT
        pc_system_prompt = self._context._build_system_prompt()

        await self._llm_gateway.warm_up(system_prompt=gw_system_prompt)
        await self._llm_personality.warm_up(system_prompt=pc_system_prompt)

        # ---- AEC Calibration ----
        # Run calibration if enabled and AEC provider supports it
        if (aec_cfg.enabled and
            aec_cfg.provider == "calibration" and
            aec_cfg.calibration_auto_run):
            await self._calibrate_aec(aec_provider)

        log.info("[Pipeline] All components initialized [OK]")
        return True

    async def _calibrate_aec(self, aec_provider) -> None:
        """
        Run AEC calibration phase.

        Plays a chirp signal through the speaker while recording from mic,
        then learns room acoustics for echo cancellation.
        """
        from audio.echo_cancellation import CalibrationAEC

        if not isinstance(aec_provider, CalibrationAEC):
            log.debug("[Pipeline] AEC provider does not support calibration")
            return

        log.info("[Pipeline] Starting AEC calibration...")
        log.info("[Pipeline] Please remain silent during calibration")

        # Ensure playback and capture are ready
        if not self._playback or not self._capture:
            log.warning("[Pipeline] Playback/Capture not ready for calibration")
            return

        # Run calibration in a thread to avoid blocking the event loop
        loop = asyncio.get_event_loop()

        def _run_calibration():
            """Run calibration synchronously."""
            import time

            import numpy as np
            import sounddevice as sd

            sample_rate = self.settings.audio.capture.sample_rate
            cal_duration = self.settings.audio.echo_cancellation.calibration_duration_s

            # Generate chirp signal
            chirp = aec_provider._generate_chirp()
            chirp_samples = len(chirp)

            # Play chirp and record simultaneously
            log.info(f"[Pipeline] Playing calibration chirp ({cal_duration:.1f}s)...")

            # Start recording in a separate thread
            recorded_data = np.zeros(chirp_samples, dtype=np.float32)
            recording_done = threading.Event()

            def _record():
                nonlocal recorded_data
                try:
                    recorded = sd.rec(
                        chirp_samples,
                        samplerate=sample_rate,
                        channels=1,
                        dtype="float32",
                        device=self._capture.device_index,
                    )
                    sd.wait()
                    recorded_data = recorded[:, 0]
                except Exception as e:
                    log.error(f"[Pipeline] Calibration recording failed: {e}")
                finally:
                    recording_done.set()

            record_thread = threading.Thread(target=_record, daemon=True)
            record_thread.start()

            # Small delay to ensure recording has started
            time.sleep(0.1)

            # Play chirp through speaker
            try:
                sd.play(
                    chirp,
                    samplerate=sample_rate,
                    device=self._playback.device_index,
                    blocking=True,
                )
            except Exception as e:
                log.error(f"[Pipeline] Calibration playback failed: {e}")

            # Wait for recording to complete
            recording_done.wait(timeout=cal_duration + 2.0)

            # Run calibration analysis
            calibration_data = aec_provider._analyze_calibration(chirp, recorded_data)
            aec_provider._calibration = calibration_data

            if calibration_data.is_valid:
                aec_provider._initialize_lms_filter()
                log.info(f"[Pipeline] AEC calibration complete — quality={calibration_data.quality_score:.2f}")
                log.info(f"[Pipeline] Echo delay: {calibration_data.echo_delay_ms:.1f}ms")
                log.info(f"[Pipeline] Interrupt threshold: {calibration_data.interrupt_threshold:.3f}")
            else:
                log.warning("[Pipeline] AEC calibration failed — using default settings")

        try:
            await loop.run_in_executor(None, _run_calibration)
        except Exception as e:
            log.error(f"[Pipeline] AEC calibration error: {e}")

    async def run(self) -> None:
        """Start all workers and run until shutdown."""
        loop = asyncio.get_event_loop()

        # Subscribe to playback-finished to unmute the mic
        self.bus.subscribe(EventType.PLAYBACK_FINISHED, self._on_playback_finished)

        # Launch async worker tasks (playback must run for greeting)
        self._tasks = [
            asyncio.create_task(self._stt_worker(), name="STTWorker"),
            asyncio.create_task(self._cognitive_worker(), name="CognitiveWorker"),
            asyncio.create_task(self._tts_worker(), name="TTSWorker"),
            asyncio.create_task(self._playback.run(), name="PlaybackWorker"),
        ]

        # ── Startup greeting (BEFORE starting mic) ──────────────────────
        # The mic capture is NOT started yet, so there is zero chance of
        # Sorachio hearing its own greeting through the speakers.
        if self.settings.pipeline.startup_greeting and self._tts._available:
            msg = self.settings.pipeline.startup_message
            log.info(f"[Pipeline] Greeting: {msg!r}")
            # Mute during greeting playback to avoid capturing TTS output
            self._capture.mute()

            # Temporarily disable interruption callback so the greeting does not interrupt itself
            old_interrupt_callback = self._capture.interrupt_callback
            self._capture.interrupt_callback = None

            greeting_done = asyncio.Event()

            async def _on_greeting_done(event_data) -> None:
                greeting_done.set()

            self.bus.subscribe(EventType.PLAYBACK_FINISHED, _on_greeting_done)

            try:
                await self._tts.speak(msg)
                # Wait for the greeting playback to actually finish completely
                try:
                    await asyncio.wait_for(greeting_done.wait(), timeout=15.0)
                except asyncio.TimeoutError:
                    log.warning("[Pipeline] Startup greeting playback timeout")
            finally:
                self.bus.unsubscribe(EventType.PLAYBACK_FINISHED, _on_greeting_done)

            # Let speaker reverb / room echo die down before opening the mic
            await asyncio.sleep(0.5)
            log.info("[Pipeline] Greeting complete — starting mic capture")

        # ── NOW start audio capture (mic is clean, no greeting leak) ────
        self._capture.start(loop)

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

            # Use streaming if available for lower latency
            if self._stt.streaming:
                transcript_parts: list[str] = []
                async for partial in self._stt.transcribe_streaming(audio_bytes):
                    transcript_parts.append(partial)
                    # Emit partial result for real-time feedback
                    if len(transcript_parts) > 1:
                        await self.bus.emit(
                            EventType.STT_RESULT,
                            data=" ".join(transcript_parts),
                            source="stt",
                        )
                transcript = " ".join(transcript_parts) if transcript_parts else None
            else:
                transcript = await self._stt.transcribe(audio_bytes)

            self._stt_queue.task_done()

            if transcript:
                # Propagate detected language to TTS for voice routing
                detected_lang = self._stt.last_detected_language
                self._last_stt_lang = detected_lang
                if detected_lang and hasattr(self._tts, 'set_language'):
                    self._tts.set_language(detected_lang, from_stt=True)

                await self.bus.emit(
                    EventType.STT_RESULT, data=transcript, source="stt"
                )
                await self._cognitive_queue.put(transcript)

    def _flush_queues(self) -> None:
        """Drain stale data from TTS chunk queue and audio queue.

        Must be called before starting a new response turn so that leftover
        chunks from an interrupted response don't interfere.
        """
        flushed_tts = 0
        while not self._tts_chunk_queue.empty():
            try:
                self._tts_chunk_queue.get_nowait()
                self._tts_chunk_queue.task_done()
                flushed_tts += 1
            except asyncio.QueueEmpty:
                break

        flushed_audio = 0
        while not self._audio_queue.empty():
            try:
                self._audio_queue.get_nowait()
                self._audio_queue.task_done()
                flushed_audio += 1
            except asyncio.QueueEmpty:
                break

        if flushed_tts or flushed_audio:
            log.info(
                f"[Pipeline] Flushed stale queues: "
                f"tts_chunks={flushed_tts}, audio={flushed_audio}"
            )

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

            # Rate limiting check
            if self._rate_limiter and not await self._rate_limiter.allow():
                log.warning("[Cognitive] Rate limit exceeded — dropping input")
                self._cognitive_queue.task_done()
                # Unmute mic so user can try again
                if self._capture:
                    self._capture.unmute()
                continue

            # ── Mute the mic while the pipeline is busy ─────────────────
            if self._capture:
                self._capture.mute()

            # Cognitive Gateway analysis
            decision = await self._cognitive.analyze(transcript)
            decision["detected_language"] = getattr(self, "_last_stt_lang", None)
            self._cognitive_queue.task_done()

            await self.bus.emit(
                EventType.COGNITIVE_RESULT, data=decision, source="cognitive"
            )

            if not decision.get("respond", True):
                log.info("[Cognitive] Decision: NO RESPONSE (not addressed to AI)")
                # No response coming — unmute immediately
                if self._capture:
                    self._capture.unmute()
                continue

            # ── Prepare for new turn ────────────────────────────────────
            # 1. Stop any ongoing playback first
            if self._playback_active_event.is_set():
                log.info("[Cognitive] Interrupting current playback for new turn")
                self._playback.interrupt()

            # 2. Clear the interrupt flag AFTER stopping playback
            self._interrupt_event.clear()

            # 3. Drain any stale chunks from previous (interrupted) turn
            self._flush_queues()

            # Vision integration: capture snapshot if requested with explicit visual intent
            image_b64 = None
            is_visual_topic = decision.get("topic") == "visual_analysis"
            visual_triggers = ("look", "see", "watch", "camera", "picture", "photo", "show", "view", "lihat", "kamera", "foto", "gambar")
            has_visual_intent = any(w in transcript.lower() for w in visual_triggers)

            if is_visual_topic and has_visual_intent and self.settings.vision.enabled:
                from vision.capture import capture_frame_base64
                log.info("[Vision] Capturing snapshot from webcam...")
                image_b64 = capture_frame_base64(
                    device_index=self.settings.vision.device_index,
                    max_size=self.settings.vision.max_size,
                )
                if not image_b64:
                    log.warning("[Vision] Failed to capture image, proceeding with text only.")

            # Build context prompt
            messages = await self._context.build_prompt(
                user_input=transcript,
                cognitive_decision=decision,
                image_b64=image_b64,
            )

            # Generate streaming response
            log.info("[Cognitive] Starting response generation")
            await self.bus.emit(EventType.RESPONSE_START, source="cognitive")
            response = await self._personality.generate_streaming(messages)
            await self.bus.emit(
                EventType.RESPONSE_END, data=response, source="cognitive"
            )
            log.info(f"[Cognitive] Response complete: {len(response)} chars")

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
        """Called when user speaks during playback (barge-in).

        Flow:
        1. Signal the interrupt to stop generation + TTS synthesis
        2. Stop audio playback immediately
        3. Unmute mic so barge-in speech is captured
        4. Drain stale queues (cognitive worker will drain again for safety)
        """
        log.info("[Pipeline] ══ INTERRUPT TRIGGERED ══")

        # 1. Signal interrupt — stops personality generation + TTS synthesis
        self._interrupt_event.set()

        # 2. Stop playback — calls sd.stop() and drains audio_queue
        self._playback.interrupt()

        # Unmute the mic immediately so the barge-in speech can be captured
        if self._capture:
            self._capture.unmute()

        # Inject interruption metadata into STM
        if self._stm:
            await self._stm.mark_last_interrupted()

        # 5. Drain stale TTS text chunks left from the interrupted response
        flushed = 0
        while not self._tts_chunk_queue.empty():
            try:
                self._tts_chunk_queue.get_nowait()
                self._tts_chunk_queue.task_done()
                flushed += 1
            except asyncio.QueueEmpty:
                break
        if flushed:
            log.info(f"[Pipeline] Drained {flushed} stale TTS chunks")

        await self.bus.emit(EventType.INTERRUPT, source="pipeline")
        log.info("[Pipeline] ══ INTERRUPT COMPLETE ══")

    async def inject_text(self, text: str) -> None:
        """
        Inject text directly as if it were a speech transcript.
        Used by the CLI in --text mode for testing without microphone.
        """
        await self._cognitive_queue.put(text)

    async def _on_playback_finished(self, event) -> None:
        """Called when TTS playback reaches the end-of-stream sentinel."""
        log.debug("[Pipeline] PLAYBACK_FINISHED → unmuting mic")
        if self._capture:
            self._capture.unmute()

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
