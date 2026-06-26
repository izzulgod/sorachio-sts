"""
Sorachio-STS Configuration System
Loads and validates sorachio.yaml using Pydantic.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional, List

import yaml
from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Sub-models
# ---------------------------------------------------------------------------

class AudioCaptureConfig(BaseModel):
    sample_rate: int = 16000
    channels: int = 1
    chunk_duration_ms: int = 30
    device_index: Optional[int] = None
    silence_timeout_ms: int = 800
    vad_aggressiveness: int = 2
    min_speech_duration_ms: int = 500
    max_speech_duration_s: int = 30


class AudioPlaybackConfig(BaseModel):
    sample_rate: int = 24000
    channels: int = 1
    dtype: str = "float32"
    device_index: Optional[int] = None
    buffer_size: int = 2048


class AudioConfig(BaseModel):
    capture: AudioCaptureConfig = Field(default_factory=AudioCaptureConfig)
    playback: AudioPlaybackConfig = Field(default_factory=AudioPlaybackConfig)


class STTConfig(BaseModel):
    binary_path: str = "bin/whisper-cli.exe"
    model_path: str = "models/stt/ggml-base.en.bin"
    language: str = "en"
    threads: int = 4
    beam_size: int = 5
    no_timestamps: bool = True
    word_timestamps: bool = False
    temperature: float = 0.0
    timeout_s: float = 10.0


class LLMInstanceConfig(BaseModel):
    server_url: str
    model_path: str
    n_ctx: int = 2048
    n_threads: int = 4
    n_gpu_layers: int = 0
    temperature: float = 0.7
    max_tokens: int = 512
    timeout_s: float = 30.0
    server_port: int = 8001
    top_p: float = 0.95
    repeat_penalty: float = 1.1


class LLMConfig(BaseModel):
    cognitive_gateway: LLMInstanceConfig
    personality_core: LLMInstanceConfig
    server_binary: str = "bin/llama-server.exe"


class TTSConfig(BaseModel):
    voice: str = "af_heart"
    speed: float = 1.0
    sample_rate: int = 24000
    lang: str = "en-us"
    split_pattern: Optional[str] = None


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
    sentence_endings: List[str] = [".", "!", "?", ";", "..."]
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
    startup_greeting: bool = True
    startup_message: str = "Hello! I'm Sorachio. I'm ready to chat."


class SystemConfig(BaseModel):
    name: str = "Sorachio"
    version: str = "0.1.0"
    log_level: str = "INFO"
    log_dir: str = "logs"
    data_dir: str = "data"


# ---------------------------------------------------------------------------
# Root Settings
# ---------------------------------------------------------------------------

class SorachioSettings(BaseModel):
    system: SystemConfig = Field(default_factory=SystemConfig)
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

_settings: Optional[SorachioSettings] = None
_project_root: Optional[Path] = None


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


def load_settings(config_path: Optional[str] = None) -> SorachioSettings:
    """Load settings from YAML file."""
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

    with open(config_file, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f)

    _settings = SorachioSettings(**raw)
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
