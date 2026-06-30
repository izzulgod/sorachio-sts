"""
Sorachio-STS Audio Playback
Interruptible, non-blocking audio playback queue for TTS output.

Features:
  - Audio chunk queue (fill → drain pattern)
  - Immediate interrupt support
  - Non-blocking put/get via asyncio
  - Tracks playback state for VAD interrupt detection
"""

from __future__ import annotations

import asyncio
import threading

import numpy as np
import sounddevice as sd

from audio.echo_cancellation import AECProvider
from utils.logging_setup import get_logger

log = get_logger("audio.playback")


# ---------------------------------------------------------------------------
# AudioPlayback
# ---------------------------------------------------------------------------

class AudioPlayback:
    """
    Interruptible audio playback queue.

    Consumes numpy audio arrays from a queue and plays them via sounddevice.
    Interrupt clears the queue and stops playback immediately.
    """

    def __init__(
        self,
        audio_queue: asyncio.Queue,
        playback_active_event: asyncio.Event,
        sample_rate: int = 24000,
        channels: int = 1,
        dtype: str = "float32",
        device_index: int | None = None,
        aec: AECProvider | None = None,
    ):
        self.audio_queue = audio_queue
        self.playback_active_event = playback_active_event
        self.sample_rate = sample_rate
        self.channels = channels
        self.dtype = dtype
        self.device_index = device_index
        self._aec = aec

        self._running = False
        self._stream: sd.OutputStream | None = None
        self._stream_lock = threading.Lock()
        self._interrupted = False

    async def run(self) -> None:
        """Main playback loop — drain audio queue and play chunks."""
        self._running = True
        log.info(f"[Playback] Ready — rate={self.sample_rate}Hz dtype={self.dtype}")

        while self._running:
            try:
                # Wait for next audio chunk
                audio_chunk: np.ndarray = await asyncio.wait_for(
                    self.audio_queue.get(), timeout=0.5
                )
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                break

            if audio_chunk is None:
                # Sentinel: end of this TTS segment
                self.playback_active_event.clear()
                if self._aec:
                    self._aec.set_reference_active(False)
                # Notify pipeline that playback is done
                try:
                    from core.events import EventType, get_bus
                    await get_bus().emit(EventType.PLAYBACK_FINISHED, source="playback")
                except Exception:
                    pass
                continue

            await self._play_chunk(audio_chunk)
            self.audio_queue.task_done()

    async def _play_chunk(self, audio: np.ndarray) -> None:
        """Play a single audio chunk synchronously (in threadpool)."""
        self.playback_active_event.set()
        if self._aec:
            self._aec.set_reference_active(True)
        self._interrupted = False

        loop = asyncio.get_event_loop()

        def _blocking_play():
            try:
                sd.play(audio, samplerate=self.sample_rate, blocking=True)
            except sd.PortAudioError as e:
                log.error(f"[Playback] PortAudio error: {e}")
            except Exception as e:
                log.error(f"[Playback] Playback error: {e}", exc_info=True)

        await loop.run_in_executor(None, _blocking_play)

    def interrupt(self) -> None:
        """
        Immediately stop playback and clear the audio queue.
        Called when user speaks during TTS output.
        """
        log.info("[Playback] INTERRUPT — clearing audio queue")
        self._interrupted = True

        # Stop sounddevice
        try:
            sd.stop()
        except Exception:
            pass

        # Drain the queue
        cleared = 0
        while not self.audio_queue.empty():
            try:
                self.audio_queue.get_nowait()
                self.audio_queue.task_done()
                cleared += 1
            except asyncio.QueueEmpty:
                break

        self.playback_active_event.clear()
        if self._aec:
            self._aec.set_reference_active(False)
        log.info(f"[Playback] Cleared {cleared} queued chunks")

    def stop(self) -> None:
        """Graceful shutdown."""
        self._running = False
        try:
            sd.stop()
        except Exception:
            pass
        log.info("[Playback] Stopped")
