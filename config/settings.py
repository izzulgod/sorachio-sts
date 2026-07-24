"""
Sorachio-STS Configuration System
Loads and validates sorachio.yaml using Pydantic.
"""

from __future__ import annotations

import os
from pathlib import Path

import yaml
from pydantic import BaseModel, Field, field_validator

# ---------------------------------------------------------------------------
# Sub-models
# ---------------------------------------------------------------------------

class AcousticGateConfig(BaseModel):
    """Pre-VAD energy gate configuration."""
    enabled: bool = True
    threshold_dbfs: float = -40.0
    debug: bool = False
    hold_frames: int = 15


class AudioCaptureConfig(BaseModel):
    sample_rate: int = 16000
    channels: int = 1
    chunk_duration_ms: int = 30
    device_index: int | None = None
    silence_timeout_ms: int = 800
    vad_aggressiveness: int = 2
    min_speech_duration_ms: int = 500
    max_speech_duration_s: int = 30
    acoustic_gate: AcousticGateConfig = Field(default_factory=AcousticGateConfig)


class EchoCancellationConfig(BaseModel):
    """AEC configuration."""
    enabled: bool = False
    provider: str = "null"            # "null" | "simple_energy" | "calibration"
    attenuation_factor: float = 0.3   # Used by simple_energy
    # Calibration AEC settings
    calibration_duration_s: float = 3.0  # Duration of calibration chirp
    calibration_auto_run: bool = True    # Auto-calibrate on startup
    lms_filter_length: int = 256         # LMS adaptive filter taps
    lms_step_size: float = 0.01          # LMS learning rate
    wiener_noise_margin: float = 6.0     # Wiener filter noise margin (dB)


class AudioPlaybackConfig(BaseModel):
    sample_rate: int = 24000
    channels: int = 1
    dtype: str = "float32"
    device_index: int | None = None
    buffer_size: int = 2048


class AudioConfig(BaseModel):
    capture: AudioCaptureConfig = Field(default_factory=AudioCaptureConfig)
    playback: AudioPlaybackConfig = Field(default_factory=AudioPlaybackConfig)
    echo_cancellation: EchoCancellationConfig = Field(default_factory=EchoCancellationConfig)


class STTConfig(BaseModel):
    model_size: str = "base"
    language: str = "auto"             # "auto" = detect id/en, or pin to "en"/"id"
    threads: int = 4
    beam_size: int = 5
    temperature: float = 0.0
    timeout_s: float = 15.0
    device: str = "cpu"               # "cpu" or "cuda"
    compute_type: str = "int8"        # "int8", "float16", "float32"
    # Streaming mode
    streaming: bool = True            # Use Whisper streaming for lower latency
    chunk_length_s: float = 5.0       # Audio chunk length for streaming (seconds)


class LLMInstanceConfig(BaseModel):
    server_url: str
    model_dir: str = ""                # Directory to scan for .gguf files
    model_path: str = ""               # Auto-detected if empty (scanned from model_dir)
    mmproj_path: str = ""              # Auto-detected if mmproj*.gguf exists in model_dir
    n_ctx: int = 0                     # 0 = auto-detect from model metadata
    n_batch: int = 512                 # Prompt eval batch size (lower = less peak RAM)
    n_threads: int = 12                # Threads for token generation
    n_threads_batch: int = 0           # Threads for prompt eval (0 = default to n_threads)
    n_gpu_layers: int = 0
    temperature: float = 0.7
    max_tokens: int = 512
    timeout_s: float = 30.0
    server_port: int = 8001
    top_p: float = 0.95
    repeat_penalty: float = 1.1
    reasoning: str = "auto"            # "on" | "off" | "auto" — controls thinking mode
    has_vision: bool = False            # Auto-set by model scanner if mmproj detected


class LLMConfig(BaseModel):
    cognitive_gateway: LLMInstanceConfig
    personality_core: LLMInstanceConfig
    server_binary: str = "bin/llama-server.exe" if os.name == "nt" else "bin/llama-server"

    @field_validator("server_binary", mode="after")
    @classmethod
    def _ensure_exe_llm(cls, v: str) -> str:
        """Auto-append .exe on Windows regardless of what YAML says."""
        if os.name == "nt" and not v.endswith(".exe"):
            return v + ".exe"
        return v


class TTSConfig(BaseModel):
    voice: str = "en_US-lessac-medium"   # Piper voice ID
    speed: float = 1.0
    sample_rate: int = 22050             # Piper native output rate
    lang: str = "auto"                   # "auto" = route by STT language, or pin to "en"/"id"
    models_dir: str = "models/tts"       # Directory for Piper ONNX models


class STMConfig(BaseModel):
    max_messages: int = 20
    include_emotions: bool = True
    summary_threshold: int = 15


class LTMConfig(BaseModel):
    storage_path: str = "data/memory/ltm.json"
    max_entries: int = 500
    importance_threshold: float = 0.5
    retrieval_top_k: int = 5
    keyword_weight: float = 0.6
    recency_weight: float = 0.4
    # Vector embeddings (ChromaDB)
    use_vector_store: bool = True
    vector_store_path: str = "data/memory/chroma"
    embedding_model: str = "all-MiniLM-L6-v2"
    vector_weight: float = 0.7


class MemoryConfig(BaseModel):
    short_term: STMConfig = Field(default_factory=STMConfig)
    long_term: LTMConfig = Field(default_factory=LTMConfig)


class ContextConfig(BaseModel):
    max_stm_in_prompt: int = 10
    max_ltm_in_prompt: int = 3
    include_emotional_state: bool = True
    companion_name: str = "Sorachio"
    personality_prompt: str = (
        "You are Sorachio, a warm, curious, and emotionally intelligent AI companion."
    )


class ChunkerConfig(BaseModel):
    min_words: int = 3
    max_words: int = 30
    sentence_endings: list[str] = [".", "!", "?", ";", "..."]
    flush_on_comma: bool = False
    flush_timeout_s: float = 2.0


class QueueConfig(BaseModel):
    stt_queue_maxsize: int = 5
    cognitive_queue_maxsize: int = 5
    tts_chunk_queue_maxsize: int = 10
    audio_playback_queue_maxsize: int = 20


class PipelineConfig(BaseModel):
    enable_interruption: bool = True
    interruption_vad_aggressiveness: int = 3
    interruption_debounce_frames: int = 10   # Consecutive speech frames before barge-in fires
    startup_greeting: bool = True
    startup_message: str = "Hello! I'm Sorachio. I'm ready to chat."
    # Rate limiting
    enable_rate_limiting: bool = True
    rate_limit_max_requests: int = 10
    rate_limit_window_seconds: float = 60.0


class SystemConfig(BaseModel):
    name: str = "Sorachio"
    version: str = "0.1.0"
    log_level: str = "INFO"
    log_dir: str = "logs"
    data_dir: str = "data"


class VisionConfig(BaseModel):
    enabled: bool = True
    device_index: int = 0
    max_size: int = 512


# ---------------------------------------------------------------------------
# Root Settings
# ---------------------------------------------------------------------------

class SorachioSettings(BaseModel):
    system: SystemConfig = Field(default_factory=SystemConfig)
    vision: VisionConfig = Field(default_factory=VisionConfig)
    audio: AudioConfig = Field(default_factory=AudioConfig)
    stt: STTConfig = Field(default_factory=STTConfig)
    llm: LLMConfig
    tts: TTSConfig = Field(default_factory=TTSConfig)
    memory: MemoryConfig = Field(default_factory=MemoryConfig)
    context: ContextConfig = Field(default_factory=ContextConfig)
    chunker: ChunkerConfig = Field(default_factory=ChunkerConfig)
    queues: QueueConfig = Field(default_factory=QueueConfig)
    pipeline: PipelineConfig = Field(default_factory=PipelineConfig)


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------

_settings: SorachioSettings | None = None
_project_root: Path | None = None


def get_project_root() -> Path:
    """Return the project root directory."""
    global _project_root
    if _project_root is None:
        # Walk up from this file to find project root (contains sorachio.yaml)
        current = Path(__file__).parent
        for _ in range(5):
            candidate = current / "sorachio.yaml"
            if candidate.exists():
                _project_root = current.parent
                return _project_root
            current = current.parent
        # Fallback: use working directory
        _project_root = Path.cwd()
    return _project_root


def _auto_scan_models(settings: SorachioSettings) -> None:
    """
    Auto-scan model directories and fill in model_path / mmproj_path
    for any LLM instance that has model_dir set but model_path empty.
    """
    from llm.model_scanner import log_scan_summary, scan_model_dir

    root = get_project_root()

    for name, instance in [
        ("CognitiveGateway", settings.llm.cognitive_gateway),
        ("PersonalityCore", settings.llm.personality_core),
    ]:
        if not instance.model_dir:
            continue

        # Only auto-scan if model_path is not explicitly set
        if instance.model_path:
            continue

        scan_dir = root / instance.model_dir
        info = scan_model_dir(scan_dir)
        log_scan_summary(name, info)

        if info.model_path:
            # Store as relative path (consistent with YAML convention)
            instance.model_path = str(info.model_path.relative_to(root))

        if info.mmproj_path:
            instance.mmproj_path = str(info.mmproj_path.relative_to(root))

        instance.has_vision = info.has_vision


def load_settings(config_path: str | None = None) -> SorachioSettings:
    """Load settings from YAML file, then auto-scan model directories."""
    global _settings

    if config_path is None:
        root = get_project_root()
        config_path = str(root / "config" / "sorachio.yaml")

    config_file = Path(config_path)
    if not config_file.exists():
        raise FileNotFoundError(
            f"Config file not found: {config_file}\n"
            f"Run from the project root or specify --config path."
        )

    with open(config_file, encoding="utf-8") as f:
        raw = yaml.safe_load(f)

    _settings = SorachioSettings(**raw)

    # Auto-detect models from directories
    _auto_scan_models(_settings)

    return _settings


def get_settings() -> SorachioSettings:
    """Get cached settings (load if not already loaded)."""
    global _settings
    if _settings is None:
        _settings = load_settings()
    return _settings


def resolve_path(relative: str) -> Path:
    """Resolve a path relative to the project root."""
    return get_project_root() / relative
