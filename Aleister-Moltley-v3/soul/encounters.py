"""
Encounters — People Aleister meets in Cefalù.

No pre-made NPCs. Every person is generated when first encountered,
with random traits, and develops through interaction.
Each NPC has their own memory of interactions with Aleister.

Generation uses Sicilian naming conventions, realistic occupations,
and character traits shaped by random seed + context of meeting.
"""

from __future__ import annotations
import hashlib
import json
import random
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional
import logging

logger = logging.getLogger(__name__)

# Realistic Cefalù name pools
FIRST_NAMES_M = ["Giuseppe", "Salvatore", "Antonio", "Marco", "Giovanni", "Luca", "Pietro",
                  "Francesco", "Rosario", "Vincenzo", "Carmelo", "Stefano", "Ignazio",
                  "Angelo", "Filippo", "Paolo", "Matteo", "Andrea", "Roberto", "Nunzio"]
FIRST_NAMES_F = ["Maria", "Rosa", "Concetta", "Angela", "Giuseppina", "Lucia", "Francesca",
                  "Anna", "Carmela", "Silvana", "Valentina", "Giovanna", "Patrizia",
                  "Teresa", "Serena", "Elena", "Claudia", "Daniela", "Margherita", "Chiara"]
LAST_NAMES = ["Ferrara", "Seminara", "Ortoleva", "Carollo", "Di Garbo", "Mazzola", "Cimino",
              "Cirincione", "La Barbera", "Ferrante", "Randazzo", "Vitale", "Grasso",
              "Lombardo", "Amato", "Russo", "Rizzo", "Puglisi", "Battaglia", "Cataldo"]
OCCUPATIONS = [
    "fisherman", "baker", "pharmacist", "schoolteacher", "bartender", "mechanic",
    "butcher", "olive farmer", "ceramicist", "priest", "nun", "retired carabiniere",
    "restaurant owner", "boat mechanic", "street vendor", "tailor", "florist",
    "wine merchant", "postman", "taxi driver", "hotel receptionist", "diver",
    "carpenter", "stone mason", "painter (houses)", "electrician", "plumber",
    "tourist guide", "library volunteer", "market stallholder", "cheese maker",
]
PERSONALITY_AXES = [
    ("warm", "cold"), ("talkative", "quiet"), ("trusting", "suspicious"),
    ("traditional", "modern"), ("generous", "frugal"), ("patient", "impatient"),
    ("curious", "incurious"), ("humorous", "serious"), ("bold", "timid"),
]
QUIRKS = [
    "always wears a hat", "speaks with hands more than words", "whistles while walking",
    "has a dog that follows everywhere", "collects seashells", "knows everyone's birthday",
    "has a strong opinion about pasta shapes", "tells the same three stories",
    "reads the obituaries every morning", "smells of basil", "wakes before dawn",
    "has never left Sicily", "claims to see ghosts", "makes their own limoncello",
    "feeds every stray cat", "sings while working", "always carries a knife (for fruit)",
    "has a gold tooth", "remembers the old lira prices", "plays cards every evening",
]


@dataclass
class NPCMemory:
    """An NPC's memory of Aleister."""
    interactions: list[dict] = field(default_factory=list)  # {timestamp, what_happened, aleister_mood}
    trust_level: float = 0.0  # -1 (hostile) to +1 (trusted friend)
    familiarity: int = 0  # Number of encounters
    last_seen: float = 0.0
    opinion: str = ""  # Their evolving opinion of Aleister

    def record(self, what: str, aleister_mood: str = ""):
        self.interactions.append({
            "ts": time.time(), "what": what[:200], "mood": aleister_mood,
        })
        if len(self.interactions) > 50:
            self.interactions = self.interactions[-50:]
        self.familiarity += 1
        self.last_seen = time.time()

    def to_dict(self) -> dict:
        return {
            "interactions": self.interactions[-20:],
            "trust": round(self.trust_level, 2),
            "familiarity": self.familiarity,
            "last_seen": self.last_seen,
            "opinion": self.opinion,
        }


@dataclass
class NPC:
    """A person in Cefalù. Generated, not scripted."""
    id: str
    first_name: str
    last_name: str
    gender: str
    age: int
    occupation: str
    traits: dict  # {axis: value} e.g. {"warm": 0.7, "talkative": 0.3}
    quirk: str
    location_met: str  # Where Aleister first encountered them
    appearance: str  # Brief physical description
    memory: NPCMemory = field(default_factory=NPCMemory)
    created_at: float = field(default_factory=time.time)

    @property
    def full_name(self) -> str:
        return f"{self.first_name} {self.last_name}"

    @property
    def summary(self) -> str:
        trait_strs = [f"{k}={v:.1f}" for k, v in sorted(self.traits.items())]
        return (
            f"{self.full_name}, {self.age}, {self.occupation}. "
            f"{self.quirk}. Traits: {', '.join(trait_strs[:4])}. "
            f"Met at: {self.location_met}. Encounters: {self.memory.familiarity}."
        )

    def to_dict(self) -> dict:
        return {
            "id": self.id, "first": self.first_name, "last": self.last_name,
            "gender": self.gender, "age": self.age, "occ": self.occupation,
            "traits": {k: round(v, 2) for k, v in self.traits.items()},
            "quirk": self.quirk, "loc": self.location_met,
            "appearance": self.appearance, "memory": self.memory.to_dict(),
            "created": self.created_at,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "NPC":
        mem = NPCMemory(**{k: v for k, v in d.get("memory", {}).items()
                          if k in NPCMemory.__dataclass_fields__})
        return cls(
            id=d["id"], first_name=d["first"], last_name=d["last"],
            gender=d["gender"], age=d["age"], occupation=d["occ"],
            traits=d.get("traits", {}), quirk=d.get("quirk", ""),
            location_met=d.get("loc", ""), appearance=d.get("appearance", ""),
            memory=mem, created_at=d.get("created", 0),
        )


def generate_npc(location: str = "Cefalù", seed: str = "") -> NPC:
    """Generate a random Cefalù resident."""
    rng = random.Random(seed or f"npc_{time.time()}")
    gender = rng.choice(["m", "f"])
    first = rng.choice(FIRST_NAMES_M if gender == "m" else FIRST_NAMES_F)
    last = rng.choice(LAST_NAMES)
    age = rng.randint(18, 88)
    occ = rng.choice(OCCUPATIONS)
    quirk = rng.choice(QUIRKS)

    traits = {}
    for pos, neg in PERSONALITY_AXES:
        val = rng.gauss(0.5, 0.2)  # Normal distribution centered at 0.5
        val = max(0.0, min(1.0, val))
        traits[pos] = round(val, 2)

    # Age-appropriate appearance
    build = rng.choice(["thin", "stocky", "wiry", "broad-shouldered", "slight"])
    feature = rng.choice([
        "deep-set eyes", "calloused hands", "sun-darkened skin",
        "silver hair", "a weathered face", "sharp cheekbones",
        "a broad smile", "a permanent squint from the sea-light",
        "thick eyebrows", "a nose that's been broken once",
    ])
    appearance = f"{build}, {feature}"

    npc_id = hashlib.md5(f"{first}{last}{age}{occ}".encode()).hexdigest()[:8]

    return NPC(
        id=npc_id, first_name=first, last_name=last, gender=gender,
        age=age, occupation=occ, traits=traits, quirk=quirk,
        location_met=location, appearance=appearance,
    )


class EncounterEngine:
    """Manages all NPCs Aleister has met."""

    def __init__(self, data_dir: str = ""):
        self._dir = Path(data_dir or ".") / "soul" / "encounters"
        self._dir.mkdir(parents=True, exist_ok=True)
        self._npcs: dict[str, NPC] = {}
        self._load()

    def _load(self):
        path = self._dir / "npcs.json"
        if path.exists():
            try:
                data = json.loads(path.read_text())
                for d in data:
                    npc = NPC.from_dict(d)
                    self._npcs[npc.id] = npc
            except Exception as e:
                logger.warning(f"Failed to load encounters: {e}")

    def _save(self):
        data = [npc.to_dict() for npc in self._npcs.values()]
        (self._dir / "npcs.json").write_text(json.dumps(data, ensure_ascii=False, indent=2))

    def meet_someone(self, location: str = "Cefalù") -> NPC:
        """Aleister encounters a new person. Returns the generated NPC."""
        npc = generate_npc(location, seed=f"meet_{location}_{time.time()}")
        self._npcs[npc.id] = npc
        self._save()
        logger.info(f"New encounter: {npc.full_name}, {npc.age}, {npc.occupation}")
        return npc

    def interact(self, npc_id: str, what: str, aleister_mood: str = "",
                 trust_delta: float = 0.0) -> Optional[NPC]:
        """Record an interaction with a known NPC."""
        npc = self._npcs.get(npc_id)
        if not npc:
            return None
        npc.memory.record(what, aleister_mood)
        npc.memory.trust_level = max(-1.0, min(1.0, npc.memory.trust_level + trust_delta))
        self._save()
        return npc

    def get_npc(self, npc_id: str) -> Optional[NPC]:
        return self._npcs.get(npc_id)

    def find_by_name(self, name: str) -> Optional[NPC]:
        needle = name.lower()
        for npc in self._npcs.values():
            if needle in npc.full_name.lower():
                return npc
        return None

    def list_known(self, limit: int = 20) -> list[NPC]:
        """List NPCs sorted by familiarity."""
        npcs = sorted(self._npcs.values(),
                      key=lambda n: n.memory.familiarity, reverse=True)
        return npcs[:limit]

    def get_prompt_context(self) -> str:
        """Brief summary of known people for system prompt."""
        known = self.list_known(limit=5)
        if not known:
            return "You haven't met anyone in Cefalù yet."
        lines = ["People you know:"]
        for npc in known:
            trust = "trusted" if npc.memory.trust_level > 0.5 else (
                "wary" if npc.memory.trust_level < -0.3 else "acquaintance")
            lines.append(f"  {npc.full_name} ({npc.occupation}, {trust}, met {npc.memory.familiarity}x)")
        return "\n".join(lines)

    def get_stats(self) -> dict:
        return {
            "total_known": len(self._npcs),
            "most_familiar": self.list_known(1)[0].full_name if self._npcs else "nobody",
            "occupations": list(set(n.occupation for n in self._npcs.values())),
        }


_encounter_engine: Optional[EncounterEngine] = None

def get_encounter_engine(data_dir: str = "") -> EncounterEngine:
    global _encounter_engine
    if _encounter_engine is None:
        _encounter_engine = EncounterEngine(data_dir)
    return _encounter_engine
