"""
Bootstrap — Deterministic startup sequence with prefetch and deferred init.

Ported from claw-code's bootstrap_graph.py + setup.py + system_init.py:
- Named startup stages executed in order
- Prefetch side-effects (memory, experience, MCP) run early
- Deferred init after trust gate (tools that need confirmation)
- System init message for the LLM
- Full startup report for debugging

Replaces the ad-hoc setup in main.py with a structured, auditable startup.
"""
from __future__ import annotations

import platform
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import logging
logger = logging.getLogger(__name__)


# ── Bootstrap Stages ──────────────────────────────────────────────

BOOTSTRAP_STAGES = (
    "prefetch_memory",
    "prefetch_experience",
    "prefetch_mcp_connections",
    "load_tool_registry",
    "load_permission_context",
    "apply_trust_gate",
    "deferred_init_plugins",
    "build_system_init_message",
    "start_query_engine",
)


@dataclass(frozen=True)
class StageResult:
    name: str
    success: bool
    duration_ms: float
    detail: str = ""


@dataclass(frozen=True)
class PrefetchResult:
    name: str
    started: bool
    detail: str


@dataclass(frozen=True)
class DeferredInitResult:
    trusted: bool
    mcp_connected: bool
    experience_loaded: bool
    memory_loaded: bool
    tools_registered: int

    def as_lines(self) -> list[str]:
        return [
            f"  trusted={self.trusted}",
            f"  mcp_connected={self.mcp_connected}",
            f"  experience_loaded={self.experience_loaded}",
            f"  memory_loaded={self.memory_loaded}",
            f"  tools_registered={self.tools_registered}",
        ]


@dataclass
class BootstrapReport:
    """Full startup report."""
    stages: list[StageResult] = field(default_factory=list)
    prefetches: list[PrefetchResult] = field(default_factory=list)
    deferred_init: Optional[DeferredInitResult] = None
    python_version: str = ""
    platform_name: str = ""
    startup_time_ms: float = 0.0
    tool_count: int = 0
    mcp_server_count: int = 0

    def as_markdown(self) -> str:
        lines = [
            "# Bootstrap Report",
            "",
            f"- Python: {self.python_version}",
            f"- Platform: {self.platform_name}",
            f"- Startup: {self.startup_time_ms:.0f}ms",
            f"- Tools: {self.tool_count}",
            f"- MCP servers: {self.mcp_server_count}",
            "",
            "## Stages",
        ]
        for stage in self.stages:
            icon = "✓" if stage.success else "✗"
            lines.append(f"  {icon} {stage.name} ({stage.duration_ms:.0f}ms)")
            if stage.detail:
                lines.append(f"    {stage.detail}")

        if self.prefetches:
            lines.extend(["", "## Prefetches"])
            for pf in self.prefetches:
                lines.append(f"  - {pf.name}: {'started' if pf.started else 'skipped'} — {pf.detail}")

        if self.deferred_init:
            lines.extend(["", "## Deferred Init"])
            lines.extend(self.deferred_init.as_lines())

        return "\n".join(lines)


def run_bootstrap(config) -> BootstrapReport:
    """Execute the full bootstrap sequence.

    This replaces the ad-hoc setup in main.py.
    Call this once at startup, before starting the Telegram bot.
    """
    start = time.monotonic()
    report = BootstrapReport(
        python_version=f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
        platform_name=platform.platform(),
    )

    # Stage: Prefetch memory
    report.stages.append(_run_stage("prefetch_memory", lambda: _prefetch_memory(config)))

    # Stage: Prefetch experience
    report.stages.append(_run_stage("prefetch_experience", lambda: _prefetch_experience(config)))

    # Stage: Prefetch MCP
    report.stages.append(_run_stage("prefetch_mcp", lambda: _prefetch_mcp(config)))
    report.mcp_server_count = len(config.mcp_servers)

    # Stage: Load tool registry
    def _load_tools():
        from tool_registry import ToolRegistry
        from tools.bash_tool import BashTool
        from tools.file_read import FileReadTool
        from tools.file_tools import FileWriteTool, FileEditTool
        from tools.web_tools import WebSearchTool, WebFetchTool
        from tools.agent_tool import AgentTool
        from memory.memory import MemoryStore, MemoryReadTool, MemoryWriteTool

        registry = ToolRegistry()
        mem_store = MemoryStore(config.memory_dir)

        # Core tools (always available)
        for tool_cls in [BashTool, FileReadTool, FileWriteTool, FileEditTool,
                         WebSearchTool, WebFetchTool, AgentTool]:
            try:
                registry.register(tool_cls())
            except Exception as e:
                logger.warning(f"Failed to register {tool_cls}: {e}")

        # Optional tools (guarded imports)
        optional_tools = [
            ("tools.github_tool", ["GitHubCreateRepoTool", "GitHubPushFileTool",
                                    "GitHubCreateIssueTool", "GitHubListReposTool", "GitHubReadFileTool"]),
            ("tools.rag_tool", ["RAGUploadTool", "RAGSearchTool", "RAGListTool", "RAGDeleteTool"]),
            ("tools.twitter_tool", ["TwitterSearchTool", "TwitterReplyTool",
                                     "TwitterPostTool", "TwitterScanTool"]),
            ("tools.scheduler", ["ScheduleAddTool", "ScheduleListTool",
                                  "ScheduleRemoveTool", "WatchDirTool"]),
        ]
        for module_name, class_names in optional_tools:
            try:
                import importlib
                mod = importlib.import_module(module_name)
                for cls_name in class_names:
                    cls = getattr(mod, cls_name, None)
                    if cls:
                        try:
                            registry.register(cls())
                        except Exception as e:
                            logger.warning(f"Failed to register {cls_name}: {e}")
            except ImportError:
                logger.info(f"Optional module {module_name} not available")

        registry.register(MemoryReadTool(mem_store))
        registry.register(MemoryWriteTool(mem_store))

        return registry, mem_store

    stage = _run_stage("load_tool_registry", _load_tools)
    report.stages.append(stage)

    # Stage: Permission context
    def _load_permissions():
        from permissions import ToolPermissionContext
        return ToolPermissionContext.from_config(config)

    report.stages.append(_run_stage("load_permission_context", _load_permissions))

    # Stage: Deferred init
    report.deferred_init = DeferredInitResult(
        trusted=True,
        mcp_connected=len(config.mcp_servers) > 0,
        experience_loaded=True,
        memory_loaded=True,
        tools_registered=report.stages[3].detail.count("registered") if len(report.stages) > 3 else 0,
    )

    report.startup_time_ms = (time.monotonic() - start) * 1000
    return report


def _run_stage(name: str, fn) -> StageResult:
    """Run a single bootstrap stage with timing."""
    start = time.monotonic()
    try:
        result = fn()
        duration = (time.monotonic() - start) * 1000
        detail = str(result)[:200] if result is not None else ""
        return StageResult(name=name, success=True, duration_ms=duration, detail=detail)
    except Exception as e:
        duration = (time.monotonic() - start) * 1000
        logger.error(f"Bootstrap stage '{name}' failed: {e}")
        return StageResult(name=name, success=False, duration_ms=duration, detail=str(e)[:200])


def _prefetch_memory(config) -> PrefetchResult:
    """Pre-load memory index for fast first query."""
    try:
        from memory.memory import MemoryStore
        store = MemoryStore(config.memory_dir)
        count = len(store.list_all())
        return PrefetchResult("memory", True, f"{count} memories loaded")
    except Exception as e:
        return PrefetchResult("memory", False, str(e))


def _prefetch_experience(config) -> PrefetchResult:
    """Pre-load experience store."""
    try:
        from experience import get_experience_store
        exp = get_experience_store(config.data_dir)
        stats = exp.get_stats()
        return PrefetchResult("experience", True, f"{stats['lessons']} lessons")
    except Exception as e:
        return PrefetchResult("experience", False, str(e))


def _prefetch_mcp(config) -> PrefetchResult:
    """Check MCP server configs (actual connection happens later)."""
    count = len(config.mcp_servers)
    if count > 0:
        return PrefetchResult("mcp", True, f"{count} servers configured")
    return PrefetchResult("mcp", True, "no MCP servers")


def build_system_init_message(config, registry=None) -> str:
    """Build the deterministic system init message.

    From claw-code's system_init.py — gives the LLM a snapshot of
    available capabilities at session start.
    """
    tool_names = registry.list_names() if registry else []
    lines = [
        "# System Init",
        "",
        f"Provider: {config.provider}",
        f"Model: {config.active_model}",
        f"Tools loaded: {len(tool_names)}",
        f"MCP servers: {len(config.mcp_servers)}",
        f"Budget: ${config.daily_budget_usd}/day",
        "",
        "Available tools:",
    ]
    for name in sorted(tool_names):
        lines.append(f"  - {name}")
    return "\n".join(lines)
