# 3. Data Flow

```
[User speaks]
    |
    v PCM bytes (16kHz, 16-bit mono)
[Acoustic Gate] -- drops frames below -45 dBFS
    |
    v
[CalibrationAEC / SpectralSubAEC] -- 3-second chirp room impulse calibration & Wiener/LMS filter
    |
    v
[Peak Follower + Playback Gate Shield]
    | during TTS: threshold = max(-15.0 dBFS, speaker_peak + 7.0 dB)
    | blocks speaker bleed, prevents self-interruption
    |
    v VAD Ring Buffer (8-frame pre-history)
[webrtcvad] -- speech segment with onset consonants preserved
    |
    v audio bytes
[STT Worker: faster-whisper]
    |  Streaming partial transcripts + batch transcription
    |  Language detection: audio classifier + text-level verifier
    |  Hallucination filter (noise, repetition, domain names)
    |  local_files_only=True -- instant offline load
    v transcript + verified_language
[Rate Limiter] -- sliding window check (protects Cognitive Worker from rapid-fire spikes)
    |
    v
[Cognitive Worker: LLM #1]
    |  POST /v1/chat/completions to llama-server:8001
    |  --reasoning off
    v JSON: {respond, emotion, topic, store_memory, importance, memory_queries}
    + detected_language injected by pipeline
    |
[Memory System & Emotion Tracker]
    |  STM + LTM retrieval (JSON keyword matching + ChromaDB Vector Store semantic search)
    |  EmotionTracker: mood trend detection & periodic emotion summaries to LTM
    |
[Context Manager]
    |  Emotional context injection
    |  [Spoken Language: English. You MUST respond in English.]  <- per-turn directive
    v messages[]
[Personality Worker: LLM #2]
    |  POST /v1/chat/completions (stream=true) to llama-server:8002
    v token stream
[Chunk Assembler]
    v speech chunks
[Hybrid TTS Client]
    |  Text language detected -> engine selected
    |  en text -> Kokoro TTS (hexgrad/Kokoro-82M, af_heart)
    |  id text -> Piper TTS (id_ID-news_tts-medium)
    v audio arrays
[Playback Queue] -> Speaker
```
