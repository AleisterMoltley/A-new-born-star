"""Scheduler — Autonomous task execution.

Cron-like scheduling: the bot executes tasks on its own without being prompted.
File watching: auto-process files dropped into a watch directory.
Recurring jobs: health checks, monitoring, periodic scans.

Tools:
  schedule_add    — Add a recurring or one-shot task
  schedule_list   — List all scheduled tasks
  schedule_remove — Remove a scheduled task
  watch_dir       — Watch a directory for new files

The scheduler runs as a background asyncio task alongside the Telegram bot.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Callable, Awaitable

from tool_registry import BaseTool, ToolResult, ToolContext
import logging

logger = logging.getLogger(__name__)


@dataclass
class ScheduledTask:
    id: str
    description: str  # What to do (natural language — sent to the LLM)
    interval_seconds: int = 0  # 0 = one-shot
    next_run: float = 0.0
    last_run: float = 0.0
    run_count: int = 0
    enabled: bool = True
    created_at: float = field(default_factory=time.time)

    @property
    def is_recurring(self) -> bool:
        return self.interval_seconds > 0

    @property
    def is_due(self) -> bool:
        return self.enabled and time.time() >= self.next_run

    def to_dict(self) -> dict:
        return {
            "id": self.id, "description": self.description,
            "interval_seconds": self.interval_seconds,
            "next_run": self.next_run, "last_run": self.last_run,
            "run_count": self.run_count, "enabled": self.enabled,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "ScheduledTask":
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


class Scheduler:
    """Background scheduler that runs tasks autonomously."""

    def __init__(self, data_dir: str = ""):
        self._tasks: dict[str, ScheduledTask] = {}
        self._watchers: dict[str, dict] = {}  # path -> {callback, seen_files}
        self._running = False
        self._data_dir = Path(data_dir or Path.home() / ".compagnon")
        self._data_dir.mkdir(parents=True, exist_ok=True)
        self._save_path = self._data_dir / "scheduler.json"
        self._load()

        # Set by telegram_interface at startup
        self.on_task_execute: Optional[Callable[[str, str], Awaitable[str]]] = None
        self.on_notify: Optional[Callable[[str], Awaitable[None]]] = None

    def _load(self):
        if self._save_path.exists():
            try:
                data = json.loads(self._save_path.read_text())
                for td in data.get("tasks", []):
                    task = ScheduledTask.from_dict(td)
                    self._tasks[task.id] = task
                logger.info("Scheduler loaded %d tasks", len(self._tasks))
            except Exception as e:
                logger.warning("Scheduler load failed: %s", e)

    def _save(self):
        try:
            data = {"tasks": [t.to_dict() for t in self._tasks.values()]}
            self._save_path.write_text(json.dumps(data, indent=2))
        except Exception as e:
            logger.warning("Scheduler save failed: %s", e)

    def add_task(self, task_id: str, description: str, interval_seconds: int = 0,
                 delay_seconds: int = 0) -> ScheduledTask:
        """Add a task. interval=0 for one-shot, >0 for recurring."""
        task = ScheduledTask(
            id=task_id,
            description=description,
            interval_seconds=interval_seconds,
            next_run=time.time() + delay_seconds,
        )
        self._tasks[task_id] = task
        self._save()
        logger.info("Scheduled: %s (every %ds)" if interval_seconds else "Scheduled: %s (one-shot)",
                     task_id, interval_seconds)
        return task

    def remove_task(self, task_id: str) -> bool:
        if task_id in self._tasks:
            del self._tasks[task_id]
            self._save()
            return True
        return False

    def list_tasks(self) -> list[dict]:
        now = time.time()
        result = []
        for t in self._tasks.values():
            d = t.to_dict()
            d["seconds_until_next"] = max(0, t.next_run - now) if t.enabled else -1
            result.append(d)
        return sorted(result, key=lambda x: x["next_run"])

    def add_watcher(self, path: str, description: str = "Process new file"):
        """Watch a directory for new files."""
        p = Path(path)
        p.mkdir(parents=True, exist_ok=True)
        existing = {f.name for f in p.iterdir() if f.is_file()}
        self._watchers[str(p)] = {
            "description": description,
            "seen": existing,
        }
        logger.info("Watching: %s", p)

    async def run(self):
        """Main scheduler loop — runs alongside Telegram bot."""
        self._running = True
        logger.info("Scheduler started (%d tasks, %d watchers)",
                     len(self._tasks), len(self._watchers))

        while self._running:
            try:
                await self._tick()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("Scheduler tick error: %s", e, exc_info=True)
            await asyncio.sleep(5)  # Check every 5 seconds

        logger.info("Scheduler stopped")

    async def _tick(self):
        now = time.time()

        # ── Check scheduled tasks ──
        for task in list(self._tasks.values()):
            if not task.is_due:
                continue

            logger.info("⏰ Running scheduled task: %s", task.id)
            task.last_run = now
            task.run_count += 1

            if task.is_recurring:
                task.next_run = now + task.interval_seconds
            else:
                task.enabled = False  # One-shot: disable after run

            self._save()

            # Execute the task via LLM
            if self.on_task_execute:
                try:
                    result = await self.on_task_execute(task.id, task.description)
                    # Notify user of result
                    if self.on_notify and result:
                        summary = result[:500] if len(result) > 500 else result
                        await self.on_notify(f"⏰ <b>{task.id}</b>\n\n{summary}")
                except Exception as e:
                    logger.error("Task %s failed: %s", task.id, e)
                    if self.on_notify:
                        await self.on_notify(f"⏰ <b>{task.id}</b> failed: {e}")

        # ── Check file watchers ──
        for path_str, watcher in self._watchers.items():
            p = Path(path_str)
            if not p.exists():
                continue

            current_files = {f.name for f in p.iterdir() if f.is_file()}
            new_files = current_files - watcher["seen"]

            for fname in new_files:
                filepath = p / fname
                logger.info("📁 New file detected: %s", filepath)
                watcher["seen"].add(fname)

                if self.on_task_execute:
                    desc = f"{watcher['description']}: {filepath}"
                    try:
                        result = await self.on_task_execute(f"file_watch_{fname}", desc)
                        if self.on_notify and result:
                            summary = result[:500]
                            await self.on_notify(f"📁 <b>{fname}</b>\n\n{summary}")
                    except Exception as e:
                        logger.error("File watch task failed: %s", e)

    def stop(self):
        self._running = False


# Singleton
_scheduler: Optional[Scheduler] = None


def get_scheduler(data_dir: str = "") -> Scheduler:
    global _scheduler
    if _scheduler is None:
        _scheduler = Scheduler(data_dir)
    return _scheduler


# ── Tools ──────────────────────────────────────────────────────

def _parse_interval(text: str) -> int:
    """Parse human interval: '5m', '1h', '30s', '2h30m'."""
    import re
    total = 0
    for match in re.finditer(r'(\d+)\s*(s|m|h|d)', text.lower()):
        val = int(match.group(1))
        unit = match.group(2)
        if unit == 's': total += val
        elif unit == 'm': total += val * 60
        elif unit == 'h': total += val * 3600
        elif unit == 'd': total += val * 86400
    # If just a number, treat as seconds
    if total == 0:
        try:
            total = int(text)
        except ValueError:
            pass
    return total


class ScheduleAddTool(BaseTool):
    category = "scheduler"
    name = "schedule_add"
    description = (
        "Schedule a recurring or one-shot autonomous task. The bot will execute "
        "the task description as if you sent it as a message. "
        "Examples: 'check if my Railway services are running', "
        "'scan Twitter for polymarket mentions', 'summarize new files in /data'."
    )
    is_read_only = False

    def get_input_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "task_id": {"type": "string", "description": "Unique short name (e.g. 'health_check')."},
                "description": {"type": "string", "description": "What to do (natural language task)."},
                "interval": {"type": "string", "description": "Repeat interval: '5m', '1h', '30s', '2h'. Empty for one-shot."},
                "delay": {"type": "string", "description": "Delay before first run: '10s', '5m'. Default: immediate."},
            },
            "required": ["task_id", "description"],
        }

    def needs_confirmation(self, params, config):
        return False

    async def execute(self, params: dict, context: ToolContext) -> ToolResult:
        task_id = params.get("task_id", "")
        desc = params.get("description", "")
        interval = _parse_interval(params.get("interval", ""))
        delay = _parse_interval(params.get("delay", "0"))

        if not task_id or not desc:
            return ToolResult(error="Need task_id and description", is_error=True)

        data_dir = context.config.data_dir if context.config else ""
        sched = get_scheduler(data_dir)
        task = sched.add_task(task_id, desc, interval, delay)

        interval_str = f"every {interval}s" if interval else "one-shot"
        delay_str = f" (starts in {delay}s)" if delay else ""
        return ToolResult(output=f"Scheduled '{task_id}': {desc}\n{interval_str}{delay_str}")


class ScheduleListTool(BaseTool):
    category = "scheduler"
    name = "schedule_list"
    description = "List all scheduled tasks with their status and next run time."
    is_read_only = True

    def get_input_schema(self) -> dict:
        return {"type": "object", "properties": {}}

    def needs_confirmation(self, params, config):
        return False

    async def execute(self, params: dict, context: ToolContext) -> ToolResult:
        data_dir = context.config.data_dir if context.config else ""
        sched = get_scheduler(data_dir)
        tasks = sched.list_tasks()
        if not tasks:
            return ToolResult(output="No scheduled tasks.")
        lines = [f"{len(tasks)} tasks:\n"]
        for t in tasks:
            status = "✅" if t["enabled"] else "⏸️"
            interval = f"every {t['interval_seconds']}s" if t["interval_seconds"] else "one-shot"
            next_in = f"in {t['seconds_until_next']:.0f}s" if t["seconds_until_next"] >= 0 else "disabled"
            lines.append(f"  {status} {t['id']} — {interval} — {next_in} — runs: {t['run_count']}")
            lines.append(f"      {t['description'][:80]}")
        return ToolResult(output="\n".join(lines))


class ScheduleRemoveTool(BaseTool):
    category = "scheduler"
    name = "schedule_remove"
    description = "Remove a scheduled task by ID."
    is_read_only = False

    def get_input_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {"task_id": {"type": "string", "description": "Task ID to remove."}},
            "required": ["task_id"],
        }

    def needs_confirmation(self, params, config):
        return False

    async def execute(self, params: dict, context: ToolContext) -> ToolResult:
        task_id = params.get("task_id", "")
        data_dir = context.config.data_dir if context.config else ""
        sched = get_scheduler(data_dir)
        if sched.remove_task(task_id):
            return ToolResult(output=f"Removed: {task_id}")
        return ToolResult(error=f"Not found: {task_id}", is_error=True)


class WatchDirTool(BaseTool):
    category = "scheduler"
    name = "watch_dir"
    description = (
        "Watch a directory for new files. When a file appears, the bot will "
        "automatically process it using the given instructions. "
        "Example: watch /data/logs with 'summarize this log file and alert if errors found'."
    )
    is_read_only = False

    def get_input_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Directory to watch."},
                "task": {"type": "string", "description": "What to do when a new file appears."},
            },
            "required": ["path"],
        }

    def needs_confirmation(self, params, config):
        return False

    async def execute(self, params: dict, context: ToolContext) -> ToolResult:
        path = params.get("path", "")
        task = params.get("task", "Process and summarize the new file")
        if not path:
            return ToolResult(error="Need path", is_error=True)

        p = Path(path)
        if not p.is_absolute():
            p = Path(context.working_dir) / p

        data_dir = context.config.data_dir if context.config else ""
        sched = get_scheduler(data_dir)
        sched.add_watcher(str(p.resolve()), task)
        return ToolResult(output=f"Watching: {p.resolve()}\nTask: {task}")
