"""
Model Router — Automatic Haiku/Sonnet switching + dynamic max_tokens.

Cost optimization strategy:
1. Simple tasks → Haiku (60-80% cheaper)
2. Complex tasks → Sonnet (full reasoning)
3. max_tokens dynamically sized per turn (not fixed 8192)

Routing heuristics:
- Short messages (<100 chars) with no code/analysis keywords → Haiku
- Single-tool tasks (file_read, web_search, memory) → Haiku
- Multi-step tasks, code generation, debugging, analysis → Sonnet
- Explicit model override via /model command bypasses router

max_tokens optimizer:
- First turn: 4096 (most responses are <2k tokens)
- Tool-use turns: 2048 (model just needs to emit tool calls)
- Final response after tools: 8192 (might be long)
- Code generation: 8192
- Simple Q&A: 2048
- Compact/summarize: 4096

Savings estimate: 40-60% reduction in output token waste.
"""
from __future__ import annotations

import re
import logging
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)

# ── Models ────────────────────────────────────────────────────────

HAIKU = "claude-haiku-4-5-20251001"
SONNET = "claude-sonnet-4-20250514"

# Cost per MTok (output, which dominates cost)
# Haiku: $5/MTok output, Sonnet: $15/MTok output → 3x cheaper
# Haiku: $1/MTok input, Sonnet: $3/MTok input → 3x cheaper

# ── Complexity Signals ────────────────────────────────────────────

# Keywords that signal a task needs Sonnet's reasoning
SONNET_KEYWORDS = {
    # Code generation / debugging
    "implement", "refactor", "debug", "fix the bug", "write a script",
    "create a class", "architecture", "design pattern", "optimize",
    "build", "deploy", "dockerfile", "ci/cd", "pipeline",
    # Analysis / reasoning
    "analyze", "compare", "evaluate", "explain why", "trade-off",
    "strategy", "plan", "review", "audit", "assess",
    # Multi-step
    "step by step", "first then", "and then", "after that",
    "systematically", "comprehensive",
    # Creative / complex
    "write a post", "blog", "essay", "report", "whitepaper",
    "dream", "atelier", "draw", "create art",
    # Explicit complexity
    "complex", "difficult", "advanced", "deep dive",
}

# Keywords that are fine for Haiku
HAIKU_SAFE_PATTERNS = {
    # Simple lookups
    "what is", "who is", "when did", "how much", "how many",
    "show me", "list", "find", "search for", "look up",
    # Simple file ops
    "read file", "cat ", "ls ", "show file", "open",
    # Memory
    "remember", "recall", "what do you know",
    # Status
    "status", "cost", "budget", "help",
    # Short confirmations
    "yes", "no", "ok", "sure", "thanks", "do it",
}

# Tool categories that Haiku handles well
HAIKU_SAFE_TOOLS = {
    "file_read", "memory_read", "memory_write", "web_search",
    "rag_search", "rag_list", "schedule_list", "github_list_repos",
    "github_read_file",
}

# Tool categories that benefit from Sonnet
SONNET_PREFERRED_TOOLS = {
    "bash", "file_write", "file_edit", "agent",
    "github_push_file", "github_create_repo", "github_create_issue",
    "twitter_post", "twitter_reply",
}


@dataclass
class RoutingDecision:
    model: str
    reason: str
    max_tokens: int
    confidence: float  # 0-1, how sure we are about this routing

    @property
    def is_haiku(self) -> bool:
        return "haiku" in self.model.lower()

    @property
    def cost_savings_pct(self) -> int:
        """Estimated cost savings vs always using Sonnet."""
        return 67 if self.is_haiku else 0


class ModelRouter:
    """Routes requests to the cheapest model that can handle them."""

    def __init__(self, default_model: str = SONNET, haiku_model: str = HAIKU,
                 force_model: str = "", enable_routing: bool = True):
        self.default_model = default_model
        self.haiku_model = haiku_model
        self.force_model = force_model  # Set by /model command — overrides router
        self.enable_routing = enable_routing
        self._total_routed = 0
        self._haiku_count = 0
        self._sonnet_count = 0

    def route(self, user_message: str, messages: list[dict] = None,
              is_tool_turn: bool = False, tool_names: list[str] = None) -> RoutingDecision:
        """Decide which model and max_tokens to use for this turn."""

        # Override: if user set explicit model via /model
        if self.force_model:
            return RoutingDecision(
                model=self.force_model,
                reason="user override",
                max_tokens=self._estimate_max_tokens(user_message, is_tool_turn, False),
                confidence=1.0,
            )

        # Override: routing disabled
        if not self.enable_routing:
            return RoutingDecision(
                model=self.default_model,
                reason="routing disabled",
                max_tokens=self._estimate_max_tokens(user_message, is_tool_turn, False),
                confidence=1.0,
            )

        # ── Classify complexity ──
        msg_lower = user_message.lower().strip()
        complexity = self._score_complexity(msg_lower, messages, tool_names)

        # Route decision
        if complexity < 0.3:
            model = self.haiku_model
            reason = "simple task"
            self._haiku_count += 1
        elif complexity < 0.5:
            model = self.haiku_model
            reason = "moderate but Haiku-safe"
            self._haiku_count += 1
        else:
            model = self.default_model
            reason = "complex task"
            self._sonnet_count += 1

        self._total_routed += 1

        is_code = complexity > 0.6 or any(kw in msg_lower for kw in ["code", "script", "implement", "write"])
        max_tokens = self._estimate_max_tokens(user_message, is_tool_turn, is_code)

        return RoutingDecision(
            model=model,
            reason=reason,
            max_tokens=max_tokens,
            confidence=min(1.0, abs(complexity - 0.4) * 3 + 0.3),
        )

    def _score_complexity(self, msg_lower: str, messages: list[dict] = None,
                          tool_names: list[str] = None) -> float:
        """Score 0-1 how complex this task is. <0.4 = Haiku, >=0.5 = Sonnet."""
        score = 0.0

        # Message length
        if len(msg_lower) < 30:
            score -= 0.15
        elif len(msg_lower) > 300:
            score += 0.15
        elif len(msg_lower) > 500:
            score += 0.25

        # Sonnet keywords
        sonnet_hits = sum(1 for kw in SONNET_KEYWORDS if kw in msg_lower)
        score += sonnet_hits * 0.15

        # Haiku-safe patterns
        haiku_hits = sum(1 for kw in HAIKU_SAFE_PATTERNS if kw in msg_lower)
        score -= haiku_hits * 0.12

        # Code indicators
        if any(c in msg_lower for c in ['```', 'def ', 'class ', 'import ', 'function']):
            score += 0.2
        if any(c in msg_lower for c in ['error', 'traceback', 'exception', 'bug']):
            score += 0.15

        # Multi-step indicators
        if msg_lower.count(' and ') >= 2 or msg_lower.count(' then ') >= 1:
            score += 0.15

        # Question mark = often simpler
        if msg_lower.endswith('?') and len(msg_lower) < 100:
            score -= 0.1

        # Tool-based routing
        if tool_names:
            sonnet_tools = sum(1 for t in tool_names if t in SONNET_PREFERRED_TOOLS)
            haiku_tools = sum(1 for t in tool_names if t in HAIKU_SAFE_TOOLS)
            score += sonnet_tools * 0.1
            score -= haiku_tools * 0.08

        # Conversation depth — longer conversations tend to be more complex
        if messages and len(messages) > 10:
            score += 0.1

        return max(0.0, min(1.0, score + 0.35))  # Base at 0.35

    def _estimate_max_tokens(self, user_message: str, is_tool_turn: bool,
                              is_code: bool) -> int:
        """Dynamic max_tokens sizing instead of fixed 8192.

        Most responses are <2k tokens. Setting max_tokens too high doesn't
        directly cost more (you pay for actual output), but it:
        - Wastes model attention budget
        - Can lead to unnecessarily verbose responses
        - On some providers, reserves output capacity
        """
        msg_lower = user_message.lower()

        # Tool-use intermediate turns: model just emits tool_use blocks
        if is_tool_turn:
            return 2048

        # Code generation: needs room
        if is_code:
            return 8192

        # Explicit length requests
        if any(kw in msg_lower for kw in ["detailed", "comprehensive", "full", "complete", "long"]):
            return 8192
        if any(kw in msg_lower for kw in ["brief", "short", "quick", "one line", "tldr", "tl;dr"]):
            return 1024

        # Analysis/report tasks
        if any(kw in msg_lower for kw in ["analyze", "report", "review", "audit", "compare"]):
            return 6144

        # Simple Q&A
        if len(user_message) < 80 and ('?' in user_message or any(
            kw in msg_lower for kw in ["what", "who", "when", "where", "how much"]
        )):
            return 2048

        # Default: moderate
        return 4096

    def set_force_model(self, model: str):
        """Set by /model command. Empty string = auto-routing."""
        self.force_model = model
        logger.info(f"Model router: force_model={'auto' if not model else model}")

    def get_stats(self) -> dict:
        total = max(1, self._total_routed)
        return {
            "total_routed": self._total_routed,
            "haiku_count": self._haiku_count,
            "sonnet_count": self._sonnet_count,
            "haiku_pct": round(self._haiku_count / total * 100),
            "estimated_savings_pct": round(self._haiku_count / total * 67),
        }

    def format_stats(self) -> str:
        s = self.get_stats()
        return (
            f"Router: {s['total_routed']} calls, "
            f"Haiku {s['haiku_pct']}% / Sonnet {100 - s['haiku_pct']}%, "
            f"~{s['estimated_savings_pct']}% savings"
        )
