"""Sorachio-STS TTS package."""
from .kokoro_client import KokoroTTSClient
from .piper_client import PiperTTSClient

TTSClient = KokoroTTSClient

__all__ = ["KokoroTTSClient", "PiperTTSClient", "TTSClient"]
