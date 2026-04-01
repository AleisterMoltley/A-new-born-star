"""
Tool Registry v3 — With permission gating, tool pool assembly, and execution registry.

Merges patterns from claw-code:
- ToolPermissionContext integration (blocks tools before execution)
- ToolPool assembly (filtered snapshot of available tools)
- ExecutionRegistry (typed command/tool dispatch)
- Structured ToolExecution results with audit trail
"""
from __future__ import annotations
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from config import CompagnonConfig
    from permissions import ToolPermissionContext


@dataclass
class ToolResult:
    output: str = ""
    error: str = ""
    is_error: bool = False
    metadata: dict = field(default_factory=dict)

    def to_content(self) -> list[dict]:
        if self.is_error:
            return [{"type": "text", "text": f"Error: {self.error}"}]
        return [{"type": "text", "text": self.output}]


@dataclass
class ToolExecution:
    """Structured execution result — for audit trail."""
    name: str
    success: bool
    duration_ms: float = 0.0
    result: Optional[ToolResult] = None
    denied: bool = False
    denial_reason: str = ""

    @property
    def summary(self) -> str:
        if self.denied:
            return f"DENIED {self.name}: {self.denial_reason}"
        status = "ok" if self.success else "error"
        return f"{self.name} [{status}] ({self.duration_ms:.0f}ms)"


@dataclass
class ToolContext:
    working_dir: str = "."
    config: Optional[CompagnonConfig] = None
    session_id: str = ""
    agent_id: str = "main"
    depth: int = 0
    permission_callback: Any = None


class BaseTool(ABC):
    name: str = ""
    description: str = ""
    is_read_only: bool = False
    is_enabled_default: bool = True
    category: str = "core"

    @abstractmethod
    def get_input_schema(self) -> dict: ...

    @abstractmethod
    async def execute(self, params: dict, context: ToolContext) -> ToolResult: ...

    def needs_confirmation(self, params: dict, config: CompagnonConfig) -> bool:
        if self.is_read_only:
            return False
        return True

    def is_enabled(self) -> bool:
        return self.is_enabled_default

    def to_api_schema(self) -> dict:
        return {"name": self.name, "description": self.description, "input_schema": self.get_input_schema()}


class ToolRegistry:
    def __init__(self):
        self._tools: dict[str, BaseTool] = {}
        self._mcp_tools: dict[str, BaseTool] = {}

    def register(self, tool: BaseTool):
        self._tools[tool.name] = tool

    def register_mcp(self, tool: BaseTool):
        self._mcp_tools[tool.name] = tool

    def unregister(self, name: str):
        self._tools.pop(name, None)
        self._mcp_tools.pop(name, None)

    def get(self, name: str) -> Optional[BaseTool]:
        return self._tools.get(name) or self._mcp_tools.get(name)

    def get_all_enabled(self) -> list[BaseTool]:
        seen, result = set(), []
        for tool in sorted(self._tools.values(), key=lambda t: t.name):
            if tool.is_enabled() and tool.name not in seen:
                result.append(tool)
                seen.add(tool.name)
        for tool in sorted(self._mcp_tools.values(), key=lambda t: t.name):
            if tool.is_enabled() and tool.name not in seen:
                result.append(tool)
                seen.add(tool.name)
        return result

    def get_api_schemas(self) -> list[dict]:
        return [t.to_api_schema() for t in self.get_all_enabled()]

    def list_names(self) -> list[str]:
        return [t.name for t in self.get_all_enabled()]

    def get_by_category(self, category: str) -> list[BaseTool]:
        return [t for t in self.get_all_enabled() if t.category == category]

    def get_categories(self) -> dict[str, list[str]]:
        cats: dict[str, list[str]] = {}
        for tool in self.get_all_enabled():
            cats.setdefault(tool.category, []).append(tool.name)
        return cats

    def assemble_pool(
        self,
        permission_context: Optional[ToolPermissionContext] = None,
        include_mcp: bool = True,
        categories: Optional[set[str]] = None,
    ) -> "ToolPool":
        """Build a filtered tool pool snapshot (claw-code pattern)."""
        tools = self.get_all_enabled()
        if not include_mcp:
            tools = [t for t in tools if t.name not in self._mcp_tools]
        if categories:
            tools = [t for t in tools if t.category in categories]
        if permission_context:
            tools = [t for t in tools if not permission_context.blocks(t.name)]
        return ToolPool(
            tools=tuple(tools),
            schemas=tuple(t.to_api_schema() for t in tools),
            include_mcp=include_mcp,
            categories=categories or set(),
        )


@dataclass(frozen=True)
class ToolPool:
    """Immutable snapshot of available tools for a turn."""
    tools: tuple[BaseTool, ...]
    schemas: tuple[dict, ...]
    include_mcp: bool = True
    categories: set[str] = field(default_factory=set)

    @property
    def count(self) -> int:
        return len(self.tools)

    @property
    def names(self) -> list[str]:
        return [t.name for t in self.tools]

    def as_markdown(self) -> str:
        lines = [
            "# Tool Pool",
            f"Tools: {self.count} | MCP: {self.include_mcp}",
        ]
        for tool in self.tools:
            lines.append(f"  - {tool.name} [{tool.category}]")
        return "\n".join(lines)
