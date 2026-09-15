# 6. Installation

### Path A — Windows (Pre-built Binaries)

#### Step 1 — Install Python 3.10–3.12

Download from [python.org](https://www.python.org/downloads/). Check **"Add Python to PATH"**.

#### Step 2 — Download Pre-built Binaries

Download `llama-server.exe` from [llama.cpp releases](https://github.com/ggerganov/llama.cpp/releases) → latest `llama-*-bin-win-*.zip`. Place `llama-server.exe` and all `.dll` files into the `bin/` folder.

> **Note**: `whisper-cli` is no longer required. faster-whisper runs entirely in-process.

#### Step 3 — Download LLM Models

```
models/
+-- llm1/
|   +-- YourModel.gguf              # Any instruction-following GGUF model
+-- llm2/
|   +-- YourModel.gguf              # Any chat GGUF model
|   +-- mmproj-YourModel.gguf       # Optional: vision projector
```

STT (faster-whisper) and TTS (Piper) models are **auto-downloaded by MBG**.

#### Step 4 — Clone and Run

```powershell
git clone https://github.com/izzulgod/sorachio-sts.git
cd sorachio-sts
python main.py run
```

MBG runs automatically and:
- Creates `venv_runtime/` and installs all Python packages
- Downloads faster-whisper `small` model (~484MB)
- Downloads Piper TTS voices to `models/tts/`

---

### Path B — Linux / macOS (Build from Source)

#### Step 1 — Install Prerequisites

**macOS:**
```bash
brew install python@3.12 git cmake
```

**Linux (Ubuntu/Debian):**
```bash
sudo apt install python3.12 python3.12-venv git cmake build-essential
```

**Linux (Fedora/RHEL):**
```bash
sudo dnf install python3.12 git cmake gcc gcc-c++
```

#### Step 2 — Clone and Run

```bash
git clone https://github.com/izzulgod/sorachio-sts.git
cd sorachio-sts
python main.py run
```

MBG handles everything else automatically:
- Creates virtual environment and installs packages
- Installs system dependencies (Vulkan SDK, PortAudio)
- Clones and compiles `llama.cpp` into `bin/` with Vulkan GPU support
- Downloads faster-whisper `small` and Piper ONNX voices

> First run takes 5–15 minutes due to compilation.

---

### What MBG Downloads Automatically

| Asset | Size | Purpose | Local Path |
|-------|------|---------|------------|
| faster-whisper-small | ~484 MB | STT model | `models/stt/` |
| Kokoro-82M + voices | ~327 MB | English TTS (`af_heart`) | `models/tts/kokoro/` |
| id_ID-news_tts-medium | ~67 MB | Indonesian TTS voice (Piper ONNX) | `models/tts/` |
| all-MiniLM-L6-v2 | ~90 MB | Vector embedding model (offline LTM search) | `models/vector/all-MiniLM-L6-v2/` |

> All downloads are stored **inside the project directory** under `models/`. Nothing is written to your home directory cache.

LLM models are **user-managed** — download from Hugging Face and place in `models/llm1/` or `models/llm2/`.
