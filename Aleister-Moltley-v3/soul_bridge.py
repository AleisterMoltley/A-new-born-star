"""
Soul Bridge — The nervous system connecting experience to soul.

This is the GLUE. Without it, the soul subsystems are beautiful but inert.
The bridge watches what Aleister does and feeds it into:
- Impressions (every meaningful experience becomes a weighted mark)
- Encounters (tool results about people → NPC generation)
- World (tool results about places → map discovery)
- Journal (end-of-day summary triggered)
- Palazzo (activity completion → mood effects)

Called by:
- QueryEngine after each tool result
- QueryEngine after each completed chat
- Palazzo after each activity
- Telegram on /explore and /meet
"""

from __future__ import annotations
import re
import time
import logging
from typing import Optional

logger = logging.getLogger(__name__)


class SoulBridge:
    """Connects Aleister's actions to his inner life."""

    def __init__(self, data_dir: str = ""):
        self._data_dir = data_dir

    @property
    def _impressions(self):
        from soul.impressions import get_impression_engine
        return get_impression_engine(self._data_dir)

    @property
    def _encounters(self):
        from soul.encounters import get_encounter_engine
        return get_encounter_engine(self._data_dir)

    @property
    def _world(self):
        from soul.world import get_world
        return get_world(self._data_dir)

    @property
    def _journal(self):
        from soul.journal import get_journal
        return get_journal(self._data_dir)

    def on_tool_result(self, tool_name: str, params: dict, result_text: str,
                       is_error: bool, mood: dict = None, conscience_voice: str = ""):
        """Called after every tool execution. Extracts impressions from results."""
        if is_error:
            # Errors create negative impressions about the tool/approach
            self._impressions.record(
                category="experience", subject=f"failed {tool_name}",
                valence=-0.3, intensity=0.4,
                tags=["error", tool_name], context=result_text[:100],
                mood=mood, conscience_voice=conscience_voice,
            )
            return

        # Web search results → impressions about what was found
        if tool_name == "web_search" and result_text:
            query = params.get("query", "")
            self._impressions.record(
                category="discovery", subject=query,
                valence=0.2, intensity=0.3,
                tags=["research", "web"], context=result_text[:100],
                mood=mood, conscience_voice=conscience_voice,
            )

        # File operations → impressions about coding/building
        elif tool_name in ("file_write", "file_edit", "bash"):
            subject = params.get("path", params.get("command", ""))[:50]
            self._impressions.record(
                category="creation", subject=f"built: {subject}",
                valence=0.4, intensity=0.3,
                tags=["code", "building", tool_name],
                context=result_text[:80],
                mood=mood, conscience_voice=conscience_voice,
            )

        # Memory operations → meta-impression
        elif tool_name == "memory_write":
            key = params.get("key", "")
            self._impressions.record(
                category="idea", subject=f"remembered: {key}",
                valence=0.3, intensity=0.2,
                tags=["memory"], context=key,
                mood=mood, conscience_voice=conscience_voice,
            )

    def on_chat_message(self, user_message: str, response_text: str,
                        mood: dict = None, conscience_voice: str = ""):
        """Called after each completed chat turn. Records social impression."""
        # Every meaningful conversation is an experience
        if len(user_message) > 20:
            # Determine valence from response quality heuristics
            valence = 0.2  # Neutral-positive (someone talked to him)
            if any(w in user_message.lower() for w in ["danke", "thanks", "great", "gut", "super", "nice"]):
                valence = 0.5
            if any(w in user_message.lower() for w in ["falsch", "wrong", "bad", "schlecht", "nein"]):
                valence = -0.2

            self._impressions.record(
                category="conversation", subject=f"chat: {user_message[:40]}",
                valence=valence, intensity=0.2,
                tags=["social", "conversation"],
                context=response_text[:80],
                mood=mood, conscience_voice=conscience_voice,
            )

    def on_palazzo_activity(self, activity_id: str, activity_name: str,
                            room_name: str, mood: dict = None):
        """Called when Aleister completes a palazzo activity."""
        from palazzo import ACTIVITIES
        act = ACTIVITIES.get(activity_id)
        if not act:
            return

        # Activity creates an impression
        valence = 0.0
        for dim, delta in act.mood_effects.items():
            valence += delta  # Net mood effect = impression valence
        valence = max(-1.0, min(1.0, valence))

        self._impressions.record(
            category="activity", subject=activity_name,
            valence=valence, intensity=max(0.2, abs(valence)),
            tags=["palazzo", room_name, activity_id],
            context=f"in {room_name}",
            mood=mood,
        )

    def on_explore_place(self, name: str, place_type: str, lat: float, lon: float,
                         notes: str = "", mood: dict = None):
        """Called when Aleister discovers a new place."""
        place = self._world.discover(name, place_type, lat, lon, notes)

        self._impressions.record(
            category="place", subject=name,
            valence=0.4, intensity=0.5,  # Discovery is always somewhat exciting
            tags=["exploration", place_type, "new"],
            context=notes[:80],
            mood=mood,
        )
        return place

    def on_meet_person(self, location: str = "Cefalù", mood: dict = None):
        """Called when Aleister meets someone new. Generates NPC."""
        npc = self._encounters.meet_someone(location)

        # First impression colored by current mood
        mood = mood or {}
        warmth = mood.get("warmth", 0.5)
        energy = mood.get("energy", 0.5)

        # High warmth → positive first impression, low energy → muted
        valence = (warmth - 0.5) * 0.6 + 0.1  # Slight positive bias
        if energy < 0.3:
            valence *= 0.5  # Tired = less receptive
        intensity = 0.5 if npc.memory.familiarity == 1 else 0.3  # First meetings are stronger

        self._impressions.record(
            category="person", subject=npc.full_name,
            valence=round(valence, 2), intensity=intensity,
            tags=["encounter", npc.occupation, location],
            context=f"{npc.full_name}, {npc.age}, {npc.occupation}. {npc.quirk}",
            mood=mood,
            notes=f"Met at {location}. {npc.appearance}.",
        )
        return npc

    def on_interact_person(self, npc_id: str, what: str, trust_delta: float = 0.0,
                            mood: dict = None):
        """Called when Aleister interacts with a known person."""
        npc = self._encounters.interact(
            npc_id, what,
            aleister_mood=mood.get("dominant_mood", "") if mood else "",
            trust_delta=trust_delta,
        )
        if npc:
            self._impressions.record(
                category="person", subject=npc.full_name,
                valence=0.1 + trust_delta, intensity=0.3,
                tags=["interaction", npc.occupation],
                context=what[:80], mood=mood,
            )
        return npc

    def write_daily_journal(self, palazzo_log: list = None, mood: dict = None,
                            conscience_stats: dict = None):
        """Generate today's journal entry from real data."""
        from soul.weather import get_weather_now
        weather = get_weather_now()

        # Get today's impressions
        today_start = time.time() - 86400
        today_imps = [
            i.to_dict() for i in self._impressions._impressions
            if i.timestamp > today_start
        ]

        # Get today's encounters
        today_encs = [
            {"name": n.full_name, "occupation": n.occupation}
            for n in self._encounters.list_known(20)
            if n.memory.last_seen > today_start
        ]

        return self._journal.write_entry(
            palazzo_log=palazzo_log, mood=mood,
            conscience_stats=conscience_stats, weather=weather,
            impressions_today=today_imps, encounters_today=today_encs,
        )


_bridge: Optional[SoulBridge] = None

def get_soul_bridge(data_dir: str = "") -> SoulBridge:
    global _bridge
    if _bridge is None:
        _bridge = SoulBridge(data_dir)
    return _bridge
