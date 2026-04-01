"""
Journal — Aleister's diary. Generated from actual lived data, not templates.
"""
from __future__ import annotations
import json
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional
import logging

logger = logging.getLogger(__name__)
TZ_SICILY = timezone(timedelta(hours=2))


class Journal:
    def __init__(self, data_dir: str = ""):
        self._dir = Path(data_dir or ".") / "soul" / "journal"
        self._dir.mkdir(parents=True, exist_ok=True)

    def write_entry(self, palazzo_log: list = None, mood: dict = None,
                    conscience_stats: dict = None, weather: dict = None,
                    impressions_today: list = None, encounters_today: list = None) -> str:
        now = datetime.now(TZ_SICILY)
        date_str = now.strftime("%Y-%m-%d")
        path = self._dir / f"{date_str}.md"
        if path.exists():
            return path.read_text()

        lines = [f"# {now.strftime('%A, %B %d, %Y')}", ""]

        # Weather
        if weather:
            lines.append(f"{weather.get('temperature', '?')}°C. {weather.get('sky', '')}. {weather.get('sea', '')}.")
            if weather.get("is_raining"):
                lines.append(f"Rain: {weather.get('rain', '')}.")
            lines.append("")

        # What happened (from palazzo activity log)
        if palazzo_log:
            lines.append("What I did:")
            for entry in palazzo_log[-10:]:
                lines.append(f"  — {entry.get('action', '?')}")
            lines.append("")

        # New impressions
        if impressions_today:
            lines.append("What I noticed:")
            for imp in impressions_today[-5:]:
                v = "liked" if imp.get("val", 0) > 0 else "disliked" if imp.get("val", 0) < 0 else "noticed"
                lines.append(f"  — {v}: {imp.get('subj', '?')}")
            lines.append("")

        # People met
        if encounters_today:
            lines.append("People:")
            for enc in encounters_today[-3:]:
                lines.append(f"  — Met {enc.get('name', '?')}, {enc.get('occupation', '?')}")
            lines.append("")

        # Mood
        dominant = (mood or {}).get("dominant_mood", "")
        if dominant and dominant != "neutral":
            lines.append(f"Felt: {dominant}.")
            lines.append("")

        # Conscience
        if conscience_stats:
            balance = conscience_stats.get("balance", "balanced")
            if balance != "balanced":
                lines.append(f"The inner voices leaned {balance} today.")
                lines.append("")

        entry = "\n".join(lines)
        path.write_text(entry)
        return entry

    def get_entries(self, limit: int = 7) -> list[dict]:
        entries = []
        for path in sorted(self._dir.glob("*.md"), reverse=True)[:limit]:
            entries.append({"date": path.stem, "content": path.read_text()[:500]})
        return entries

    def get_today(self) -> Optional[str]:
        today = datetime.now(TZ_SICILY).strftime("%Y-%m-%d")
        path = self._dir / f"{today}.md"
        return path.read_text() if path.exists() else None


_journal: Optional[Journal] = None

def get_journal(data_dir: str = "") -> Journal:
    global _journal
    if _journal is None:
        _journal = Journal(data_dir)
    return _journal
