"""
Sorachio-STS Acoustic Echo Cancellation (AEC)
Pluggable AEC interface with multiple implementations.

Pipeline position:
    sounddevice callback → [AEC] → [AcousticGate] → _raw_queue → VAD

Purpose:
    Prevent the assistant from "hearing itself" during TTS playback.
    Without AEC, the microphone picks up speaker output, which can:
    - Trigger spurious VAD / STT on the assistant's own voice
    - Cause feedback loops in open-ear setups
    - Corrupt the barge-in detection signal

Implementations:
    NullAEC          — passthrough, default (no processing)
    SimpleEnergyAEC  — attenuates mic amplitude when playback is active
    SpectralSubAEC   — frequency-domain spectral subtraction with reference
                       signal for actual echo cancellation

Concurrency contract:
    process()              — called from PortAudio callback thread (hot path)
    set_reference_active() — called from asyncio playback worker task
    Threading.Event is used for cross-thread signaling (lock-free read on set/clear).
"""

from __future__ import annotations

import threading
from abc import ABC, abstractmethod

import numpy as np

from utils.logging_setup import get_logger

log = get_logger("audio.aec")


# ---------------------------------------------------------------------------
# Abstract base
# ---------------------------------------------------------------------------

class AECProvider(ABC):
    """
    Abstract interface for Acoustic Echo Cancellation providers.

    All implementations must be safe to call from the PortAudio callback
    thread. Avoid I/O, locking (other than threading.Event), or heavy compute.
    """

    @abstractmethod
    def process(self, mic_frame: bytes) -> bytes:
        """
        Process a microphone PCM frame.

        Args:
            mic_frame: Raw int16 mono PCM bytes from the microphone.

        Returns:
            Processed PCM bytes (same length, same dtype).
            Returning the original bytes unchanged is always valid.
        """
        ...

    @abstractmethod
    def set_reference_active(self, active: bool) -> None:
        """
        Notify the AEC that TTS playback has started or stopped.

        Called from the asyncio playback worker — must be thread-safe.

        Args:
            active: True when TTS audio is being played back.
                    False when playback stops (silence or interruption).
        """
        ...

    def set_reference_signal(self, audio: bytes) -> None:
        """
        Feed the TTS playback audio as a reference signal for echo cancellation.

        Called from the playback worker — must be thread-safe.
        Only needed for reference-based AEC implementations.
        """
        pass


# ---------------------------------------------------------------------------
# NullAEC — default passthrough
# ---------------------------------------------------------------------------

class NullAEC(AECProvider):
    """
    No-op AEC implementation.

    All microphone frames pass through unchanged.
    Zero compute overhead. Use when AEC is disabled in config.
    """

    def process(self, mic_frame: bytes) -> bytes:
        return mic_frame

    def set_reference_active(self, active: bool) -> None:
        pass  # No state to update


# ---------------------------------------------------------------------------
# SimpleEnergyAEC — amplitude attenuation during playback
# ---------------------------------------------------------------------------

class SimpleEnergyAEC(AECProvider):
    """
    Simple energy-based echo suppressor.

    When TTS playback is active, attenuates the microphone frame
    by a configurable factor. This is NOT production AEC — it does not
    perform frequency-domain subtraction or adaptive filtering.

    Use case:
        Reduces self-triggering in open speaker setups where the assistant
        can clearly hear its own TTS output through the microphone.
        Works best when the assistant's voice is significantly louder than
        expected user voice (headphone or near-field speaker scenarios).

    Limitations:
        - Does not remove actual echo, only reduces mic energy globally.
        - May attenuate genuine user speech during playback (barge-in).
        - Recommended: pair with the acoustic gate for best results.

    Args:
        attenuation_factor: Amplitude multiplier applied when playback is
            active. Range [0.0, 1.0]. Default 0.3 (-10.5 dBFS attenuation).
            0.0 = complete mute during playback.
            1.0 = no attenuation (same as NullAEC).
    """

    def __init__(self, attenuation_factor: float = 0.3) -> None:
        self.attenuation_factor = max(0.0, min(1.0, attenuation_factor))

        # threading.Event: thread-safe boolean flag.
        # .set() from asyncio executor thread, .is_set() from audio thread.
        self._playback_active = threading.Event()

        log.info(
            f"[AEC] SimpleEnergyAEC — attenuation={self.attenuation_factor:.2f} "
            f"({20 * __import__('math').log10(max(self.attenuation_factor, 1e-10)):.1f} dBFS)"
        )

    def process(self, mic_frame: bytes) -> bytes:
        """
        Attenuate microphone frame if playback is active.

        Zero-copy where possible: if no attenuation needed, return original bytes.
        When attenuating: one numpy frombuffer + multiply + tobytes.
        """
        if not self._playback_active.is_set():
            return mic_frame

        # Attenuate: convert to float32, scale, convert back to int16
        samples = np.frombuffer(mic_frame, dtype=np.int16).astype(np.float32)
        samples *= self.attenuation_factor
        return samples.astype(np.int16).tobytes()

    def set_reference_active(self, active: bool) -> None:
        """Thread-safe state update from playback worker."""
        if active:
            self._playback_active.set()
            log.debug("[AEC] Reference active (playback started)")
        else:
            self._playback_active.clear()
            log.debug("[AEC] Reference inactive (playback stopped)")


# ---------------------------------------------------------------------------
# SpectralSubAEC — frequency-domain spectral subtraction
# ---------------------------------------------------------------------------

class SpectralSubAEC(AECProvider):
    """
    Spectral subtraction AEC using frequency-domain processing.

    Uses the reference signal (TTS playback audio) to estimate the echo
    spectrum and subtract it from the microphone signal. This provides
    actual echo cancellation rather than just energy attenuation.

    Algorithm:
        1. Compute FFT of both mic and reference signals
        2. Estimate echo transfer function from reference
        3. Subtract estimated echo spectrum from mic spectrum
        4. Inverse FFT to get cleaned audio

    Args:
        sample_rate: Audio sample rate (default 16000)
        frame_size: Processing frame size in samples (default 480 = 30ms)
        attenuation_factor: Echo suppression strength (0.0-1.0, default 0.5)
        spectral_floor: Minimum spectral magnitude floor (default 0.01)
    """

    def __init__(
        self,
        sample_rate: int = 16000,
        frame_size: int = 480,
        attenuation_factor: float = 0.5,
        spectral_floor: float = 0.01,
    ) -> None:
        self.sample_rate = sample_rate
        self.frame_size = frame_size
        self.attenuation_factor = max(0.0, min(1.0, attenuation_factor))
        self.spectral_floor = max(0.001, min(1.0, spectral_floor))

        self._playback_active = threading.Event()
        self._reference_buffer = bytearray()
        self._reference_lock = threading.Lock()

        # Pre-compute Hann window for overlap-add
        self._window = np.hanning(frame_size * 2)
        self._hop_size = frame_size

        log.info(
            f"[AEC] SpectralSubAEC — rate={sample_rate}Hz "
            f"frame={frame_size} attenuation={self.attenuation_factor:.2f}"
        )

    def process(self, mic_frame: bytes) -> bytes:
        """
        Process mic frame with spectral subtraction echo cancellation.
        """
        if not self._playback_active.is_set():
            return mic_frame

        mic_samples = np.frombuffer(mic_frame, dtype=np.int16).astype(np.float32)

        # Get reference signal
        ref_samples = self._get_reference(len(mic_samples))

        if ref_samples is None or len(ref_samples) != len(mic_samples):
            # Fallback to simple attenuation if reference unavailable
            return self._simple_attenuate(mic_frame)

        # Spectral subtraction
        try:
            cleaned = self._spectral_subtract(mic_samples, ref_samples)
            return cleaned.astype(np.int16).tobytes()
        except Exception as e:
            log.debug(f"[AEC] Spectral subtraction failed: {e}")
            return self._simple_attenuate(mic_frame)

    def _get_reference(self, length: int) -> np.ndarray | None:
        """Get reference signal of specified length from buffer."""
        with self._reference_lock:
            if len(self._reference_buffer) < length * 2:  # int16 = 2 bytes
                return None

            ref_bytes = bytes(self._reference_buffer[:length * 2])
            # Remove consumed bytes
            self._reference_buffer = self._reference_buffer[length * 2:]

        return np.frombuffer(ref_bytes, dtype=np.int16).astype(np.float32)

    def _spectral_subtract(
        self,
        mic: np.ndarray,
        ref: np.ndarray,
    ) -> np.ndarray:
        """
        Perform spectral subtraction to remove echo.
        """
        # Pad to frame size if needed
        if len(mic) < self.frame_size * 2:
            mic = np.pad(mic, (0, self.frame_size * 2 - len(mic)))
            ref = np.pad(ref, (0, self.frame_size * 2 - len(ref)))

        # Process in overlapping frames
        output = np.zeros_like(mic)

        for start in range(0, len(mic) - self.frame_size, self._hop_size):
            end = start + self.frame_size * 2

            if end > len(mic):
                break

            # Extract and window frames
            mic_frame = mic[start:end] * self._window
            ref_frame = ref[start:end] * self._window

            # FFT
            mic_spectrum = np.fft.rfft(mic_frame)
            ref_spectrum = np.fft.rfft(ref_frame)

            # Estimate echo power spectrum
            ref_power = np.abs(ref_spectrum) ** 2

            # Spectral subtraction
            mic_power = np.abs(mic_spectrum) ** 2
            clean_power = mic_power - self.attenuation_factor * ref_power

            # Apply spectral floor
            clean_power = np.maximum(clean_power, self.spectral_floor * mic_power)

            # Reconstruct
            clean_magnitude = np.sqrt(clean_power)
            clean_phase = np.angle(mic_spectrum)
            clean_spectrum = clean_magnitude * np.exp(1j * clean_phase)

            # IFFT and overlap-add
            clean_frame = np.fft.irfft(clean_spectrum)
            output[start:end] += clean_frame * self._window

        return output[:len(mic)]

    def _simple_attenuate(self, mic_frame: bytes) -> bytes:
        """Fallback: simple amplitude attenuation."""
        samples = np.frombuffer(mic_frame, dtype=np.int16).astype(np.float32)
        samples *= self.attenuation_factor
        return samples.astype(np.int16).tobytes()

    def set_reference_active(self, active: bool) -> None:
        """Thread-safe state update from playback worker."""
        if active:
            self._playback_active.set()
        else:
            self._playback_active.clear()
            # Clear reference buffer when playback stops
            with self._reference_lock:
                self._reference_buffer.clear()

    def set_reference_signal(self, audio: bytes) -> None:
        """Feed TTS playback audio as reference signal."""
        if not self._playback_active.is_set():
            return

        with self._reference_lock:
            self._reference_buffer.extend(audio)
            # Limit buffer size to prevent memory issues (max 1 second)
            max_buffer = self.sample_rate * 2  # 1 second of int16
            if len(self._reference_buffer) > max_buffer:
                self._reference_buffer = self._reference_buffer[-max_buffer:]


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

def create_aec(provider: str = "null", **kwargs) -> AECProvider:
    """
    Factory function for AEC provider selection.

    Args:
        provider: "null" | "simple_energy" | "spectral_sub"
        **kwargs: Provider-specific configuration.

    Returns:
        An AECProvider instance ready for use.

    Raises:
        ValueError: If provider name is unrecognized.

    Extension point:
        Add new elif branches here to register additional AEC implementations.
        The rest of the pipeline (capture.py, pipeline.py) does not need changes.
    """
    if provider == "null":
        log.debug("[AEC] Using NullAEC (passthrough)")
        return NullAEC()

    elif provider == "simple_energy":
        factor = float(kwargs.get("attenuation_factor", 0.3))
        return SimpleEnergyAEC(attenuation_factor=factor)

    elif provider == "spectral_sub":
        sample_rate = int(kwargs.get("sample_rate", 16000))
        frame_size = int(kwargs.get("frame_size", 480))
        factor = float(kwargs.get("attenuation_factor", 0.5))
        floor = float(kwargs.get("spectral_floor", 0.01))
        return SpectralSubAEC(
            sample_rate=sample_rate,
            frame_size=frame_size,
            attenuation_factor=factor,
            spectral_floor=floor,
        )

    else:
        log.warning(f"[AEC] Unknown provider '{provider}' — falling back to NullAEC")
        return NullAEC()
