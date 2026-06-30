"""
Sorachio-STS Acoustic Echo Cancellation (AEC) Scaffold
Pluggable AEC interface for future DSP integration.

Pipeline position:
    sounddevice callback → [AEC] → [AcousticGate] → _raw_queue → VAD

Purpose:
    Prevent the assistant from "hearing itself" during TTS playback.
    Without AEC, the microphone picks up speaker output, which can:
    - Trigger spurious VAD / STT on the assistant's own voice
    - Cause feedback loops in open-ear setups
    - Corrupt the barge-in detection signal

Current implementations:
    NullAEC          — passthrough, default (no processing)
    SimpleEnergyAEC  — attenuates mic amplitude when playback is active
                       Not production-grade DSP; demonstrates interface only.

Future extension points:
    - WebRTC AEC3 (via py-webrtc or native binding)
    - SpeexDSP acoustic echo canceller
    - ONNX-based neural AEC (e.g., DeepFilterNet, RNNoise)
    - Reference-signal-based subtraction (requires TTS audio capture loopback)

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
            active. Range [0.0, 1.0]. Default 0.3 (−10.5 dBFS attenuation).
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
# Factory
# ---------------------------------------------------------------------------

def create_aec(provider: str = "null", **kwargs) -> AECProvider:
    """
    Factory function for AEC provider selection.

    Args:
        provider: "null" | "simple_energy"
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

    else:
        log.warning(f"[AEC] Unknown provider '{provider}' — falling back to NullAEC")
        return NullAEC()
