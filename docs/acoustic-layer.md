# 11. Acoustic Intelligence Layer

### 1. Acoustic Gate

Every frame is energy-checked before reaching VAD. Frames below `-40.0 dBFS` are immediately dropped — no wasted STT/LLM cycles on silence. Configurable via `audio.capture.acoustic_gate.threshold_dbfs` in `sorachio.yaml`.

### 2. Pre-Trigger Ring Buffer (Onset Capture)

The VAD worker maintains a rolling **8-frame (~240ms) history**. When VAD fires, these frames are prepended to the segment — preserving onset consonants of the first word (e.g., the "C" in "Coba lihat").

### 3. Peak Follower + Playback Gate Shield

During TTS playback, the gate threshold is tracked by a **peak follower with slow decay** (-0.2 dB/frame). Minimum cap: **-15.0 dBFS**. Typical speaker bleed (-17 to -19 dBFS) stays safely below this — preventing self-interruptions.

### 4. Calibration-Based Adaptive Echo Cancellation (CalibrationAEC)

To ensure the mic never triggers VAD from speaker playback, Sorachio includes `CalibrationAEC`:
- **3-second chirp sweep** (100Hz–8kHz) during startup calibration phase.
- Learns room impulse response, transfer function $H(f)$, and round-trip delay.
- Combines Wiener filtering and LMS adaptive filtering for continuous real-time echo suppression.
- Auto-calculates dynamic barge-in amplitude thresholds based on measured speaker-to-mic leakage.
