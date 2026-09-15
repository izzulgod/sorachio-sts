# 5. Threading Model

```
Main Thread (asyncio event loop)
|
+-- [asyncio Task] STT Worker           -- awaits stt_queue, runs faster-whisper in executor
+-- [asyncio Task] Cognitive Worker     -- awaits cognitive_queue, HTTP to LLM #1
+-- [asyncio Task] Personality Worker   -- HTTP streaming to LLM #2
+-- [asyncio Task] TTS Worker           -- synthesizes via Piper in thread executor
+-- [asyncio Task] Playback Worker      -- drains audio queue, plays via sounddevice
|
+-- [Thread] VAD Worker                 -- continuous mic monitoring + ring buffer
|   +-- puts audio to stt_queue via run_coroutine_threadsafe()
|
+-- [Thread Executor] Piper Synthesis   -- blocking ONNX inference offloaded
+-- [Thread Executor] Whisper Transcribe -- blocking CTranslate2 inference offloaded
```
