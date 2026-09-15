# 9. Configuration Guide

All configuration lives in `config/sorachio.yaml`.

### Key Settings

```yaml
# Companion personality
context:
  companion_name: "Sorachio"
  personality_prompt: |
    You are Sorachio, an AI companion. Created by izzulgod.
    ...

# STT settings (streaming is enabled by default in settings.py)
stt:
  model_size: "small"      # tiny | base | small | medium
  language: "auto"         # "auto" = bilingual EN/ID
  beam_size: 2             # 1=fastest, 5=most accurate
  models_dir: "models/stt" # Directory storing STT model weights

# Acoustic Echo Cancellation (AEC) — disabled by default
audio:
  echo_cancellation:
    enabled: false         # Set to true to activate
    provider: "null"       # "null" | "simple_energy" | "calibration"
    attenuation_factor: 0.3
    calibration_duration_s: 3.0
    lms_filter_length: 512
    lms_step_size: 0.01
    wiener_noise_margin: 1.5
    calibration_auto_run: true

# Memory (JSON + Vector Store + Emotion Tracker)
memory:
  long_term:
    importance_threshold: 0.8
    use_vector_store: true             # Enable ChromaDB semantic search
    vector_store_path: "data/memory/chroma"
    vector_model_dir: "models/vector/all-MiniLM-L6-v2"  # Offline model
    embedding_model: "all-MiniLM-L6-v2"
    vector_weight: 0.7                 # 70% vector, 30% keyword in hybrid retrieval

# Rate Limiter (configured in settings.py defaults)
# enable_rate_limiting: true
# rate_limit_max_requests: 10
# rate_limit_window_seconds: 60.0
```
