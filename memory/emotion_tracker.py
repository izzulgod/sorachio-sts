"""
Sorachio-STS Emotion Tracker
Tracks emotional patterns over time for long-term personality adaptation.

Features:
  - Rolling emotion history with timestamps
  - Emotion frequency analysis
  - Mood trend detection
  - Periodic summary storage in LTM
  - Personality adaptation signals
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from utils.logging_setup import get_logger

log = get_logger("memory.emotion")


@dataclass
class EmotionEntry:
    """Single emotion observation."""
    emotion: str
    timestamp: datetime = field(default_factory=datetime.now)
    topic: str = "general"
    intensity: float = 0.5


class EmotionTracker:
    """
    Tracks emotional patterns over time for personality adaptation.

    Aggregates emotion data from cognitive decisions and provides
    summary signals for the personality core.
    """

    def __init__(
        self,
        history_size: int = 50,
        summary_interval_turns: int = 10,
    ):
        self._history: deque[EmotionEntry] = deque(maxlen=history_size)
        self._turn_count = 0
        self._summary_interval = summary_interval_turns
        self._current_mood: str = "neutral"
        self._mood_history: deque[str] = deque(maxlen=20)

    def record_emotion(
        self,
        emotion: str,
        topic: str = "general",
        importance: float = 0.5,
    ) -> None:
        """Record an observed emotion from a cognitive decision."""
        entry = EmotionEntry(
            emotion=emotion,
            topic=topic,
            intensity=min(1.0, importance),
        )
        self._history.append(entry)
        self._turn_count += 1

        # Update mood tracking
        self._update_mood(emotion)

        log.debug(
            f"[EmotionTracker] Recorded: {emotion} "
            f"(turn={self._turn_count}, mood={self._current_mood})"
        )

    def _update_mood(self, emotion: str) -> None:
        """Update current mood based on recent emotions."""
        if len(self._history) < 3:
            self._current_mood = emotion
            return

        # Weight recent emotions more heavily
        recent = list(self._history)[-5:]
        emotion_counts: dict[str, float] = {}

        for i, entry in enumerate(recent):
            weight = 1.0 + (i * 0.2)  # More recent = higher weight
            emotion_counts[entry.emotion] = emotion_counts.get(entry.emotion, 0) + weight

        # Find dominant emotion
        if emotion_counts:
            dominant = max(emotion_counts.items(), key=lambda x: x[1])[0]
            self._current_mood = dominant
            self._mood_history.append(dominant)

    def get_mood_summary(self) -> str:
        """Return a brief mood summary for personality adaptation."""
        if not self._history:
            return "neutral"

        # Get frequency of emotions in recent history
        recent = list(self._history)[-10:]
        emotion_counts: dict[str, int] = {}
        for entry in recent:
            emotion_counts[entry.emotion] = emotion_counts.get(entry.emotion, 0) + 1

        if not emotion_counts:
            return "neutral"

        # Return most frequent emotion
        return max(emotion_counts.items(), key=lambda x: x[1])[0]

    def get_emotion_trend(self) -> dict[str, Any]:
        """Return emotion trend analysis."""
        if not self._history:
            return {
                "current_mood": "neutral",
                "dominant_emotion": "neutral",
                "emotion_frequency": {},
                "mood_stability": 1.0,
            }

        recent = list(self._history)[-10:]

        # Count emotions
        emotion_counts: dict[str, int] = {}
        for entry in recent:
            emotion_counts[entry.emotion] = emotion_counts.get(entry.emotion, 0) + 1

        dominant = max(emotion_counts.items(), key=lambda x: x[1])[0] if emotion_counts else "neutral"

        # Calculate mood stability (lower = more stable)
        if len(self._mood_history) >= 3:
            mood_list = list(self._mood_history)[-10:]
            unique_moods = len(set(mood_list))
            stability = 1.0 - (unique_moods / len(mood_list))
        else:
            stability = 1.0

        return {
            "current_mood": self._current_mood,
            "dominant_emotion": dominant,
            "emotion_frequency": emotion_counts,
            "mood_stability": stability,
            "turn_count": self._turn_count,
        }

    def should_summarize(self) -> bool:
        """Return True if it's time to generate an emotion summary for LTM."""
        return self._turn_count > 0 and self._turn_count % self._summary_interval == 0

    def generate_summary(self) -> str | None:
        """Generate a human-readable emotion summary for LTM storage."""
        if not self._history:
            return None

        trend = self.get_emotion_trend()
        dominant = trend["dominant_emotion"]
        mood = trend["current_mood"]
        stability = trend["mood_stability"]

        summary_parts = []
        if mood != "neutral":
            summary_parts.append(f"User's recent mood: {mood}")
        if dominant != "neutral":
            summary_parts.append(f"Dominant emotion: {dominant}")
        if stability < 0.6:
            summary_parts.append("Emotional state is fluctuating")
        elif stability > 0.8:
            summary_parts.append("Emotional state is stable")

        if not summary_parts:
            return None

        return "; ".join(summary_parts)

    def get_personality_adaptation(self) -> dict[str, Any]:
        """
        Return personality adaptation signals for the LLM system prompt.

        Used by ContextManager to adjust the companion's tone.
        """
        trend = self.get_emotion_trend()
        mood = trend["current_mood"]
        stability = trend["mood_stability"]

        # Determine adaptation signals
        adaptation = {
            "user_mood": mood,
            "tone_suggestion": self._get_tone_suggestion(mood),
            "energy_level": self._get_energy_level(mood),
            "empathy_level": self._get_empathy_level(stability),
        }

        return adaptation

    def _get_tone_suggestion(self, mood: str) -> str:
        """Get suggested tone based on user mood."""
        tone_map = {
            "happy": "warm and cheerful",
            "sad": "gentle and supportive",
            "anxious": "calm and reassuring",
            "frustrated": "patient and understanding",
            "excited": "enthusiastic and engaged",
            "confused": "clear and helpful",
            "tired": "soft and considerate",
            "neutral": "friendly and natural",
        }
        return tone_map.get(mood, "friendly and natural")

    def _get_energy_level(self, mood: str) -> str:
        """Get suggested energy level based on mood."""
        high_energy = {"excited", "happy"}
        low_energy = {"tired", "sad", "anxious"}

        if mood in high_energy:
            return "high"
        if mood in low_energy:
            return "low"
        return "medium"

    def _get_empathy_level(self, stability: float) -> str:
        """Get suggested empathy level based on mood stability."""
        if stability < 0.5:
            return "high"  # Fluctuating = more empathy needed
        if stability < 0.8:
            return "medium"
        return "normal"
