# Sorachio-STS

> **Speech To Speech AI Companion System**
> *Foundation for a future robotics companion platform*

---

### System in Action (CLI Showcase)

Here is a preview of how the interactive CLI behaves in different operational modes, showcasing the real-time **Cognitive Gateway** status bar and state transitions.

#### 1. Full Voice/Run Mode (`python main.py run`)
In voice mode, the pipeline continuously monitors microphone input using VAD. Once speech is detected and transcribed, the Cognitive Gateway immediately computes the emotional state, topic, and response confidence, seamlessly transitioning into the streaming audio playback phase. Filler or hesitant speech (e.g., "Um...") is filtered out and marked as `X ignore`, preventing unnecessary processing on non-substantive input.

![Sorachio-STS Voice Mode](docs/ss-run.png)


#### 2. Interactive Text Mode (`python main.py text`)
In text mode, you can chat with the companion using keyboard inputs. This mode is perfect for testing prompts and observing how the Cognitive Gateway filters out filler words (e.g., "eumm") by marking them as `X ignore`, just like in voice mode — saving valuable compute cycles.

![Sorachio-STS Text Mode](docs/ss-txt.png)

---

## Table of Contents

1. [Project Overview](#1-project-overview)
2. [Architecture Diagram](#2-architecture-diagram)
3. [Data Flow](#3-data-flow)
4. [Folder Structure](#4-folder-structure)
5. [Threading Model](#5-threading-model)
6. [Installation](#6-installation)
7. [Model Setup](#7-model-setup)
8. [Running the System](#8-running-the-system)
9. [Configuration Guide](#9-configuration-guide)
10. [Cognitive Gateway Explained](#10-cognitive-gateway-explained)
11. [Streaming Pipeline Explained](#11-streaming-pipeline-explained)
12. [Memory Architecture](#12-memory-architecture)
13. [CLI Reference](#13-cli-reference)
14. [MBG System](#14-mbg-system)
15. [Troubleshooting](#15-troubleshooting)
16. [Future Robotics Expansion](#16-future-robotics-expansion)

---

## 1. Project Overview

Sorachio-STS is a **complete, local-first, real-time Speech-to-Speech (STS) AI Companion** system. It runs entirely on your local machine — no cloud APIs, no subscriptions, no data sent anywhere.

The system is designed from the ground up as a **scalable AI companion operating system** — with architecture that anticipates future expansion into robotics, multi-agent systems, cameras, sensors, and ROS2 integration.

### Key Properties

| Property | Detail |
|----------|--------|
| **Fully Local** | All inference runs on-device via llama.cpp |
| **Real-Time Streaming** | TTS begins before LLM finishes generating |
| **Two-LLM Architecture** | Cognitive Gateway + Personality Core |
| **Interruptible** | VAD detects user speech, stops playback instantly |
| **Persistent Memory** | Remembers you across sessions (JSON -> future vector DB) |
| **Modular** | Each component is a separate async worker |
| **Rich CLI UI** | Transient spinners, animated loaders, and cognitive status pills |
| **Cross-Platform** | Works on macOS, Linux, and Windows |

---

## 2. Architecture Diagram

```
┌─────────────────────────────────────────────────────────────────┐
│                    Sorachio-STS Pipeline                        │
│                                                                 │
│  ┌──────────┐    ┌──────────────┐    ┌─────────────────────┐    │
│  │Microphone│───▶│ AudioCapture │───▶│   STT Queue         │    │
│  └──────────┘    │    (VAD)     │    │   (asyncio.Queue)   │    │
│                  └──────────────┘    └────────┬────────────┘    │
│                        │ interrupt            │                 │
│                        ▼                      ▼                 │
│               ┌─────────────────┐    ┌──────────────────────┐   │
│               │  PlaybackState  │    │   STT Worker         │   │
│               │  (asyncio.Event)│    │   (whisper.cpp CLI)  │   │
│               └─────────────────┘    └────────┬─────────────┘   │
│                                               │ transcript      │
│                                               ▼                 │
│                                      ┌──────────────────────┐   │
│                                      │   Cognitive Worker   │   │
│                                      │   LLM #1             │   │
│                                      │   Qwen3-0.6B         │   │
│                                      │   → JSON decision    │   │
│                                      └────────┬─────────────┘   │
│                                               │ decision        │
│                                               ▼                 │
│                           ┌─────────────────────────────────┐   │
│                           │          Memory System          │   │
│                           │  ┌────────────┐ ┌────────────┐  │   │
│                           │  │    STM     │ │    LTM     │  │   │
│                           │  │ (in-memory)│ │ (JSON file)│  │   │
│                           │  └────────────┘ └────────────┘  │   │
│                           └────────────────┬────────────────┘   │
│                                            │ context            │
│                                            ▼                    │
│                           ┌─────────────────────────────────┐   │
│                           │         Context Manager         │   │
│                           │ system prompt + STM + LTM + emo │   │
│                           └────────────────┬────────────────┘   │
│                                            │ messages[]         │
│                                            ▼                    │
│                           ┌─────────────────────────────────┐   │
│                           │       Personality Worker        │   │
│                           │     LLM #2 (gemma-3-1b-it)      │   │
│                           │   Streaming token generation    │   │
│                           └────────────────┬────────────────┘   │
│                                            │ token stream       │
│                                            ▼                    │
│                           ┌─────────────────────────────────┐   │
│                           │         Chunk Assembler         │   │
│                           │   sentence boundary detection   │   │
│                           │  "Hello there." "How are you?"  │   │
│                           └────────────────┬────────────────┘   │
│                                            │ speech chunks      │
│                                            ▼                    │
│                           ┌─────────────────────────────────┐   │
│                           │       TTS Worker (Kokoro)       │   │
│                           │       per-chunk synthesis       │   │
│                           └────────────────┬────────────────┘   │
│                                            │ audio arrays       │
│                                            ▼                    │
│                           ┌─────────────────────────────────┐   │
│                           │      Audio Playback Queue       │   │
│                           │  (interruptible, sounddevice)   │   │
│                           └────────────────┬────────────────┘   │
│                                            │                    │
│                                            ▼                    │
│                                        ┌───────┐                │
│                                        │Speaker│                │
│                                        └───────┘                │
└─────────────────────────────────────────────────────────────────┘
```

### Server Architecture

```
Python Orchestrator (asyncio event loop)
|
+-- HTTP -> llama-server :8001 -- LLM #1 Cognitive Gateway (Qwen3-0.6B-Q8_0)
+-- HTTP -> llama-server :8002 -- LLM #2 Personality Core (gemma-3-1b-it-Q8_0)
+-- Subprocess -> whisper-cli    -- STT (whisper-base.en)
+-- In-process -> Kokoro         -- TTS (kokoro Python library)
```

---

## 3. Data Flow

### Full Pipeline Flow

```
[User speaks]
    |
    v PCM bytes (16kHz, 16-bit mono)
[webrtcvad] -- silence detected --> speech segment assembled
    |
    v audio bytes
[stt_queue] ----------------------> [STT Worker]
    |                                    |
    |                          whisper-cli subprocess
    |                                    |
    |                          <-- transcript string
    |
    v
[cognitive_queue] --------------> [Cognitive Worker]
    |
    |  POST /v1/chat/completions
    |  to llama-server:8001 (Qwen3)
    |
    v JSON decision:
    {
        "respond": true,
        "emotion": "anxious",
        "topic": "education",
        "store_memory": true,
        "importance": 0.85,
        "memory_queries": ["exam", "stress"]
    }
    |
    +-- LTM retrieval (memory_queries -> top-K memories)
    +-- STM injection (last N messages)
    +-- Emotional context injection
    +-- Personality prompt assembly
    |
    v messages[]
[Personality Worker]
    |
    |  POST /v1/chat/completions (stream=true)
    |  to llama-server:8002 (gemma-3-1b-it)
    |
    v token stream: "Hello " "there! " "I " "can " "hear " ...
    |
[Chunk Assembler]
    |
    v "Hello there!" -> TTS -> Audio -> Speaker
    | "I can hear that you're stressed." -> TTS -> Audio -> Speaker
    | "Tell me more about what's going on." -> TTS -> ...
    |
    v (while still streaming LLM tokens!)

[STM] <- store user message + response
[LTM] <- conditionally store if importance >= threshold
```

---

## 4. Folder Structure

```
Sorachio-STS/
|
+-- main.py                 # Entry point (MBG runs automatically)
+-- bootstrapper.py         # Legacy bootstrapper (kept for compatibility)
+-- pyproject.toml          # Ruff + pyrefly configuration
+-- README.md
|
+-- config/                 # Configuration system
|   +-- sorachio.yaml       # Master config (edit this!)
|   +-- settings.py         # Pydantic settings loader
|
+-- core/                   # Pipeline orchestrator
|   +-- pipeline.py         # Master async pipeline
|   +-- events.py           # Event bus (pub/sub)
|
+-- audio/                  # Audio I/O
|   +-- capture.py          # Mic capture + VAD
|   +-- playback.py         # Interruptible playback queue
|
+-- stt/                    # Speech-to-Text
|   +-- whisper_client.py   # whisper.cpp subprocess client
|
+-- tts/                    # Text-to-Speech
|   +-- kokoro_client.py    # Kokoro streaming TTS client
|
+-- cognition/              # LLM #1 -- Cognitive Gateway
|   +-- cognitive_gateway.py
|
+-- llm/                    # LLM HTTP clients
|   +-- llama_client.py     # Async llama-server client
|
+-- context/                # Context Manager
|   +-- context_manager.py  # Prompt assembly
|
+-- memory/                 # Memory System
|   +-- short_term.py       # Rolling conversation window
|   +-- long_term.py        # JSON persistent memory + retrieval
|
+-- personality/            # LLM #2 -- Personality Core
|   +-- personality_core.py # Streaming conversation engine
|
+-- services/               # External service management
|   +-- server_manager.py   # llama-server lifecycle
|
+-- utils/                  # Utilities
|   +-- logging_setup.py    # Structured logging (Rich + file)
|   +-- chunk_assembler.py  # Token -> speech chunk converter
|
+-- cli/                    # CLI interface
|   +-- main.py             # All commands (run, text, test-*, ...)
|
+-- models/                 # Local model files (auto-downloaded by MBG)
|   +-- llm1/               # Qwen3-0.6B-Q8_0.gguf
|   +-- llm2/               # gemma-3-1b-it-Q8_0.gguf
|   +-- stt/                # ggml-base.en.bin
|
+-- bin/                    # Binaries (place pre-built binaries here)
|   +-- llama-server        # llama-server (llama-server.exe on Windows)
|   +-- whisper-cli         # whisper-cli (whisper-cli.exe on Windows)
|
+-- data/
|   +-- memory/
|       +-- ltm.json        # Long-term memory (auto-created)
|
+-- logs/                   # Runtime logs
|   +-- sorachio.log
|   +-- llm1_server.log
|   +-- llm2_server.log
|
+-- .repos/                 # Cloned repositories (auto-managed by MBG)
|   +-- llama.cpp/
|   +-- whisper.cpp/
|
+-- venv_runtime/           # Virtual environment (auto-created by MBG)
|
+-- sensors/                # Future: cameras, IMU, LIDAR
+-- actuators/              # Future: motors, servos, LED rings
```

---

## 5. Threading Model

Sorachio-STS uses a **hybrid threading model**:

```
Main Thread (asyncio event loop)
|
+-- [asyncio Task] STT Worker           -- awaits stt_queue, calls subprocess
+-- [asyncio Task] Cognitive Worker     -- awaits cognitive_queue, HTTP to LLM #1
+-- [asyncio Task] Personality Worker   -- HTTP streaming to LLM #2
+-- [asyncio Task] TTS Worker           -- synthesizes chunks in thread executor
+-- [asyncio Task] Playback Worker      -- drains audio queue, plays via sounddevice
|
+-- [Thread] VAD Worker                 -- continuous mic monitoring (webrtcvad)
|   +-- puts audio to stt_queue via run_coroutine_threadsafe()
|
+-- [Thread Executor] Kokoro Synthesis  -- blocking TTS synthesis offloaded to thread
```

**Why this design?**
- `asyncio` handles all I/O-bound work (HTTP, queues, file I/O) efficiently
- CPU-bound work (synthesis, subprocess) runs in thread executors
- VAD runs in a dedicated thread for lowest possible latency
- No GIL contention issues -- audio capture is pure C (sounddevice/PortAudio)

---

## 6. Installation

Choose the path that matches your operating system.

---

### Path A — Windows (Pre-built Binaries)

> Easiest setup. No compiler required.

#### Step 1 — Install Python 3.10–3.12

Download from [python.org](https://www.python.org/downloads/). During installation, **check "Add Python to PATH"**.

Verify:
```powershell
python --version
```

#### Step 2 — Install espeak-ng

Kokoro TTS requires espeak-ng for English phoneme conversion.

1. Download the latest installer from the [espeak-ng releases page](https://github.com/espeak-ng/espeak-ng/releases) — get the `.msi` file
2. Run the installer
3. Verify it is on your PATH:
```powershell
espeak-ng --version
```

#### Step 3 — Download Pre-built Binaries

Download and place the following files into the `bin/` folder of the project:

| File | Download from |
|------|--------------|
| `llama-server.exe` | [llama.cpp releases](https://github.com/ggerganov/llama.cpp/releases) → latest `llama-*-bin-win-*.zip` → extract `llama-server.exe` |
| `whisper-cli.exe` | [whisper.cpp releases](https://github.com/ggerganov/whisper.cpp/releases) → latest `whisper-*-bin-win-*.zip` → extract `whisper-cli.exe` |

Also copy any `.dll` files from the same zip archives into `bin/` — they are required for the executables to run.

Your `bin/` folder should look like this:
```
bin/
+-- llama-server.exe
+-- whisper-cli.exe
+-- ggml.dll
+-- llama.dll
+-- ... (other .dll files from the zip)
```

#### Step 4 — Clone and Run

```powershell
git clone https://github.com/izzulgod/sorachio-sts.git
cd sorachio-sts

#Voice mode
python main.py run

#Text mode
python main.py text
```

MBG runs automatically on first launch and handles everything else:
- Creates `venv_runtime/` virtual environment
- Installs all Python packages (including `kokoro` and `misaki[en]`)
- Downloads AI models (~1.8GB total)
- Detects your binaries in `bin/`

---

### Path B — Linux / macOS (Build from Source)

> Fully automated. MBG builds everything for you.

#### Step 1 — Install Python 3.10–3.12

**macOS:**
```bash
brew install python@3.12
```

**Linux (Ubuntu/Debian):**
```bash
sudo apt install python3.12 python3.12-venv
```

#### Step 2 — Install Git and CMake

**macOS:**
```bash
brew install git cmake
```

**Linux (Ubuntu/Debian):**
```bash
sudo apt install git cmake build-essential
```

#### Step 3 — Clone and Run

```bash
git clone https://github.com/izzulgod/sorachio-sts.git
cd sorachio-sts

#Voice mode
python main.py run

#Text mode
python main.py text
```

MBG runs automatically on first launch and handles everything else:
- Creates `venv_runtime/` virtual environment
- Installs all Python packages (including `kokoro`)
- Clones and compiles `llama.cpp` and `whisper.cpp` into `bin/`
- Downloads AI models (~1.8GB total)

> First run takes 5–15 minutes due to model downloads and compilation.

---

### What MBG Does Automatically (All Platforms)

Once prerequisites are in place, every subsequent step is handled by MBG:

| Step | Automatic? |
|------|-----------|
| Create virtual environment | ✓ Always |
| Install Python packages | ✓ Always |
| Download AI models | ✓ Always |
| Detect pre-built binaries | ✓ Always |
| Build binaries from source | ✓ Linux/macOS only |
| Install espeak-ng | ✗ Manual (Windows) |

### MBG Commands

```bash
# Check system status (verify everything is detected correctly)
python mbg.py --check

# Force reinstall dependencies and re-download models
python mbg.py --force

# Download models only
python mbg.py --models

# Build binaries from source only
python mbg.py --build
```

## 7. Model Setup

### LLM Models (Auto-downloaded by MBG)

| Model | Size | Role |
|-------|------|------|
| Qwen3-0.6B-Q8_0 | 639MB | Cognitive Gateway |
| gemma-3-1b-it-Q8_0 | 1.07GB | Personality Core |

### STT Model (Auto-downloaded by MBG)

| Model | Size | Accuracy | Speed |
|-------|------|----------|-------|
| ggml-tiny.en.bin | 75MB | Low | Fast |
| **ggml-base.en.bin** | 148MB | Medium | Medium (Default) |
| ggml-small.en.bin | 488MB | High | Slow |
| ggml-medium.en.bin | 1.5GB | Highest | Very Slow |

---

## 8. Running the System

### Quick Start - Text Mode (no microphone required)

```bash
# Run in text mode (MBG auto-runs on first launch)
python main.py text
```

### Full Voice Mode

```bash
# Starts servers AND voice pipeline
python main.py run
```

### Single Message Test

```bash
python main.py text -m "Hello Sorachio, how are you?"
```

---

## 9. Configuration Guide

All configuration lives in `config/sorachio.yaml`.

### Key Settings to Customize

```yaml
# Change companion name/personality
context:
  companion_name: "Sorachio"
  personality_prompt: |
    You are Sorachio, a warm AI companion...

# Adjust LLM creativity
llm:
  personality_core:
    temperature: 0.8      # 0.1=focused, 1.2=creative
    max_tokens: 512

# TTS voice (see kokoro docs for available voices)
tts:
  voice: "af_heart"       # or: af_bella, am_adam, bf_emma, etc.
  speed: 1.0              # 0.5=slow, 2.0=fast

# Memory thresholds
memory:
  long_term:
    importance_threshold: 0.5   # Only store memories above this score

# GPU acceleration (if you have a GPU)
llm:
  cognitive_gateway:
    n_gpu_layers: 35      # Set -1 for all layers on GPU
  personality_core:
    n_gpu_layers: 35
```

### Environment Variables

You can override config values with environment variables:

```bash
export SORACHIO_LOG_LEVEL=DEBUG
```

---

## 10. Cognitive Gateway Explained

**LLM #1** (Qwen3-0.6B) acts as a fast routing and filtering brain. It **never generates conversation** -- only makes structured decisions.

### Why a separate Cognitive LLM?

Without a cognitive layer, the personality LLM would:
- Respond to background TV/music as if spoken to
- Have no way to determine emotional tone
- Generate responses even when not addressed
- Have no automatic memory prioritization

The Cognitive Gateway handles all of this in <500ms.

### Input / Output

**Input** (from STT):
```
"Hey Sorachio, I've been really stressed about my exams this week."
```

**Output** (JSON):
```json
{
    "respond": true,
    "addressed_to_ai": true,
    "store_memory": true,
    "importance": 0.91,
    "emotion": "anxious",
    "topic": "education",
    "memory_queries": ["exam", "stress", "study"],
    "confidence": 0.88
}
```

### Visual Status Indicator

In both text and run modes, the Cognitive Gateway's decision is visually rendered in real-time as a rich UI pill bar before the response generation begins:

```text
  >>> STATUS   happy    respond    memory    topic: general    conf 75%
```

This UI provides immediate feedback on the AI's internal state (emotion, decision to respond, memory storage, topic, and confidence level) while the system transitions smoothly using transient loading spinners.

### Thinking Mode Disabled

Qwen3 has a built-in reasoning/thinking mode that generates `<think>...</think>` tokens. This is disabled via:

```python
SYSTEM_PROMPT = """/no_think
You are a cognitive filter...
```

This reduces latency from ~3s to ~0.3s for the cognitive decision.

---

## 11. Streaming Pipeline Explained

Sorachio begins **speaking before it finishes thinking**. Here's how:

```
LLM #2 generates:  "Hello " -> "there! " -> "I " -> "can " -> "hear " -> "you." -> ...
                                                                            |
Chunk Assembler:            ["Hello there!"]          ["I can hear you."]
                                   |                           |
TTS Synthesis:            audio1 ready        audio2 synthesizing...
                                |
Audio Queue:              [audio1] -> playback -> speaker
                                          | (while playing)
                                    [audio2] -> queued -> next
```

**First audio output** is typically heard within **0.5-1.5 seconds** of the LLM starting -- regardless of how long the full response takes.

### Chunk Assembly Strategy

Chunks are assembled by:
1. **Sentence endings**: `.`, `!`, `?`, `;` followed by whitespace
2. **Max word limit**: flush if chunk exceeds 30 words (prevents long pauses)
3. **Minimum word threshold**: don't send single-word fragments

**Good chunks:**
- `"Hello there!"`
- `"How are you doing today?"`
- `"That sounds really stressful."`

**Bad (avoided):**
- `"Hel"` `"lo"` (raw tokens -- too fragmented)
- 200-word wall of text (too long -- TTS takes forever)

---

## 12. Memory Architecture

### Short-Term Memory (STM)

- **Type**: In-memory rolling deque
- **Capacity**: Last 20 messages (configurable)
- **Content**: role, content, emotion, topic, importance, timestamp
- **Used for**: Recent conversation context injected into LLM #2 prompt
- **Lifecycle**: Cleared on session end (not persistent)

### Long-Term Memory (LTM)

- **Type**: JSON file (`data/memory/ltm.json`)
- **Capacity**: Up to 500 entries
- **Content**: content, topic, emotion, importance, keywords, created_at, access_count
- **Retrieval**: Keyword matching + importance scoring + recency weighting
- **Persistence**: Survives across sessions

#### LTM Retrieval Scoring

```python
relevance = (
    keyword_match_score * 0.5 +
    importance * 0.3 +
    recency_score * 0.2
)
```

#### Future: Vector Database Migration

The LTM is designed for easy migration to ChromaDB, FAISS, or Qdrant. Each `LTMEntry` maps 1:1 to a vector store document. Replace `LongTermMemory._load/_save` with DB calls, and `retrieve()` with semantic vector search.

---

## 13. CLI Reference

```bash
# Full voice mode
python main.py run [--config path] [--no-greeting] [--no-servers]

# Interactive text mode
python main.py text [--config path] [--no-servers]

# Single message test
python main.py text --message "Hello Sorachio"

# Test individual components
python main.py test-stt [--file audio.wav]
python main.py test-tts "Hello, I am Sorachio!"
python main.py test-cognitive "Hey Sorachio, I feel tired"

# Server management
python main.py servers status
python main.py servers start
python main.py servers stop

# Memory management
python main.py memory list
python main.py memory clear [--yes]
```

---

## 14. MBG System

### What is MBG?

**MBG: Master Bootstrap Guardian** is the automated build and compatibility system for Sorachio-STS. It handles all setup tasks automatically, ensuring the system is ready to run on any supported platform.

### Features

- **Python Version Management** - Auto-detects and relaunches with compatible Python (3.10–3.12)
- **Virtual Environment** - Creates and manages `venv_runtime/` isolated from your system Python
- **Dependency Installation** - Installs all required packages including `kokoro` and `misaki[en]` for TTS
- **Binary Detection** - Validates existing binaries (handles `.exe` automatically on Windows); builds from source if not found
- **Model Downloads** - Downloads all required AI models
- **Platform Detection** - Handles macOS, Linux, and Windows transparently

### Usage

```bash
# Check system status
python mbg.py --check

# Force rebuild everything
python mbg.py --force

# Download models only
python mbg.py --models

# Build binaries only
python mbg.py --build

# Show version
python mbg.py --version
```

### What Gets Built (or detected if pre-built)

| Component | Source | Output (Linux/macOS) | Output (Windows) |
|-----------|--------|----------------------|------------------|
| llama-server | llama.cpp | `bin/llama-server` | `bin/llama-server.exe` |
| whisper-cli | whisper.cpp | `bin/whisper-cli` | `bin/whisper-cli.exe` |

### What Gets Downloaded

| Model | Size | Purpose |
|-------|------|---------|
| ggml-base.en.bin | 148MB | Speech-to-Text |
| Qwen3-0.6B-Q8_0.gguf | 639MB | Cognitive Gateway |
| gemma-3-1b-it-Q8_0.gguf | 1.07GB | Personality Core |

---

## 15. Troubleshooting

### "Python version outside compatible range"

MBG will automatically try to find and relaunch with a compatible Python version (3.10-3.12). If it can't find one, install Python 3.12:
- **macOS**: `brew install python@3.12`
- **Linux**: `sudo apt install python3.12`
- **Windows**: Download from [python.org](https://python.org)

### "Binary not found" / Binaries show ✗ in status

On Windows, binaries must have the `.exe` extension. MBG detects this automatically. If you placed binaries in `bin/` manually, ensure they are named `llama-server.exe` and `whisper-cli.exe`. MBG will detect them on the next run:

```bash
python mbg.py --check
```

If you want to use pre-built releases instead of building from source:
1. Download `llama-server.exe` from [llama.cpp releases](https://github.com/ggerganov/llama.cpp/releases)
2. Download `whisper-cli.exe` from [whisper.cpp releases](https://github.com/ggerganov/whisper.cpp/releases)
3. Place both in the `bin/` folder
4. Run `python mbg.py --check` to verify

### "No module named 'kokoro'" / TTS not working

This means kokoro was not installed into the project's virtual environment. Run MBG to reinstall all dependencies into `venv_runtime/`:

```bash
python mbg.py
```

On Windows, Kokoro also requires **espeak-ng** for the English phonemizer. Download and install it from the [espeak-ng releases page](https://github.com/espeak-ng/espeak-ng/releases), then ensure it is on your system PATH before running.

### "LLM server not responding"

1. Check if servers are running:
   ```bash
   python main.py servers status
   ```
2. Check server logs:
   ```
   logs/llm1_server.log
   logs/llm2_server.log
   ```
3. Try starting manually:
   ```bash
   python main.py servers start
   ```

### "Cognitive Gateway returning garbage JSON"

- Check that the Qwen3 model path is correct in `config/sorachio.yaml`
- Verify LLM #1 is running: `curl http://127.0.0.1:8001/health`
- The `/no_think` prefix in the system prompt disables Qwen3 reasoning mode
- Try increasing `max_tokens` in config if response is getting cut off

### "Audio device issues"

Set explicit device in `config/sorachio.yaml`:
```yaml
audio:
  capture:
    device_index: 0    # Use python -m sounddevice to list devices
  playback:
    device_index: 1
```

List devices:
```bash
python -c "import sounddevice; print(sounddevice.query_devices())"
```

### "High latency"

For faster response:
1. Enable GPU offload: set `n_gpu_layers: -1` in config (requires CUDA/Metal)
2. Use smaller models (tiny, mini variants)
3. Reduce `n_ctx` to 1024 if conversations are short
4. Increase `n_threads` to match your CPU core count

---

## 16. Future Robotics Expansion

Sorachio-STS is architected as the **brain** of a future companion robot.

### ROS2 Integration

The `sensors/` and `actuators/` packages are scaffolded for ROS2 nodes:

```python
# sensors/camera.py (future)
class CameraNode(Node):
    def __init__(self, event_bus: EventBus):
        # Publish EventType.VISUAL_INPUT on detection
        ...

# actuators/servo.py (future)
class ServoController:
    def on_emotion(self, emotion: str):
        # Move face servos based on detected emotion
        ...
```

### Planned Expansion Modules

| Module | Description | Status |
|--------|-------------|--------|
| `sensors/camera.py` | OpenCV face detection, emotion recognition | Planned |
| `sensors/imu.py` | Accelerometer/gyroscope for physical awareness | Planned |
| `actuators/servo.py` | Facial expression servos | Planned |
| `actuators/led.py` | LED ring for emotional state display | Planned |
| `memory/vector_ltm.py` | ChromaDB/FAISS semantic memory | Planned |
| `cognition/vision_gate.py` | Visual cognitive gateway | Planned |
| `core/ros2_bridge.py` | ROS2 topic publisher/subscriber | Planned |
| `agents/task_agent.py` | Goal-oriented sub-agent (LangGraph) | Planned |

### Multi-Agent Architecture (Vision)

```
Sorachio Core Brain
+-- Cognitive Gateway (LLM #1) -- fast routing
+-- Personality Core (LLM #2) -- conversation
+-- Vision Agent -- camera + face recognition
+-- Task Agent -- goal planning + execution
+-- Emotion Agent -- multi-modal emotion fusion
+-- Memory Agent -- LTM consolidation + reflection
```

---

## License

MIT License -- see [LICENSE](LICENSE)

## Contributing

This project is a foundation. All contributions welcome:
- Bug fixes and improvements
- New sensor/actuator integrations
- Alternative STT/TTS backends
- Vector database LTM implementation
- ROS2 bridge
- Multi-modal capabilities
