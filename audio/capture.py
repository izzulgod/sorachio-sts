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
import datetime

from audio.acoustic_gate import AcousticGate
from audio.echo_cancellation import AECProvider
from config.settings import AcousticGateConfig
from utils.logging_setup import get_logger

log = get_logger("audio.capture")

# Global flag to enable raw per-frame debug print spam
DEBUG_VERBOSE = False

def _log_event(msg: str, force: bool = False) -> None:
    if DEBUG_VERBOSE or force:
        log.info(f"[AUDIO-EVENT] {msg}")


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
        interruption_debounce_frames: int = 3,
        acoustic_gate_config: AcousticGateConfig | None = None,
        aec: AECProvider | None = None,
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
        self.interruption_debounce_frames = interruption_debounce_frames
        self._aec = aec
        
        if acoustic_gate_config:
            self._acoustic_gate = AcousticGate(
                threshold_dbfs=acoustic_gate_config.threshold_dbfs,
                enabled=acoustic_gate_config.enabled,
                debug=acoustic_gate_config.debug,
                hold_frames=acoustic_gate_config.hold_frames
            )
        else:
            self._acoustic_gate = AcousticGate(enabled=False)

        self._gate_passed_last = False

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
        # Processing gate: when set, captured speech is discarded (mic is
        # "logically muted").  VAD still runs so interrupt detection works,
        # but audio never reaches the STT queue.
        self._muted = threading.Event()
        self._playback_preroll_frames = 0

        # ── Probe audio input device at init ─────────────────────
        self._audio_available = self._probe_input_device()
        if not self._audio_available:
            log.warning(
                "[Capture] No audio input device found — "
                "mic capture disabled (WSL / headless detected). "
                "Use text mode instead."
            )

    def _probe_input_device(self) -> bool:
        """Return True if we can open an input stream on the target device."""
        try:
            dev = self.device_index  # None ⟹ default device
            info = sd.query_devices(dev, kind="input")
            if info is None:
                return False
            test = sd.InputStream(
                samplerate=self.sample_rate,
                channels=self.channels,
                dtype="int16",
                blocksize=self._frame_size,
                device=dev,
            )
            test.close()
            return True
        except (sd.PortAudioError, OSError, Exception):
            return False

    def _calibrate_acoustic_gate(self) -> None:
        """Measure background noise floor for 0.8 seconds and calibrate Acoustic Gate threshold."""
        if not hasattr(self, "_acoustic_gate") or not self._acoustic_gate.enabled:
            return

        try:
            log.info("[Capture] Calibrating Acoustic Gate noise floor... Please remain silent.")
            duration_s = 0.8
            num_samples = int(self.sample_rate * duration_s)
            
            # Record a short snippet of background noise
            noise_data = sd.rec(
                num_samples,
                samplerate=self.sample_rate,
                channels=self.channels,
                dtype="int16",
                device=self.device_index,
            )
            sd.wait()
            
            # Calculate average dBFS of the noise snippet
            from audio.acoustic_gate import compute_dbfs
            dbfs = compute_dbfs(noise_data.tobytes())
            
            # Set threshold to 8.0 dB above the background noise floor, clamped to safe ranges (min -38 dBFS)
            calibrated_threshold = max(-38.0, min(-20.0, dbfs + 8.0))
            
            self._calibrated_threshold = calibrated_threshold
            self._acoustic_gate.threshold_dbfs = calibrated_threshold
            log.info(
                f"[Capture] Calibration complete: Background Noise={dbfs:.1f} dBFS | "
                f"Acoustic Gate threshold set to {calibrated_threshold:.1f} dBFS"
            )
        except Exception as e:
            log.warning(f"[Capture] Auto-calibration failed, using default: {e}")
            self._calibrated_threshold = -38.0

    def start(self, loop: asyncio.AbstractEventLoop) -> None:
        """Start capture in background threads."""
        if not self._audio_available:
            log.info("[Capture] Skipped — no audio input device")
            self._loop = loop
            return

        self._loop = loop
        self._running = True

        # Run auto-calibration for noise floor
        self._calibrate_acoustic_gate()

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
            f"rate={self.sample_rate}Hz VAD={self.vad_aggressiveness} "
            f"GateThreshold={self._acoustic_gate.threshold_dbfs:.1f}dBFS"
        )

    def stop(self) -> None:
        """Stop capture."""
        self._running = False
        if self._stream:
            self._stream.stop()
            self._stream.close()
            self._stream = None
        log.info("[Capture] Stopped")

    def mute(self) -> None:
        """Logically mute the mic — VAD runs but speech is discarded."""
        self._muted.set()
        _log_event("Playback muted: Mic logically muted", force=True)
        log.debug("[Capture] Muted")

    def unmute(self) -> None:
        """Un-mute — resume sending speech segments to STT."""
        self._muted.clear()
        _log_event("Playback unmuted: Mic logically unmuted", force=True)
        log.debug("[Capture] Unmuted")

    def _audio_callback(
        self, indata: np.ndarray, frames: int, time_info, status
    ) -> None:
        """sounddevice callback — runs in audio thread."""
        if status:
            log.debug(f"[Capture] Status: {status}")
            _log_event(f"Sounddevice callback status warning: {status}", force=True)
        if DEBUG_VERBOSE:
            _log_event(f"Mic callback: shape={indata.shape}, dtype={indata.dtype}, frames={frames}")
        if indata.dtype != np.int16:
            indata = (indata * 32767).clip(-32768, 32767).astype(np.int16)
        # Convert to bytes for webrtcvad
        pcm_bytes = indata[:, 0].tobytes() if self.channels == 1 else indata.tobytes()
        
        # AEC processing
        if self._aec:
            pcm_bytes = self._aec.process(pcm_bytes)

        # ── Dynamic Acoustic Gate Threshold during TTS Playback ────────────────
        # Tracks speaker output baseline energy in real-time via exponential moving average.
        # Threshold stays 10.0 dB above the speaker baseline so TTS speaker bleed alone is
        # ALWAYS dropped by the Acoustic Gate (never reaches WebRTC VAD → no self-interrupts),
        # while user voice (+10 dB energy jump above speaker) passes the gate.
        cal_thresh = getattr(self, "_calibrated_threshold", -38.0)
        from audio.acoustic_gate import compute_dbfs
        dbfs = compute_dbfs(pcm_bytes)

        is_playback = bool(self.playback_active_event and self.playback_active_event.is_set())

        if is_playback:
            # Reset baseline at start of playback session
            if not getattr(self, "_in_playback", False):
                self._in_playback = True
                self._speaker_baseline_dbfs = max(dbfs, cal_thresh + 10.0)
                self._playback_preroll_frames = 8  # 8 frames * 30ms = 240ms pre-roll warmup
            else:
                # Fast attack on peaks, slow decay (0.2 dB per frame) during pauses between words
                if dbfs > self._speaker_baseline_dbfs:
                    self._speaker_baseline_dbfs = 0.5 * self._speaker_baseline_dbfs + 0.5 * dbfs
                else:
                    self._speaker_baseline_dbfs = max(cal_thresh + 6.0, self._speaker_baseline_dbfs - 0.2)

            # High-watermark threshold during playback: speaker peak + 10.0 dB (min -12.0 dBFS)
            # During the pre-roll warmup period, we force threshold very high (-8.0 dBFS)
            # to let baseline stabilize and prevent false startup interrupts.
            if getattr(self, "_playback_preroll_frames", 0) > 0:
                self._playback_preroll_frames -= 1
                playback_thresh = -8.0
            else:
                playback_thresh = max(-12.0, self._speaker_baseline_dbfs + 10.0)

            self._acoustic_gate.threshold_dbfs = playback_thresh
        else:
            if getattr(self, "_in_playback", False):
                self._in_playback = False  # Mark transition back to idle
                self._playback_preroll_frames = 0
            self._speaker_baseline_dbfs = cal_thresh
            self._acoustic_gate.threshold_dbfs = cal_thresh

        # Acoustic Gate processing
        gate_result = self._acoustic_gate.gate(pcm_bytes)
        
        if gate_result != self._gate_passed_last:
            self._gate_passed_last = gate_result
            _log_event(f"Acoustic gate state changed: passed={gate_result} (dBFS={dbfs:.2f}, thresh={self._acoustic_gate.threshold_dbfs:.1f})", force=True)

        if not gate_result:
            # Enqueue a sentinel (empty bytes) so the VAD worker knows time passed.
            try:
                self._raw_queue.put_nowait(b"")
                if DEBUG_VERBOSE:
                    _log_event("Enqueued sentinel (b'')")
            except queue.Full:
                _log_event("VAD queue full, dropped sentinel", force=True)
            return  # Frame dropped — below dBFS threshold
            
        try:
            self._raw_queue.put_nowait(pcm_bytes)
            if DEBUG_VERBOSE:
                _log_event(f"Enqueued audio frame ({len(pcm_bytes)} bytes)")
        except queue.Full:
            _log_event("VAD queue full, dropped audio frame", force=True)

    def _vad_worker(self) -> None:
        """VAD processing thread — detects speech segments with pre-trigger history."""
        speech_frames: list[bytes] = []
        history_frames: list[bytes] = []  # Rolling buffer of frames prior to speech onset
        triggered = False
        silent_frames = 0
        active_speech_frames = 0
        interrupt_speech_frames = 0
        max_silent_frames = self.silence_timeout_ms // self.chunk_ms
        min_speech_frames = self.min_speech_duration_ms // self.chunk_ms
        max_frames = int(self.max_speech_duration_s * 1000 / self.chunk_ms)

        # Minimum active speech frames (non-silence) to consider it a real speech turn.
        min_active_speech_frames = 6

        while self._running:
            try:
                pcm = self._raw_queue.get(timeout=0.1)
                if DEBUG_VERBOSE:
                    _log_event(f"VAD worker received frame: size={len(pcm)} bytes")
            except queue.Empty:
                continue

            if pcm == b"":
                # Frame was dropped by Acoustic Gate (silence)
                is_speech = False
                if DEBUG_VERBOSE:
                    _log_event("VAD worker: Sentinel bypass (silence)")
            else:
                try:
                    is_speech = self._vad.is_speech(pcm, self.sample_rate)
                    if DEBUG_VERBOSE:
                        _log_event(f"webrtcvad.is_speech={is_speech}")
                except Exception as e:
                    _log_event(f"VAD error: is_speech failed: {e}", force=True)
                    log.error(f"[VAD ERROR] is_speech failed: {e}")
                    is_speech = False

            # Keep a rolling history of the last 8 frames (~240ms) prior to triggering speech.
            # This captures the consonants/quiet onsets of words (e.g. "co" in "coba").
            if not triggered and pcm != b"":
                history_frames.append(pcm)
                if len(history_frames) > 8:
                    history_frames.pop(0)

            if is_speech:
                if not triggered:
                    triggered = True
                    active_speech_frames = 0
                    # Prepend the pre-trigger history to catch speech onset
                    speech_frames.extend(history_frames)
                    history_frames.clear()
                    _log_event("VAD state: Speech started (prepended history)", force=True)
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
                    interrupt_speech_frames += 1
                    if interrupt_speech_frames >= self.interruption_debounce_frames:
                        _log_event(f"VAD: Interrupt detected (debounce={interrupt_speech_frames})", force=True)
                        log.info(f"[VAD] Interrupt: speech during playback (debounce={interrupt_speech_frames})")
                        if self._loop:
                            asyncio.run_coroutine_threadsafe(
                                self._do_interrupt(), self._loop
                            )
                        # Synchronously unmute from this thread so that _flush_speech
                        # will NOT discard the barge-in speech due to async timing.
                        self._muted.clear()

                        # Purge speaker audio buffered prior to interrupt trigger
                        speech_frames.clear()
                        history_frames.clear()
                        active_speech_frames = 0
                        silent_frames = 0
                        interrupt_speech_frames = 0

                speech_frames.append(pcm)
                silent_frames = 0
                active_speech_frames += 1
                if DEBUG_VERBOSE:
                    _log_event(f"speech_frames={len(speech_frames)}, silent_frames={silent_frames}, active={active_speech_frames}")

                # Max duration exceeded — flush now
                if len(speech_frames) >= max_frames:
                    _log_event("VAD: Max duration exceeded, flushing now", force=True)
                    self._flush_speech(speech_frames, active_speech_frames, min_active_speech_frames)
                    speech_frames = []
                    history_frames.clear()
                    triggered = False
                    silent_frames = 0
                    active_speech_frames = 0

            else:
                interrupt_speech_frames = 0
                if triggered:
                    silent_frames += 1
                    if pcm:
                        speech_frames.append(pcm)
                    else:
                        # Append digital silence to preserve timing for STT
                        speech_frames.append(b'\x00' * (self._frame_size * 2))
                    if DEBUG_VERBOSE:
                        _log_event(f"speech_frames={len(speech_frames)}, silent_frames={silent_frames}, active={active_speech_frames}")

                    if silent_frames >= max_silent_frames:
                        _log_event(f"VAD state: Speech ended. STT Flush triggered (silent_frames={silent_frames})", force=True)
                        # End of utterance
                        self._flush_speech(speech_frames, active_speech_frames, min_active_speech_frames)
                        speech_frames = []
                        history_frames.clear()
                        triggered = False
                        silent_frames = 0
                        active_speech_frames = 0

    async def _do_interrupt(self) -> None:
        """Signal interruption (coroutine, runs in event loop)."""
        if self.interrupt_event:
            self.interrupt_event.set()
        if self.interrupt_callback:
            await self.interrupt_callback()

    def _flush_speech(self, frames: list[bytes], active_speech_frames: int, min_active_speech_frames: int) -> None:
        """Send accumulated speech frames to STT queue."""
        if DEBUG_VERBOSE:
            _log_event(f"_flush_speech: total frames={len(frames)}, active speech frames={active_speech_frames}, min_required_active={min_active_speech_frames}")
        
        # Guard: Discard clicks, pops, static spikes that lack vocal duration
        if active_speech_frames < min_active_speech_frames:
            _log_event(
                f"_flush_speech discarded: too few active speech frames ({active_speech_frames} < {min_active_speech_frames})",
                force=True
            )
            log.info(f"[VAD] Too few active frames ({active_speech_frames}) — discarding noise/pop")
            return

        audio_bytes = b"".join(frames)
        duration_s = len(audio_bytes) / (self.sample_rate * 2)  # 16-bit mono

        # Absolute minimum speech length (0.3 seconds = 9600 bytes) to filter quick noise spikes
        min_bytes = int(self.sample_rate * 2 * 0.3)
        if len(audio_bytes) < min_bytes:
            _log_event(f"_flush_speech discarded: audio too short ({len(audio_bytes)} bytes < {min_bytes})", force=True)
            log.info(
                f"[VAD] Audio too short ({duration_s:.2f}s / {len(audio_bytes)} bytes) — discarding noise"
            )
            return

        # Target minimum length for STT robustness is 1.0 second (32000 bytes).
        # Pad short utterances (0.3s - 1.0s) with digital silence so faster-whisper gets clean audio.
        target_bytes = self.sample_rate * 2 * 1
        if len(audio_bytes) < target_bytes:
            padding_len = target_bytes - len(audio_bytes)
            audio_bytes = audio_bytes + (b"\x00" * padding_len)
            log.info(f"[VAD] Padded short utterance ({duration_s:.2f}s) with {padding_len} bytes silence to 1.0s")

        # ── Mute gate: discard audio while pipeline is busy ──────────
        if self._muted.is_set():
            _log_event(f"_flush_speech discarded: pipeline muted", force=True)
            log.debug(f"[VAD] Muted — discarding {len(frames)} frames")
            return

        _log_event(f"STT enqueue: Putting {len(audio_bytes)} bytes into stt_queue", force=True)
        log.info(f"[VAD] ✓ Flushing speech: {len(frames)} frames, {duration_s:.1f}s, {len(audio_bytes)} bytes")

        if self._loop:
            try:
                from core.events import EventType, get_bus
                asyncio.run_coroutine_threadsafe(
                    get_bus().emit(EventType.USER_SPEECH_END, source="vad"), self._loop
                )
                asyncio.run_coroutine_threadsafe(
                    self.stt_queue.put(audio_bytes), self._loop
                ).result(timeout=1.0)
                _log_event("STT enqueue success", force=True)
            except Exception as e:
                _log_event(f"STT enqueue failure: {e}", force=True)
                log.error(f"[Capture] Failed to enqueue speech: {e}")
