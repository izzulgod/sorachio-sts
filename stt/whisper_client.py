"""
Sorachio-STS STT Client (faster-whisper / CTranslate2)
In-process speech-to-text transcription using faster-whisper.

Uses CTranslate2 backend — no subprocess, no C++ build required.
Input: raw PCM audio bytes (16kHz, 16-bit, mono)
Output: transcribed text string

Flow:
  1. Convert PCM bytes to float32 numpy array
  2. Run faster-whisper model.transcribe()
  3. Collect segments, detect language
  4. Clean and return text
"""

from __future__ import annotations

import asyncio
import re

import numpy as np

from utils.logging_setup import get_logger

log = get_logger("stt.whisper")


# ---------------------------------------------------------------------------
# Audio helpers
# ---------------------------------------------------------------------------

def _pcm_to_float32(pcm_bytes: bytes, sample_rate: int = 16000) -> np.ndarray:
    """Convert raw 16-bit mono PCM bytes to float32 numpy array."""
    audio_int16 = np.frombuffer(pcm_bytes, dtype=np.int16)
    audio_float32 = audio_int16.astype(np.float32) / 32768.0
    return audio_float32


def _clean_transcript(text: str) -> str:
    """Remove whisper artifacts and clean up transcript."""
    # Remove [BLANK_AUDIO], (music), timing markers
    text = re.sub(r"\[.*?\]", "", text)
    text = re.sub(r"\(.*?\)", "", text)
    text = re.sub(r"\d{2}:\d{2}:\d{2}\.\d{3}\s*-->\s*\d{2}:\d{2}:\d{2}\.\d{3}", "", text)
    # Normalize whitespace
    text = " ".join(text.split())
    return text.strip()


# Known Whisper hallucination phrases — generated on silence/noise.
# These are well-documented artefacts of the Whisper model.
_HALLUCINATION_PHRASES: set[str] = {
    "thank you",
    "thank you.",
    "thanks.",
    "thanks for watching.",
    "thanks for watching!",
    "thank you for watching.",
    "thank you for watching!",
    "bye.",
    "bye!",
    "bye bye.",
    "goodbye.",
    "you.",
    "you",
    "hmm.",
    "hmm",
    "um.",
    "uh.",
    "oh.",
    "ah.",
    "so.",
    "okay.",
    "yeah.",
    "yes.",
    "no.",
    "...",
    "the end.",
    "the end",
    "subscribe.",
    "please subscribe.",
    "like and subscribe.",
    "silence.",
    "i'm sorry.",
    # Indonesian hallucinations
    "terima kasih.",
    "terima kasih",
    "makasih.",
    "makasih",
    "ya.",
    "ya",
    "oke.",
    "oke",
    "baik.",
    "hm.",
    "eh.",
    "untuk melihat diri sendiri.",
    "untuk melihat diri sendiri",
    "dan.",
    "dan",
}


def _is_hallucination(text: str) -> bool:
    """Return True if the transcript looks like a Whisper hallucination."""
    normalised = text.strip().lower()
    if normalised in _HALLUCINATION_PHRASES:
        return True

    # 1. Check for phrase-level repetition
    # Split text by commas, periods, or other punctuation, and strip whitespace.
    import re
    phrases = [p.strip() for p in re.split(r'[,.!?]+', normalised) if p.strip()]
    if len(phrases) >= 3:
        from collections import Counter
        counts = Counter(phrases)
        for phrase, count in counts.items():
            if len(phrase) >= 4 and count >= 3:
                log.debug(f"[STT] Filtered phrase repetition loop: '{phrase}' repeated {count} times")
                return True

    # 2. Check for consecutive word repetition loops
    words = normalised.rstrip(".,!?").split()
    if len(words) >= 4:
        consecutive_repeats = 0
        for i in range(len(words) - 1):
            if words[i] == words[i+1]:
                consecutive_repeats += 1
            else:
                consecutive_repeats = 0
            if consecutive_repeats >= 2:  # Same word 3 times consecutively
                return True

    # 3. Word n-gram level repetition detection
    if len(words) >= 6:
        # Check for repeating word sequences of length 2 to 5
        for n in range(2, 6):
            for i in range(len(words) - 2 * n + 1):
                ngram1 = words[i : i + n]
                ngram2 = words[i + n : i + 2 * n]
                if ngram1 == ngram2:
                    repeats = 1
                    idx = i + n
                    while idx + n <= len(words) and words[idx : idx + n] == ngram1:
                        repeats += 1
                        idx += n
                    if (n >= 3 and repeats >= 2) or (n >= 2 and repeats >= 3):
                        log.debug(f"[STT] Filtered ngram repetition loop: {ngram1} repeated {repeats} times")
                        return True

    # Single word of 4 chars or fewer is almost certainly noise
    if len(normalised.split()) == 1 and len(normalised.rstrip(".,!?")) <= 4:
        return True
    return False


# ---------------------------------------------------------------------------
# WhisperClient
# ---------------------------------------------------------------------------

class WhisperClient:
    """
    In-process Whisper STT using faster-whisper (CTranslate2).

    Transcribes audio segments to text with automatic language detection
    for Indonesian ('id') and English ('en').
    """

    def __init__(
        self,
        model_size: str = "base",
        language: str | None = None,
        threads: int = 4,
        beam_size: int = 5,
        temperature: float = 0.0,
        timeout_s: float = 10.0,
        device: str = "cpu",
        compute_type: str = "int8",
    ):
        self.model_size = model_size
        # None or "auto" = auto-detect; otherwise pin to a language
        self.language = None if language in (None, "auto") else language
        self.threads = threads
        self.beam_size = beam_size
        self.temperature = temperature
        self.timeout_s = timeout_s
        self.device = device
        self.compute_type = compute_type

        self._model = None
        self._available = False
        self._last_detected_language: str | None = None

    @property
    def last_detected_language(self) -> str | None:
        """Language code detected from the most recent transcription (e.g. 'en', 'id')."""
        return self._last_detected_language

    async def initialize(self) -> bool:
        """Load the faster-whisper model (blocking, run once at startup)."""
        loop = asyncio.get_event_loop()
        ok = await loop.run_in_executor(None, self._load_model)
        self._available = ok

        if ok:
            lang_desc = self.language if self.language else "auto (id/en)"
            log.info(
                f"[STT] faster-whisper ready — model={self.model_size} "
                f"language={lang_desc} device={self.device}"
            )
        else:
            log.warning(
                "[STT] faster-whisper not available — install with: pip install faster-whisper"
            )
        return ok

    def _load_model(self) -> bool:
        """Load faster-whisper model in thread (avoids blocking event loop)."""
        try:
            from faster_whisper import WhisperModel

            self._model = WhisperModel(
                self.model_size,
                device=self.device,
                compute_type=self.compute_type,
                cpu_threads=self.threads,
            )

            log.info(f"[STT] Model '{self.model_size}' loaded successfully")

            # Warmup: run a dummy transcription to trigger ONNX JIT
            # compilation now, not on the first real user utterance.
            # Pin to 'en' to skip Whisper's language detection in warmup.
            try:
                dummy = np.zeros(16000, dtype=np.float32)  # 1s silence
                segs, _info = self._model.transcribe(
                    dummy,
                    language="en",
                    beam_size=1,
                    temperature=0.0,
                )
                _ = list(segs)  # consume generator
                log.info("[STT] Warmup complete — model is hot")
            except Exception as wu_err:
                log.warning(f"[STT] Warmup failed (non-fatal): {wu_err}")

            return True

        except ImportError:
            log.error(
                "[STT] faster-whisper not installed. Run: pip install faster-whisper"
            )
            return False
        except Exception as e:
            log.error(f"[STT] Failed to load faster-whisper: {e}", exc_info=True)
            return False

    async def transcribe(self, audio_bytes: bytes) -> str | None:
        """
        Transcribe raw PCM audio bytes to text.

        Args:
            audio_bytes: Raw 16-bit mono 16kHz PCM audio

        Returns:
            Transcribed text string, or None on failure
        """
        if not self._available or self._model is None:
            log.warning("[STT] Model not loaded — call initialize() first")
            return None

        if len(audio_bytes) < 1000:
            log.debug("[STT] Audio too short, skipping")
            return None

        loop = asyncio.get_event_loop()

        try:
            transcript = await asyncio.wait_for(
                loop.run_in_executor(None, self._transcribe_sync, audio_bytes),
                timeout=self.timeout_s,
            )
        except asyncio.TimeoutError:
            log.warning(f"[STT] Timeout after {self.timeout_s}s")
            return None
        except Exception as e:
            log.error(f"[STT] Transcription error: {e}", exc_info=True)
            return None

        return transcript

    def _transcribe_sync(self, audio_bytes: bytes) -> str | None:
        """Synchronous transcription (runs in executor).

        IMPORTANT: faster-whisper's transcribe() returns a lazy generator.
        We MUST consume ALL segments into a list immediately — otherwise
        the generator is never evaluated and the call appears to hang.

        Language routing (auto mode):
            We run detect_language() first (extremely fast, ~0.02s) to get
            probabilities. We sum Indonesian and regional candidates (ms, jw, su)
            and compare against English (en). We then force Whisper to transcribe
            using either 'id' or 'en'. This completely prevents Whisper from
            misdetecting background noise or short speech as random languages
            (like Arabic, French, etc.) and outputting incorrect scripts.
        """
        try:
            audio = _pcm_to_float32(audio_bytes)

            # In auto mode: run fast language candidate check first
            if self.language is None:
                try:
                    _, _, all_probs = self._model.detect_language(audio)
                    probs = dict(all_probs)
                    
                    id_prob = probs.get("id", 0.0)
                    ms_prob = probs.get("ms", 0.0)   # Malay
                    jw_prob = probs.get("jw", 0.0)   # Javanese
                    su_prob = probs.get("su", 0.0)   # Sundanese
                    en_prob = probs.get("en", 0.0)
                    
                    total_id_prob = id_prob + ms_prob + jw_prob + su_prob
                    
                    # Log language detection probabilities
                    log.debug(
                        f"[STT] Candidate probabilities — id/ms/jw/su: {total_id_prob:.3f}, en: {en_prob:.3f}"
                    )
                    
                    # Require a confident threshold (0.15) to route to Indonesian;
                    # otherwise default to English. This prevents static or short English
                    # words from being misrouted and translated to Indonesian.
                    if total_id_prob > en_prob and total_id_prob > 0.15:
                        target_lang = "id"
                    else:
                        target_lang = "en"
                except Exception as detect_err:
                    log.warning(f"[STT] Language detection failed: {detect_err}")
                    target_lang = "en"
            else:
                target_lang = self.language

            self._last_detected_language = target_lang

            segments_gen, info = self._model.transcribe(
                audio,
                language=target_lang,
                beam_size=self.beam_size,
                temperature=self.temperature,
                vad_filter=True,
                vad_parameters=dict(
                    min_silence_duration_ms=300,
                    speech_pad_ms=200,
                ),
                # Prevent repetition loops (hallucinations)
                compression_ratio_threshold=2.0,   # Strict limit on highly repetitive text
                log_prob_threshold=-1.0,
                no_speech_threshold=0.6,
                condition_on_previous_text=False,  # DO NOT carry over context/loops from previous turns
            )

            # CRITICAL: consume the lazy generator immediately.
            # faster-whisper does all actual decoding during iteration.
            # Not calling list() here causes the pipeline to silently stall.
            segments = list(segments_gen)

            log.debug(
                f"[STT] Transcribed | lang={target_lang} | "
                f"whisper_detected={info.language} (prob={info.language_probability:.2f})"
            )

            # Collect all segment texts
            text_parts = [seg.text for seg in segments]
            full_text = " ".join(text_parts)
            transcript = _clean_transcript(full_text)

            if transcript:
                # Filter out known Whisper hallucinations
                if _is_hallucination(transcript):
                    log.debug(f"[STT] Filtered hallucination: {transcript!r}")
                    return None

                log.info(f"[STT] Transcript ({target_lang}): {transcript!r}")
            else:
                log.debug("[STT] Empty transcript")

            return transcript if transcript else None

        except Exception as e:
            log.error(f"[STT] Transcription error: {e}", exc_info=True)
            return None
