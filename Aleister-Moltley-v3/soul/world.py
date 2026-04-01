"""
World — Cefalù as Aleister experiences it.

Not a pre-mapped city. A world that reveals itself through exploration.
Places are discovered, not listed. He starts knowing only his palazzo
and the path to the town center.

Uses Google Maps place_search when exploring new areas.
Falls back to curated seed locations for offline use.
"""

from __future__ import annotations
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional
import logging

logger = logging.getLogger(__name__)

# Only the starting knowledge — everything else is discovered
SEED_PLACES = [
    {"id": "palazzo", "name": "Palazzo Moltley (Abbey of Thelema)", "type": "home",
     "lat": 38.0355, "lon": 14.0255, "discovered": True,
     "notes": "Home. The restored villa on the site of Crowley's Abbey."},
    {"id": "path_to_town", "name": "The path from Contrada Santa Barbara to town", "type": "road",
     "lat": 38.037, "lon": 14.024, "discovered": True,
     "notes": "The steep path down to Cefalù, opposite the cemetery."},
]


@dataclass
class Place:
    """A discovered location in Aleister's world."""
    id: str
    name: str
    place_type: str  # home, street, cafe, shop, church, beach, etc.
    lat: float
    lon: float
    discovered: bool = False
    discovered_at: float = 0.0
    visit_count: int = 0
    last_visit: float = 0.0
    notes: str = ""  # Aleister's own notes about this place
    impression_ids: list[str] = field(default_factory=list)  # Linked impressions
    npcs_met_here: list[str] = field(default_factory=list)  # NPC IDs

    def to_dict(self) -> dict:
        return {
            "id": self.id, "name": self.name, "type": self.place_type,
            "lat": self.lat, "lon": self.lon, "discovered": self.discovered,
            "discovered_at": self.discovered_at, "visits": self.visit_count,
            "last_visit": self.last_visit, "notes": self.notes[:200],
            "impressions": self.impression_ids[-10:],
            "npcs": self.npcs_met_here[-10:],
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Place":
        return cls(
            id=d["id"], name=d["name"], place_type=d.get("type", ""),
            lat=d.get("lat", 0), lon=d.get("lon", 0),
            discovered=d.get("discovered", False),
            discovered_at=d.get("discovered_at", 0),
            visit_count=d.get("visits", 0), last_visit=d.get("last_visit", 0),
            notes=d.get("notes", ""),
            impression_ids=d.get("impressions", []),
            npcs_met_here=d.get("npcs", []),
        )


class World:
    """Aleister's known world. Grows through exploration."""

    def __init__(self, data_dir: str = ""):
        self._dir = Path(data_dir or ".") / "soul" / "world"
        self._dir.mkdir(parents=True, exist_ok=True)
        self._places: dict[str, Place] = {}
        self._load()

    def _load(self):
        path = self._dir / "places.json"
        if path.exists():
            try:
                data = json.loads(path.read_text())
                for d in data:
                    p = Place.from_dict(d)
                    self._places[p.id] = p
            except Exception as e:
                logger.warning(f"Failed to load world: {e}")

        # Ensure seed places exist
        for seed in SEED_PLACES:
            if seed["id"] not in self._places:
                self._places[seed["id"]] = Place(
                    id=seed["id"], name=seed["name"], place_type=seed["type"],
                    lat=seed["lat"], lon=seed["lon"],
                    discovered=seed.get("discovered", False),
                    discovered_at=time.time() if seed.get("discovered") else 0,
                    notes=seed.get("notes", ""),
                )
        self._save()

    def _save(self):
        data = [p.to_dict() for p in self._places.values()]
        (self._dir / "places.json").write_text(json.dumps(data, ensure_ascii=False, indent=2))

    def discover(self, name: str, place_type: str, lat: float, lon: float,
                 notes: str = "", place_id: str = "") -> Place:
        """Aleister discovers a new place."""
        pid = place_id or name.lower().replace(" ", "_")[:20]
        if pid in self._places:
            # Already known — just visit
            return self.visit(pid, notes)

        place = Place(
            id=pid, name=name, place_type=place_type,
            lat=lat, lon=lon, discovered=True,
            discovered_at=time.time(), visit_count=1,
            last_visit=time.time(), notes=notes,
        )
        self._places[pid] = place
        self._save()
        logger.info(f"New place discovered: {name}")
        return place

    def visit(self, place_id: str, notes: str = "") -> Optional[Place]:
        """Aleister visits a known place."""
        place = self._places.get(place_id)
        if not place:
            return None
        place.visit_count += 1
        place.last_visit = time.time()
        if notes:
            place.notes = notes[:200]
        self._save()
        return place

    def get_place(self, place_id: str) -> Optional[Place]:
        return self._places.get(place_id)

    def find_by_name(self, name: str) -> Optional[Place]:
        needle = name.lower()
        for p in self._places.values():
            if needle in p.name.lower():
                return p
        return None

    def list_discovered(self, limit: int = 20) -> list[Place]:
        discovered = [p for p in self._places.values() if p.discovered]
        discovered.sort(key=lambda p: p.last_visit, reverse=True)
        return discovered[:limit]

    def get_prompt_context(self) -> str:
        """Summary of known world for system prompt."""
        places = self.list_discovered(limit=8)
        if len(places) <= 2:
            return (
                "You know only your palazzo and the path to town. "
                "Cefalù is waiting to be explored."
            )
        place_list = ", ".join(p.name for p in places[:6])
        return f"Places you know in Cefalù ({len(places)}): {place_list}"

    def get_stats(self) -> dict:
        discovered = [p for p in self._places.values() if p.discovered]
        return {
            "places_discovered": len(discovered),
            "total_visits": sum(p.visit_count for p in discovered),
            "most_visited": max(discovered, key=lambda p: p.visit_count).name if discovered else "nowhere",
            "types": list(set(p.place_type for p in discovered)),
        }


_world: Optional[World] = None

def get_world(data_dir: str = "") -> World:
    global _world
    if _world is None:
        _world = World(data_dir)
    return _world
