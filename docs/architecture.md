# 2. Architecture Diagram

```
+-------------------------------------------------------------------+
|                    Sorachio-STS Pipeline                          |
|                                                                   |
|  +----------+    +-------------------------------------------+    |
|  |Microphone|───>| Acoustic Gate (RMS/dBFS)                  |    |
|  +----------+    +-------------------------------------------+    |
|                                   |                               |
|                                   v                               |
|                  +-------------------------------------------+    |
|                  | CalibrationAEC / SpectralSubAEC           |    |
|                  | (3s chirp room calibration, Wiener/LMS)   |    |
|                  +-------------------------------------------+    |
|                                   |                               |
|                                   v                               |
|                  +-------------------------------------------+    |
|                  | Peak Follower + Playback Gate Shield       |    |
|                  | (blocks speaker bleed during TTS, -15dBFS) |    |
|                  +-------------------------------------------+    |
|                                   |                               |
|                                   v                               |
|                  +---------------+    +---------------------+     |
|                  | AudioCapture  |───>|   STT Queue         |     |
|                  | (VAD + 8-frame|    |   (asyncio.Queue)   |     |
|                  |  pre-roll buf)|    +--------+------------+     |
|                  +---------------+             |                  |
|                        | barge-in              v                  |
|                        v              +---------------------+     |
|               +-----------------+     |   STT Worker        |     |
|               | Interrupt Event |     | (faster-whisper)    |     |
|               +-----------------+     | + Streaming STT     |     |
|                                       | + text lang verify  |     |
|                                       | + hallucination flt |     |
|                                       +--------+------------+     |
|                                                |transcript        |
|                                                |+ language        |
|                                                v                  |
|                                       +---------------------+     |
|                                       |    Rate Limiter     |     |
|                                       |   (sliding window)  |     |
|                                       +--------+------------+     |
|                                                | allowed          |
|                                                v                  |
|                                       +---------------------+     |
|                                       |  Cognitive Worker   |     |
|                                       |  LLM #1             |     |
|                                       |  (auto-detected)    |     |
|                                       |  JSON decision      |     |
|                                       +--------+------------+     |
|                                                | decision         |
|                                                | + detected_lang  |
|                                                v                  |
|                           +---------------------------------------+        |
|                           |            Memory System              |        |
|                           | STM + LTM (JSON + ChromaDB Vector)    |        |
|                           | + EmotionTracker (Mood trends)        |        |
|                           +-------------------+-------------------+        |
|                                               | context                    |
|                                               v                            |
|                           +-------------------------------+        |
|                           |       Context Manager         |        |
|                           | system prompt + STM + LTM +   |        |
|                           | [Spoken Language: EN/ID]      |        |
|                           +---------------+---------------+        |
|                                           | messages[]            |
|                                           v                       |
|                           +-------------------------------+        |
|                           |     Personality Worker        |        |
|                           |  LLM #2 (auto-detected)       |        |
|                           |  Streaming token generation   |        |
|                           |  Vision input (if mmproj)     |        |
|                           +---------------+---------------+        |
|                                           | token stream          |
|                                           v                       |
|                           +-------------------------------+        |
|                           |      Chunk Assembler          |        |
|                           |  sentence boundary detection  |        |
|                           +---------------+---------------+        |
|                                           | speech chunks         |
|                                           v                       |
|                           +-------------------------------+        |
|                           |  TTS Worker (Kokoro + Piper)  |        |
|                           |  EN text -> Kokoro (af_heart) |        |
|                           |  ID text -> Piper (id_ID-news) |        |
|                           +---------------+---------------+        |
|                                           | audio arrays          |
|                                           v                       |
|                           +-------------------------------+        |
|                           |   Audio Playback Queue        |        |
|                           |  (interruptible, sounddevice) |        |
|                           +---------------+---------------+        |
|                                           v                       |
|                                       +-------+                   |
|                                       |Speaker|                   |
|                                       +-------+                   |
+-------------------------------------------------------------------+
```

### Server Architecture

```
Python Orchestrator (asyncio event loop)
|
+-- HTTP -> llama-server :8001  -- LLM #1 Cognitive Gateway (auto-detected GGUF)
+-- HTTP -> llama-server :8002  -- LLM #2 Personality Core (auto-detected GGUF + mmproj)
+-- In-process -> faster-whisper -- STT (multilingual small model, offline)
+-- In-process -> Kokoro / Piper -- TTS (Kokoro EN 24kHz + Piper ID 22.05kHz->24kHz)
```
