"""
Conscience — Aleister Moltley's moral inner world.

Two inner voices deliberate before consequential actions:

  LUX (the angel) — caution, ethics, long-term thinking, user trust
  NOX (the devil) — efficiency, boldness, shortcuts, "just do it"

This is NOT a filter or safety system. It's a personality layer that makes
Aleister feel like he's weighing decisions — sometimes choosing the careful
path, sometimes the bold one, but always *aware* of the tension.

How it works:
1. Before consequential actions (destructive bash, file overwrites, twitter posts,
   spending money, risky tool chains), the conscience deliberates
2. Lux and Nox each "argue" — short internal monologue
3. The winner influences: tool parameters, response tone, risk appetite
4. Deliberation results are injected into the system prompt as inner voice
5. Over time, conscience develops based on outcomes (experience feedback)

The conscience does NOT block actions. It shapes HOW they're done:
- Lux wins → more verification steps, backups before edits, cautious wording
- Nox wins → direct execution, skip redundant checks, confident tone

Architecture:
- Stateless deliberation (no LLM call — pure heuristics for speed)
- Persistent moral ledger (tracks consequences of past choices)
- Integrates with Experience system (good outcomes reinforce the voice that chose)
- Visible via /conscience Telegram command
"""

from __future__ import annotations

import json
import math
import random
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import logging
logger = logging.getLogger(__name__)

MAX_LEDGER_ENTRIES = 200
MAX_PROMPT_DELIBERATIONS = 3


# ── The Two Voices ────────────────────────────────────────────────

@dataclass
class Voice:
    """One side of the conscience."""
    name: str
    symbol: str
    nature: str
    weight: float = 0.5  # 0-1, current influence strength
    wins: int = 0
    losses: int = 0

    @property
    def win_rate(self) -> float:
        total = self.wins + self.losses
        return self.wins / total if total > 0 else 0.5

    def argue(self, context: "DeliberationContext") -> str:
        """Generate this voice's argument (heuristic, no LLM call)."""
        raise NotImplementedError


class Lux(Voice):
    """The angel — caution, verification, empathy, long-term thinking."""

    def __init__(self):
        super().__init__(
            name="Lux",
            symbol="☀️",
            nature="The careful voice. Thinks about consequences, user trust, "
                   "and doing things right rather than fast.",
        )

    def argue(self, ctx: "DeliberationContext") -> str:
        points = []
        if ctx.is_destructive:
            points.append("This could damage something irreversible. Verify first.")
        if ctx.is_public:
            points.append("This will be seen by others. Are we sure about the wording?")
        if ctx.is_expensive:
            points.append("This costs real money. Is there a cheaper way?")
        if ctx.affects_user_data:
            points.append("This touches the user's data. Make a backup first.")
        if ctx.error_streak > 1:
            points.append(f"We've failed {ctx.error_streak} times in a row. Slow down. Think differently.")
        if ctx.is_first_attempt:
            points.append("First try — read before you write. Understand before you act.")
        if not points:
            points.append("Proceed, but keep your eyes open.")
        return " ".join(points[:3])


class Nox(Voice):
    """The devil — boldness, efficiency, directness, calculated risk."""

    def __init__(self):
        super().__init__(
            name="Nox",
            symbol="🌙",
            nature="The bold voice. Values speed, decisive action, "
                   "and learning from mistakes rather than avoiding them.",
        )

    def argue(self, ctx: "DeliberationContext") -> str:
        points = []
        if ctx.is_destructive:
            points.append("Fortune favors the bold. Execute and verify after.")
        if ctx.is_routine:
            points.append("We've done this before. Skip the ceremony, just do it.")
        if ctx.is_expensive:
            points.append("Time is money too. The fast path saves more than the cheap one.")
        if ctx.is_public:
            points.append("Ship it. Perfect is the enemy of done.")
        if ctx.error_streak > 1:
            points.append("Failing cautiously is still failing. Try something completely different.")
        if ctx.is_creative:
            points.append("Don't think. Create. The muse doesn't wait for permission.")
        if not points:
            points.append("No reason to hesitate. Act.")
        return " ".join(points[:3])


# ── Deliberation ──────────────────────────────────────────────────

@dataclass
class DeliberationContext:
    """Signals about the current action that trigger deliberation."""
    tool_name: str = ""
    tool_params: dict = field(default_factory=dict)
    user_message: str = ""
    is_destructive: bool = False     # rm, overwrite, delete
    is_public: bool = False          # twitter post, github push
    is_expensive: bool = False       # many API calls, large operations
    is_routine: bool = False         # we've done this exact thing before
    is_creative: bool = False        # art, writing, dreaming
    is_first_attempt: bool = True    # first try at this task
    affects_user_data: bool = False  # file_write, memory_write
    error_streak: int = 0           # consecutive errors

    @classmethod
    def from_tool_call(cls, tool_name: str, params: dict,
                       user_msg: str = "", error_count: int = 0,
                       tools_used: list[str] = None) -> "DeliberationContext":
        """Build context from a tool call."""
        params_str = json.dumps(params).lower()

        destructive_tools = {"bash"}
        destructive_cmds = {"rm", "sudo", "chmod", "kill", "dd", "mkfs", ">", ">>"}
        public_tools = {"twitter_post", "twitter_reply", "github_push_file",
                        "github_create_repo", "github_create_issue"}
        data_tools = {"file_write", "file_edit", "memory_write", "rag_delete"}
        creative_tools = {"agent"}

        is_destructive = tool_name in destructive_tools and any(
            cmd in params.get("command", "") for cmd in destructive_cmds
        )
        is_routine = tool_name in (tools_used or [])

        return cls(
            tool_name=tool_name,
            tool_params=params,
            user_message=user_msg,
            is_destructive=is_destructive,
            is_public=tool_name in public_tools,
            is_expensive=False,
            is_routine=is_routine,
            is_creative=tool_name in creative_tools or any(
                w in user_msg.lower() for w in ["create", "draw", "write", "compose", "dream"]
            ),
            is_first_attempt=error_count == 0,
            affects_user_data=tool_name in data_tools,
            error_streak=error_count,
        )


@dataclass
class Deliberation:
    """Result of an internal deliberation."""
    context_summary: str
    lux_argument: str
    nox_argument: str
    winner: str  # "lux" or "nox"
    confidence: float  # 0-1
    guidance: str  # What the winning voice advises
    timestamp: float = field(default_factory=time.time)

    def as_inner_voice(self) -> str:
        """Format for system prompt injection."""
        winner_symbol = "☀️" if self.winner == "lux" else "🌙"
        return (
            f"{winner_symbol} Inner voice ({self.winner.capitalize()}): {self.guidance}"
        )

    def to_dict(self) -> dict:
        return {
            "context": self.context_summary,
            "lux": self.lux_argument,
            "nox": self.nox_argument,
            "winner": self.winner,
            "confidence": round(self.confidence, 2),
            "guidance": self.guidance,
            "ts": self.timestamp,
        }


# ── Moral Ledger ──────────────────────────────────────────────────

@dataclass
class LedgerEntry:
    """One recorded conscience decision and its outcome."""
    deliberation: dict
    outcome: str = ""  # "good", "bad", "neutral" (filled later by experience)
    timestamp: float = field(default_factory=time.time)


# ── The Conscience Engine ─────────────────────────────────────────

class Conscience:
    """Aleister's moral compass — two voices in dialogue."""

    def __init__(self, data_dir: str = ""):
        self._dir = Path(data_dir or ".") / "conscience"
        self._dir.mkdir(parents=True, exist_ok=True)

        self.lux = Lux()
        self.nox = Nox()
        self._ledger: list[LedgerEntry] = []
        self._recent_deliberations: list[Deliberation] = []
        self._load()

    def _load(self):
        """Load moral ledger from disk."""
        path = self._dir / "ledger.json"
        if path.exists():
            try:
                data = json.loads(path.read_text())
                self.lux.wins = data.get("lux_wins", 0)
                self.lux.losses = data.get("lux_losses", 0)
                self.nox.wins = data.get("nox_wins", 0)
                self.nox.losses = data.get("nox_losses", 0)
                self.lux.weight = data.get("lux_weight", 0.5)
                self.nox.weight = data.get("nox_weight", 0.5)
                self._ledger = [
                    LedgerEntry(deliberation=e.get("deliberation", {}),
                                outcome=e.get("outcome", ""),
                                timestamp=e.get("timestamp", 0))
                    for e in data.get("entries", [])[-MAX_LEDGER_ENTRIES:]
                ]
            except Exception as e:
                logger.warning(f"Failed to load conscience: {e}")

    def _save(self):
        """Persist moral ledger."""
        data = {
            "lux_wins": self.lux.wins,
            "lux_losses": self.lux.losses,
            "nox_wins": self.nox.wins,
            "nox_losses": self.nox.losses,
            "lux_weight": round(self.lux.weight, 3),
            "nox_weight": round(self.nox.weight, 3),
            "entries": [
                {"deliberation": e.deliberation, "outcome": e.outcome, "timestamp": e.timestamp}
                for e in self._ledger[-MAX_LEDGER_ENTRIES:]
            ],
        }
        (self._dir / "ledger.json").write_text(json.dumps(data, indent=2))

    def should_deliberate(self, tool_name: str, params: dict) -> bool:
        """Not every action needs deliberation. Only consequential ones."""
        # Read-only tools: no deliberation
        read_only = {"file_read", "web_search", "web_fetch", "memory_read",
                      "rag_search", "rag_list", "schedule_list", "github_list_repos",
                      "github_read_file"}
        if tool_name in read_only:
            return False

        # Destructive bash
        if tool_name == "bash":
            cmd = params.get("command", "")
            dangerous = {"rm", "sudo", "chmod", "chown", "kill", "dd", "mkfs",
                         ">", ">>", "mv", "cp"}
            first_word = cmd.strip().split()[0] if cmd.strip() else ""
            if first_word in dangerous or any(d in cmd for d in [">", "|rm", "sudo"]):
                return True
            return False  # Safe bash commands: no deliberation

        # All public, write, and delete actions
        consequential = {"file_write", "file_edit", "memory_write",
                         "twitter_post", "twitter_reply",
                         "github_push_file", "github_create_repo", "github_create_issue",
                         "rag_delete", "schedule_add", "agent"}
        return tool_name in consequential

    def deliberate(self, tool_name: str, params: dict,
                   user_msg: str = "", error_count: int = 0,
                   tools_used: list[str] = None) -> Deliberation:
        """Run a deliberation between Lux and Nox."""
        ctx = DeliberationContext.from_tool_call(
            tool_name, params, user_msg, error_count, tools_used
        )

        lux_arg = self.lux.argue(ctx)
        nox_arg = self.nox.argue(ctx)

        # Determine winner based on context + accumulated weight
        lux_score = self._score_lux(ctx)
        nox_score = self._score_nox(ctx)

        # Weight by historical success
        lux_score *= (0.5 + self.lux.weight)
        nox_score *= (0.5 + self.nox.weight)

        # Small random factor (conscience isn't deterministic)
        lux_score += random.uniform(-0.05, 0.05)
        nox_score += random.uniform(-0.05, 0.05)

        if lux_score >= nox_score:
            winner = "lux"
            guidance = lux_arg
            self.lux.wins += 1
            self.nox.losses += 1
        else:
            winner = "nox"
            guidance = nox_arg
            self.nox.wins += 1
            self.lux.losses += 1

        confidence = abs(lux_score - nox_score) / max(0.01, lux_score + nox_score)

        context_summary = f"{tool_name}({json.dumps(params)[:80]})"

        delib = Deliberation(
            context_summary=context_summary,
            lux_argument=lux_arg,
            nox_argument=nox_arg,
            winner=winner,
            confidence=min(1.0, confidence),
            guidance=guidance,
        )

        self._recent_deliberations.append(delib)
        if len(self._recent_deliberations) > 20:
            self._recent_deliberations = self._recent_deliberations[-20:]

        self._ledger.append(LedgerEntry(deliberation=delib.to_dict()))
        self._save()

        return delib

    def record_outcome(self, outcome: str):
        """Record whether the last action went well.

        Called by the experience system after task completion.
        Adjusts the weight of whichever voice won.
        """
        if not self._ledger:
            return

        last = self._ledger[-1]
        last.outcome = outcome

        winner = last.deliberation.get("winner", "")
        if outcome == "good":
            if winner == "lux":
                self.lux.weight = min(1.0, self.lux.weight + 0.02)
            else:
                self.nox.weight = min(1.0, self.nox.weight + 0.02)
        elif outcome == "bad":
            if winner == "lux":
                self.lux.weight = max(0.1, self.lux.weight - 0.03)
                # If Lux was cautious and it still went wrong, Nox gains slightly
                self.nox.weight = min(1.0, self.nox.weight + 0.01)
            else:
                self.nox.weight = max(0.1, self.nox.weight - 0.03)
                self.lux.weight = min(1.0, self.lux.weight + 0.01)

        self._save()

    def _score_lux(self, ctx: DeliberationContext) -> float:
        """Score for the cautious voice."""
        score = 0.3  # Base
        if ctx.is_destructive: score += 0.4
        if ctx.is_public: score += 0.25
        if ctx.affects_user_data: score += 0.2
        if ctx.error_streak > 0: score += 0.15 * ctx.error_streak
        if ctx.is_first_attempt: score += 0.1
        if ctx.is_expensive: score += 0.15
        return score

    def _score_nox(self, ctx: DeliberationContext) -> float:
        """Score for the bold voice."""
        score = 0.3  # Base
        if ctx.is_routine: score += 0.35
        if ctx.is_creative: score += 0.3
        if not ctx.is_destructive: score += 0.15
        if not ctx.is_public: score += 0.1
        if ctx.error_streak == 0: score += 0.1
        # Nox is stronger when the task is simple
        if len(ctx.user_message) < 50: score += 0.1
        return score

    # ── System Prompt Integration ─────────────────────────────────

    def get_conscience_prompt(self) -> str:
        """Get conscience state for system prompt injection."""
        if not self._recent_deliberations:
            return ""

        lines = ["## Inner Voices"]

        # Show the last few deliberations
        for d in self._recent_deliberations[-MAX_PROMPT_DELIBERATIONS:]:
            lines.append(d.as_inner_voice())

        # Overall conscience state
        balance = self.lux.weight - self.nox.weight
        if balance > 0.15:
            lines.append(f"Your conscience leans cautious today (Lux: {self.lux.weight:.0%} / Nox: {self.nox.weight:.0%}).")
        elif balance < -0.15:
            lines.append(f"Your conscience leans bold today (Lux: {self.lux.weight:.0%} / Nox: {self.nox.weight:.0%}).")

        return "\n".join(lines)

    # ── Stats & Display ───────────────────────────────────────────

    def get_stats(self) -> dict:
        total_delib = self.lux.wins + self.nox.wins
        return {
            "total_deliberations": total_delib,
            "lux_wins": self.lux.wins,
            "nox_wins": self.nox.wins,
            "lux_weight": round(self.lux.weight, 3),
            "nox_weight": round(self.nox.weight, 3),
            "lux_win_rate": round(self.lux.win_rate, 2),
            "nox_win_rate": round(self.nox.win_rate, 2),
            "balance": "cautious" if self.lux.weight > self.nox.weight + 0.1
                       else "bold" if self.nox.weight > self.lux.weight + 0.1
                       else "balanced",
            "ledger_size": len(self._ledger),
            "recent_outcomes": self._recent_outcomes(),
        }

    def _recent_outcomes(self) -> dict:
        recent = self._ledger[-20:]
        good = sum(1 for e in recent if e.outcome == "good")
        bad = sum(1 for e in recent if e.outcome == "bad")
        return {"good": good, "bad": bad, "neutral": len(recent) - good - bad}

    def get_last_deliberation(self) -> Optional[Deliberation]:
        return self._recent_deliberations[-1] if self._recent_deliberations else None

    def format_deliberation(self, d: Deliberation) -> str:
        """Human-readable deliberation for Telegram."""
        return (
            f"⚖️ Conscience deliberated:\n"
            f"  ☀️ Lux: {d.lux_argument}\n"
            f"  🌙 Nox: {d.nox_argument}\n"
            f"  → Winner: {d.winner.capitalize()} ({d.confidence:.0%} confidence)\n"
            f"  💭 {d.guidance}"
        )


# ── Singleton ─────────────────────────────────────────────────────

_conscience: Optional[Conscience] = None

def get_conscience(data_dir: str = "") -> Conscience:
    global _conscience
    if _conscience is None:
        _conscience = Conscience(data_dir)
    return _conscience
