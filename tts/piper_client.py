"""
Sorachio-STS Piper TTS Client
Streaming text-to-speech synthesis using piper-tts (ONNX).

Pipeline:
  speech chunk (string) → Piper synthesis → numpy audio array → playback queue

Features:
  - In-process synthesis (no subprocess overhead)
  - Streams per-chunk audio immediately
  - Falls back gracefully if piper-tts unavailable
  - Bilingual female voice routing (Indonesian / English)
  - Auto-downloads missing Piper ONNX models from Hugging Face
  - Defensive sanitization for unstable TTS input
"""

from __future__ import annotations

import asyncio
import io
import json
import os
import struct
import urllib.request
import wave
from pathlib import Path

import numpy as np

from utils.logging_setup import get_logger

log = get_logger("tts.piper")


# ---------------------------------------------------------------------------
# Voice Configuration
# ---------------------------------------------------------------------------

# Primary and fallback voice models for each language
_VOICE_MAP: dict[str, list[str]] = {
    "id": ["id_ID-news_tts-medium"],
    "en": ["en_US-lessac-medium", "en_US-amy-medium"],
}

# Hugging Face base URL for piper voice downloads
_HF_PIPER_VOICES_URL = (
    "https://huggingface.co/rhasspy/piper-voices/resolve/main"
)


def _voice_download_url(voice_name: str) -> tuple[str, str]:
    """
    Build download URLs for a piper voice model.

    Piper voices follow the naming convention:
        {lang_code}/{lang_country}/{voice}/{quality}/{voice}.onnx
    e.g. en/en_US/lessac/medium/en_US-lessac-medium.onnx

    Returns (onnx_url, json_url).
    """
    # Parse voice name: "en_US-lessac-medium" → lang="en", country_lang="en_US", name="lessac", quality="medium"
    parts = voice_name.split("-")
    if len(parts) != 3:
        raise ValueError(f"Invalid piper voice name format: {voice_name}")

    country_lang = parts[0]   # e.g. "en_US" or "id_ID"
    name = parts[1]           # e.g. "lessac" or "indotts"
    quality = parts[2]        # e.g. "medium"
    lang = country_lang.split("_")[0]  # e.g. "en" or "id"

    base = f"{_HF_PIPER_VOICES_URL}/{lang}/{country_lang}/{name}/{quality}"
    onnx_url = f"{base}/{voice_name}.onnx"
    json_url = f"{base}/{voice_name}.onnx.json"

    return onnx_url, json_url


# ---------------------------------------------------------------------------
# PiperTTSClient
# ---------------------------------------------------------------------------

class PiperTTSClient:
    """
    Piper TTS wrapper that synthesizes text chunks and queues audio.

    Each text chunk is synthesized synchronously in an executor
    (to avoid blocking the event loop) and the audio is placed
    in the audio playback queue for immediate playback.

    Supports bilingual voice routing between Indonesian and English
    female voices based on STT-detected language.
    """

    def __init__(
        self,
        audio_queue: asyncio.Queue,
        voice: str = "en_US-lessac-medium",
        speed: float = 1.0,
        lang: str = "auto",
        sample_rate: int = 22050,
        models_dir: str = "models/tts",
    ):
        self.audio_queue = audio_queue
        self.voice = voice
        self.speed = speed
        self.lang = lang  # "auto", "en", "id"
        self.sample_rate = sample_rate
        self.models_dir = Path(models_dir)

        self._voices: dict[str, object] = {}  # lang_code → loaded PiperVoice
        self._voice_names: dict[str, str] = {}  # lang_code → voice file stem
        self._current_lang: str = "en"
        self._available = False

        # Language detection accumulator — collects chunks from a single
        # response until there's enough text for accurate langdetect.
        self._response_text_acc: str = ""       # accumulated text so far
        self._response_lang_locked: bool = False  # True once lang is resolved

    async def initialize(self) -> bool:
        """Load Piper voices (blocking, run once at startup)."""
        loop = asyncio.get_event_loop()
        ok = await loop.run_in_executor(None, self._load_voices)

        self._available = ok

        if ok:
            loaded = ", ".join(
                f"{lang}={name}" for lang, name in self._voice_names.items()
            )
            log.info(f"[TTS] Piper ready — voices: {loaded}")
        else:
            log.warning(
                "[TTS] Piper not available — install with: pip install piper-tts"
            )

        return ok

    def _load_voices(self) -> bool:
        """Load Piper voice models in thread (avoids blocking event loop)."""
        try:
            from piper import PiperVoice

            self.models_dir.mkdir(parents=True, exist_ok=True)

            loaded_any = False

            for lang_code, voice_candidates in _VOICE_MAP.items():
                loaded = False
                for voice_name in voice_candidates:
                    try:
                        onnx_path = self._ensure_model(voice_name)
                        voice_obj = PiperVoice.load(str(onnx_path))
                        self._voices[lang_code] = voice_obj
                        self._voice_names[lang_code] = voice_name

                        # Read actual sample rate from the model config
                        json_path = Path(str(onnx_path) + ".json")
                        if json_path.exists():
                            with open(json_path, "r", encoding="utf-8") as f:
                                config = json.load(f)
                            model_sr = config.get("audio", {}).get("sample_rate", 22050)
                            log.debug(
                                f"[TTS] Voice '{voice_name}' sample_rate={model_sr}"
                            )

                        log.info(f"[TTS] Loaded voice: {voice_name} ({lang_code})")
                        loaded = True
                        loaded_any = True
                        break

                    except Exception as e:
                        log.warning(
                            f"[TTS] Failed to load voice '{voice_name}': {e}"
                        )
                        continue

                if not loaded:
                    log.warning(
                        f"[TTS] No voice available for language '{lang_code}'"
                    )

            if loaded_any:
                # Warmup with a short synthesis
                try:
                    first_lang = next(iter(self._voices))
                    voice_obj = self._voices[first_lang]
                    audio_buf = io.BytesIO()
                    with wave.open(audio_buf, "wb") as wav_file:
                        voice_obj.synthesize("Hello", wav_file)
                    log.info("[TTS] Piper warmup complete")
                except Exception as warmup_error:
                    log.warning(f"[TTS] Warmup failed: {warmup_error}")

            return loaded_any

        except ImportError:
            log.error(
                "[TTS] piper-tts not installed. Run: pip install piper-tts"
            )
            return False
        except Exception as e:
            log.error(f"[TTS] Failed to load Piper: {e}", exc_info=True)
            return False

    def _ensure_model(self, voice_name: str) -> Path:
        """
        Ensure a Piper voice model (.onnx + .onnx.json) exists locally.
        Downloads from Hugging Face if missing.

        Returns the path to the .onnx file.
        """
        onnx_path = self.models_dir / f"{voice_name}.onnx"
        json_path = self.models_dir / f"{voice_name}.onnx.json"

        if onnx_path.exists() and json_path.exists():
            log.debug(f"[TTS] Model already exists: {voice_name}")
            return onnx_path

        log.info(f"[TTS] Downloading voice model: {voice_name}...")
        onnx_url, json_url = _voice_download_url(voice_name)

        self.models_dir.mkdir(parents=True, exist_ok=True)

        # Download .onnx
        if not onnx_path.exists():
            log.info(f"[TTS]   Downloading {voice_name}.onnx ...")
            try:
                urllib.request.urlretrieve(onnx_url, str(onnx_path))
                size_mb = onnx_path.stat().st_size / (1024 * 1024)
                log.info(f"[TTS]   Downloaded {voice_name}.onnx ({size_mb:.1f}MB)")
            except Exception as e:
                # Clean up partial download
                if onnx_path.exists():
                    os.remove(onnx_path)
                raise RuntimeError(
                    f"Failed to download {voice_name}.onnx from {onnx_url}: {e}"
                ) from e

        # Download .onnx.json
        if not json_path.exists():
            log.info(f"[TTS]   Downloading {voice_name}.onnx.json ...")
            try:
                urllib.request.urlretrieve(json_url, str(json_path))
                log.info(f"[TTS]   Downloaded {voice_name}.onnx.json")
            except Exception as e:
                if json_path.exists():
                    os.remove(json_path)
                raise RuntimeError(
                    f"Failed to download {voice_name}.onnx.json from {json_url}: {e}"
                ) from e

        return onnx_path

    def set_language(self, lang: str) -> None:
        """
        Set the active language for voice routing.

        Called by the pipeline after STT detects the spoken language.
        Valid values: 'id', 'en', or any ISO 639-1 code.
        Falls back to English for unrecognized languages.
        """
        if lang in self._voices:
            if lang != self._current_lang:
                log.debug(
                    f"[TTS] Language switched: {self._current_lang} → {lang}"
                )
            self._current_lang = lang
        else:
            # Fallback to English for unsupported languages
            if lang != self._current_lang:
                log.debug(
                    f"[TTS] Unsupported language '{lang}', "
                    f"falling back to 'en'"
                )
            self._current_lang = "en"

    def _get_current_voice(self) -> tuple[object, str] | None:
        """Get the currently active PiperVoice based on language setting."""
        lang = self._current_lang

        if lang in self._voices:
            return self._voices[lang], self._voice_names[lang]

        # Fallback chain: en → first available
        if "en" in self._voices:
            return self._voices["en"], self._voice_names["en"]

        if self._voices:
            first_lang = next(iter(self._voices))
            return self._voices[first_lang], self._voice_names[first_lang]

        return None

    def _detect_text_language(self, text: str) -> str | None:
        """
        Lightweight language detection from text using langdetect.
        Returns 'id' or 'en', or None if detection fails.

        Note: langdetect is unreliable on very short strings (< ~40 chars).
        Use the response-level accumulation logic in synthesize_chunk instead
        of calling this directly on short chunks.
        """
        try:
            from langdetect import detect, DetectorFactory
            # Seed for deterministic results across runs
            DetectorFactory.seed = 0
            detected = detect(text)
            if detected in ("id", "ms"):  # Malay is close to Indonesian
                return "id"
            return "en"
        except Exception:
            return None

    def _sanitize_text(self, text: str) -> str:
        """
        Clean problematic text before sending to Piper.
        Prevents crashes from special characters.
        """

        if not text:
            return ""

        text = text.strip()

        # Remove problematic control chars
        text = "".join(ch for ch in text if ord(ch) >= 32)

        # Replace problematic formatting chars
        replacements = {
            "*": "",
            "#": "",
            "`": "",
            "_": " ",
            "~": "",
            "|": "",
            "[": "",
            "]": "",
            "{": "",
            "}": "",
            "<": "",
            ">": "",
        }

        for old, new in replacements.items():
            text = text.replace(old, new)

        # Normalize whitespace
        text = " ".join(text.split())

        return text

    async def synthesize_chunk(self, text: str) -> np.ndarray | None:
        """
        Synthesize a single text chunk to audio.

        Returns numpy array of audio samples (int16), or None on failure.
        Runs synthesis in thread executor to not block event loop.
        """

        text = self._sanitize_text(text)

        if not text:
            return None

        # ── Response-level language detection ────────────────────────────
        # langdetect is unreliable on short strings (< ~40 chars). We
        # accumulate chunks from the current response until there's enough
        # text to make a confident decision, then lock that language in for
        # the rest of the response (until the end-of-stream sentinel resets
        # the accumulator via reset_response_language()).
        if not self._response_lang_locked:
            self._response_text_acc += " " + text
            # Only attempt detection once we have enough chars for confidence
            if len(self._response_text_acc.strip()) >= 20:
                detected = self._detect_text_language(self._response_text_acc.strip())
                if detected:
                    self.set_language(detected)
                self._response_lang_locked = True

        loop = asyncio.get_event_loop()

        def _synth():

            if not self._available:
                return None

            voice_info = self._get_current_voice()
            if voice_info is None:
                log.warning("[TTS] No voice loaded")
                return None

            voice_obj, voice_name = voice_info

            try:
                log.debug(
                    f"[TTS] Synthesizing ({voice_name}): {text!r}"
                )

                # Piper synthesizes to float32 numpy arrays natively
                audio_segments = []
                sample_rate = 22050
                for chunk in voice_obj.synthesize(text):
                    audio_segments.append(chunk.audio_float_array)
                    sample_rate = chunk.sample_rate

                if not audio_segments:
                    return None

                audio_float32 = np.concatenate(audio_segments)

                # Apply speed adjustment if not 1.0
                if self.speed != 1.0 and self.speed > 0:
                    # Simple resampling for speed change
                    indices = np.arange(0, len(audio_float32), self.speed)
                    indices = indices[indices < len(audio_float32)].astype(int)
                    audio_float32 = audio_float32[indices]

                # Update actual sample rate from voice
                self.sample_rate = sample_rate

                return audio_float32

            except Exception as e:
                log.error(f"[TTS] Synthesis error: {e}", exc_info=True)
                return None

        audio = await loop.run_in_executor(None, _synth)

        return audio

    async def process_tts_queue(
        self,
        tts_chunk_queue: asyncio.Queue,
        interrupt_event: asyncio.Event,
    ) -> None:
        """
        Worker: drain TTS chunk queue, synthesize each chunk, push to audio queue.

        This is the TTS worker loop. Call as an asyncio task.
        """

        while True:

            try:
                chunk = await asyncio.wait_for(
                    tts_chunk_queue.get(),
                    timeout=0.5,
                )

            except asyncio.TimeoutError:
                continue

            except asyncio.CancelledError:
                break

            if chunk is None:
                # End-of-stream sentinel — one complete response has finished.
                # Reset the language accumulator so the next response can
                # re-detect its own language from scratch.
                self._response_text_acc = ""
                self._response_lang_locked = False
                await self.audio_queue.put(None)
                tts_chunk_queue.task_done()
                continue

            if interrupt_event.is_set():
                tts_chunk_queue.task_done()
                continue

            try:
                log.debug(f"[TTS] Synthesizing: {chunk!r}")

                audio = await self.synthesize_chunk(chunk)

                if audio is not None and not interrupt_event.is_set():

                    await self.audio_queue.put(audio)

                    log.debug(
                        f"[TTS] → Audio queue ({len(audio)} samples)"
                    )

            except Exception as worker_error:
                log.error(
                    f"[TTS] Worker error: {worker_error}",
                    exc_info=True,
                )

            finally:
                tts_chunk_queue.task_done()

    async def speak(self, text: str) -> None:
        """
        Convenience: synthesize full text and queue all audio directly.
        Used for startup greeting and test mode.
        """

        from utils.chunk_assembler import split_into_chunks

        chunks = split_into_chunks(
            text,
            min_words=2,
            max_words=25,
        )

        if not chunks:
            chunks = [text]

        for chunk in chunks:

            try:
                audio = await self.synthesize_chunk(chunk)

                if audio is not None:
                    await self.audio_queue.put(audio)

                    # tiny natural pause between chunks
                    await asyncio.sleep(0.05)

            except Exception as e:
                log.warning(f"[TTS] Speak chunk failed: {e}")

        # End-of-stream sentinel
        await self.audio_queue.put(None)
