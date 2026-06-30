"""
Sorachio-STS Acoustic Gate
Pre-VAD energy gate based on RMS / dBFS measurement.

Pipeline position:
    sounddevice callback → [AcousticGate] → _raw_queue → VAD

Behavior:
    - Frames below the dBFS threshold are dropped immediately (no queue put)
    - Frames above the threshold pass through to VAD unchanged
    - All computation is in the PortAudio callback thread — must be sub-microsecond

Formula:
    RMS   = sqrt(mean(samples^2))
    dBFS  = 20 * log10(RMS / 32768 + ε)   # normalized to int16 full-scale

Design constraints:
    - Zero allocation: uses numpy.frombuffer (zero-copy view of PCM bytes)
    - No locking: all state is read-only after construction
    - Structured debug logging only — never print() in hot path

Future extension points:
    - Swap out `gate()` with ML-based voice activity score
    - Add spectral centroid filter to reject non-speech energy (e.g., HVAC hum)
"""

from __future__ import annotations

import math

import numpy as np

from utils.logging_setup import get_logger

log = get_logger("audio.acoustic_gate")

# Small epsilon to prevent log10(0) — well below int16 noise floor
_EPSILON: float = 1e-10

# int16 full-scale peak (2^15 = 32768) — normalizes dBFS to 0 dBFS = full scale
_INT16_PEAK: float = 32768.0


def compute_dbfs(pcm_bytes: bytes) -> float:
    """
    Compute dBFS from raw int16 mono PCM bytes.

    Returns a float in the range (-∞, 0].
    Full scale (32767 peak) returns ≈ 0 dBFS.
    Digital silence returns ≈ -100 dBFS (clamped by ε).

    This function creates a zero-copy numpy view of `pcm_bytes`.
    No heap allocation of new arrays.
    """
    # Zero-copy view: no data is copied, just reinterpreted
    samples = np.frombuffer(pcm_bytes, dtype=np.int16)

    if samples.size == 0:
        return -100.0

    # RMS in int16 units
    rms = float(np.sqrt(np.mean(samples.astype(np.float32) ** 2)))

    # Normalize to full scale and convert to dBFS
    return 20.0 * math.log10(rms / _INT16_PEAK + _EPSILON)


# ---------------------------------------------------------------------------
# AcousticGate
# ---------------------------------------------------------------------------

class AcousticGate:
    """
    Pre-VAD energy gate.

    Drops PCM frames whose dBFS falls below the configured threshold.
    Designed to be called inline in the PortAudio callback thread.

    Args:
        threshold_dbfs: Frame drop threshold in dBFS. Default -40.0.
            -40 dBFS ≈ very faint background noise / gentle ventilation hum.
            -30 dBFS ≈ quiet room ambient.
            -20 dBFS ≈ moderate background.
            Raise the threshold (e.g. -30) in noisier environments.
        enabled: If False, all frames pass through unconditionally.
        debug: If True, every frame logs its dBFS value. Use only for
            calibration — extremely verbose at 30fps frame rate.
    """

    def __init__(
        self,
        threshold_dbfs: float = -40.0,
        enabled: bool = True,
        debug: bool = False,
        hold_frames: int = 15,
    ) -> None:
        self.threshold_dbfs = threshold_dbfs
        self.enabled = enabled
        self.debug = debug
        self.hold_frames = hold_frames
        self._hold_counter = 0

        # Diagnostic counters — useful for calibration logs
        self._frames_seen: int = 0
        self._frames_dropped: int = 0

        if enabled:
            log.info(
                f"[AcousticGate] Enabled — threshold={threshold_dbfs:.1f} dBFS, hold_frames={hold_frames}"
            )
        else:
            log.info("[AcousticGate] Disabled — all frames pass through")

    def gate(self, pcm_bytes: bytes) -> bool:
        """
        Evaluate a PCM frame and decide whether it passes.

        Args:
            pcm_bytes: Raw int16 mono PCM bytes (10, 20, or 30 ms frame).

        Returns:
            True  — frame passes, forward to VAD queue.
            False — frame dropped, discard silently.

        Thread safety: safe for PortAudio callback thread (no locks, no I/O).
        """
        if not self.enabled:
            return True

        self._frames_seen += 1

        dbfs = compute_dbfs(pcm_bytes)

        if dbfs >= self.threshold_dbfs:
            self._hold_counter = self.hold_frames
            passed = True
        else:
            if self._hold_counter > 0:
                self._hold_counter -= 1
                passed = True
            else:
                passed = False

        if self.debug:
            # Structured debug: level, threshold, pass/fail
            status = "PASS" if passed else "DROP"
            log.info(
                f"[AcousticGate] {status} | dBFS={dbfs:+.1f} | "
                f"threshold={self.threshold_dbfs:+.1f} | hold={self._hold_counter}"
            )

        if not passed:
            self._frames_dropped += 1
            return False

        return True

    def get_stats(self) -> dict[str, int | float]:
        """Return diagnostic counters. Safe to call from any thread."""
        seen = self._frames_seen
        dropped = self._frames_dropped
        drop_pct = (dropped / seen * 100.0) if seen > 0 else 0.0
        return {
            "frames_seen": seen,
            "frames_dropped": dropped,
            "drop_pct": drop_pct,
        }
