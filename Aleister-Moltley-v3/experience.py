"""Experience System — The bot learns from its own actions.

Three layers:
1. Self-Evaluation — After each task, rate own success and extract lessons
2. Strategy Memory — Store what tool sequences worked for what task types
3. Feedback Loop — User thumbs up/down refines the experience weights

Storage: JSON files in ~/.compagnon/experience/
"""

from __future__ import annotations

import json
import time
import hashlib
from pathlib import Path
from typing import Optional

import logging
logger = logging.getLogger(__name__)

MAX_LESSONS = 100
MAX_PROMPT_LESSONS = 8

SELF_EVAL_PROMPT = """Evaluate the task you just completed. Respond ONLY with JSON (no markdown):
{{"outcome": "success" or "partial" or "failure", "lesson": "one sentence what worked or what to do differently", "tags": ["task_type"], "error": "error if any", "fix": "how you fixed it"}}

Task: {task}
Tools used: {tools}
Result snippet: {result}"""


class ExperienceStore:
    def __init__(self, data_dir: str = ""):
        self._dir = Path(data_dir or Path.home() / ".compagnon") / "experience"
        self._dir.mkdir(parents=True, exist_ok=True)
        self._lessons: list[dict] = self._load("lessons.json", [])
        self._strategies: dict[str, dict] = self._load("strategies.json", {})

    def _load(self, name: str, default):
        p = self._dir / name
        if p.exists():
            try: return json.loads(p.read_text())
            except Exception: pass
        return default

    def _save(self, name: str, data):
        (self._dir / name).write_text(json.dumps(data, indent=2, ensure_ascii=False))

    def record_lesson(self, task_summary: str, outcome: str, lesson: str,
                      tool_sequence: list[str] = None, error: str = "", fix: str = "",
                      tags: list[str] = None) -> str:
        lid = hashlib.md5(f"{task_summary}{time.time()}".encode()).hexdigest()[:10]
        entry = {
            "id": lid, "task": task_summary[:200], "outcome": outcome,
            "lesson": lesson[:500], "tools": tool_sequence or [],
            "error": error[:200], "fix": fix[:200],
            "score": 1.0 if outcome == "success" else (0.3 if outcome == "partial" else -0.5),
            "ts": time.time(), "tags": tags or [],
        }
        self._lessons.append(entry)
        self._lessons = self._lessons[-MAX_LESSONS:]
        self._save("lessons.json", self._lessons)

        if tool_sequence:
            self._update_strategy(tags or ["general"], tool_sequence, outcome == "success")
        return lid

    def _update_strategy(self, tags: list[str], tools: list[str], success: bool):
        for tag in tags:
            key = tag.lower()
            if key not in self._strategies:
                self._strategies[key] = {"sequences": {}, "ok": 0, "fail": 0}
            s = self._strategies[key]
            seq = "→".join(tools[:6])
            if seq not in s["sequences"]:
                s["sequences"][seq] = {"ok": 0, "fail": 0}
            s["sequences"][seq]["ok" if success else "fail"] += 1
            s["ok" if success else "fail"] += 1
        self._save("strategies.json", self._strategies)

    def record_feedback(self, lesson_id: str, positive: bool):
        delta = 0.3 if positive else -0.3
        for l in self._lessons:
            if l.get("id") == lesson_id:
                l["score"] = max(-1.0, min(1.0, l.get("score", 0) + delta))
                break
        self._save("lessons.json", self._lessons)

    def get_relevant_lessons(self, query: str, limit: int = MAX_PROMPT_LESSONS) -> list[dict]:
        if not self._lessons:
            return []
        qw = set(query.lower().split())
        scored = []
        for l in self._lessons:
            r = 0.0
            lw = set(l.get("task", "").lower().split())
            r += len(qw & lw) * 2.0
            for tag in l.get("tags", []):
                if tag.lower() in query.lower(): r += 3.0
            if l.get("error") and any(w in query.lower() for w in ["error", "fail", "bug", "fix"]):
                r += 2.0
            age = (time.time() - l.get("ts", 0)) / 86400
            r += max(0, 1.0 - age / 30)
            r *= max(0.1, 0.5 + l.get("score", 0))
            if r > 0.5:
                scored.append((r, l))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [l for _, l in scored[:limit]]

    def get_best_strategy(self, task_type: str) -> Optional[str]:
        s = self._strategies.get(task_type.lower())
        if not s: return None
        best_seq, best_rate = None, 0
        for seq, c in s.get("sequences", {}).items():
            total = c["ok"] + c["fail"]
            if total >= 2:
                rate = c["ok"] / total
                if rate > best_rate:
                    best_rate = rate
                    best_seq = seq
        if best_seq and best_rate > 0.5:
            return f"Recommended ({best_rate:.0%} success): {best_seq}"
        return None

    def get_prompt_context(self, query: str = "") -> str:
        parts = []
        lessons = self.get_relevant_lessons(query)
        if lessons:
            parts.append("<learned>")
            for l in lessons:
                icon = "✓" if l["outcome"] == "success" else "✗"
                parts.append(f"[{icon}] {l['lesson']}")
                if l.get("error") and l.get("fix"):
                    parts.append(f"  {l['error']} → {l['fix']}")
            parts.append("</learned>")
        if query:
            for tt in self._strategies:
                if tt in query.lower():
                    hint = self.get_best_strategy(tt)
                    if hint:
                        parts.append(f"<strategy>{hint}</strategy>")
                    break
        return "\n".join(parts)

    def get_stats(self) -> dict:
        return {
            "lessons": len(self._lessons),
            "positive": sum(1 for l in self._lessons if l.get("score", 0) > 0),
            "negative": sum(1 for l in self._lessons if l.get("score", 0) < 0),
            "strategies": len(self._strategies),
        }


_store: Optional[ExperienceStore] = None

def get_experience_store(data_dir: str = "") -> ExperienceStore:
    global _store
    if _store is None:
        _store = ExperienceStore(data_dir)
    return _store
