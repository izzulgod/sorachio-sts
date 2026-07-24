"""
Sorachio-STS Acoustic Echo Cancellation (AEC)
Calibration-based adaptive echo cancellation.

Pipeline position:
    sounddevice callback → [AEC] → [AcousticGate] → _raw_queue → VAD

Purpose:
    Prevent the assistant from "hearing itself" during TTS playback.
    Without AEC, the microphone picks up speaker output, which can:
    - Trigger spurious VAD / STT on the assistant's own voice
    - Cause feedback loops in open-ear setups
    - Corrupt the barge-in detection signal

Architecture:
    1. CALIBRATION PHASE (3 seconds, no interrupts)
       - Plays known chirp signal through speaker
       - Records what comes back through mic
       - Learns: room impulse response, echo delay, frequency characteristics
       - Auto-detects interrupt threshold (real voice vs echo)

    2. RUNTIME PHASE (adaptive cancellation)
       - Uses calibration data to predict echo
       - Wiener filtering for echo suppression
       - Adaptive LMS filter for continuous learning
       - Dynamic interrupt thresholds from calibration

Implementations:
    NullAEC            — passthrough, default (no processing)
    SimpleEnergyAEC    — attenuates mic amplitude when playback is active
    CalibrationAEC     — calibration-based adaptive echo cancellation

Concurrency contract:
    process()              — called from PortAudio callback thread (hot path)
    set_reference_active() — called from asyncio playback worker task
    Threading.Event is used for cross-thread signaling (lock-free read on set/clear).
"""

from __future__ import annotations

import math
import threading
from abc import ABC, abstractmethod
from dataclasses import dataclass

import numpy as np

from utils.logging_setup import get_logger

log = get_logger("audio.aec")


# ---------------------------------------------------------------------------
# Calibration data
# ---------------------------------------------------------------------------

@dataclass
class CalibrationData:
    """Results from AEC calibration phase."""
    # Room impulse response (learned from chirp)
    impulse_response: np.ndarray | None = None
    # Echo delay in samples (round-trip latency)
    echo_delay_samples: int = 0
    # Echo delay in milliseconds
    echo_delay_ms: float = 0.0
    # Frequency-domain transfer function (H(f))
    transfer_function: np.ndarray | None = None
    # Echo-to-mic ratio during playback (0.0-1.0)
    echo_ratio: float = 0.0
    # Noise floor in dBFS (measured during silence)
    noise_floor_dbfs: float = -60.0
    # Interrupt threshold (amplitude above which = real voice)
    interrupt_threshold: float = 0.1
    # Calibration quality score (0.0-1.0)
    quality_score: float = 0.0
    # Whether calibration succeeded
    is_valid: bool = False


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

    def get_interrupt_threshold(self) -> float:
        """
        Return the amplitude threshold for detecting real user voice.

        Used by VAD to distinguish echo from actual barge-in.
        """
        return 0.1  # Default threshold

    def get_calibration_data(self) -> CalibrationData | None:
        """Return calibration data if available."""
        return None


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

    Args:
        attenuation_factor: Amplitude multiplier applied when playback is
            active. Range [0.0, 1.0]. Default 0.3 (-10.5 dBFS attenuation).
    """

    def __init__(self, attenuation_factor: float = 0.3) -> None:
        self.attenuation_factor = max(0.0, min(1.0, attenuation_factor))
        self._playback_active = threading.Event()

        log.info(
            f"[AEC] SimpleEnergyAEC — attenuation={self.attenuation_factor:.2f} "
            f"({20 * math.log10(max(self.attenuation_factor, 1e-10)):.1f} dBFS)"
        )

    def process(self, mic_frame: bytes) -> bytes:
        if not self._playback_active.is_set():
            return mic_frame

        samples = np.frombuffer(mic_frame, dtype=np.int16).astype(np.float32)
        samples *= self.attenuation_factor
        return samples.astype(np.int16).tobytes()

    def set_reference_active(self, active: bool) -> None:
        if active:
            self._playback_active.set()
            log.debug("[AEC] Reference active (playback started)")
        else:
            self._playback_active.clear()
            log.debug("[AEC] Reference inactive (playback stopped)")


# ---------------------------------------------------------------------------
# CalibrationAEC — calibration-based adaptive echo cancellation
# ---------------------------------------------------------------------------

class CalibrationAEC(AECProvider):
    """
    Calibration-based adaptive AEC with room impulse response learning.

    CALIBRATION PHASE (3 seconds):
        1. Generate chirp signal (100Hz-8kHz sweep)
        2. Play chirp through speaker while recording mic
        3. Cross-correlate playback vs recording to find echo delay
        4. Compute room transfer function H(f) = Mic(f) / Ref(f)
        5. Measure echo ratio and noise floor
        6. Auto-detect interrupt threshold from echo characteristics

    RUNTIME PHASE:
        1. Use learned H(f) to predict echo from reference signal
        2. Apply Wiener filter for echo suppression
        3. Adaptive LMS filter for continuous learning
        4. Dynamic interrupt threshold from calibration

    Advantages over basic spectral subtraction:
        - Adapts to actual room acoustics
        - Handles multi-path echo (reflections)
        - Learns speaker/mic frequency response
        - Auto-calibrates interrupt detection

    Args:
        sample_rate: Audio sample rate (default 16000)
        frame_size: Processing frame size in samples (default 480 = 30ms)
        calibration_duration_s: Calibration duration in seconds (default 3.0)
        lms_filter_length: LMS adaptive filter length (default 256 taps)
        lms_step_size: LMS learning rate (default 0.01)
        wiener_noise_margin: Wiener filter noise margin in dB (default 6.0)
    """

    def __init__(
        self,
        sample_rate: int = 16000,
        frame_size: int = 480,
        calibration_duration_s: float = 3.0,
        lms_filter_length: int = 256,
        lms_step_size: float = 0.01,
        wiener_noise_margin: float = 6.0,
    ) -> None:
        self.sample_rate = sample_rate
        self.frame_size = frame_size
        self.calibration_duration_s = calibration_duration_s
        self.lms_filter_length = lms_filter_length
        self.lms_step_size = lms_step_size
        self.wiener_noise_margin = wiener_noise_margin

        self._playback_active = threading.Event()
        self._reference_buffer = bytearray()
        self._reference_lock = threading.Lock()

        # Calibration data
        self._calibration = CalibrationData()

        # Adaptive filter (LMS)
        self._lms_weights = np.zeros(lms_filter_length, dtype=np.float32)
        self._lms_initialized = False

        # Pre-compute Hann window
        self._window = np.hanning(frame_size * 2)

        log.info(
            f"[AEC] CalibrationAEC — rate={sample_rate}Hz "
            f"frame={frame_size} calibration={calibration_duration_s}s"
        )

    def calibrate(
        self,
        play_audio_fn,
        record_audio_fn,
    ) -> CalibrationData:
        """
        Run calibration phase to learn room acoustics.

        Args:
            play_audio_fn: Callable that plays audio through speaker
            record_audio_fn: Callable that records from mic

        Returns:
            CalibrationData with learned parameters
        """
        log.info("[AEC] Starting calibration phase...")
        log.info("[AEC] Please remain silent during calibration")

        # Generate chirp signal (100Hz-8kHz sweep over calibration duration)
        chirp = self._generate_chirp()
        chirp_samples = len(chirp)

        # Record while playing chirp
        recorded = np.zeros(chirp_samples, dtype=np.float32)

        # Play and record simultaneously
        log.info(f"[AEC] Playing chirp ({self.calibration_duration_s:.1f}s)...")
        play_audio_fn(chirp)
        recorded = record_audio_fn(chirp_samples)

        # Analyze calibration data
        self._calibration = self._analyze_calibration(chirp, recorded)

        # Initialize LMS filter based on calibration
        if self._calibration.is_valid:
            self._initialize_lms_filter()

        log.info(f"[AEC] Calibration complete — quality={self._calibration.quality_score:.2f}")
        log.info(f"[AEC] Echo delay: {self._calibration.echo_delay_ms:.1f}ms")
        log.info(f"[AEC] Echo ratio: {self._calibration.echo_ratio:.2f}")
        log.info(f"[AEC] Interrupt threshold: {self._calibration.interrupt_threshold:.3f}")

        return self._calibration

    def _generate_chirp(self) -> np.ndarray:
        """
        Generate a logarithmic chirp signal for calibration.

        Sweeps from 100Hz to 8kHz over the calibration duration.
        This excites all frequencies in the speaker/mic range.
        """
        duration_samples = int(self.calibration_duration_s * self.sample_rate)
        t = np.linspace(0, self.calibration_duration_s, duration_samples, dtype=np.float32)

        # Logarithmic chirp: f(t) = f0 * (f1/f0)^(t/T)
        f0 = 100.0  # Start frequency
        f1 = 8000.0  # End frequency
        T = self.calibration_duration_s

        # Instantaneous frequency
        phase = 2 * np.pi * f0 * T / np.log(f1 / f0) * (
            np.exp(np.log(f1 / f0) * t / T) - 1
        )
        chirp = 0.5 * np.sin(phase).astype(np.float32)  # -6 dBFS

        return chirp

    def _analyze_calibration(
        self,
        reference: np.ndarray,
        recorded: np.ndarray,
    ) -> CalibrationData:
        """
        Analyze calibration recording to learn room acoustics.

        Returns CalibrationData with:
        - Echo delay (via cross-correlation)
        - Room transfer function H(f)
        - Echo ratio
        - Noise floor
        - Interrupt threshold
        """
        cal = CalibrationData()

        if len(recorded) < len(reference):
            log.warning("[AEC] Calibration recording too short")
            return cal

        # Trim to same length
        reference = reference[:len(recorded)]
        recorded = recorded[:len(reference)]

        # 1. Find echo delay via cross-correlation
        correlation = np.correlate(recorded, reference, mode='full')
        lag = np.argmax(np.abs(correlation)) - (len(reference) - 1)
        cal.echo_delay_samples = int(abs(lag))
        cal.echo_delay_ms = (abs(lag) / self.sample_rate) * 1000.0

        log.debug(f"[AEC] Detected echo delay: {cal.echo_delay_ms:.1f}ms ({cal.echo_delay_samples} samples)")

        # 2. Align reference with recorded (compensate for delay)
        if lag > 0:
            aligned_ref = reference[lag:]
            aligned_rec = recorded[:len(aligned_ref)]
        elif lag < 0:
            aligned_ref = reference[:len(reference) + lag]
            aligned_rec = recorded[-lag:]
        else:
            aligned_ref = reference
            aligned_rec = recorded

        # Ensure same length
        min_len = min(len(aligned_ref), len(aligned_rec))
        aligned_ref = aligned_ref[:min_len]
        aligned_rec = aligned_rec[:min_len]

        # 3. Compute transfer function H(f) = Mic(f) / Ref(f)
        ref_spectrum = np.fft.rfft(aligned_ref * np.hanning(min_len))
        rec_spectrum = np.fft.rfft(aligned_rec * np.hanning(min_len))

        # Avoid division by zero
        ref_power = np.abs(ref_spectrum) ** 2
        ref_power = np.maximum(ref_power, 1e-10)

        # Transfer function (complex)
        cal.transfer_function = rec_spectrum / ref_spectrum

        # 4. Compute echo ratio (energy in recording vs reference)
        ref_energy = np.sqrt(np.mean(aligned_ref ** 2))
        rec_energy = np.sqrt(np.mean(aligned_rec ** 2))

        if ref_energy > 1e-10:
            cal.echo_ratio = min(1.0, rec_energy / ref_energy)
        else:
            cal.echo_ratio = 0.0

        # 5. Estimate noise floor (from silent parts of recording)
        # Use the quietest 10% of frames
        frame_size = 480
        n_frames = min_len // frame_size
        frame_energies = []

        for i in range(n_frames):
            start = i * frame_size
            end = start + frame_size
            frame = aligned_rec[start:end]
            energy = np.sqrt(np.mean(frame ** 2))
            frame_energies.append(energy)

        frame_energies.sort()
        quiet_frames = frame_energies[:max(1, n_frames // 10)]
        noise_rms = np.mean(quiet_frames)

        if noise_rms > 1e-10:
            cal.noise_floor_dbfs = 20 * math.log10(noise_rms)
        else:
            cal.noise_floor_dbfs = -100.0

        # 6. Compute interrupt threshold
        # Threshold = echo_level * margin + noise_floor
        # Real voice should be significantly above echo
        echo_level_rms = rec_energy
        noise_margin = 10 ** (self.wiener_noise_margin / 20)

        # Interrupt threshold: amplitude above which = real user voice
        # Set to 2x the echo level + noise floor margin
        cal.interrupt_threshold = max(
            echo_level_rms * 2.0,
            noise_rms * noise_margin * 3.0,
            0.02  # Minimum threshold
        )

        # 7. Compute quality score
        # High quality = strong correlation, clear echo path
        correlation_strength = np.max(np.abs(correlation)) / (min_len * 0.5)
        correlation_strength = min(1.0, correlation_strength)

        if cal.echo_ratio > 0.01:  # Echo is detectable
            cal.quality_score = correlation_strength * 0.6 + cal.echo_ratio * 0.4
        else:
            cal.quality_score = correlation_strength * 0.3

        cal.is_valid = cal.quality_score > 0.1

        return cal

    def _initialize_lms_filter(self) -> None:
        """Initialize LMS adaptive filter based on calibration data."""
        if not self._calibration.is_valid or self._calibration.transfer_function is None:
            return

        # Initialize weights from transfer function estimate
        # Use magnitude of transfer function as initial weights
        H = self._calibration.transfer_function
        n_taps = min(self.lms_filter_length, len(H))

        # Pad or truncate to filter length
        H_padded = np.zeros(self.lms_filter_length, dtype=np.float32)
        H_padded[:n_taps] = np.abs(H[:n_taps]).astype(np.float32)

        # Normalize
        weight_norm = np.sqrt(np.sum(H_padded ** 2))
        if weight_norm > 1e-10:
            H_padded /= weight_norm

        self._lms_weights = H_padded
        self._lms_initialized = True

        log.debug(f"[AEC] LMS filter initialized with {n_taps} taps from calibration")

    def process(self, mic_frame: bytes) -> bytes:
        """
        Process mic frame with calibration-based echo cancellation.

        Pipeline:
        1. If not playback active, pass through (no echo to cancel)
        2. Get reference signal (what's being played)
        3. Predict echo using transfer function
        4. Apply Wiener filter for suppression
        5. Update LMS filter (adaptive learning)
        6. Return cleaned audio
        """
        if not self._playback_active.is_set():
            return mic_frame

        mic_samples = np.frombuffer(mic_frame, dtype=np.int16).astype(np.float32)

        # Get reference signal
        ref_samples = self._get_reference(len(mic_samples))

        if ref_samples is None or len(ref_samples) != len(mic_samples):
            return self._simple_attenuate(mic_frame)

        # If calibration not valid, fall back to simple attenuation
        if not self._calibration.is_valid:
            return self._simple_attenuate(mic_frame)

        try:
            cleaned = self._cancel_echo(mic_samples, ref_samples)
            return cleaned.astype(np.int16).tobytes()
        except Exception as e:
            log.debug(f"[AEC] Echo cancellation failed: {e}")
            return self._simple_attenuate(mic_frame)

    def _cancel_echo(
        self,
        mic: np.ndarray,
        ref: np.ndarray,
    ) -> np.ndarray:
        """
        Cancel echo using calibration data and adaptive filtering.

        Steps:
        1. Predict echo using transfer function
        2. Apply Wiener filter
        3. Update LMS weights
        """
        n = len(mic)

        # 1. Predict echo using transfer function (if available)
        if self._calibration.transfer_function is not None:
            # Apply transfer function in frequency domain
            mic_spectrum = np.fft.rfft(mic * self._window[:n])
            ref_spectrum = np.fft.rfft(ref * self._window[:n])

            # Predicted echo = Ref(f) * H(f)
            H = self._calibration.transfer_function
            if len(H) >= len(ref_spectrum):
                predicted_echo_spectrum = ref_spectrum * H[:len(ref_spectrum)]
            else:
                # Pad H to match spectrum length
                H_padded = np.zeros_like(ref_spectrum)
                H_padded[:len(H)] = H
                predicted_echo_spectrum = ref_spectrum * H_padded

            # Convert back to time domain
            predicted_echo = np.fft.irfft(predicted_echo_spectrum, n=n)
        else:
            # No transfer function, use raw reference (scaled)
            predicted_echo = ref * self._calibration.echo_ratio

        # 2. Apply Wiener filter
        # Wiener gain = 1 - (echo_power / (mic_power + noise_margin))
        mic_power = np.abs(np.fft.rfft(mic * self._window[:n])) ** 2
        echo_power = np.abs(np.fft.rfft(predicted_echo * self._window[:n])) ** 2

        noise_power = 10 ** (self._calibration.noise_floor_dbfs / 10) * len(mic)
        noise_margin = 10 ** (self.wiener_noise_margin / 10)

        wiener_gain = 1.0 - (echo_power / (mic_power + noise_power * noise_margin))
        wiener_gain = np.clip(wiener_gain, 0.1, 1.0)  # Don't suppress too much

        # Apply Wiener filter in frequency domain
        mic_spectrum = np.fft.rfft(mic * self._window[:n])
        cleaned_spectrum = mic_spectrum * wiener_gain
        cleaned = np.fft.irfft(cleaned_spectrum, n=n)

        # 3. Update LMS adaptive filter
        if self._lms_initialized:
            error = mic - cleaned  # Error signal
            self._update_lms(ref, error)

        return cleaned

    def _update_lms(self, reference: np.ndarray, error: np.ndarray) -> None:
        """
        Update LMS adaptive filter weights.

        LMS algorithm:
            w(n+1) = w(n) + step_size * error(n) * x(n)

        where:
            w = filter weights
            x = reference signal
            error = mic - predicted_echo
        """
        n = min(len(reference), self.lms_filter_length)

        if n < self.lms_filter_length:
            # Pad reference
            ref_padded = np.zeros(self.lms_filter_length, dtype=np.float32)
            ref_padded[:n] = reference[:n]
        else:
            ref_padded = reference[:self.lms_filter_length]

        # Average error over frame
        error_mean = np.mean(error)

        # Update weights
        self._lms_weights += self.lms_step_size * error_mean * ref_padded

        # Normalize to prevent divergence
        weight_norm = np.sqrt(np.sum(self._lms_weights ** 2))
        if weight_norm > 1.0:
            self._lms_weights /= weight_norm

    def _get_reference(self, length: int) -> np.ndarray | None:
        """Get reference signal of specified length from buffer."""
        with self._reference_lock:
            if len(self._reference_buffer) < length * 2:
                return None

            ref_bytes = bytes(self._reference_buffer[:length * 2])
            self._reference_buffer = self._reference_buffer[length * 2:]

        return np.frombuffer(ref_bytes, dtype=np.int16).astype(np.float32)

    def _simple_attenuate(self, mic_frame: bytes) -> bytes:
        """Fallback: simple amplitude attenuation."""
        samples = np.frombuffer(mic_frame, dtype=np.int16).astype(np.float32)
        samples *= 0.3  # Default attenuation
        return samples.astype(np.int16).tobytes()

    def set_reference_active(self, active: bool) -> None:
        if active:
            self._playback_active.set()
        else:
            self._playback_active.clear()
            with self._reference_lock:
                self._reference_buffer.clear()

    def set_reference_signal(self, audio: bytes) -> None:
        if not self._playback_active.is_set():
            return

        with self._reference_lock:
            self._reference_buffer.extend(audio)
            max_buffer = self.sample_rate * 2
            if len(self._reference_buffer) > max_buffer:
                self._reference_buffer = self._reference_buffer[-max_buffer:]

    def get_interrupt_threshold(self) -> float:
        """Return interrupt threshold from calibration."""
        return self._calibration.interrupt_threshold

    def get_calibration_data(self) -> CalibrationData | None:
        """Return calibration data."""
        if self._calibration.is_valid:
            return self._calibration
        return None


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

def create_aec(provider: str = "null", **kwargs) -> AECProvider:
    """
    Factory function for AEC provider selection.

    Args:
        provider: "null" | "simple_energy" | "calibration"
        **kwargs: Provider-specific configuration.

    Returns:
        An AECProvider instance ready for use.
    """
    if provider == "null":
        log.debug("[AEC] Using NullAEC (passthrough)")
        return NullAEC()

    elif provider == "simple_energy":
        factor = float(kwargs.get("attenuation_factor", 0.3))
        return SimpleEnergyAEC(attenuation_factor=factor)

    elif provider == "calibration":
        sample_rate = int(kwargs.get("sample_rate", 16000))
        frame_size = int(kwargs.get("frame_size", 480))
        cal_duration = float(kwargs.get("calibration_duration_s", 3.0))
        filter_len = int(kwargs.get("lms_filter_length", 256))
        step_size = float(kwargs.get("lms_step_size", 0.01))
        wiener_margin = float(kwargs.get("wiener_noise_margin", 6.0))
        return CalibrationAEC(
            sample_rate=sample_rate,
            frame_size=frame_size,
            calibration_duration_s=cal_duration,
            lms_filter_length=filter_len,
            lms_step_size=step_size,
            wiener_noise_margin=wiener_margin,
        )

    else:
        log.warning(f"[AEC] Unknown provider '{provider}' — falling back to NullAEC")
        return NullAEC()
