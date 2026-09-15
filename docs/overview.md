# 1. Project Overview

Sorachio-STS is a **complete, local-first, real-time Speech-to-Speech (STS) AI Companion** system. It runs entirely on your local machine — no cloud APIs, no subscriptions, no data sent anywhere.

The system is designed from the ground up as a **scalable AI companion operating system** — with architecture that anticipates future expansion into robotics, multi-agent systems, cameras, sensors, and ROS2 integration.

### Key Properties

| Property | Detail |
|----------|--------|
| **Fully Local** | All inference runs on-device via llama.cpp + faster-whisper + Kokoro TTS / Piper TTS |
| **Real-Time Streaming** | TTS begins before LLM finishes generating |
| **Two-LLM Architecture** | Cognitive Gateway (LLM #1) + Personality Core (LLM #2) |
| **Model-Agnostic** | Auto-detects any GGUF model — just drop and restart |
| **Vision Ready** | LLM #2 supports multimodal input via mmproj projector |
| **Bilingual** | Automatic English / Indonesian language detection & voice routing |
| **Interruptible** | VAD-based barge-in stops playback instantly; self-interrupt shielded |
| **Adaptive AEC** | Calibration-based room impulse response echo cancellation (3s chirp sweep + Wiener/LMS filter) |
| **Streaming STT** | Whisper partial transcript streaming for lower latency |
| **Vector Memory** | ChromaDB semantic search + sentence-transformers embeddings for LTM |
| **Emotion Persistence** | Long-term mood trend detection and emotion pattern tracking |
| **Rate Limiting** | Sliding window algorithm to prevent rapid-fire input spikes |
| **Anteque Ashing** | Built-in Python quality code verifier (ruff + pyrefly) enforced on bootstrap |
| **Persistent Memory** | Remembers you across sessions (JSON file + ChromaDB vector store) |
| **Modular** | Each component is a separate async worker |
| **Rich CLI UI** | Transient spinners, animated loaders, and cognitive status pills |
| **Cross-Platform** | Works on macOS, Linux, and Windows |

### Current Model Configuration

| Slot | Model | Size | Role | Local Path |
|------|-------|------|------|------------|
| LLM #1 | Qwen2.5-Coder-0.5B-Instruct (Q8_0) | ~644 MB | Cognitive Gateway (JSON router) | `models/llm1/` |
| LLM #2 | Qwen3.5-4B (Q4_K_M) | ~2.6 GB | Personality Core (conversation) + **Vision** | `models/llm2/` |
| STT | faster-whisper small | ~484 MB | Speech-to-Text (multilingual ID/EN) | `models/stt/` |
| TTS (EN) | Kokoro-82M (`af_heart`) | ~327 MB | English female voice — 24kHz native | `models/tts/kokoro/` |
| TTS (ID) | Piper `id_ID-news_tts-medium` | ~67 MB | Indonesian female voice — 22.05kHz→24kHz | `models/tts/` |
| Vector Embed | all-MiniLM-L6-v2 | ~90 MB | Semantic memory embeddings (offline) | `models/vector/all-MiniLM-L6-v2/` |

> **All model weights live inside the project** under `models/` — no cloud cache, no `~/.cache` writes. Everything is self-contained and portable.

> **Flexible Model Swapping**: LLM models are auto-detected from `models/llm1/` and `models/llm2/`. Just drop a new `.gguf` and restart.

> **STT/TTS Models**: Automatically downloaded to `models/` on first run by MBG — no manual setup required.
