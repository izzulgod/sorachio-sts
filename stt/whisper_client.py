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
import queue
import re
import threading
from collections.abc import AsyncIterator

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
    "we love you",
    "we love you.",
    "we love you too",
    "i love you",
    "i love you.",
    "thank you for sharing",
    "thank you for sharing that with me",
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

    # 4. Filter out developer name/domain name hallucinations generated on silence/noise
    if "izzulgod.com" in normalised or "translated by" in normalised or normalised == "izzulgod":
        log.debug(f"[STT] Filtered developer/domain hallucination: '{text}'")
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
        beam_size: int = 1,
        temperature: float = 0.0,
        timeout_s: float = 10.0,
        device: str = "cpu",
        compute_type: str = "int8",
        streaming: bool = True,
        chunk_length_s: float = 5.0,
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
        self.streaming = streaming
        self.chunk_length_s = chunk_length_s

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

            self.models_dir.mkdir(parents=True, exist_ok=True)

            try:
                self._model = WhisperModel(
                    self.model_size,
                    device=self.device,
                    compute_type=self.compute_type,
                    cpu_threads=self.threads,
                    download_root=str(self.models_dir),
                    local_files_only=True,
                )
            except Exception as offline_err:
                log.info(f"[STT] Local offline load failed for '{self.model_size}', checking online: {offline_err}")
                self._model = WhisperModel(
                    self.model_size,
                    device=self.device,
                    compute_type=self.compute_type,
                    cpu_threads=self.threads,
                    download_root=str(self.models_dir),
                    local_files_only=False,
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

        # Debug: save first 3 audio segments to WAV files for offline analysis
        if not hasattr(self, '_debug_save_count'):
            self._debug_save_count = 0
        if self._debug_save_count < 3:
            try:
                import wave
                from pathlib import Path
                debug_dir = Path("logs/debug_audio")
                debug_dir.mkdir(parents=True, exist_ok=True)
                wav_path = debug_dir / f"stt_input_{self._debug_save_count}.wav"
                with wave.open(str(wav_path), "wb") as wf:
                    wf.setnchannels(1)
                    wf.setsampwidth(2)  # 16-bit
                    wf.setframerate(16000)
                    wf.writeframes(audio_bytes)
                log.info(f"[STT] Debug: saved audio to {wav_path} ({len(audio_bytes)} bytes)")
                self._debug_save_count += 1
            except Exception as save_err:
                log.warning(f"[STT] Debug save failed: {save_err}")

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

    async def transcribe_streaming(
        self,
        audio_bytes: bytes,
    ) -> AsyncIterator[str]:
        """
        Transcribe audio with streaming partial results.

        Yields partial transcripts as they become available.
        Falls back to non-streaming if streaming not supported.
        """
        if not self.streaming or not self._available:
            # Fallback to non-streaming
            result = await self.transcribe(audio_bytes)
            if result:
                yield result
            return

        if not self._model or len(audio_bytes) < 1000:
            return

        try:
            async for chunk in self._transcribe_streaming_async(audio_bytes):
                yield chunk
        except asyncio.TimeoutError:
            log.warning(f"[STT] Streaming timeout after {self.timeout_s}s")
        except Exception as e:
            log.error(f"[STT] Streaming error: {e}", exc_info=True)

    async def _transcribe_streaming_async(
        self,
        audio_bytes: bytes,
    ) -> AsyncIterator[str]:
        """Async wrapper for streaming transcription."""
        loop = asyncio.get_event_loop()

        # Detect language first
        target_lang = await loop.run_in_executor(
            None, self._detect_language_sync, audio_bytes
        )

        # Run streaming transcription in executor
        audio = _pcm_to_float32(audio_bytes)

        def _stream_gen():
            try:
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
                    compression_ratio_threshold=2.0,
                    log_prob_threshold=-1.0,
                    no_speech_threshold=0.6,
                    condition_on_previous_text=False,
                )

                # Yield segments as they complete
                for segment in segments_gen:
                    text = _clean_transcript(segment.text)
                    if text:
                        yield text

            except Exception as e:
                log.error(f"[STT] Streaming generation error: {e}")

        # Run in executor and yield
        import queue
        result_queue: queue.Queue = queue.Queue()
        done_event = threading.Event()

        def _run_stream():
            try:
                for chunk in _stream_gen():
                    result_queue.put(chunk)
            finally:
                done_event.set()

        stream_thread = threading.Thread(target=_run_stream, daemon=True)
        stream_thread.start()

        # Yield results as they arrive
        while not done_event.is_set() or not result_queue.empty():
            try:
                chunk = result_queue.get(timeout=0.1)
                yield chunk
            except queue.Empty:
                continue

    def _detect_language_sync(self, audio_bytes: bytes) -> str:
        """Synchronous language detection."""
        if self.language is not None:
            return self.language

        try:
            audio = _pcm_to_float32(audio_bytes)
            _, _, all_probs = self._model.detect_language(audio)
            probs = dict(all_probs)

            id_prob = probs.get("id", 0.0)
            ms_prob = probs.get("ms", 0.0)
            jw_prob = probs.get("jw", 0.0)
            su_prob = probs.get("su", 0.0)
            en_prob = probs.get("en", 0.0)

            total_id_prob = id_prob + ms_prob + jw_prob + su_prob

            if total_id_prob > en_prob and total_id_prob > 0.15:
                return "id"
            return "en"

        except Exception:
            return "en"

    def _transcribe_sync(self, audio_bytes: bytes) -> str | None:
        """Synchronous transcription (runs in executor).

        IMPORTANT: faster-whisper's transcribe() returns a lazy generator.
        We MUST consume ALL segments into a list immediately — otherwise
        the generator is never evaluated and the call appears to hang.

        Language routing (auto mode):
            We run detect_language() first (extremely fast, ~0.02s) to get
            probabilities. We sum Indonesian and regional candidates (ms, jw, su)
            and compare against English (en) with a bias correction factor.
            The Whisper base model has a massive English prior (~43% on silence),
            so Indonesian probabilities are multiplied by a correction factor
            to compensate. We then force Whisper to transcribe using either
            'id' or 'en' to prevent random language misdetection.
        """
        try:
            audio = _pcm_to_float32(audio_bytes)
            audio_duration_s = len(audio) / 16000.0

            log.info(
                f"[STT] Processing audio: {len(audio_bytes)} bytes, "
                f"{audio_duration_s:.1f}s"
            )

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

                    log.info(f"[STT] Language route → {target_lang}")
                except Exception as detect_err:
                    log.warning(f"[STT] Language detection failed: {detect_err}")
                    target_lang = "en"
            else:
                target_lang = self.language

            self._last_detected_language = target_lang

            # Audio is pre-filtered by capture.py VAD; disabling secondary VAD
            # speeds up transcription by ~1s
            init_prompt = (
                "Sorachio is an AI companion created by izzulgod. "
                "Conversation in English and Indonesian."
            )
            segments_gen, info = self._model.transcribe(
                audio,
                language=target_lang,
                beam_size=self.beam_size,
                temperature=self.temperature,
                vad_filter=False,
                initial_prompt=init_prompt,
                # Prevent repetition loops (hallucinations)
                compression_ratio_threshold=2.0,
                log_prob_threshold=-1.0,
                no_speech_threshold=0.6,
                # DO NOT carry over context/loops from previous turns
                condition_on_previous_text=False,
            )

            # CRITICAL: consume the lazy generator immediately.
            # faster-whisper does all actual decoding during iteration.
            # Not calling list() here causes the pipeline to silently stall.
            segments = list(segments_gen)

            log.info(
                f"[STT] Transcribed | lang={target_lang} | "
                f"whisper_detected={info.language} (prob={info.language_probability:.2f}) | "
                f"segments={len(segments)}"
            )

            # Collect all segment texts
            text_parts = [seg.text for seg in segments]
            full_text = " ".join(text_parts)
            transcript = _clean_transcript(full_text)

            if transcript:
                # Text-level language verification to fix audio classifier misdetections (e.g. "Introduce...")
                verified_lang = self._verify_text_language(transcript, target_lang)
                self._last_detected_language = verified_lang

                # Filter out known Whisper hallucinations
                if _is_hallucination(transcript):
                    log.info(f"[STT] Filtered hallucination: {transcript!r}")
                    return None

                log.info(f"[STT] ✓ Result ({verified_lang}): {transcript!r}")
            else:
                log.info("[STT] Empty transcript (no speech detected)")

            return transcript if transcript else None

        except Exception as e:
            log.error(f"[STT] Transcription error: {e}", exc_info=True)
            return None

    def _verify_text_language(self, text: str, initial_lang: str) -> str:
        """
        Verify and correct Whisper's audio language classification using text content.
        Whisper's audio classifier often misclassifies English words starting with 'In-'
        ('Introduce', 'Inside') as 'id' (Indonesian).
        """
        id_keywords = {
            "saya", "aku", "kamu", "dengan", "senang", "halo", "nama", "terima", "kasih",
            "apa", "bisa", "ini", "itu", "yang", "dan", "untuk", "ada", "perkenalkan",
            "siapa", "namamu", "ceritakan", "lihat", "bagaimana", "kabarlah", "kabar"
        }
        import re
        words = set(re.findall(r'\b\w+\b', text.lower()))
        if len(words.intersection(id_keywords)) >= 1:
            return "id"

        try:
            from langdetect import detect
            text_lang = detect(text)
            if text_lang == "en":
                return "en"
        except Exception:
            pass

        return initial_lang
