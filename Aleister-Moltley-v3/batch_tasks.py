"""
Batch Tasks — Async background processing via Anthropic Batch API.

The Batch API is 50% cheaper than standard API calls.
Perfect for non-time-critical tasks:
- Dreamworld dreams (can wait minutes)
- Self-evaluation after task completion
- Periodic compact/summarization
- Experience analysis

How it works:
1. Queue tasks as batch requests
2. Submit batch to Anthropic
3. Poll for completion (or use callback)
4. Process results when ready

For time-critical tasks (user chat), use standard streaming API.
For background tasks, use this module to save 50%.

Fallback: if batch API unavailable, falls back to standard sync call.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Callable, Any
from uuid import uuid4

logger = logging.getLogger(__name__)

# Batch API pricing: 50% of standard
# Haiku batch: $0.50/$2.50 (input/output) per MTok
# Sonnet batch: $1.50/$7.50 per MTok


@dataclass
class BatchTask:
    """A single task to be processed in a batch."""
    task_id: str
    task_type: str  # "dream", "self_eval", "compact", "experience"
    prompt: str
    system: str = ""
    model: str = "claude-haiku-4-5-20251001"  # Default to cheapest model
    max_tokens: int = 2048
    temperature: float = 0.7
    callback: Optional[str] = None  # Callback function name
    metadata: dict = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)

    # Result (filled after completion)
    result: str = ""
    completed: bool = False
    error: str = ""


@dataclass
class BatchSubmission:
    """A submitted batch of tasks."""
    batch_id: str
    tasks: list[BatchTask]
    submitted_at: float = field(default_factory=time.time)
    completed_at: float = 0.0
    status: str = "pending"  # pending, processing, completed, failed


class BatchProcessor:
    """Manages batch task submission and processing.

    Usage:
        processor = BatchProcessor(api_key, data_dir)

        # Queue tasks
        processor.queue("dream", prompt="Dream about...", model="claude-haiku-4-5-20251001")
        processor.queue("self_eval", prompt="Evaluate...", model="claude-haiku-4-5-20251001")

        # Submit batch (or auto-submit when queue reaches threshold)
        await processor.submit_batch()

        # Process results
        results = await processor.poll_results()
    """

    def __init__(self, api_key: str = "", data_dir: str = "",
                 auto_submit_threshold: int = 5,
                 max_queue_age_seconds: float = 60.0):
        self._api_key = api_key
        self._data_dir = Path(data_dir or ".") / "batch_tasks"
        self._data_dir.mkdir(parents=True, exist_ok=True)
        self._queue: list[BatchTask] = []
        self._submissions: list[BatchSubmission] = []
        self._callbacks: dict[str, Callable] = {}
        self._auto_submit_threshold = auto_submit_threshold
        self._max_queue_age = max_queue_age_seconds
        self._oldest_queued: float = 0.0
        self._total_tasks = 0
        self._total_saved_usd = 0.0

    def register_callback(self, name: str, fn: Callable):
        """Register a callback for batch result processing."""
        self._callbacks[name] = fn

    def queue(self, task_type: str, prompt: str, system: str = "",
              model: str = "claude-haiku-4-5-20251001", max_tokens: int = 2048,
              temperature: float = 0.7, callback: str = "",
              metadata: dict = None) -> str:
        """Add a task to the batch queue. Returns task_id."""
        task_id = uuid4().hex[:12]
        task = BatchTask(
            task_id=task_id,
            task_type=task_type,
            prompt=prompt,
            system=system,
            model=model,
            max_tokens=max_tokens,
            temperature=temperature,
            callback=callback,
            metadata=metadata or {},
        )
        self._queue.append(task)
        if not self._oldest_queued:
            self._oldest_queued = time.time()
        self._total_tasks += 1
        logger.debug(f"Batch queue: +{task_type} ({len(self._queue)} pending)")
        return task_id

    def should_submit(self) -> bool:
        """Check if we should submit the current queue."""
        if not self._queue:
            return False
        if len(self._queue) >= self._auto_submit_threshold:
            return True
        if self._oldest_queued and (time.time() - self._oldest_queued) > self._max_queue_age:
            return True
        return False

    async def submit_batch(self) -> Optional[str]:
        """Submit queued tasks as a batch. Returns batch_id or None."""
        if not self._queue:
            return None

        tasks = list(self._queue)
        self._queue.clear()
        self._oldest_queued = 0.0

        batch_id = uuid4().hex[:12]
        submission = BatchSubmission(batch_id=batch_id, tasks=tasks)
        self._submissions.append(submission)

        # Try Batch API first, fall back to sequential
        try:
            if self._api_key:
                await self._submit_via_batch_api(submission)
            else:
                await self._submit_sequential_fallback(submission)
        except Exception as e:
            logger.warning(f"Batch API failed, falling back to sequential: {e}")
            await self._submit_sequential_fallback(submission)

        return batch_id

    async def _submit_via_batch_api(self, submission: BatchSubmission):
        """Submit via Anthropic Message Batches API (50% cheaper)."""
        try:
            import anthropic
            client = anthropic.Anthropic(api_key=self._api_key)

            # Build batch requests
            requests = []
            for task in submission.tasks:
                messages = [{"role": "user", "content": task.prompt}]
                req = {
                    "custom_id": task.task_id,
                    "params": {
                        "model": task.model,
                        "max_tokens": task.max_tokens,
                        "temperature": task.temperature,
                        "messages": messages,
                    },
                }
                if task.system:
                    req["params"]["system"] = task.system
                requests.append(req)

            # Submit batch
            batch = client.messages.batches.create(requests=requests)
            submission.status = "processing"
            logger.info(f"Batch {submission.batch_id} submitted: {len(requests)} tasks via Batch API")

            # Poll for completion in background
            asyncio.create_task(self._poll_batch(client, batch.id, submission))

        except ImportError:
            raise
        except Exception as e:
            logger.warning(f"Batch API submission failed: {e}")
            raise

    async def _poll_batch(self, client, batch_api_id: str, submission: BatchSubmission):
        """Poll batch API for results."""
        try:
            max_polls = 60  # 5 minutes max
            for _ in range(max_polls):
                await asyncio.sleep(5)
                try:
                    batch = client.messages.batches.retrieve(batch_api_id)
                    if batch.processing_status == "ended":
                        # Fetch results
                        for result in client.messages.batches.results(batch_api_id):
                            task_id = result.custom_id
                            for task in submission.tasks:
                                if task.task_id == task_id:
                                    if result.result.type == "succeeded":
                                        text_parts = []
                                        for block in result.result.message.content:
                                            if hasattr(block, "text"):
                                                text_parts.append(block.text)
                                        task.result = "\n".join(text_parts)
                                        task.completed = True
                                    else:
                                        task.error = str(result.result.error) if hasattr(result.result, "error") else "failed"
                                    break

                        submission.status = "completed"
                        submission.completed_at = time.time()
                        await self._process_callbacks(submission)
                        logger.info(f"Batch {submission.batch_id} completed: {len(submission.tasks)} tasks")
                        return
                except Exception as e:
                    logger.debug(f"Batch poll error: {e}")

            submission.status = "timeout"
            logger.warning(f"Batch {submission.batch_id} timed out")
        except Exception as e:
            submission.status = "failed"
            logger.error(f"Batch polling failed: {e}")

    async def _submit_sequential_fallback(self, submission: BatchSubmission):
        """Fallback: process tasks sequentially via standard API.

        Still useful — we use Haiku for background tasks regardless.
        """
        try:
            import anthropic
            client = anthropic.Anthropic(api_key=self._api_key) if self._api_key else None
        except ImportError:
            client = None

        for task in submission.tasks:
            try:
                if client:
                    messages = [{"role": "user", "content": task.prompt}]
                    kwargs = {
                        "model": task.model,
                        "max_tokens": task.max_tokens,
                        "temperature": task.temperature,
                        "messages": messages,
                    }
                    if task.system:
                        kwargs["system"] = task.system
                    response = client.messages.create(**kwargs)
                    task.result = "\n".join(
                        b.text for b in response.content if hasattr(b, "text")
                    )
                    task.completed = True
                else:
                    task.error = "no API client"
            except Exception as e:
                task.error = str(e)[:200]
                logger.warning(f"Sequential fallback failed for {task.task_id}: {e}")

        submission.status = "completed"
        submission.completed_at = time.time()
        await self._process_callbacks(submission)

    async def _process_callbacks(self, submission: BatchSubmission):
        """Process callbacks for completed tasks."""
        for task in submission.tasks:
            if task.completed and task.callback and task.callback in self._callbacks:
                try:
                    cb = self._callbacks[task.callback]
                    if asyncio.iscoroutinefunction(cb):
                        await cb(task)
                    else:
                        cb(task)
                except Exception as e:
                    logger.warning(f"Callback {task.callback} failed: {e}")

    async def auto_submit_loop(self, interval: float = 10.0):
        """Background loop that auto-submits when threshold is reached."""
        while True:
            try:
                if self.should_submit():
                    await self.submit_batch()
                await asyncio.sleep(interval)
            except asyncio.CancelledError:
                # Submit remaining on shutdown
                if self._queue:
                    await self.submit_batch()
                break
            except Exception as e:
                logger.error(f"Batch auto-submit error: {e}")
                await asyncio.sleep(30)

    def get_stats(self) -> dict:
        completed = sum(1 for s in self._submissions if s.status == "completed")
        total_tasks = sum(len(s.tasks) for s in self._submissions)
        return {
            "queued": len(self._queue),
            "submissions": len(self._submissions),
            "completed_batches": completed,
            "total_tasks": total_tasks,
        }

    def format_stats(self) -> str:
        s = self.get_stats()
        return f"Batch: {s['queued']} queued, {s['completed_batches']}/{s['submissions']} batches done, {s['total_tasks']} total tasks"
