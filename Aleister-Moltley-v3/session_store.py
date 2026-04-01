"""
Session Persistence v2 — With transcript + history integration.

From claw-code patterns:
- Sessions now store transcript entries (not just raw messages)
- History snapshots saved alongside sessions
- Permission denials persisted for audit
- Replay support for context rebuilding
"""
import json
import time
import uuid
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional
import logging

logger = logging.getLogger(__name__)

MAX_SESSIONS_PER_USER = 20


@dataclass
class Session:
    user_id: int
    session_id: str = ""
    messages: list[dict] = field(default_factory=list)
    working_dir: str = ""
    created_at: float = 0.0
    updated_at: float = 0.0
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    total_cost_usd: float = 0.0
    tool_calls: int = 0
    compactions: int = 0
    title: str = ""
    # New: from claw-code patterns
    permission_denials: int = 0
    history_summary: str = ""
    transcript_turns: int = 0
    session_budget_tokens: int = 0  # 0 = unlimited

    def __post_init__(self):
        if not self.session_id:
            self.session_id = uuid.uuid4().hex[:12]
        if not self.created_at:
            self.created_at = time.time()
        self.updated_at = time.time()

    def clear(self):
        self.messages.clear()
        self.session_id = uuid.uuid4().hex[:12]
        self.created_at = time.time()
        self.updated_at = time.time()
        self.total_input_tokens = 0
        self.total_output_tokens = 0
        self.total_cost_usd = 0.0
        self.tool_calls = 0
        self.compactions = 0
        self.title = ""
        self.permission_denials = 0
        self.history_summary = ""
        self.transcript_turns = 0

    def to_dict(self) -> dict:
        return {
            "user_id": self.user_id,
            "session_id": self.session_id,
            "messages": self.messages,
            "working_dir": self.working_dir,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "total_input_tokens": self.total_input_tokens,
            "total_output_tokens": self.total_output_tokens,
            "total_cost_usd": self.total_cost_usd,
            "tool_calls": self.tool_calls,
            "compactions": self.compactions,
            "title": self.title,
            "permission_denials": self.permission_denials,
            "history_summary": self.history_summary,
            "transcript_turns": self.transcript_turns,
            "session_budget_tokens": self.session_budget_tokens,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Session":
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


class SessionStore:
    """File-based session persistence."""

    def __init__(self, session_dir: str):
        self.session_dir = Path(session_dir)
        self.session_dir.mkdir(parents=True, exist_ok=True)

    def _session_path(self, user_id: int, session_id: str) -> Path:
        user_dir = self.session_dir / str(user_id)
        user_dir.mkdir(exist_ok=True)
        return user_dir / f"{session_id}.json"

    def _history_path(self, user_id: int, session_id: str) -> Path:
        """Separate file for full history log (from claw-code pattern)."""
        user_dir = self.session_dir / str(user_id)
        user_dir.mkdir(exist_ok=True)
        return user_dir / f"{session_id}_history.json"

    def save(self, session: Session, history_log=None):
        """Save session to disk, optionally with full history."""
        session.updated_at = time.time()
        path = self._session_path(session.user_id, session.session_id)
        try:
            path.write_text(json.dumps(session.to_dict(), ensure_ascii=False, default=str))

            # Save history log alongside session (claw-code pattern)
            if history_log:
                h_path = self._history_path(session.user_id, session.session_id)
                h_path.write_text(json.dumps({
                    "session_id": session.session_id,
                    "events": [
                        {"category": e.category, "title": e.title,
                         "detail": e.detail, "timestamp": e.timestamp}
                        for e in history_log.events
                    ],
                }, ensure_ascii=False))
        except Exception as e:
            logger.error(f"Failed to save session {session.session_id}: {e}")

    def load(self, user_id: int, session_id: str) -> Optional[Session]:
        path = self._session_path(user_id, session_id)
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text())
            return Session.from_dict(data)
        except Exception as e:
            logger.error(f"Failed to load session {session_id}: {e}")
            return None

    def load_history(self, user_id: int, session_id: str) -> Optional[dict]:
        """Load the history log for a session."""
        h_path = self._history_path(user_id, session_id)
        if not h_path.exists():
            return None
        try:
            return json.loads(h_path.read_text())
        except Exception:
            return None

    def load_latest(self, user_id: int) -> Optional[Session]:
        user_dir = self.session_dir / str(user_id)
        if not user_dir.exists():
            return None
        sessions = sorted(user_dir.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
        # Skip history files
        sessions = [s for s in sessions if not s.stem.endswith("_history")]
        if not sessions:
            return None
        try:
            data = json.loads(sessions[0].read_text())
            return Session.from_dict(data)
        except Exception:
            return None

    def list_sessions(self, user_id: int, limit: int = 10) -> list[dict]:
        user_dir = self.session_dir / str(user_id)
        if not user_dir.exists():
            return []
        results = []
        files = sorted(user_dir.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
        files = [f for f in files if not f.stem.endswith("_history")]
        for f in files[:limit]:
            try:
                data = json.loads(f.read_text())
                results.append({
                    "session_id": data.get("session_id", f.stem),
                    "title": data.get("title", ""),
                    "created_at": data.get("created_at", 0),
                    "updated_at": data.get("updated_at", 0),
                    "messages": len(data.get("messages", [])),
                    "cost_usd": data.get("total_cost_usd", 0),
                    "tool_calls": data.get("tool_calls", 0),
                    "permission_denials": data.get("permission_denials", 0),
                    "transcript_turns": data.get("transcript_turns", 0),
                })
            except Exception:
                pass
        return results

    def cleanup_old(self, user_id: int, keep: int = MAX_SESSIONS_PER_USER):
        user_dir = self.session_dir / str(user_id)
        if not user_dir.exists():
            return
        files = sorted(user_dir.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
        for f in files[keep:]:
            f.unlink(missing_ok=True)

    def get_daily_cost(self, user_id: int) -> float:
        import datetime
        user_dir = self.session_dir / str(user_id)
        if not user_dir.exists():
            return 0.0
        today_start = datetime.datetime.now().replace(hour=0, minute=0, second=0).timestamp()
        total = 0.0
        for f in user_dir.glob("*.json"):
            if f.stem.endswith("_history"):
                continue
            try:
                data = json.loads(f.read_text())
                if data.get("updated_at", 0) >= today_start:
                    total += data.get("total_cost_usd", 0)
            except Exception:
                pass
        return total
