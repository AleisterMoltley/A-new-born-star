"""
Transcript Store — Structured conversation transcript with replay and compaction.

Ported from claw-code's transcript.py pattern:
- append/replay/compact/flush lifecycle
- Separate from session persistence (session_store handles disk)
- Supports structured entries (not just strings)
- Token-aware compaction threshold
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class TranscriptEntry:
    """Single entry in the transcript — richer than raw message dict."""
    role: str  # "user", "assistant", "system", "tool_result"
    content: str
    timestamp: float = field(default_factory=time.time)
    tool_name: str = ""
    token_estimate: int = 0
    turn_index: int = 0
    is_compacted: bool = False

    def to_dict(self) -> dict:
        return {
            "role": self.role,
            "content": self.content[:500],  # Preview only for serialization
            "timestamp": self.timestamp,
            "tool_name": self.tool_name,
            "token_estimate": self.token_estimate,
            "turn_index": self.turn_index,
        }


@dataclass
class TranscriptStore:
    """In-memory transcript manager with compaction support.

    Lifecycle:
    1. append() — add entries as conversation progresses
    2. replay() — get all entries (for context rebuild)
    3. compact() — trim old entries when approaching token limit
    4. flush() — mark as persisted (session_store writes to disk)
    """
    entries: list[TranscriptEntry] = field(default_factory=list)
    flushed: bool = False
    _turn_counter: int = 0
    _total_tokens_estimate: int = 0

    def append(self, role: str, content: str, tool_name: str = "") -> TranscriptEntry:
        """Add a new entry to the transcript."""
        token_est = len(content) // 4  # Rough estimate
        self._turn_counter += 1
        entry = TranscriptEntry(
            role=role,
            content=content,
            tool_name=tool_name,
            token_estimate=token_est,
            turn_index=self._turn_counter,
        )
        self.entries.append(entry)
        self._total_tokens_estimate += token_est
        self.flushed = False
        return entry

    def append_from_message(self, message: dict) -> TranscriptEntry:
        """Append from a raw Anthropic API message dict."""
        role = message.get("role", "unknown")
        content = message.get("content", "")
        if isinstance(content, list):
            # Extract text from content blocks
            parts = []
            for block in content:
                if isinstance(block, dict):
                    if block.get("type") == "text":
                        parts.append(block.get("text", ""))
                    elif block.get("type") == "tool_use":
                        parts.append(f"[tool:{block.get('name', '?')}]")
                    elif block.get("type") == "tool_result":
                        parts.append(f"[result:{block.get('content', '')[:100]}]")
            content = "\n".join(parts)
        return self.append(role, content)

    def replay(self) -> tuple[TranscriptEntry, ...]:
        """Get all entries for context rebuild."""
        return tuple(self.entries)

    def replay_messages(self) -> list[str]:
        """Get just the user messages (for experience/eval)."""
        return [e.content for e in self.entries if e.role == "user"]

    def compact(self, keep_last: int = 10) -> int:
        """Trim old entries, keeping the last N.

        Returns number of entries removed.
        """
        if len(self.entries) <= keep_last:
            return 0
        removed = len(self.entries) - keep_last
        dropped = self.entries[:-keep_last]
        self.entries[:] = self.entries[-keep_last:]
        self._total_tokens_estimate = sum(e.token_estimate for e in self.entries)
        return removed

    def compact_to_token_budget(self, max_tokens: int) -> int:
        """Remove oldest entries until under token budget.

        Returns number of entries removed.
        """
        removed = 0
        while self._total_tokens_estimate > max_tokens and len(self.entries) > 2:
            dropped = self.entries.pop(0)
            self._total_tokens_estimate -= dropped.token_estimate
            removed += 1
        return removed

    def flush(self) -> None:
        """Mark transcript as persisted."""
        self.flushed = True

    @property
    def token_estimate(self) -> int:
        return self._total_tokens_estimate

    @property
    def turn_count(self) -> int:
        return self._turn_counter

    @property
    def user_message_count(self) -> int:
        return sum(1 for e in self.entries if e.role == "user")

    def get_summary(self) -> str:
        return (
            f"Transcript: {len(self.entries)} entries, "
            f"~{self._total_tokens_estimate:,} tokens, "
            f"{self._turn_counter} turns, "
            f"flushed={self.flushed}"
        )
