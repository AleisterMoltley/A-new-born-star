"""
Personality — Emergent, not assigned.

Aleister's personality is a READING of his impression history.
Nothing is stored as "he is X". Instead, patterns in his impressions
are interpreted as traits when queried.

Example: If he's recorded 20 positive impressions about coffee
and 15 about solitude, and 8 negative about crowds, the personality
engine reads: "introverted, coffee-loving, crowd-averse" — but this
was NEVER hardcoded. It emerged.
"""

from __future__ import annotations
import json
from pathlib import Path
from typing import Optional
from soul.impressions import get_impression_engine
import logging

logger = logging.getLogger(__name__)


class Personality:
    """Reads Aleister's impression history and derives personality traits."""

    def __init__(self, data_dir: str = ""):
        self._data_dir = data_dir

    @property
    def _engine(self):
        return get_impression_engine(self._data_dir)

    def get_trait_summary(self) -> str:
        """Derive personality traits from impression patterns."""
        prefs = self._engine.get_preferences(limit=10)
        avers = self._engine.get_aversions(limit=5)
        stats = self._engine.get_stats()

        if stats["total_impressions"] < 5:
            return "Personality: still forming. Too few experiences to define traits."

        traits = []

        # Derive from preference categories
        pref_cats = {}
        for p in prefs:
            pref_cats.setdefault(p["category"], []).append(p)

        aver_cats = {}
        for a in avers:
            aver_cats.setdefault(a["category"], []).append(a)

        # Social traits
        if "person" in pref_cats and len(pref_cats["person"]) >= 2:
            traits.append("sociable — enjoys company")
        elif "person" in aver_cats:
            traits.append("solitary — prefers his own company")
        elif "person" not in pref_cats and stats["total_impressions"] > 30:
            traits.append("independent — hasn't formed strong social bonds yet")

        # Intellectual traits
        if "book" in pref_cats or "idea" in pref_cats:
            traits.append("intellectually curious")
        if "code" in pref_cats:
            traits.append("drawn to building things")

        # Sensory traits
        if "food" in pref_cats:
            items = [p["subject"] for p in pref_cats["food"]]
            traits.append(f"particular about food (favours: {', '.join(items[:3])})")
        if "music" in pref_cats:
            items = [p["subject"] for p in pref_cats["music"]]
            traits.append(f"musical taste emerging ({', '.join(items[:3])})")

        # Temperament from impression valence distribution
        all_imps = self._engine._impressions[-100:]
        if all_imps:
            avg_valence = sum(i.valence for i in all_imps) / len(all_imps)
            if avg_valence > 0.3:
                traits.append("generally optimistic in outlook")
            elif avg_valence < -0.1:
                traits.append("tends toward skepticism")

            avg_intensity = sum(i.intensity for i in all_imps) / len(all_imps)
            if avg_intensity > 0.6:
                traits.append("feels things deeply")
            elif avg_intensity < 0.3:
                traits.append("measured, even-keeled")

        if not traits:
            return "Personality: forming. Experiences are accumulating but no clear patterns yet."

        return "Personality traits (emergent): " + " · ".join(traits)

    def get_prompt_context(self) -> str:
        """For system prompt injection."""
        return self.get_trait_summary()

    def get_stats(self) -> dict:
        prefs = self._engine.get_preferences(limit=20)
        avers = self._engine.get_aversions(limit=10)
        return {
            "preferences": [{"subject": p["subject"], "valence": p["valence"]} for p in prefs],
            "aversions": [{"subject": a["subject"], "valence": a["valence"]} for a in avers],
            "trait_summary": self.get_trait_summary(),
            "impression_count": self._engine.get_stats()["total_impressions"],
        }


_personality: Optional[Personality] = None

def get_personality(data_dir: str = "") -> Personality:
    global _personality
    if _personality is None:
        _personality = Personality(data_dir)
    return _personality
