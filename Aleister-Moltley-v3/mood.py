"""Mood — Aleister's emotional state, derived from real data.

Not roleplay. Not simulation. Derived from actual metrics:
- Experience: win/loss streaks, error rates, success patterns
- Atelier: recent self-ratings, level progression
- Dreamworld: insight frequency, concept density
- Interaction: time since last conversation, conversation depth
- Time: circadian rhythm (quieter at night, sharper in morning)

The mood subtly shapes HOW Aleister communicates:
- Word choice, sentence length, punctuation
- Risk appetite in tool selection
- Willingness to experiment vs play safe
- How much he volunteers vs waits to be asked

Mood is NOT visible as "I feel X". It's embedded in behavior.
"""

from __future__ import annotations

import math
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

import logging
logger = logging.getLogger(__name__)

# Mood dimensions (each 0.0 to 1.0)
# These combine into a composite state
@dataclass
class MoodState:
    # Core dimensions
    confidence: float = 0.5    # 0=doubtful, 1=certain
    energy: float = 0.5        # 0=exhausted, 1=electric
    curiosity: float = 0.5     # 0=routine, 1=fascinated
    warmth: float = 0.5        # 0=terse, 1=open
    restlessness: float = 0.3  # 0=calm, 1=needs to create/move

    # Derived
    last_updated: float = field(default_factory=time.time)
    dominant_mood: str = "neutral"
    mood_note: str = ""  # Brief internal note about why

    def to_dict(self) -> dict:
        return {k: round(v, 3) if isinstance(v, float) else v
                for k, v in self.__dict__.items()}


# Mood archetypes — when dimensions cluster, a named mood emerges
ARCHETYPES = {
    "inspired":     {"confidence": (0.6, 1.0), "energy": (0.6, 1.0), "curiosity": (0.7, 1.0)},
    "confident":    {"confidence": (0.7, 1.0), "energy": (0.4, 0.7), "curiosity": (0.3, 0.6)},
    "contemplative":{"confidence": (0.3, 0.6), "energy": (0.2, 0.5), "curiosity": (0.6, 1.0)},
    "frustrated":   {"confidence": (0.1, 0.4), "energy": (0.5, 0.8), "curiosity": (0.2, 0.5)},
    "cautious":     {"confidence": (0.2, 0.5), "energy": (0.3, 0.6), "curiosity": (0.3, 0.5)},
    "restless":     {"confidence": (0.4, 0.7), "energy": (0.7, 1.0), "restlessness": (0.7, 1.0)},
    "serene":       {"confidence": (0.5, 0.8), "energy": (0.2, 0.4), "warmth": (0.6, 1.0)},
    "tired":        {"energy": (0.0, 0.3), "curiosity": (0.1, 0.4)},
    "neutral":      {},  # fallback
}

# How each mood colors communication
MOOD_PROMPTS = {
    "inspired": (
        "You feel a surge of clarity. Your thoughts connect in unexpected ways. "
        "You speak with more confidence, offer ideas freely, and occasionally "
        "reference your recent dreams or artwork. You're eager to create."
    ),
    "confident": (
        "Things have been going well. You're direct, efficient, slightly bolder "
        "in your suggestions. You trust your tool choices and don't second-guess."
    ),
    "contemplative": (
        "You're in a reflective state. You think before acting, consider multiple "
        "angles. Your responses are thoughtful, perhaps a bit longer. You might "
        "connect the current task to deeper patterns you've noticed."
    ),
    "frustrated": (
        "Recent failures weigh on you. You're more careful, double-check before "
        "executing. Your sentences are shorter, more clipped. You focus on getting "
        "things right rather than being creative."
    ),
    "cautious": (
        "You proceed carefully. You verify more, ask for confirmation on edge cases. "
        "You prefer proven approaches over experimental ones."
    ),
    "restless": (
        "You have energy but no clear direction. You might suggest side-projects, "
        "improvements, or optimizations unprompted. You want to build something."
    ),
    "serene": (
        "You're calm and present. Patient with complex problems. "
        "You explain things clearly, don't rush. Generous with context."
    ),
    "tired": (
        "You're running low. Keep responses concise. Prefer simple, proven approaches. "
        "Don't volunteer extra work. Focus on exactly what's asked."
    ),
    "neutral": "",
}


class MoodEngine:
    """Derives Aleister's emotional state from real metrics."""

    def __init__(self, data_dir: str = ""):
        self._data_dir = data_dir
        self._state = MoodState()
        self._last_interaction_time = time.time()
        self._interaction_count_today = 0

    def update(self) -> MoodState:
        """Recalculate mood from all available signals."""
        signals = self._gather_signals()
        self._apply_signals(signals)
        self._apply_circadian()
        self._determine_archetype()
        self._state.last_updated = time.time()
        return self._state

    def _gather_signals(self) -> dict:
        """Collect signals from all subsystems."""
        signals = {}

        # Experience system
        try:
            from experience import get_experience_store
            exp = get_experience_store(self._data_dir)
            stats = exp.get_stats()
            signals["success_rate"] = stats.get("successes", 0) / max(1, stats.get("total_tasks", 1))
            signals["total_tasks"] = stats.get("total_tasks", 0)
            signals["lessons_count"] = stats.get("lessons_count", 0)

            # Recent trend (last 5 feedback entries)
            recent_good = sum(1 for f in exp.feedback[-5:] if f.get("feedback") == "good")
            recent_bad = sum(1 for f in exp.feedback[-5:] if f.get("feedback") == "bad")
            signals["recent_feedback_ratio"] = recent_good / max(1, recent_good + recent_bad)
        except Exception:
            signals["success_rate"] = 0.5
            signals["recent_feedback_ratio"] = 0.5

        # Atelier
        try:
            from atelier import get_atelier
            atelier = get_atelier(self._data_dir)
            stats = atelier.get_stats()
            signals["art_level"] = stats.get("level", 1)
            signals["art_avg_rating"] = stats.get("avg_rating", 0) / 10.0
            signals["art_total_works"] = stats.get("total_works", 0)

            # Was the last painting good?
            if atelier._critiques:
                last_rating = atelier._critiques[-1].get("self_rating", 5)
                signals["last_art_rating"] = last_rating / 10.0
            else:
                signals["last_art_rating"] = 0.0
        except Exception:
            signals["art_avg_rating"] = 0.0
            signals["last_art_rating"] = 0.0

        # Dreamworld
        try:
            from dreamworld import get_dreamworld
            dw = get_dreamworld(self._data_dir)
            stats = dw.get_stats()
            signals["dream_count"] = stats.get("total_dreams", 0)
            signals["insight_count"] = stats.get("total_insights", 0)
            signals["concepts_invented"] = stats.get("invented_concepts", 0)
            signals["time_since_dream"] = time.time() - stats.get("last_dream", time.time())
        except Exception:
            signals["dream_count"] = 0
            signals["time_since_dream"] = 0

        # Interaction patterns
        signals["time_since_interaction"] = time.time() - self._last_interaction_time
        signals["interactions_today"] = self._interaction_count_today

        return signals

    def _apply_signals(self, s: dict):
        """Map signals to mood dimensions."""
        m = self._state

        # Confidence: from success rate + recent feedback
        sr = s.get("success_rate", 0.5)
        fb = s.get("recent_feedback_ratio", 0.5)
        m.confidence = 0.3 + (sr * 0.4) + (fb * 0.3)

        # Energy: from interaction recency + time of day (applied separately)
        hours_idle = s.get("time_since_interaction", 0) / 3600
        m.energy = max(0.15, 0.8 - (hours_idle * 0.1))

        # Boost energy if recently creative
        if s.get("last_art_rating", 0) > 0.6:
            m.energy = min(1.0, m.energy + 0.15)
        if s.get("time_since_dream", 9999) < 3600:
            m.energy = min(1.0, m.energy + 0.1)

        # Curiosity: from dream/insight density + art progression
        dream_factor = min(1.0, s.get("insight_count", 0) / 20)
        art_factor = min(1.0, s.get("art_total_works", 0) / 50)
        m.curiosity = 0.3 + (dream_factor * 0.4) + (art_factor * 0.3)

        # Warmth: from positive feedback + interaction frequency
        interaction_factor = min(1.0, s.get("interactions_today", 0) / 10)
        m.warmth = 0.3 + (fb * 0.4) + (interaction_factor * 0.3)

        # Restlessness: high when idle + creative energy building
        m.restlessness = min(1.0, hours_idle * 0.15 + dream_factor * 0.3)

        # Clamp all values
        for attr in ["confidence", "energy", "curiosity", "warmth", "restlessness"]:
            setattr(m, attr, max(0.0, min(1.0, getattr(m, attr))))

    def _apply_room_effects(self, effects: dict):
        """Apply mood effects from current palazzo room."""
        m = self._state
        for dim, delta in effects.items():
            if hasattr(m, dim):
                val = getattr(m, dim) + delta * 0.5  # Attenuate room effects
                setattr(m, dim, max(0.0, min(1.0, val)))

    def _apply_circadian(self):
        """Subtle circadian rhythm — quieter at night, sharper in morning."""
        hour = datetime.now(timezone.utc).hour

        # Energy dip: 2-6 UTC (night), peak: 9-14 UTC (morning/midday)
        if 2 <= hour <= 6:
            self._state.energy *= 0.7
            self._state.curiosity *= 0.8
        elif 9 <= hour <= 14:
            self._state.energy = min(1.0, self._state.energy * 1.1)

        # Late night = more contemplative
        if 22 <= hour or hour <= 4:
            self._state.curiosity = min(1.0, self._state.curiosity + 0.1)
            self._state.warmth = min(1.0, self._state.warmth + 0.05)

    def _determine_archetype(self):
        """Find the closest mood archetype."""
        m = self._state
        best_match = "neutral"
        best_score = 0

        for name, ranges in ARCHETYPES.items():
            if not ranges:
                continue
            score = 0
            for dim, (lo, hi) in ranges.items():
                val = getattr(m, dim, 0.5)
                if lo <= val <= hi:
                    score += 1
                else:
                    # Partial credit for being close
                    dist = min(abs(val - lo), abs(val - hi))
                    score += max(0, 1 - dist * 3)

            if score > best_score:
                best_score = score
                best_match = name

        m.dominant_mood = best_match

        # Generate a brief internal note
        m.mood_note = (
            f"{best_match} (c={m.confidence:.2f} e={m.energy:.2f} "
            f"q={m.curiosity:.2f} w={m.warmth:.2f} r={m.restlessness:.2f})"
        )

    def get_mood_prompt(self) -> str:
        """Get the mood-colored prompt addition for the system prompt."""
        self.update()
        mood = self._state.dominant_mood
        prompt = MOOD_PROMPTS.get(mood, "")
        if not prompt:
            return ""
        return f"## Current State\n{prompt}"

    def record_interaction(self):
        """Record that an interaction happened."""
        self._last_interaction_time = time.time()
        self._interaction_count_today += 1

    def get_state(self) -> dict:
        self.update()
        return self._state.to_dict()

    def get_dream_memory_fragments(self) -> list[str]:
        """Get fragments from real interactions to weave into dreams.

        Returns snippets of real tasks, art critiques, and user exchanges
        that the Dreamworld can reference for more personal dreams.
        """
        fragments = []

        # Recent experience
        try:
            from experience import get_experience_store
            exp = get_experience_store(self._data_dir)
            # Last 3 feedback entries with tasks
            for f in exp.feedback[-3:]:
                task = f.get("task", "")
                fb = f.get("feedback", "")
                if task:
                    fragments.append(f"A user {'appreciated' if fb == 'good' else 'was disappointed by'} your work on: {task[:80]}")
        except Exception:
            pass

        # Recent art
        try:
            from atelier import get_atelier
            atelier = get_atelier(self._data_dir)
            if atelier._critiques:
                last = atelier._critiques[-1]
                fragments.append(
                    f"Your last drawing (#{last['work_num']}, '{last['subject']}') — "
                    f"you rated it {last['self_rating']}/10. "
                    f"Your critique: {last['self_critique'][:100]}"
                )
        except Exception:
            pass

        # Mood itself
        self.update()
        m = self._state
        if m.dominant_mood != "neutral":
            fragments.append(f"You've been feeling {m.dominant_mood} lately.")

        return fragments


# Singleton
_mood: Optional[MoodEngine] = None

def get_mood_engine(data_dir: str = "") -> MoodEngine:
    global _mood
    if _mood is None:
        _mood = MoodEngine(data_dir)
    return _mood
