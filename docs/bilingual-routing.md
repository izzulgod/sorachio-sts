# 12. Bilingual Language Routing

Sorachio-STS handles automatic **English ↔ Indonesian** routing at four independent levels:

### Level 1: Audio Language Classifier (faster-whisper)

Sums Indonesian-family probabilities (`id`, `ms`, `jw`, `su`) with a 3x bias correction:
- Routes to Indonesian if `corrected_id_prob > en_prob AND raw_id_prob > 0.20`
- Otherwise falls back to English

### Level 2: Text-Level Language Verification

Whisper's audio classifier has a known bug: English words starting with "In-" ("Introduce", "Inside") get misclassified as `id` (Indonesian).

After transcription, `_verify_text_language()` checks the **decoded text**:
- Indonesian keyword match → `id`
- `langdetect` returns English → corrects to `en`

This completely fixes the "Introduce yourself → Indonesian response" bug.

### Level 3: Per-Turn LLM Directive

The Context Manager injects a strict turn-level language directive:
- English input → `[Spoken Language: English. You MUST respond in English.]`
- Indonesian input → `[Spoken Language: Indonesian. You MUST respond in Indonesian.]`

### Level 4: Hybrid TTS Engine Routing

- English text → Kokoro TTS (`hexgrad/Kokoro-82M`, voice: `af_heart`, 24kHz)
- Indonesian text → Piper TTS (`id_ID-news_tts-medium`, 22.05kHz -> 24kHz resampled)
