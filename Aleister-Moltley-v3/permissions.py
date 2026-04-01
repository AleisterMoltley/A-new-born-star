"""
Tool Permission Context — Fine-grained tool access control.

Ported from claw-code's permissions.py pattern:
- Deny-list by exact name
- Deny-list by prefix (e.g. block all "twitter_*" tools)
- Trust-gated: destructive tools require explicit unlock
- Per-session overrides

This replaces the flat auto_approve booleans in config with a composable system.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass(frozen=True)
class ToolPermissionContext:
    """Immutable permission snapshot for a single session or turn."""
    deny_names: frozenset[str] = field(default_factory=frozenset)
    deny_prefixes: tuple[str, ...] = ()
    allow_destructive: bool = False
    trusted: bool = True

    @classmethod
    def from_config(cls, config) -> "ToolPermissionContext":
        """Build from CompagnonConfig."""
        deny = set()
        prefixes = []

        # If not auto-approved, gate destructive tools
        if not config.auto_approve_bash_destructive:
            for cmd in config.require_confirmation_for:
                deny.add(cmd)

        return cls(
            deny_names=frozenset(n.lower() for n in deny),
            deny_prefixes=tuple(p.lower() for p in prefixes),
            allow_destructive=config.auto_approve_bash_destructive,
            trusted=True,
        )

    @classmethod
    def from_iterables(
        cls,
        deny_names: list[str] | None = None,
        deny_prefixes: list[str] | None = None,
        allow_destructive: bool = False,
        trusted: bool = True,
    ) -> "ToolPermissionContext":
        return cls(
            deny_names=frozenset(n.lower() for n in (deny_names or [])),
            deny_prefixes=tuple(p.lower() for p in (deny_prefixes or [])),
            allow_destructive=allow_destructive,
            trusted=trusted,
        )

    def blocks(self, tool_name: str) -> bool:
        """Check if a tool is blocked by this permission context."""
        lowered = tool_name.lower()
        if lowered in self.deny_names:
            return True
        return any(lowered.startswith(prefix) for prefix in self.deny_prefixes)

    def with_override(
        self,
        allow_names: list[str] | None = None,
        deny_names: list[str] | None = None,
    ) -> "ToolPermissionContext":
        """Create a derived context with overrides (for sub-agents, etc)."""
        new_deny = set(self.deny_names)
        if deny_names:
            new_deny.update(n.lower() for n in deny_names)
        if allow_names:
            new_deny -= {n.lower() for n in allow_names}
        return ToolPermissionContext(
            deny_names=frozenset(new_deny),
            deny_prefixes=self.deny_prefixes,
            allow_destructive=self.allow_destructive,
            trusted=self.trusted,
        )


@dataclass(frozen=True)
class PermissionDenial:
    """Record of a denied tool call — for audit trail."""
    tool_name: str
    reason: str
    session_id: str = ""
    timestamp: float = 0.0


class PermissionGate:
    """Runtime permission checker that integrates with ToolRegistry."""

    def __init__(self, context: ToolPermissionContext):
        self.context = context
        self.denials: list[PermissionDenial] = []

    def check(self, tool_name: str, params: dict | None = None) -> PermissionDenial | None:
        """Check if a tool call is permitted. Returns denial reason or None."""
        if self.context.blocks(tool_name):
            denial = PermissionDenial(
                tool_name=tool_name,
                reason=f"Blocked by permission context (deny-list)",
            )
            self.denials.append(denial)
            return denial

        # Check destructive bash commands
        if tool_name == "bash" and params and not self.context.allow_destructive:
            cmd = params.get("command", "")
            first_word = cmd.strip().split()[0] if cmd.strip() else ""
            if first_word.lower() in self.context.deny_names:
                denial = PermissionDenial(
                    tool_name=tool_name,
                    reason=f"Destructive command '{first_word}' requires confirmation",
                )
                self.denials.append(denial)
                return denial

        return None

    def get_denial_summary(self) -> str:
        if not self.denials:
            return "No permission denials."
        lines = [f"Permission denials ({len(self.denials)}):"]
        for d in self.denials:
            lines.append(f"  - {d.tool_name}: {d.reason}")
        return "\n".join(lines)
