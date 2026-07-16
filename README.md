# Sorachio-STS

**Speech To Speech AI Companion System**
*Foundation for a future robotics companion platform*

---

### System in Action (CLI Showcase)

Here is a preview of how the interactive CLI behaves in different operational modes, showcasing the real-time **Cognitive Gateway** status bar and state transitions.

#### 1. Full Voice/Run Mode (`python main.py run`)
In voice mode, the pipeline continuously monitors microphone input using VAD. Once speech is detected and transcribed, the Cognitive Gateway immediately computes the emotional state and topic, seamlessly transitioning into the streaming audio playback phase. Filler or hesitant speech (e.g., "Um...") is filtered out and marked as `X ignore`, preventing unnecessary processing on non-substantive input.

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
11. [Acoustic Intelligence Layer](#11-acoustic-intelligence-layer)
12. [Streaming Pipeline Explained](#12-streaming-pipeline-explained)
13. [Memory Architecture](#13-memory-architecture)
14. [CLI Reference](#14-cli-reference)
15. [MBG System](#15-mbg-system)
16. [Troubleshooting](#16-troubleshooting)
17. [Future Robotics Expansion](#17-future-robotics-expansion)

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
| **Model-Agnostic** | Auto-detects any GGUF model — just drop and restart |
| **Vision Ready** | LLM #2 supports multimodal input via mmproj projector |
| **Interruptible** | VAD detects user speech, stops playback instantly |
| **Persistent Memory** | Remembers you across sessions (JSON → future vector DB) |
| **Modular** | Each component is a separate async worker |
| **Rich CLI UI** | Transient spinners, animated loaders, and cognitive status pills |
| **Cross-Platform** | Works on macOS, Linux, and Windows |

### Current Model Configuration

| Slot | Model | Size | Role | Vision |
|------|-------|------|------|--------|
| LLM #1 | Qwen3.5-0.8B (Q8_0) | 774 MB | Cognitive Gateway (JSON router) | No |
| LLM #2 | Qwen3.5-2B (Q8_0) | 1.87 GB | Personality Core (conversation) | **Yes** (mmproj) |
| STT | whisper-base.en | 148 MB | Speech-to-Text | — |
| TTS | Kokoro | — | Text-to-Speech (in-process) | — |

> **Flexible Model Swapping**: Models are auto-detected from `models/llm1/` and `models/llm2/` directories. Just drop a new `.gguf` file and restart — no config editing required. If an `mmproj*.gguf` file is present alongside the model, vision is automatically enabled.

---

## 2. Architecture Diagram

```
┌─────────────────────────────────────────────────────────────────┐
│                    Sorachio-STS Pipeline                        │
│                                                                 │
│  ┌──────────┐    ┌─────────────────────────────────────────┐    │
│  │Microphone│───►│ Acoustic Gate (RMS/dBFS)                │    │
│  └──────────┘    └─────────────┬───────────────────────────┘    │
│                                ▼                                │
│                  ┌─────────────────────────────────────────┐    │
│                  │ Acoustic Echo Cancellation (AEC)        │    │
│                  │ Reference Signal ◄─────── Playback      │    │
│                  └─────────────┬───────────────────────────┘    │
│                                ▼                                │
│                  ┌──────────────┐    ┌─────────────────────┐    │
│                  │ AudioCapture │───►│   STT Queue         │    │
│                  │    (VAD)     │    │   (asyncio.Queue)   │    │
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
│                                      │   (auto-detected)    │   │
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
│                           │   LLM #2 (auto-detected)        │   │
│                           │   Streaming token generation    │   │
│                           │   🔮 Vision input (if mmproj)   │   │
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
+-- HTTP -> llama-server :8001 -- LLM #1 Cognitive Gateway (auto-detected GGUF)
+-- HTTP -> llama-server :8002 -- LLM #2 Personality Core (auto-detected GGUF + mmproj)
+-- Subprocess -> whisper-cli    -- STT (whisper-base.en)
+-- In-process -> Kokoro         -- TTS (kokoro Python library)
```

### Model Auto-Detection Flow

```
┌──────────────────────────────────────────────────────┐
│  User drops new model into models/llm1/ or llm2/     │
│                                                      │
│  ┌──────────────┐    ┌───────────────────────────┐   │
│  │ models/llm1/ │    │ models/llm2/              │   │
│  │  *.gguf      │    │  *.gguf (main model)      │   │
│  │              │    │  mmproj*.gguf (vision)     │   │
│  └──────┬───────┘    └──────────┬────────────────┘   │
│         │                       │                    │
│         ▼                       ▼                    │
│  ┌──────────────────────────────────────────────┐    │
│  │         Model Scanner (auto-detect)          │    │
│  │  - Finds largest .gguf → model_path          │    │
│  │  - Finds mmproj*.gguf → mmproj_path          │    │
│  │  - Sets has_vision = True/False              │    │
│  └──────────────────┬───────────────────────────┘    │
│                     │                                │
│                     ▼                                │
│  ┌──────────────────────────────────────────────┐    │
│  │         Server Manager (launch)              │    │
│  │  llama-server --model X                      │    │
│  │    --mmproj Y (if vision)                    │    │
│  │    --ctx-size 0 (auto from GGUF metadata)    │    │
│  │    --cache-ram 0 (disables prompt cache)     │    │
│  │    --reasoning off/auto                      │    │
│  └──────────────────────────────────────────────┘    │
│                                                      │
│  ✅ No YAML editing needed!                          │
│  ✅ Native C++ template handler (vision-ready)       │
│  ✅ Context size auto-detected                       │
│  ✅ Vision auto-enabled if mmproj present             │
└──────────────────────────────────────────────────────┘
```

---

## 3. Data Flow

### Full Pipeline Flow

```
[User speaks]
    |
    v PCM bytes (16kHz, 16-bit mono)
[Acoustic Gate] -- passes if volume > -40 dBFS
    |
    v PCM bytes
[AEC Provider] -- suppresses echo if Playback is active
    |
    v clean PCM bytes
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
    |  to llama-server:8001 (LLM #1, auto-detected)
    |  --reasoning off (no thinking tokens)
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
    |  to llama-server:8002 (LLM #2, auto-detected)
    |  --cache-ram 0 (disables prompt cache)
    |  --mmproj (vision projector, if present)
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
|   +-- settings.py         # Pydantic settings loader + model auto-scanner
|
+-- core/                   # Pipeline orchestrator
|   +-- pipeline.py         # Master async pipeline
|   +-- events.py           # Event bus (pub/sub)
|
+-- audio/                  # Audio I/O
|   +-- capture.py          # Mic capture + VAD
|   +-- playback.py         # Interruptible playback queue
|   +-- acoustic_gate.py    # Pre-VAD energy filter and silence sentinel injection
|   +-- echo_cancellation.py # Acoustic Echo Cancellation (AEC)
|
+-- vision/                 # Multimodal Vision I/O
|   +-- capture.py          # Webcam single-snapshot capture (OpenCV)
|
+-- stt/                    # Speech-to-Text
|   +-- whisper_client.py   # whisper.cpp subprocess client
|
+-- tts/                    # Text-to-Speech
|   +-- kokoro_client.py    # Kokoro streaming TTS client
|
+-- cognition/              # LLM #1 -- Cognitive Gateway
|   +-- cognitive_gateway.py  # Model-agnostic JSON decision router
|
+-- llm/                    # LLM HTTP clients + model detection
|   +-- llama_client.py     # Async llama-server client (multimodal ready)
|   +-- model_scanner.py    # Auto-detect GGUF models + mmproj from directories
|
+-- context/                # Context Manager
|   +-- context_manager.py  # Prompt assembly
|
+-- memory/                 # Memory System
|   +-- short_term.py       # Rolling conversation window
|   +-- long_term.py        # JSON persistent memory + retrieval
|
+-- personality/            # LLM #2 -- Personality Core
|   +-- personality_core.py # Streaming conversation engine (model-agnostic)
|
+-- services/               # External service management
|   +-- server_manager.py   # llama-server lifecycle (mmproj, jinja, reasoning)
|
+-- utils/                  # Utilities
|   +-- logging_setup.py    # Structured logging (Rich + file)
|   +-- chunk_assembler.py  # Token -> speech chunk converter
|
+-- cli/                    # CLI interface
|   +-- main.py             # All commands (run, text, test-*, ...)
|
+-- models/                 # Local model files (auto-detected!)
|   +-- llm1/               # Drop any GGUF model here for Cognitive Gateway
|   +-- llm2/               # Drop any GGUF model + optional mmproj here
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
|   +-- cognitivegateway_server.log
|   +-- personalitycore_server.log
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
+-- mtmd.dll            # Required for vision/multimodal support
+-- ... (other .dll files from the zip)
```

#### Step 4 — Download Models

Download GGUF models and place them in the appropriate directories:

```
models/
+-- llm1/                           # Cognitive Gateway
|   +-- YourModel.gguf              # Any instruction-following model
+-- llm2/                           # Personality Core
|   +-- YourModel.gguf              # Any chat/instruction model
|   +-- mmproj-YourModel.gguf       # Optional: vision projector
+-- stt/
    +-- ggml-base.en.bin            # Auto-downloaded by MBG
```

> **Tip**: Models are auto-detected! The system picks the largest `.gguf` file in each folder as the main model, and any `mmproj*.gguf` as the vision projector.

#### Step 5 — Clone and Run

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
- Downloads STT model (~148MB)
- Detects your binaries in `bin/`
- Auto-scans model directories

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
- Downloads STT model (~148MB)

> First run takes 5–15 minutes due to compilation. Model downloads are user-managed.

---

### What MBG Does Automatically (All Platforms)

Once prerequisites are in place, every subsequent step is handled by MBG:

| Step | Automatic? |
|------|-----------|
| Create virtual environment | ✓ Always |
| Install Python packages | ✓ Always |
| Download STT model | ✓ Always |
| Detect pre-built binaries | ✓ Always |
| Build binaries from source | ✓ Linux/macOS only |
| Auto-detect LLM models | ✓ Always |
| Auto-detect vision projectors | ✓ Always |
| Install espeak-ng | ✗ Manual (Windows) |
| Download LLM models | ✗ User-managed |

### MBG Commands

```bash
# Check system status (verify everything is detected correctly)
python mbg.py --check

# Force reinstall dependencies and re-download models
python mbg.py --force

# Download STT model only
python mbg.py --models

# Build binaries from source only
python mbg.py --build
```

## 7. Model Setup

### Swapping Models (Drop & Go)

Sorachio-STS uses **model auto-detection** — no config editing required when swapping models:

1. **Download** a GGUF model from [Hugging Face](https://huggingface.co/models?library=gguf)
2. **Drop** it into `models/llm1/` (Cognitive Gateway) or `models/llm2/` (Personality Core)
3. **Restart** — the system auto-detects the new model

```bash
# Example: swap Personality Core to a different model
# 1. Remove old model
rm models/llm2/old-model.gguf

# 2. Drop new model
cp ~/Downloads/Qwen3.5-2B-Q8_0.gguf models/llm2/

# 3. Optionally add vision projector
cp ~/Downloads/mmproj-Qwen3.5-2B-BF16.gguf models/llm2/

# 4. Restart — auto-detected!
python main.py run
```

### What Gets Auto-Detected

| Feature | How it works |
|---------|-------------|
| **Model file** | Largest `.gguf` in the directory (excluding mmproj) |
| **Vision projector** | Any `mmproj*.gguf` file in the same directory |
| **Context size** | Read from GGUF metadata by llama-server (`--ctx-size 0`) |
| **Chat template** | Read from GGUF metadata by llama-server (`--jinja`) |
| **Thinking mode** | Auto-detected from template or controlled via `reasoning` config |


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

### Model Auto-Detection Config

```yaml
llm:
  cognitive_gateway:
    model_dir: "models/llm1"     # Scanner picks largest .gguf here
    # model_path: ""             # Leave empty for auto-detect, or set explicit path
    n_ctx: 0                     # 0 = auto from model metadata
    n_threads: 8                 # 8 threads is optimal for small 0.8B models
    reasoning: "off"             # Disable thinking for fast JSON routing

  personality_core:
    model_dir: "models/llm2"     # Scanner picks largest .gguf + mmproj
    # model_path: ""             # Leave empty for auto-detect
    # mmproj_path: ""            # Auto-detected if mmproj*.gguf present
    n_ctx: 0                     # 0 = auto from model metadata
    n_threads: 12                # 12 threads is optimal for 8-core CPUs
    reasoning: "off"             # Disable thinking for direct conversation
```

> **Override auto-detection**: If you set `model_path` explicitly in the YAML, auto-scan is skipped for that instance. This lets you pin a specific model version.

### Environment Variables

You can override config values with environment variables:

```bash
export SORACHIO_LOG_LEVEL=DEBUG
```

---

## 10. Cognitive Gateway Explained

**LLM #1** acts as a fast routing and filtering brain. It **never generates conversation** -- only makes structured decisions. The Cognitive Gateway is **model-agnostic** — any instruction-following GGUF model can be used.

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
    "topic": "exams",
    "emotion": "anxious",
    "store_memory": true,
    "importance": 0.8,
    "memory_queries": ["exams", "stress"]
}
```

### Visual Status Indicator

In both text and run modes, the Cognitive Gateway's decision is visually rendered in real-time as a rich UI pill bar before the response generation begins:

```text
  >>> STATUS   happy    respond    memory    topic: general
```

This UI provides immediate feedback on the AI's internal state (emotion, decision to respond, memory storage, and topic) while the system transitions smoothly using transient loading spinners.

### Thinking Mode & JSON Mode Control

The Cognitive Gateway uses `--reasoning off` at the server level to disable any thinking/reasoning tokens. Additionally, it forces native JSON output using the OpenAI-compatible `response_format={"type": "json_object"}` option. This forces `llama-server` to use grammar-based decoding constraints, preventing the router model from generating conversational filler or trailing explanation text. This approach reduces latency from ~3s to ~0.1-0.2s for the cognitive decision.

---

## 11. Acoustic Intelligence Layer

Before audio reaches the STT or Cognitive layers, it passes through the **Acoustic Intelligence Layer**. This acts as the first line of defense against wasting compute cycles on background noise.

### Components

1. **Acoustic Gate (RMS/dBFS)**: Computes the actual volume of every audio frame. If the volume is below the threshold (e.g. `-40.0 dBFS`), the frame is instantly dropped.
2. **VAD Synchronization (Sentinels)**: When the Acoustic Gate drops a frame, it injects an empty "sentinel" byte frame into the pipeline. This allows the VAD to realize that time is passing (silence) without processing actual audio bytes, preventing pipeline deadlocks while saving CPU.
3. **Acoustic Echo Cancellation (AEC)**: If playback is active, the speaker output is subtracted from the microphone input to prevent the system from hearing itself.

By stopping noise at the gate, we prevent meaningless STT transcriptions (like `[wind blowing]`, `(clears throat)`) and save the Cognitive Gateway from having to process them.

---

## 12. Streaming Pipeline Explained

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

## 13. Memory Architecture

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

## 14. CLI Reference

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

## 15. MBG System

### What is MBG?

**MBG: Master Bootstrap Guardian** is the automated build and compatibility system for Sorachio-STS. It handles all setup tasks automatically, ensuring the system is ready to run on any supported platform.

### Features

- **Python Version Management** - Auto-detects and relaunches with compatible Python (3.10–3.12)
- **Virtual Environment** - Creates and manages `venv_runtime/` isolated from your system Python
- **Dependency Installation** - Installs all required packages including `kokoro` and `misaki[en]` for TTS
- **Binary Detection** - Validates existing binaries (handles `.exe` automatically on Windows); builds from source if not found
- **STT Model Downloads** - Downloads the Whisper STT model
- **Model Verification** - Checks that LLM model directories contain `.gguf` files and reports vision projector status
- **Platform Detection** - Handles macOS, Linux, and Windows transparently

### Usage

```bash
# Check system status
python mbg.py --check

# Force rebuild everything
python mbg.py --force

# Download STT model only
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

> **Note**: LLM models are user-managed. Download GGUF models from Hugging Face and place them in `models/llm1/` and `models/llm2/`.

---

## 16. Troubleshooting

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

### "No .gguf model found in models/llm1/"

This means you haven't placed a model in the directory. Download a GGUF model and drop it in:

```bash
# Example: download and place a model
# Visit https://huggingface.co/models?library=gguf
# Download your preferred model
# Place the .gguf file in models/llm1/ or models/llm2/
```

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
   logs/cognitivegateway_server.log
   logs/personalitycore_server.log
   ```
3. Try starting manually:
   ```bash
   python main.py servers start
   ```

### "Cognitive Gateway returning garbage JSON"

- Verify LLM #1 is running: `curl http://127.0.0.1:8001/health`
- The `reasoning: "off"` setting in config disables thinking mode at the server level
- Try increasing `max_tokens` in config if response is getting cut off
- Ensure the model supports instruction following (chat/instruct models work best)

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
3. Reduce `n_ctx` to a specific value if conversations are short (otherwise leave at 0 for auto)
4. Increase `n_threads` to match your CPU core count

---

## 17. Future Robotics Expansion

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
| `cognition/vision_gate.py` | Visual cognitive gateway (foundation ready via mmproj) | Planned |
| `core/ros2_bridge.py` | ROS2 topic publisher/subscriber | Planned |
| `agents/task_agent.py` | Goal-oriented sub-agent (LangGraph) | Planned |

### Multi-Agent Architecture (Vision)

```
Sorachio Core Brain
+-- Cognitive Gateway (LLM #1) -- fast routing (model-agnostic)
+-- Personality Core (LLM #2) -- conversation + vision (mmproj ready)
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
- Vision pipeline integration
