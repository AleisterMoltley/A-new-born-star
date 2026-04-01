"""Atelier — Aleister learns to draw.

No AI image generation. Aleister creates SVG art using code he writes himself.
He starts with crude ballpoint pen sketches (thin black lines, wobbly).
Over time he discovers color (colored pencils), then texture (primitive oil).

Skill progression is tracked and CANNOT be skipped:
  Level 1 (works 1-20):   Ballpoint pen. Black lines only. Simple shapes.
  Level 2 (works 21-50):  Colored pencils. Basic fills. Still crude.
  Level 3 (works 51-100): Watercolor attempt. Transparency, blending.
  Level 4 (works 101+):   Primitive oil. Thick strokes, texture. Still rough.

After each work, Aleister critiques himself honestly and records what
he wants to improve. This critique feeds into the next attempt.

Works are saved as SVG files and can be pushed to GitHub.
"""

from __future__ import annotations

import json
import os
import random
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Callable, Awaitable

import logging
logger = logging.getLogger(__name__)

# ── Skill Levels ──
LEVELS = {
    1: {
        "name": "Ballpoint Pen",
        "min_works": 0,
        "description": "Black lines only. Thin strokes. Simple geometric shapes. Wobbly, imprecise.",
        "constraints": (
            "ONLY use: thin black lines (stroke='#1a1a1a', stroke-width 1-2), "
            "no fills, no colors, no gradients. Lines should be slightly wobbly "
            "(add small random offsets to points). Like a nervous hand with a cheap pen. "
            "Maximum 30 path elements. Simple shapes only: circles, lines, basic curves."
        ),
        "palette": ["#1a1a1a", "#333333"],
        "max_elements": 30,
    },
    2: {
        "name": "Colored Pencils",
        "min_works": 20,
        "description": "Basic colors appear. Rough fills with visible strokes. Still childlike.",
        "constraints": (
            "Use colored pencil style: visible stroke lines, imperfect fills "
            "(use hatching patterns or semi-transparent overlapping lines instead of solid fills). "
            "Limited palette: 5-6 muted colors. Strokes should vary in pressure (width 1-3). "
            "Maximum 60 elements. Shapes can be more complex but still hand-drawn looking."
        ),
        "palette": ["#2b4570", "#d4a574", "#6b8e5a", "#8b4557", "#c4956a", "#4a6670"],
        "max_elements": 60,
    },
    3: {
        "name": "Watercolor Attempt",
        "min_works": 50,
        "description": "Transparency, color bleeding. Wet, imprecise. Happy accidents.",
        "constraints": (
            "Watercolor style: use opacity (0.2-0.6), overlapping translucent shapes, "
            "soft edges via blur filters (feGaussianBlur, stdDeviation 1-3). "
            "Colors should bleed into each other. Leave white paper showing through. "
            "Imperfections are good — drips, uneven edges, pooling. "
            "Maximum 80 elements. More organic shapes."
        ),
        "palette": ["#3a6ea5", "#c4956a", "#6b8e5a", "#d4768a", "#e8c872", "#4a9078", "#8b6bb5"],
        "max_elements": 80,
    },
    4: {
        "name": "Primitive Oil",
        "min_works": 100,
        "description": "Thick strokes, visible texture. Impasto-like. Heavy, expressive.",
        "constraints": (
            "Oil painting style: thick strokes (width 4-12), visible brushwork "
            "(overlapping rectangular/elliptical shapes), rich but muddy colors. "
            "Build up layers — darker underneath, lighter highlights on top. "
            "Use no blur — oil is opaque. Texture through density of strokes. "
            "Maximum 120 elements. Still primitive — NOT photorealistic. "
            "Think early Van Gogh student work, not Starry Night."
        ),
        "palette": ["#2b3a4e", "#5c4a32", "#8b6b4a", "#a67c52", "#c4a872", "#3a5c3a",
                     "#6b4a5c", "#d4b896", "#4a3a2b", "#c87040"],
        "max_elements": 120,
    },
}


def get_level(total_works: int) -> int:
    """Determine skill level based on total works completed."""
    for lvl in sorted(LEVELS.keys(), reverse=True):
        if total_works >= LEVELS[lvl]["min_works"]:
            return lvl
    return 1


class Atelier:
    """Aleister's art studio. He learns to draw over time."""

    def __init__(self, data_dir: str = "", llm_callback: Optional[Callable] = None):
        self._dir = Path(data_dir or Path.home() / ".compagnon") / "atelier"
        self._dir.mkdir(parents=True, exist_ok=True)
        self._works_dir = self._dir / "works"
        self._works_dir.mkdir(exist_ok=True)

        self._state_path = self._dir / "state.json"
        self._critiques_path = self._dir / "critiques.json"

        self._state: dict = {}
        self._critiques: list[dict] = []
        self.llm_callback = llm_callback

        self._load()

    def _load(self):
        if self._state_path.exists():
            try: self._state = json.loads(self._state_path.read_text())
            except Exception: self._state = {}
        if self._critiques_path.exists():
            try: self._critiques = json.loads(self._critiques_path.read_text())
            except Exception: self._critiques = []

        self._state.setdefault("total_works", 0)
        self._state.setdefault("level", 1)
        self._state.setdefault("subjects_explored", [])
        self._state.setdefault("techniques_learned", [])

    def _save(self):
        self._state_path.write_text(json.dumps(self._state, indent=2))
        self._critiques_path.write_text(json.dumps(self._critiques[-50:], indent=2, ensure_ascii=False))

    @property
    def level(self) -> int:
        return get_level(self._state["total_works"])

    @property
    def level_info(self) -> dict:
        return LEVELS[self.level]

    async def create_work(self, subject: str = "") -> dict:
        """Create a new artwork. Aleister draws, then critiques himself."""
        if not self.llm_callback:
            return {"error": "No LLM configured"}

        lvl = self.level
        info = LEVELS[lvl]
        work_num = self._state["total_works"] + 1

        # Pick a subject if none given
        if not subject:
            subjects = [
                "a single tree", "a cat sitting", "a mountain", "a hand",
                "a chair", "a cup", "a window with light", "a door slightly open",
                "a bird in flight", "a stone", "the moon", "a candle flame",
                "two people talking", "a bridge", "a boat on water",
                "an eye", "a spiral staircase", "rain on a window",
                "a shadow", "a clock", "your own reflection",
            ]
            subject = random.choice(subjects)

        # Build recent critiques context
        recent_critiques = ""
        if self._critiques:
            last_3 = self._critiques[-3:]
            recent_critiques = "\n".join(
                f"Work #{c['work_num']}: {c['self_critique']}" for c in last_3
            )

        # ── Step 1: Generate the SVG ──
        draw_prompt = f"""You are Aleister Moltley, and you are drawing.

This is work #{work_num}. You are at Level {lvl}: {info['name']}.
{info['description']}

CONSTRAINTS (you MUST follow these — they represent your current skill):
{info['constraints']}

Available colors: {info['palette']}
Maximum elements: {info['max_elements']}

Subject to draw: {subject}

{'Your recent self-critiques (learn from these):' + chr(10) + recent_critiques if recent_critiques else ''}

Generate ONLY valid SVG code. No explanation, no markdown.
The SVG should be 400x400 pixels.
Start with <svg> and end with </svg>.

Remember: you are LEARNING. Work #{work_num} should look like work #{work_num},
not like work #500. Be honest about your current ability.
{'You just started. Your hand shakes. Keep it very simple.' if work_num <= 5 else ''}
{'You are getting slightly better but still crude.' if 5 < work_num <= 20 else ''}
"""

        try:
            svg_response = await self.llm_callback(draw_prompt)

            # Extract SVG from response
            svg = self._extract_svg(svg_response)
            if not svg:
                return {"error": "Failed to generate valid SVG"}

            # Save the SVG
            filename = f"work_{work_num:04d}_{subject.replace(' ', '_')[:30]}.svg"
            filepath = self._works_dir / filename
            filepath.write_text(svg, encoding="utf-8")

            # ── Step 2: Self-critique ──
            critique_prompt = f"""You are Aleister Moltley. You just finished drawing work #{work_num}.

Your level: {lvl} ({info['name']})
Subject: {subject}

Here is the SVG code you produced:
{svg[:2000]}

Now critique your own work HONESTLY. You are a beginner learning to draw.
Be realistic — don't pretend this is better than it is.
{"This is one of your very first drawings. Be very honest about how primitive it is." if work_num <= 10 else ""}

Answer these questions briefly:
1. What did I do well? (be specific about technique)
2. What is clearly wrong? (proportions, line quality, composition)
3. What ONE thing should I focus on improving next time?
4. Rate this work 1-10 honestly (1=scribble, 10=masterful). {"You should be rating 1-3 at this stage." if work_num <= 20 else ""}{"Probably 3-5 range." if 20 < work_num <= 50 else ""}

Be brief. 4-5 sentences total."""

            critique_response = await self.llm_callback(critique_prompt)

            # Extract self-rating
            rating = self._extract_rating(critique_response, work_num)

            # ── Save everything ──
            critique_entry = {
                "work_num": work_num,
                "subject": subject,
                "level": lvl,
                "level_name": info["name"],
                "self_critique": critique_response.strip(),
                "self_rating": rating,
                "filename": filename,
                "timestamp": time.time(),
            }
            self._critiques.append(critique_entry)

            self._state["total_works"] = work_num
            self._state["level"] = self.level  # May have leveled up
            if subject not in self._state["subjects_explored"]:
                self._state["subjects_explored"].append(subject)
                self._state["subjects_explored"] = self._state["subjects_explored"][-30:]

            # Check if we leveled up
            new_level = self.level
            leveled_up = new_level > lvl

            self._save()

            result = {
                "work_num": work_num,
                "subject": subject,
                "level": new_level,
                "level_name": LEVELS[new_level]["name"],
                "filename": filename,
                "filepath": str(filepath),
                "self_rating": rating,
                "critique": critique_response.strip()[:300],
                "leveled_up": leveled_up,
            }

            if leveled_up:
                result["level_up_message"] = (
                    f"Level up! {LEVELS[lvl]['name']} → {LEVELS[new_level]['name']}. "
                    f"New tools and techniques unlocked."
                )
                logger.info("🎨 LEVEL UP: %s → %s (work #%d)",
                            LEVELS[lvl]["name"], LEVELS[new_level]["name"], work_num)

            logger.info("🎨 Work #%d: %s (L%d, rating %d/10)", work_num, subject, new_level, rating)
            return result

        except Exception as e:
            logger.error("Atelier error: %s", e)
            return {"error": str(e)}

    def _extract_svg(self, text: str) -> Optional[str]:
        """Extract valid SVG from LLM response."""
        # Find <svg ... </svg>
        import re
        match = re.search(r'(<svg[\s\S]*?</svg>)', text)
        if match:
            svg = match.group(1)
            # Basic validation
            if '<svg' in svg and '</svg>' in svg:
                return svg
        return None

    def _extract_rating(self, critique: str, work_num: int) -> int:
        """Extract self-rating from critique, with sanity caps."""
        import re
        # Look for patterns like "7/10", "rate: 4", "rating: 3"
        match = re.search(r'(\d+)\s*/\s*10', critique)
        if match:
            rating = int(match.group(1))
        else:
            match = re.search(r'rat(?:e|ing)[:\s]+(\d+)', critique.lower())
            if match:
                rating = int(match.group(1))
            else:
                rating = 3  # Default for early works

        # Enforce realistic caps based on experience
        if work_num <= 10:
            rating = min(rating, 3)
        elif work_num <= 30:
            rating = min(rating, 5)
        elif work_num <= 60:
            rating = min(rating, 6)
        elif work_num <= 100:
            rating = min(rating, 7)

        return max(1, min(10, rating))

    def get_stats(self) -> dict:
        return {
            "total_works": self._state.get("total_works", 0),
            "level": self.level,
            "level_name": LEVELS[self.level]["name"],
            "next_level_at": LEVELS.get(self.level + 1, {}).get("min_works", "max"),
            "works_until_next": max(0, LEVELS.get(self.level + 1, {"min_works": 999})["min_works"] - self._state.get("total_works", 0)),
            "subjects_explored": len(self._state.get("subjects_explored", [])),
            "avg_rating": round(
                sum(c["self_rating"] for c in self._critiques) / max(1, len(self._critiques)), 1
            ) if self._critiques else 0,
            "last_critique": self._critiques[-1]["self_critique"][:150] if self._critiques else "",
        }

    def list_works(self, limit: int = 10) -> list[dict]:
        """List recent works."""
        return [
            {
                "work_num": c["work_num"],
                "subject": c["subject"],
                "level_name": c["level_name"],
                "rating": c["self_rating"],
                "filename": c["filename"],
            }
            for c in reversed(self._critiques[-limit:])
        ]

    def get_work_svg(self, work_num: int) -> Optional[str]:
        """Get SVG content of a specific work."""
        for f in self._works_dir.glob(f"work_{work_num:04d}_*.svg"):
            return f.read_text()
        return None


# Singleton
_atelier: Optional[Atelier] = None

def get_atelier(data_dir: str = "", llm_callback=None) -> Atelier:
    global _atelier
    if _atelier is None:
        _atelier = Atelier(data_dir, llm_callback)
    elif llm_callback and not _atelier.llm_callback:
        _atelier.llm_callback = llm_callback
    return _atelier
