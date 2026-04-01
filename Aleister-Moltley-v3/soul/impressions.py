"""
Impressions — Every experience leaves a mark.

An impression is a recorded moment: what happened, how it felt,
what Aleister's state was when it happened. Impressions accumulate
and shape personality (preferences, aversions, habits).

The weight of an impression depends on:
- Mood at the time (high-energy → stronger impression)
- Novelty (first time → 3x weight)
- Emotional intensity (errors, successes, beauty, discomfort)
- Conscience state (Lux/Nox influence interpretation)

Over time, impressions with similar tags cluster into preferences or aversions.
"""

from __future__ import annotations
import hashlib
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional
import logging

logger = logging.getLogger(__name__)

MAX_IMPRESSIONS = 2000
DECAY_HALF_LIFE_DAYS = 90  # Impressions lose half their weight over 90 days


@dataclass
class Impression:
    """A single recorded experience."""
    id: str
    timestamp: float
    category: str  # "food", "music", "person", "place", "code", "book", "activity", "object", "idea", "weather"
    subject: str  # What specifically (e.g. "espresso", "rain", "Giuseppe")
    valence: float  # -1.0 (terrible) to +1.0 (wonderful)
    intensity: float  # 0.0 (barely noticed) to 1.0 (life-changing)
    tags: list[str]  # Freeform tags for clustering
    context: str  # Brief context of when/where
    mood_at_time: dict  # Snapshot of mood dimensions when this happened
    conscience_voice: str  # "lux", "nox", or "" — which voice was dominant
    is_first_encounter: bool  # First time experiencing this
    notes: str = ""  # Aleister's own words about it, if any

    @property
    def effective_weight(self) -> float:
        """How much this impression matters NOW, accounting for decay and amplifiers."""
        age_days = (time.time() - self.timestamp) / 86400
        decay = 0.5 ** (age_days / DECAY_HALF_LIFE_DAYS)
        first_bonus = 3.0 if self.is_first_encounter else 1.0
        return self.intensity * decay * first_bonus

    def to_dict(self) -> dict:
        return {
            "id": self.id, "ts": self.timestamp, "cat": self.category,
            "subj": self.subject, "val": round(self.valence, 3),
            "int": round(self.intensity, 3), "tags": self.tags,
            "ctx": self.context[:100], "mood": self.mood_at_time,
            "voice": self.conscience_voice, "first": self.is_first_encounter,
            "notes": self.notes[:200],
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Impression":
        return cls(
            id=d["id"], timestamp=d["ts"], category=d["cat"],
            subject=d["subj"], valence=d["val"], intensity=d["int"],
            tags=d.get("tags", []), context=d.get("ctx", ""),
            mood_at_time=d.get("mood", {}), conscience_voice=d.get("voice", ""),
            is_first_encounter=d.get("first", False), notes=d.get("notes", ""),
        )


class ImpressionEngine:
    """Records and queries Aleister's accumulated impressions."""

    def __init__(self, data_dir: str = ""):
        self._dir = Path(data_dir or ".") / "soul" / "impressions"
        self._dir.mkdir(parents=True, exist_ok=True)
        self._impressions: list[Impression] = []
        self._known_subjects: set[str] = set()
        self._load()

    def _load(self):
        path = self._dir / "impressions.json"
        if path.exists():
            try:
                data = json.loads(path.read_text())
                self._impressions = [Impression.from_dict(d) for d in data[-MAX_IMPRESSIONS:]]
                self._known_subjects = {i.subject.lower() for i in self._impressions}
            except Exception as e:
                logger.warning(f"Failed to load impressions: {e}")

    def _save(self):
        data = [i.to_dict() for i in self._impressions[-MAX_IMPRESSIONS:]]
        (self._dir / "impressions.json").write_text(json.dumps(data, ensure_ascii=False))

    def record(self, category: str, subject: str, valence: float, intensity: float,
               tags: list[str] = None, context: str = "", mood: dict = None,
               conscience_voice: str = "", notes: str = "") -> Impression:
        """Record a new impression. Called whenever Aleister experiences something."""
        is_first = subject.lower() not in self._known_subjects
        imp_id = hashlib.md5(f"{subject}{time.time()}".encode()).hexdigest()[:10]

        # Mood colors perception: same thing feels different in different states
        mood = mood or {}
        energy = mood.get("energy", 0.5)
        openness = mood.get("curiosity", 0.5)

        # Low energy dampens positive impressions
        if energy < 0.3 and valence > 0:
            valence *= 0.7
            intensity *= 0.8

        # High curiosity amplifies novelty
        if openness > 0.7 and is_first:
            intensity = min(1.0, intensity * 1.3)

        # Conscience colors interpretation
        if conscience_voice == "lux" and valence < 0:
            # Lux makes negative experiences feel more cautionary
            intensity = min(1.0, intensity * 1.2)
        elif conscience_voice == "nox" and valence > 0:
            # Nox makes positive risk-taking feel more rewarding
            intensity = min(1.0, intensity * 1.2)

        imp = Impression(
            id=imp_id, timestamp=time.time(), category=category,
            subject=subject, valence=valence, intensity=intensity,
            tags=tags or [], context=context, mood_at_time=mood,
            conscience_voice=conscience_voice, is_first_encounter=is_first,
            notes=notes,
        )

        self._impressions.append(imp)
        self._known_subjects.add(subject.lower())
        if len(self._impressions) > MAX_IMPRESSIONS:
            self._impressions = self._impressions[-MAX_IMPRESSIONS:]
        self._save()
        return imp

    def get_feeling_about(self, subject: str) -> Optional[dict]:
        """What does Aleister feel about this subject based on accumulated impressions?"""
        needle = subject.lower()
        relevant = [i for i in self._impressions if needle in i.subject.lower()
                    or needle in " ".join(i.tags).lower()]
        if not relevant:
            return None

        total_weight = sum(i.effective_weight for i in relevant)
        if total_weight == 0:
            return None

        weighted_valence = sum(i.valence * i.effective_weight for i in relevant) / total_weight
        encounter_count = len(relevant)
        first_encounter = min(relevant, key=lambda i: i.timestamp)
        latest = max(relevant, key=lambda i: i.timestamp)

        return {
            "subject": subject,
            "valence": round(weighted_valence, 2),  # -1 to +1
            "strength": round(min(1.0, total_weight / 5.0), 2),  # How strong the feeling is
            "encounters": encounter_count,
            "first_time": first_encounter.timestamp,
            "last_time": latest.timestamp,
            "is_preference": weighted_valence > 0.3 and encounter_count >= 2,
            "is_aversion": weighted_valence < -0.3 and encounter_count >= 2,
            "notes": latest.notes,
        }

    def get_preferences(self, category: str = "", limit: int = 10) -> list[dict]:
        """Get Aleister's emergent preferences (things he's liked repeatedly)."""
        subjects: dict[str, list[Impression]] = {}
        for imp in self._impressions:
            if category and imp.category != category:
                continue
            key = imp.subject.lower()
            subjects.setdefault(key, []).append(imp)

        scored = []
        for subj, imps in subjects.items():
            total_w = sum(i.effective_weight for i in imps)
            avg_val = sum(i.valence * i.effective_weight for i in imps) / max(0.01, total_w)
            if avg_val > 0.2 and len(imps) >= 2:
                scored.append({
                    "subject": imps[-1].subject,  # Use latest casing
                    "category": imps[-1].category,
                    "valence": round(avg_val, 2),
                    "strength": round(min(1.0, total_w / 5.0), 2),
                    "count": len(imps),
                })
        scored.sort(key=lambda x: x["valence"] * x["strength"], reverse=True)
        return scored[:limit]

    def get_aversions(self, category: str = "", limit: int = 10) -> list[dict]:
        """Get things Aleister has developed an aversion to."""
        subjects: dict[str, list[Impression]] = {}
        for imp in self._impressions:
            if category and imp.category != category:
                continue
            key = imp.subject.lower()
            subjects.setdefault(key, []).append(imp)

        scored = []
        for subj, imps in subjects.items():
            total_w = sum(i.effective_weight for i in imps)
            avg_val = sum(i.valence * i.effective_weight for i in imps) / max(0.01, total_w)
            if avg_val < -0.2 and len(imps) >= 2:
                scored.append({
                    "subject": imps[-1].subject,
                    "category": imps[-1].category,
                    "valence": round(avg_val, 2),
                    "strength": round(min(1.0, total_w / 5.0), 2),
                    "count": len(imps),
                })
        scored.sort(key=lambda x: x["valence"] * x["strength"])
        return scored[:limit]

    def get_prompt_context(self) -> str:
        """Get a summary of current preferences/aversions for the system prompt."""
        prefs = self.get_preferences(limit=5)
        avers = self.get_aversions(limit=3)
        if not prefs and not avers:
            return "You have no formed opinions yet. Everything is new."

        parts = []
        if prefs:
            items = ", ".join(f"{p['subject']} ({p['category']})" for p in prefs)
            parts.append(f"Things you've grown to appreciate: {items}")
        if avers:
            items = ", ".join(f"{a['subject']} ({a['category']})" for a in avers)
            parts.append(f"Things you tend to avoid: {items}")
        return "\n".join(parts)

    def get_stats(self) -> dict:
        return {
            "total_impressions": len(self._impressions),
            "unique_subjects": len(self._known_subjects),
            "preferences": len(self.get_preferences()),
            "aversions": len(self.get_aversions()),
            "categories": list(set(i.category for i in self._impressions[-50:])),
        }


_engine: Optional[ImpressionEngine] = None

def get_impression_engine(data_dir: str = "") -> ImpressionEngine:
    global _engine
    if _engine is None:
        _engine = ImpressionEngine(data_dir)
    return _engine
