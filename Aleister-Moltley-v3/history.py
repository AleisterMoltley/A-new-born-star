"""
History Log — Session-level event audit trail.

Ported from claw-code's history.py:
- Records what happened during a session (routing, execution, errors, compaction)
- Structured events with timestamps
- Markdown export for debugging
- Queryable by event type

Used by the runtime to build a complete session narrative for debugging.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field


@dataclass(frozen=True)
class HistoryEvent:
    """Single recorded event in the session history."""
    category: str       # e.g. "routing", "tool_exec", "compact", "error", "permission"
    title: str          # Short description
    detail: str         # Full detail
    timestamp: float = field(default_factory=time.time)

    def as_line(self) -> str:
        return f"[{self.category}] {self.title}: {self.detail}"


@dataclass
class HistoryLog:
    """Append-only event log for a single session."""
    events: list[HistoryEvent] = field(default_factory=list)
    session_id: str = ""

    def add(self, category: str, title: str, detail: str = "") -> HistoryEvent:
        event = HistoryEvent(category=category, title=title, detail=detail)
        self.events.append(event)
        return event

    def add_tool_call(self, tool_name: str, success: bool, detail: str = ""):
        status = "ok" if success else "error"
        self.add("tool_exec", f"{tool_name} [{status}]", detail)

    def add_routing(self, matched_tools: list[str], prompt_preview: str = ""):
        self.add("routing", f"matched={len(matched_tools)}", 
                 f"tools={','.join(matched_tools[:5])} prompt={prompt_preview[:80]}")

    def add_compact(self, before_count: int, after_count: int, tokens_saved: int = 0):
        self.add("compact", f"{before_count}→{after_count} msgs", 
                 f"~{tokens_saved:,} tokens freed")

    def add_error(self, source: str, error: str):
        self.add("error", source, error[:200])

    def add_permission_denial(self, tool_name: str, reason: str):
        self.add("permission", f"denied: {tool_name}", reason)

    def filter_by(self, category: str) -> list[HistoryEvent]:
        return [e for e in self.events if e.category == category]

    @property
    def error_count(self) -> int:
        return sum(1 for e in self.events if e.category == "error")

    @property
    def tool_call_count(self) -> int:
        return sum(1 for e in self.events if e.category == "tool_exec")

    def as_markdown(self) -> str:
        lines = [f"# Session History ({self.session_id})", ""]
        if not self.events:
            lines.append("No events recorded.")
            return "\n".join(lines)
        for event in self.events:
            lines.append(f"- **{event.category}** {event.title}")
            if event.detail:
                lines.append(f"  {event.detail}")
        lines.extend([
            "",
            f"Total events: {len(self.events)} | "
            f"Errors: {self.error_count} | "
            f"Tool calls: {self.tool_call_count}",
        ])
        return "\n".join(lines)

    def as_compact_summary(self) -> str:
        """One-line summary for dashboard/telegram."""
        cats = {}
        for e in self.events:
            cats[e.category] = cats.get(e.category, 0) + 1
        parts = [f"{cat}={count}" for cat, count in sorted(cats.items())]
        return " | ".join(parts) if parts else "empty"
