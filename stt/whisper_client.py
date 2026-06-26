"""
Sorachio-STS STT Client (whisper.cpp)
Async subprocess-based speech-to-text transcription.

Uses whisper.cpp CLI binary (whisper-cli.exe / main).
Input: raw PCM audio bytes (16kHz, 16-bit, mono)
Output: transcribed text string

Flow:
  1. Write audio bytes to temp WAV file
  2. Call whisper-cli with model and flags
  3. Parse stdout for transcript
  4. Clean and return text
"""

from __future__ import annotations

import asyncio
import os
import struct
import tempfile
import wave
from pathlib import Path
from typing import Optional

from utils.logging_setup import get_logger

log = get_logger("stt.whisper")


# ---------------------------------------------------------------------------
# WAV helper
# ---------------------------------------------------------------------------

def _write_wav(path: str, pcm_bytes: bytes, sample_rate: int = 16000) -> None:
    """Write raw 16-bit mono PCM bytes to a WAV file."""
    with wave.open(path, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)  # 16-bit
        wf.setframerate(sample_rate)
        wf.writeframes(pcm_bytes)


def _clean_transcript(text: str) -> str:
    """Remove whisper artifacts and clean up transcript."""
    import re
    # Remove [BLANK_AUDIO], (music), timing markers
    text = re.sub(r"\[.*?\]", "", text)
    text = re.sub(r"\(.*?\)", "", text)
    text = re.sub(r"\d{2}:\d{2}:\d{2}\.\d{3}\s*-->\s*\d{2}:\d{2}:\d{2}\.\d{3}", "", text)
    # Normalize whitespace
    text = " ".join(text.split())
    return text.strip()


# ---------------------------------------------------------------------------
# WhisperClient
# ---------------------------------------------------------------------------

class WhisperClient:
    """
    Async subprocess wrapper for whisper.cpp CLI.

    Transcribes audio segments to text using the whisper-base.en model.
    """

    def __init__(
        self,
        binary_path: str,
        model_path: str,
        language: str = "en",
        threads: int = 4,
        beam_size: int = 5,
        temperature: float = 0.0,
        timeout_s: float = 10.0,
    ):
        self.binary_path = Path(binary_path)
        self.model_path = Path(model_path)
        self.language = language
        self.threads = threads
        self.beam_size = beam_size
        self.temperature = temperature
        self.timeout_s = timeout_s

    def _check_availability(self) -> bool:
        """Check that binary and model exist."""
        if not self.binary_path.exists():
            log.error(f"[STT] whisper binary not found: {self.binary_path}")
            log.error("Run scripts/build_whispercpp.ps1 to build whisper.cpp")
            return False
        if not self.model_path.exists():
            log.error(f"[STT] Whisper model not found: {self.model_path}")
            log.error("Run scripts/download_whisper_model.ps1 to download model")
            return False
        return True

    async def transcribe(self, audio_bytes: bytes) -> Optional[str]:
        """
        Transcribe raw PCM audio bytes to text.

        Args:
            audio_bytes: Raw 16-bit mono 16kHz PCM audio

        Returns:
            Transcribed text string, or None on failure
        """
        if not self._check_availability():
            return None

        if len(audio_bytes) < 1000:
            log.debug("[STT] Audio too short, skipping")
            return None

        # Write to temp WAV file
        with tempfile.NamedTemporaryFile(
            suffix=".wav", delete=False, prefix="sorachio_stt_"
        ) as tmp:
            tmp_path = tmp.name

        try:
            _write_wav(tmp_path, audio_bytes)

            cmd = self._build_command(tmp_path)
            log.debug(f"[STT] Running: {' '.join(cmd)}")

            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

            try:
                stdout, stderr = await asyncio.wait_for(
                    proc.communicate(), timeout=self.timeout_s
                )
            except asyncio.TimeoutError:
                proc.kill()
                log.warning(f"[STT] Timeout after {self.timeout_s}s")
                return None

            if proc.returncode != 0:
                err = stderr.decode("utf-8", errors="replace").strip()
                # Log full command to help diagnose flag/path issues
                log.warning(
                    f"[STT] whisper exited {proc.returncode}: {err[:300]}"
                    f" | cmd: {' '.join(cmd)}"
                )
                return None

            text = stdout.decode("utf-8", errors="replace")
            transcript = _clean_transcript(text)

            if transcript:
                log.info(f"[STT] Transcript: {transcript!r}")
            else:
                log.debug("[STT] Empty transcript")

            return transcript if transcript else None

        finally:
            try:
                os.unlink(tmp_path)
            except Exception:
                pass

    def _build_command(self, wav_path: str) -> list[str]:
        """Build the whisper-cli command.

        IMPORTANT flags removed intentionally:
          --output-txt   : writes to a .txt file instead of stdout, breaks our stdout parsing
                           and can segfault on some builds when output path is not writable
          --print-special: removed in newer whisper.cpp builds, causes segfault (0xC0000005)
                           when passed as unknown flag
        """
        cmd = [
            str(self.binary_path),
            "--model", str(self.model_path),
            "--file", wav_path,
            "--language", self.language,
            "--threads", str(self.threads),
            "--temperature", str(self.temperature),
            "--no-timestamps",
        ]
        return cmd