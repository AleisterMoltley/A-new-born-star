"""Planner — Multi-step task decomposition.

Before executing a complex task, the planner breaks it into steps,
identifies which can run in parallel, and optimizes execution order.

This is used internally by the QueryEngine — not exposed as a tool.
The LLM is asked to create a plan, then the engine executes it.

Inspired by Claude Code's EnterPlanModeTool/ExitPlanModeTool pattern.
"""

from __future__ import annotations

import json
import logging
from typing import Optional

logger = logging.getLogger(__name__)

PLAN_PROMPT = """Analyze this task and create a concise execution plan.

Task: {task}

Available tools: {tools}

Respond ONLY with a JSON object (no markdown, no explanation):
{{
  "steps": [
    {{"id": 1, "tool": "tool_name", "params": {{}}, "depends_on": [], "description": "what this does"}},
    ...
  ],
  "parallel_groups": [[1, 2], [3]]  // Steps that can run in parallel grouped together
}}

Rules:
- Use minimum steps necessary
- Group independent steps for parallel execution
- depends_on lists step IDs that must complete before this step
- If the task is simple (1-2 steps), just list them sequentially
- params must match the tool's input schema exactly
"""


def should_plan(message: str) -> bool:
    """Heuristic: should we create a plan for this message?

    Plans are useful for complex multi-step tasks but wasteful for
    simple questions or single-tool operations.
    """
    # Short messages are usually simple
    if len(message) < 50:
        return False

    # Multiple action words suggest complexity
    action_words = ["and", "then", "also", "after that", "first", "next",
                    "compare", "check all", "for each", "every"]
    count = sum(1 for w in action_words if w in message.lower())
    if count >= 2:
        return True

    # Explicit planning language
    plan_words = ["step by step", "plan", "systematically", "all the",
                  "multiple", "several", "each", "every"]
    if any(w in message.lower() for w in plan_words):
        return True

    return False


def parse_plan(response_text: str) -> Optional[dict]:
    """Parse a plan JSON from LLM response."""
    try:
        # Try direct parse
        plan = json.loads(response_text.strip())
        if "steps" in plan:
            return plan
    except json.JSONDecodeError:
        pass

    # Try extracting JSON from markdown code block
    import re
    match = re.search(r'```(?:json)?\s*(\{[\s\S]*?\})\s*```', response_text)
    if match:
        try:
            plan = json.loads(match.group(1))
            if "steps" in plan:
                return plan
        except json.JSONDecodeError:
            pass

    # Try finding raw JSON object
    match = re.search(r'\{[\s\S]*"steps"[\s\S]*\}', response_text)
    if match:
        try:
            plan = json.loads(match.group(0))
            if "steps" in plan:
                return plan
        except json.JSONDecodeError:
            pass

    return None


def get_execution_order(plan: dict) -> list[list[int]]:
    """Convert plan into execution groups (parallel within group, sequential between).

    Returns list of groups, each group is a list of step IDs to run in parallel.
    """
    # Use parallel_groups if provided
    if "parallel_groups" in plan:
        return plan["parallel_groups"]

    # Otherwise derive from dependencies
    steps = plan.get("steps", [])
    if not steps:
        return []

    completed = set()
    groups = []
    remaining = {s["id"] for s in steps}

    while remaining:
        # Find steps whose dependencies are all completed
        ready = []
        for s in steps:
            if s["id"] in remaining:
                deps = set(s.get("depends_on", []))
                if deps.issubset(completed):
                    ready.append(s["id"])

        if not ready:
            # Deadlock — just run remaining sequentially
            groups.append(list(remaining))
            break

        groups.append(ready)
        completed.update(ready)
        remaining -= set(ready)

    return groups
