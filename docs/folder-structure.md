# 4. Folder Structure

```
Sorachio-STS/
|
+-- main.py                 # Entry point (MBG runs automatically)
+-- mbg.py                  # Master Bootstrap Guardian + Anteque Ashing quality checks
+-- pyproject.toml          # Ruff + pyrefly configuration
+-- README.md
|
+-- config/
|   +-- sorachio.yaml       # Master config (edit this!)
|   +-- settings.py         # Pydantic settings loader + model auto-scanner
|
+-- core/
|   +-- pipeline.py         # Master async pipeline (AEC calibration, STT, interrupt)
|   +-- events.py           # Event bus (pub/sub)
|
+-- audio/
|   +-- capture.py          # Mic capture + VAD + peak follower + pre-roll ring buffer
|   +-- playback.py         # Interruptible playback queue
|   +-- acoustic_gate.py    # Pre-VAD energy filter + silence sentinel injection
|   +-- echo_cancellation.py # CalibrationAEC (3s chirp sweep) + SpectralSubAEC + NullAEC
|
+-- vision/
|   +-- capture.py          # Webcam snapshot capture (OpenCV)
|
+-- stt/
|   +-- whisper_client.py   # faster-whisper in-process client
|                           #  - Streaming STT (transcribe_streaming) for minimal latency
|                           #  - Configurable models_dir and model_size
|                           #  - Audio language classifier + text-level verifier
|                           #  - Hallucination filter (noise, repetition, domains)
|                           #  - Pre-trigger ring buffer (onset capture)
|                           #  - local_files_only=True (offline load, no deadlock)
|
+-- tts/
|   +-- kokoro_client.py    # Hybrid Kokoro & Piper TTS client
|                           #  - Kokoro TTS for English (af_heart, 24kHz)
|                           #  - Piper TTS for Indonesian (id_ID-news_tts-medium)
|                           #  - Automatic text langdetect + STT language lock
|                           #  - Dynamic resampling to 24kHz
|   +-- piper_client.py     # Piper ONNX fallback client (Indonesian TTS engine)
|
+-- cognition/
|   +-- cognitive_gateway.py  # Model-agnostic JSON decision router
|
+-- llm/
|   +-- llama_client.py     # Async llama-server client (multimodal ready)
|   +-- model_scanner.py    # Auto-detect GGUF models + mmproj
|
+-- context/
|   +-- context_manager.py  # Prompt assembly + per-turn language directive injection
|
+-- memory/
|   +-- short_term.py       # Rolling conversation window
|   +-- long_term.py        # Hybrid LTM: JSON persistent storage + VectorStore integration
|   +-- vector_store.py     # ChromaDB vector store + sentence-transformers (all-MiniLM-L6-v2)
|   +-- emotion_tracker.py  # Rolling emotion history & mood trend detection
|
+-- personality/
|   +-- personality_core.py # Streaming conversation engine (model-agnostic)
|
+-- services/
|   +-- server_manager.py   # llama-server lifecycle (mmproj, jinja, reasoning)
|
+-- utils/
|   +-- logging_setup.py    # Structured logging (Rich + file)
|   +-- chunk_assembler.py  # Token -> speech chunk converter
|   +-- rate_limiter.py     # Sliding window rate limiter for pipeline input protection
|
+-- cli/
|   +-- main.py             # All commands (run, text, test-*, ...)
|
+-- models/
|   +-- llm1/               # Drop any GGUF here for Cognitive Gateway
|   +-- llm2/               # Drop any GGUF + optional mmproj here
|   +-- stt/                # faster-whisper weights (auto-downloaded by MBG)
|   +-- tts/
|       +-- id_ID-news_tts-medium.onnx  # Piper Indonesian voice (auto-downloaded)
|       +-- id_ID-news_tts-medium.onnx.json
|       +-- kokoro/         # Kokoro-82M English TTS weights (auto-downloaded)
|
+-- bin/
|   +-- llama-server        # llama-server binary (built by MBG or placed manually)
|   +-- libggml-vulkan.so   # Vulkan GPU backend shared library (Linux)
|   +-- libggml.so          # GGML core (Linux)
|   +-- libllama.so         # llama.cpp runtime (Linux)
|   +-- *.dll               # Windows: all DLLs copied alongside llama-server.exe
|
+-- data/
|   +-- memory/
|       +-- ltm.json        # Long-term memory (auto-created)
|       +-- chroma/         # ChromaDB vector embeddings database (auto-created)
|
+-- logs/
|   +-- sorachio.log
|   +-- cognitivegateway_server.log
|   +-- personalitycore_server.log
|
+-- .repos/                 # Cloned repos (auto-managed by MBG)
+-- venv_runtime/           # Virtual environment (auto-created by MBG)
+-- sensors/                # Future: cameras, IMU, LIDAR
+-- actuators/              # Future: motors, servos, LED rings
```
