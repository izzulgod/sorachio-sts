"""
Sorachio-STS Audio Capture
Microphone input with Voice Activity Detection (VAD).

Pipeline:
  sounddevice mic → raw PCM frames → webrtcvad → speech segments → STT queue

Features:
  - Real-time VAD using webrtcvad
  - Configurable silence timeout
  - Continuous monitoring (even during TTS playback)
  - Interrupt detection during playback
"""

from __future__ import annotations

import asyncio
import queue
import threading
from collections.abc import Callable

import numpy as np
import sounddevice as sd
import webrtcvad

from utils.logging_setup import get_logger

log = get_logger("audio.capture")


# ---------------------------------------------------------------------------
# AudioCapture
# ---------------------------------------------------------------------------

class AudioCapture:
    """
    Real-time microphone capture with WebRTC VAD.

    Emits complete speech segments to an asyncio queue.
    Runs mic capture in a background thread (sounddevice callback).
    VAD processing happens in a separate worker thread.
    """

    def __init__(
        self,
        stt_queue: asyncio.Queue,
        interrupt_callback: Callable | None = None,
        sample_rate: int = 16000,
        channels: int = 1,
        chunk_duration_ms: int = 30,
        device_index: int | None = None,
        silence_timeout_ms: int = 800,
        vad_aggressiveness: int = 2,
        min_speech_duration_ms: int = 500,
        max_speech_duration_s: int = 30,
        playback_active_event: asyncio.Event | None = None,
        interrupt_event: asyncio.Event | None = None,
    ):
        self.stt_queue = stt_queue
        self.interrupt_callback = interrupt_callback
        self.sample_rate = sample_rate
        self.channels = channels
        self.chunk_ms = chunk_duration_ms
        self.device_index = device_index
        self.silence_timeout_ms = silence_timeout_ms
        self.vad_aggressiveness = vad_aggressiveness
        self.min_speech_duration_ms = min_speech_duration_ms
        self.max_speech_duration_s = max_speech_duration_s
        self.playback_active_event = playback_active_event
        self.interrupt_event = interrupt_event

        # VAD requires frame sizes of 10, 20, or 30 ms
        assert chunk_duration_ms in (10, 20, 30), \
            f"chunk_duration_ms must be 10, 20, or 30, got {chunk_duration_ms}"

        self._vad = webrtcvad.Vad(vad_aggressiveness)
        self._frame_size = int(sample_rate * chunk_duration_ms / 1000)
        self._raw_queue: queue.Queue = queue.Queue(maxsize=200)
        self._loop: asyncio.AbstractEventLoop | None = None
        self._running = False
        self._stream: sd.InputStream | None = None
        self._vad_thread: threading.Thread | None = None

    def start(self, loop: asyncio.AbstractEventLoop) -> None:
        """Start capture in background threads."""
        self._loop = loop
        self._running = True

        # VAD worker thread
        self._vad_thread = threading.Thread(
            target=self._vad_worker, daemon=True, name="VADWorker"
        )
        self._vad_thread.start()

        # sounddevice stream
        self._stream = sd.InputStream(
            samplerate=self.sample_rate,
            channels=self.channels,
            dtype="int16",
            blocksize=self._frame_size,
            device=self.device_index,
            callback=self._audio_callback,
        )
        self._stream.start()
        log.info(
            f"[Capture] Started — device={self.device_index or 'default'} "
            f"rate={self.sample_rate}Hz VAD={self.vad_aggressiveness}"
        )

    def stop(self) -> None:
        """Stop capture."""
        self._running = False
        if self._stream:
            self._stream.stop()
            self._stream.close()
            self._stream = None
        log.info("[Capture] Stopped")

    def _audio_callback(
        self, indata: np.ndarray, frames: int, time_info, status
    ) -> None:
        """sounddevice callback — runs in audio thread."""
        if status:
            log.debug(f"[Capture] Status: {status}")
        # Ensure int16 — some Windows drivers deliver float32
        if indata.dtype != np.int16:
            indata = (indata * 32767).clip(-32768, 32767).astype(np.int16)
        # Convert to bytes for webrtcvad
        pcm_bytes = indata[:, 0].tobytes() if self.channels == 1 else indata.tobytes()
        try:
            self._raw_queue.put_nowait(pcm_bytes)
        except queue.Full:
            pass  # Drop frame if queue full

    def _vad_worker(self) -> None:
        """VAD processing thread — detects speech segments."""
        speech_frames: list[bytes] = []
        triggered = False
        silent_frames = 0
        max_silent_frames = self.silence_timeout_ms // self.chunk_ms
        min_speech_frames = self.min_speech_duration_ms // self.chunk_ms
        max_frames = int(self.max_speech_duration_s * 1000 / self.chunk_ms)

        while self._running:
            try:
                pcm = self._raw_queue.get(timeout=0.1)
            except queue.Empty:
                continue

            try:
                is_speech = self._vad.is_speech(pcm, self.sample_rate)
            except Exception:
                is_speech = False

            if is_speech:
                if not triggered:
                    triggered = True
                    log.debug("[VAD] Speech detected")
                    if self._loop:
                        from core.events import EventType, get_bus
                        asyncio.run_coroutine_threadsafe(
                            get_bus().emit(EventType.USER_SPEECH_START, source="vad"), self._loop
                        )
                    # Check for interrupt (speech during TTS playback)
                    if (self.playback_active_event and
                            self.playback_active_event.is_set() and
                            self.interrupt_callback and
                            self.interrupt_event):
                        log.info("[VAD] Interrupt: speech during playback")
                        if self._loop:
                            asyncio.run_coroutine_threadsafe(
                                self._do_interrupt(), self._loop
                            )

                speech_frames.append(pcm)
                silent_frames = 0

                # Max duration exceeded — flush now
                if len(speech_frames) >= max_frames:
                    self._flush_speech(speech_frames, min_speech_frames)
                    speech_frames = []
                    triggered = False
                    silent_frames = 0

            else:
                if triggered:
                    silent_frames += 1
                    speech_frames.append(pcm)

                    if silent_frames >= max_silent_frames:
                        # End of utterance
                        self._flush_speech(speech_frames, min_speech_frames)
                        speech_frames = []
                        triggered = False
                        silent_frames = 0

    async def _do_interrupt(self) -> None:
        """Signal interruption (coroutine, runs in event loop)."""
        if self.interrupt_event:
            self.interrupt_event.set()
        if self.interrupt_callback:
            await self.interrupt_callback()

    def _flush_speech(self, frames: list[bytes], min_frames: int) -> None:
        """Send accumulated speech frames to STT queue."""
        if len(frames) < min_frames:
            log.debug(f"[VAD] Too short ({len(frames)} frames) — discarding")
            return

        audio_bytes = b"".join(frames)

        # Guard: whisper-cli crashes (0xC0000005) on audio shorter than ~1 second.
        # 16kHz * 2 bytes/sample * 1 second = 32000 bytes minimum.
        min_bytes = self.sample_rate * 2 * 1  # 1 second of 16-bit mono
        if len(audio_bytes) < min_bytes:
            log.debug(
                f"[VAD] Audio too short ({len(audio_bytes)} bytes < {min_bytes}) — discarding"
            )
            return

        log.debug(f"[VAD] Flushing {len(frames)} frames ({len(audio_bytes)} bytes)")

        if self._loop:
            try:
                from core.events import EventType, get_bus
                asyncio.run_coroutine_threadsafe(
                    get_bus().emit(EventType.USER_SPEECH_END, source="vad"), self._loop
                )
                asyncio.run_coroutine_threadsafe(
                    self.stt_queue.put(audio_bytes), self._loop
                ).result(timeout=1.0)
            except Exception as e:
                log.error(f"[Capture] Failed to enqueue speech: {e}")
