# 16. MBG System

### What is MBG?

**MBG: Master Bootstrap Guardian** — automated build and compatibility system for Sorachio-STS.

### Features

- Python 3.10–3.12 version management and auto-relaunch
- Creates and manages `venv_runtime/` virtual environment
- Installs all Python packages including `chromadb` and `sentence-transformers` + system dependencies (Vulkan, PortAudio)
- **Anteque Ashing Quality Code Verifier**: Mandatory `ruff` and `pyrefly` code quality checks executed automatically during bootstrap
- Builds `llama-server` from source (Linux/macOS) with **Vulkan GPU backend** (auto-detected)
- Copies all Vulkan/GGML shared libraries (`libggml-vulkan.so`, `libllama.so`, etc.) alongside binary
- Applies `cap_ipc_lock` for zero-swap RAM locking on Linux
- Downloads `faster-whisper-small` STT model to `models/stt/` (self-contained, no `~/.cache`)
- Downloads `Kokoro-82M` English TTS weights + voice files to `models/tts/kokoro/`
- Downloads Piper ONNX Indonesian TTS voice (`id_ID-news_tts-medium`) to `models/tts/`
- Downloads `all-MiniLM-L6-v2` vector embedding model to `models/vector/` for offline LTM semantic search
- Auto-detects GGUF models and vision projectors in `models/llm1/` and `models/llm2/`
- Smart re-run detection: checks for actual model weight files (`.bin`/`.safetensors`) — skips download if already present

### Commands

```bash
python mbg.py           # Full bootstrap
python mbg.py --check   # Check system status
python mbg.py --force   # Force rebuild everything
python mbg.py --models  # Download STT/TTS models only
python mbg.py --build   # Build binaries only
python mbg.py --version # Show version
```
