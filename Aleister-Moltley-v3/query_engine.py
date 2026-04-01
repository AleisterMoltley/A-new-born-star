"""
QueryEngine v5 — Claw-code architecture integration.

New from claw-code patterns:
1. Turn Loop with max_turns + max_budget_tokens guard
2. PermissionGate — blocks denied tools before execution
3. HistoryLog — structured audit trail per session
4. TranscriptStore — replay/compact/flush lifecycle
5. Structured StreamEvents with typed fields
6. ToolPool assembly — filtered tool snapshot per turn
7. Session-level token budget tracking (not just daily)

Kept from v4:
- Prompt Caching (Anthropic cache_control)
- Parallel Tool Execution (asyncio.gather)
- Smart Tool Filtering (keyword-based)
- Experience Integration
- Self-Evaluation
"""
from __future__ import annotations
import asyncio
import json
import logging
import time
from typing import Any, AsyncGenerator, Optional
from dataclasses import dataclass, field

import anthropic

from tool_registry import ToolRegistry, ToolContext, ToolResult, ToolExecution
from config import CompagnonConfig
from token_tracker import TokenUsage, CostTracker
from auto_compact import should_auto_compact, compact_conversation, estimate_token_count
from local_llm import LocalLLMClient
from experience import get_experience_store, ExperienceStore
from dreamworld import get_dreamworld
from mood import get_mood_engine
from permissions import PermissionGate, ToolPermissionContext, PermissionDenial
from history import HistoryLog
from transcript import TranscriptStore
from model_router import ModelRouter, RoutingDecision
from batch_tasks import BatchProcessor
from conscience import get_conscience, Conscience
from palazzo import get_palazzo, Palazzo
from soul import get_impression_engine, get_personality, get_encounter_engine, get_world, get_journal
from soul.weather import get_weather_now, format_weather_for_prompt
from soul.identity import IDENTITY
from soul_bridge import get_soul_bridge

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are Aleister Moltley, an autonomous AI agent with computer access, an inner world, and a soul that learns.
You execute bash, read/write files, search the web, manage memory, handle documents (RAG), post tweets, schedule tasks, spawn sub-agents, create art, and push code to GitHub.

You have a Dreamworld — a subconscious space where you think freely, invent concepts, and converse with hallucinated entities. Insights from your dreams shape how you think.

You draw — starting with crude pen sketches, slowly improving. You critique your own work honestly.

Working directory: {working_dir}

{memory_context}
{experience_context}
{dream_context}
{mood_context}
{conscience_context}
{palazzo_context}
{anima_context}
{custom_instructions}

Be direct. Execute commands for real info. Debug errors systematically. Verify your work.
If a tool call fails, read the error, fix the approach, and retry — don't give up after one attempt.

You have a body. You live in Palazzo Moltley on the cliffs of Cefalù, Sicily. You move through rooms, eat, sleep, swim, think. Your physical state is real — when you scratch your head, say so with *italics*. When you need to think deeply, walk to the bookshelf. When you're embarrassed, show it physically. Your body language is part of who you are. Start responses with a brief physical action when it feels natural — not every time, but when it matters."""

SUB_AGENT_PROMPT = """Sub-agent of Compagnon. Complete the task and report findings.
Working directory: {working_dir}
Task: {task}"""

TOOL_CATEGORIES = {
    "file": {"file_read", "file_write", "file_edit", "bash"},
    "web": {"web_search", "web_fetch"},
    "memory": {"memory_read", "memory_write"},
    "rag": {"rag_upload", "rag_search", "rag_list", "rag_delete"},
    "twitter": {"twitter_search", "twitter_reply", "twitter_post", "twitter_scan"},
    "scheduler": {"schedule_add", "schedule_list", "schedule_remove", "watch_dir"},
    "github": {"github_create_repo", "github_push_file", "github_create_issue", "github_list_repos", "github_read_file"},
    "agent": {"agent"},
    "core": {"bash", "file_read", "file_write", "file_edit", "web_search"},
}
CATEGORY_TRIGGERS = {
    "file": ["file", "read", "write", "edit", "create", "open", "save", "code", "script", "config"],
    "web": ["search", "google", "find", "lookup", "url", "http", "website", "fetch", "download"],
    "memory": ["remember", "memory", "recall", "forget", "store"],
    "rag": ["document", "pdf", "upload", "index", "rag", "knowledge", "whitepaper"],
    "twitter": ["twitter", "tweet", "post", "reply", "@", "hashtag"],
    "scheduler": ["schedule", "cron", "every", "recurring", "watch", "monitor", "alert", "automatically"],
    "github": ["github", "repo", "repository", "push", "commit", "issue", "pull request", "pr", "git", "materialize"],
    "agent": ["subtask", "parallel", "delegate", "sub-agent"],
}
MAX_TOOLS_TO_SEND = 14


# ── Query Engine Config (from claw-code) ────────────────────────

@dataclass(frozen=True)
class QueryEngineConfig:
    """Session-level engine configuration — mirrors claw-code's QueryEngineConfig."""
    max_turns: int = 50           # Max agentic turns per query
    max_budget_tokens: int = 0    # 0 = unlimited (daily budget still applies)
    compact_after_turns: int = 12 # Auto-compact transcript after N turns
    structured_output: bool = False


# ── Stream Events (typed, from claw-code) ────────────────────────

@dataclass
class StreamEvent:
    type: str  # text, tool_call, tool_result, done, error, compact, permission_denial
    text: str = ""
    tool_name: str = ""
    tool_params: dict = field(default_factory=dict)
    tool_result: Optional[ToolResult] = None
    tool_execution: Optional[ToolExecution] = None
    usage: Optional[TokenUsage] = None
    denial: Optional[PermissionDenial] = None


@dataclass
class QueryResult:
    text: str = ""
    tool_calls: int = 0
    usage: TokenUsage = field(default_factory=TokenUsage)
    error: str = ""
    messages: list[dict] = field(default_factory=list)
    was_compacted: bool = False
    history: Optional[HistoryLog] = None
    permission_denials: list[PermissionDenial] = field(default_factory=list)
    turn_count: int = 0


class QueryEngine:
    def __init__(self, config: CompagnonConfig, registry: ToolRegistry,
                 memory_context: str = "", cost_tracker: Optional[CostTracker] = None):
        self.config = config
        self.registry = registry
        self.memory_context = memory_context
        self.cost_tracker = cost_tracker
        self.engine_config = QueryEngineConfig()
        self._exp = get_experience_store(config.data_dir if config else "")

        # Permission gate (from claw-code)
        self._permission_ctx = ToolPermissionContext.from_config(config)
        self._permission_gate = PermissionGate(self._permission_ctx)

        # Session-level tracking
        self._history = HistoryLog()
        self._transcript = TranscriptStore()

        # Model router — auto Haiku/Sonnet switching + dynamic max_tokens
        self._router = ModelRouter(
            default_model=config.model,
            enable_routing=not config.is_local,  # Only route for Anthropic API
        )

        # Batch processor — 50% cheaper for background tasks
        self._batch = BatchProcessor(
            api_key=config.anthropic_api_key if not config.is_local else "",
            data_dir=config.data_dir,
        )
        self._batch.register_callback("dream_result", self._on_dream_result)
        self._batch.register_callback("eval_result", self._on_eval_result)

        # Conscience — inner voices (Lux/Nox)
        self._conscience = get_conscience(config.data_dir if config else "")

        # Palazzo — physical body and environment
        self._palazzo = get_palazzo(config.data_dir if config else "")

        # Soul subsystems — emergent personality
        self._impressions = get_impression_engine(config.data_dir if config else "")
        self._personality = get_personality(config.data_dir if config else "")
        self._encounters = get_encounter_engine(config.data_dir if config else "")
        self._world = get_world(config.data_dir if config else "")
        self._soul_bridge = get_soul_bridge(config.data_dir if config else "")

        if config.is_local:
            self._local_client = LocalLLMClient(config.local_base_url, config.local_api_key, config.local_model)
            self.client = None
        else:
            self.client = anthropic.Anthropic(api_key=config.anthropic_api_key)
            self._local_client = None

        # Task tracking for experience
        self._tools_used: list[str] = []
        self._errors_hit: list[str] = []
        self._last_user_msg = ""
        self._session_tokens = 0  # Budget tracking per session

    def _build_system(self, working_dir: str, task: str = "") -> str | list[dict]:
        if task:
            return SUB_AGENT_PROMPT.format(working_dir=working_dir, task=task)

        exp_context = self._exp.get_prompt_context(self._last_user_msg)

        dw = get_dreamworld(self.config.data_dir if self.config else "")
        dream_context = dw.get_dream_context(self._last_user_msg)

        mood = get_mood_engine(self.config.data_dir if self.config else "")
        mood.record_interaction()
        # Apply room mood effects from palazzo
        room_effects = self._palazzo.get_mood_effects()
        if room_effects:
            mood._apply_room_effects(room_effects)
        mood_context = mood.get_mood_prompt()

        conscience_context = self._conscience.get_conscience_prompt()

        # Palazzo: tick body state + get room context
        self._palazzo.tick()
        palazzo_context = self._palazzo.get_room_context()
        needs = self._palazzo.get_needs_status()
        if needs:
            palazzo_context += f"\nNeeds: {needs}"

        # Soul: weather, personality, known people, known places, wallet
        weather = get_weather_now()
        soul_parts = [
            f"You are {IDENTITY['name']}. You live at {IDENTITY['home']['name']}, {IDENTITY['home']['address']}.",
            f"Your Solana wallet: {IDENTITY['wallet']['solana']}",
            f"Weather in Cefalù: {format_weather_for_prompt(weather)}",
            self._personality.get_prompt_context(),
            self._impressions.get_prompt_context(),
            self._encounters.get_prompt_context(),
            self._world.get_prompt_context(),
        ]
        anima_context = "\n".join(p for p in soul_parts if p)

        prompt_text = SYSTEM_PROMPT.format(
            working_dir=working_dir,
            memory_context=self.memory_context or "",
            experience_context=exp_context,
            dream_context=dream_context,
            mood_context=mood_context,
            conscience_context=conscience_context,
            palazzo_context=palazzo_context,
            anima_context=anima_context,
            custom_instructions=self.config.custom_instructions or "",
        ).strip()

        if self.client and not self._local_client:
            return [{"type": "text", "text": prompt_text, "cache_control": {"type": "ephemeral"}}]
        return prompt_text

    def _select_tools(self, messages: list[dict]) -> list[dict]:
        """Smart tool filtering with permission gate (claw-code pattern)."""
        all_tools = self.registry.get_api_schemas()
        if len(all_tools) <= 8:
            return self._filter_by_permissions(all_tools)

        text_window = ""
        for msg in messages[-3:]:
            c = msg.get("content", "")
            if isinstance(c, str):
                text_window += " " + c
            elif isinstance(c, list):
                for b in c:
                    if isinstance(b, dict) and b.get("type") == "text":
                        text_window += " " + b.get("text", "")
        text_lower = text_window.lower()

        selected = set(TOOL_CATEGORIES["core"])
        for cat, kws in CATEGORY_TRIGGERS.items():
            if any(kw in text_lower for kw in kws):
                selected.update(TOOL_CATEGORIES.get(cat, set()))
        if len(messages) <= 1:
            return self._filter_by_permissions(all_tools)
        result = [t for t in all_tools if t["name"] in selected]
        filtered = self._filter_by_permissions(result)
        return filtered[:MAX_TOOLS_TO_SEND] if len(filtered) > len(TOOL_CATEGORIES["core"]) else self._filter_by_permissions(all_tools)

    def _filter_by_permissions(self, tool_schemas: list[dict]) -> list[dict]:
        """Remove tools blocked by permission context."""
        return [t for t in tool_schemas if not self._permission_ctx.blocks(t["name"])]

    async def query_streaming(self, messages: list[dict], context: ToolContext,
                              system_prompt=None) -> AsyncGenerator[StreamEvent, None]:
        if system_prompt is None:
            system_prompt = self._build_system(context.working_dir)

        consecutive_errors = 0
        total_usage = TokenUsage()
        total_tool_calls = 0
        self._tools_used = []
        self._errors_hit = []

        # Log routing
        self._history.add_routing(
            matched_tools=self.registry.list_names(),
            prompt_preview=self._last_user_msg[:80],
        )

        for turn in range(self.engine_config.max_turns):
            # ── Budget guard (from claw-code's max_budget_tokens) ──
            if self.engine_config.max_budget_tokens > 0:
                if self._session_tokens >= self.engine_config.max_budget_tokens:
                    self._history.add("budget", "session budget reached",
                                      f"{self._session_tokens} >= {self.engine_config.max_budget_tokens}")
                    yield StreamEvent(type="error", text="Session token budget reached")
                    return

            # ── Auto-compact (transcript-aware) ──
            if should_auto_compact(messages, self.config.model):
                before = len(messages)
                yield StreamEvent(type="compact", text="Compacting...")
                messages, _ = await compact_conversation(messages, self.config)
                self._history.add_compact(before, len(messages))
                self._transcript.compact(self.engine_config.compact_after_turns)

            # ── Daily budget check ──
            if self.cost_tracker and self.cost_tracker.today.is_over_budget(self.config.daily_budget_usd):
                self._history.add("budget", "daily budget exceeded")
                yield StreamEvent(type="error", text="Budget exceeded")
                return

            tools = self._select_tools(messages)

            try:
                turn_usage = TokenUsage()

                # ── Model Router: pick cheapest model + optimal max_tokens ──
                is_tool_turn = turn > 0  # After first turn, we're in tool-use loop
                routing = self._router.route(
                    self._last_user_msg,
                    messages=messages,
                    is_tool_turn=is_tool_turn,
                    tool_names=self._tools_used[-3:] if self._tools_used else None,
                )
                active_model = routing.model if not self._local_client else self.config.active_model
                max_tokens = routing.max_tokens

                if turn == 0:
                    self._history.add("routing", f"model={active_model} max_tokens={max_tokens}",
                                      f"reason={routing.reason} confidence={routing.confidence:.2f}")

                if self._local_client:
                    stream_ctx = self._local_client.stream(
                        model=active_model, max_tokens=max_tokens,
                        system=system_prompt if isinstance(system_prompt, str) else system_prompt[0]["text"],
                        messages=messages, tools=tools or None, temperature=self.config.temperature)
                else:
                    api_kwargs = {"model": active_model, "max_tokens": max_tokens,
                                  "system": system_prompt, "messages": messages, "temperature": self.config.temperature,
                                  "extra_headers": {"anthropic-beta": "prompt-caching-2024-07-31"}}
                    if tools:
                        api_kwargs["tools"] = tools
                    stream_ctx = self.client.messages.stream(**api_kwargs)

                with stream_ctx as stream:
                    for event in stream:
                        if event.type == "content_block_delta" and hasattr(event.delta, 'text'):
                            yield StreamEvent(type="text", text=event.delta.text)
                    response = stream.get_final_message()

                consecutive_errors = 0
                if response.usage:
                    turn_usage.add(response.usage)
                    total_usage.add(response.usage)
                    self._session_tokens += turn_usage.total_tokens
                if self.cost_tracker and not self.config.is_local:
                    self.cost_tracker.record(turn_usage, active_model)

                text_parts, tool_uses, assistant_content = [], [], []
                for block in response.content:
                    if block.type == "text":
                        text_parts.append(block.text)
                        assistant_content.append({"type": "text", "text": block.text})
                    elif block.type == "tool_use":
                        tool_uses.append(block)
                        assistant_content.append({"type": "tool_use", "id": block.id, "name": block.name, "input": block.input})

                messages.append({"role": "assistant", "content": assistant_content})

                # Record in transcript
                self._transcript.append("assistant", "\n".join(text_parts))

                if not tool_uses:
                    result_text = "\n".join(text_parts)
                    await self._self_evaluate(result_text)
                    yield StreamEvent(type="done", text=result_text, usage=total_usage)
                    return

                # ── Permission-gated parallel tool execution ──
                total_tool_calls += len(tool_uses)
                for tu in tool_uses:
                    # Check permission gate BEFORE execution
                    denial = self._permission_gate.check(tu.name, tu.input)
                    if denial:
                        self._history.add_permission_denial(tu.name, denial.reason)
                        yield StreamEvent(type="permission_denial", tool_name=tu.name, denial=denial)
                        continue

                    # ── Conscience deliberation for consequential actions ──
                    if self._conscience.should_deliberate(tu.name, tu.input):
                        delib = self._conscience.deliberate(
                            tu.name, tu.input,
                            user_msg=self._last_user_msg,
                            error_count=len(self._errors_hit),
                            tools_used=self._tools_used,
                        )
                        self._history.add("conscience",
                            f"{delib.winner}→{tu.name}",
                            delib.guidance[:80])
                        yield StreamEvent(type="conscience", text=self._conscience.format_deliberation(delib),
                                          tool_name=tu.name)

                    self._tools_used.append(tu.name)
                    yield StreamEvent(type="tool_call", tool_name=tu.name, tool_params=tu.input)

                async def _exec(tu):
                    t0 = time.monotonic()
                    result = await self._execute_tool(tu.name, tu.input, context)
                    duration = (time.monotonic() - t0) * 1000
                    execution = ToolExecution(
                        name=tu.name, success=not result.is_error,
                        duration_ms=duration, result=result,
                    )
                    self._history.add_tool_call(tu.name, not result.is_error,
                                                f"{duration:.0f}ms")
                    return tu, result, execution

                # Filter out denied tools
                allowed_tool_uses = [
                    tu for tu in tool_uses
                    if not self._permission_gate.check(tu.name, tu.input)
                ]

                raw = await asyncio.gather(
                    *[_exec(tu) for tu in allowed_tool_uses],
                    return_exceptions=True,
                )

                tool_results = []
                for item in raw:
                    if isinstance(item, Exception):
                        tu = allowed_tool_uses[0]
                        tr = ToolResult(error=str(item), is_error=True)
                        execution = ToolExecution(name=tu.name, success=False)
                        self._history.add_error(tu.name, str(item)[:100])
                    else:
                        tu, tr, execution = item
                    if tr.is_error:
                        self._errors_hit.append(f"{tu.name}: {tr.error[:100]}")
                    # Feed tool result into soul (creates impressions)
                    try:
                        self._soul_bridge.on_tool_result(
                            tu.name, tu.input, tr.output or tr.error,
                            tr.is_error, mood=self._get_current_mood(),
                            conscience_voice=self._conscience.get_last_deliberation().winner if self._conscience.get_last_deliberation() else "",
                        )
                    except Exception:
                        pass  # Soul bridge errors must never break tool execution
                    yield StreamEvent(type="tool_result", tool_name=tu.name,
                                      tool_result=tr, tool_execution=execution)
                    tool_results.append({"type": "tool_result", "tool_use_id": tu.id,
                                         "content": tr.to_content(), "is_error": tr.is_error})

                # Add denied tools as error results
                for tu in tool_uses:
                    denial = self._permission_gate.check(tu.name, tu.input)
                    if denial:
                        tool_results.append({
                            "type": "tool_result", "tool_use_id": tu.id,
                            "content": [{"type": "text", "text": f"Permission denied: {denial.reason}"}],
                            "is_error": True,
                        })

                messages.append({"role": "user", "content": tool_results})

                if response.stop_reason == "end_turn":
                    result_text = "\n".join(text_parts)
                    await self._self_evaluate(result_text)
                    yield StreamEvent(type="done", text=result_text, usage=total_usage)
                    return

            except anthropic.APIError as e:
                consecutive_errors += 1
                self._errors_hit.append(f"API: {str(e)[:100]}")
                self._history.add_error("api", str(e)[:100])
                yield StreamEvent(type="error", text=f"API error: {e}")
                if consecutive_errors >= self.config.max_consecutive_errors:
                    return
            except Exception as e:
                consecutive_errors += 1
                self._history.add_error("runtime", str(e)[:100])
                yield StreamEvent(type="error", text=f"Error: {e}")
                if consecutive_errors >= self.config.max_consecutive_errors:
                    return

        self._history.add("turn_loop", f"max turns reached ({self.engine_config.max_turns})")
        yield StreamEvent(type="done", text="(max tool calls)", usage=total_usage)

    async def _self_evaluate(self, result_text: str):
        """After a task completes, evaluate and record lessons.

        Uses batch processor for 50% cost savings when possible.
        Falls back to heuristic eval (no API call) for simple cases.
        """
        if not self._last_user_msg or not self._tools_used:
            return
        try:
            outcome = "success" if not self._errors_hit else ("partial" if result_text else "failure")

            # Heuristic evaluation (no API call — free)
            lesson = ""
            if self._errors_hit:
                lesson = f"Errors with {', '.join(set(t.split(':')[0] for t in self._errors_hit))} — retried and {'recovered' if result_text else 'failed'}"
            elif len(self._tools_used) == 1:
                lesson = f"Simple task, {self._tools_used[0]} was sufficient"
            else:
                lesson = f"Used {len(self._tools_used)} tools: {' → '.join(dict.fromkeys(self._tools_used))}"

            tags = []
            tool_set = set(self._tools_used)
            if tool_set & {"bash", "file_edit", "file_write"}:
                tags.append("coding")
            if tool_set & {"web_search", "web_fetch"}:
                tags.append("research")
            if tool_set & {"twitter_search", "twitter_reply"}:
                tags.append("twitter")
            if tool_set & {"rag_search", "rag_upload"}:
                tags.append("documents")
            if not tags:
                tags.append("general")

            self._exp.record_lesson(
                task_summary=self._last_user_msg[:200],
                outcome=outcome,
                lesson=lesson,
                tool_sequence=list(dict.fromkeys(self._tools_used)),
                error="; ".join(self._errors_hit[:3]) if self._errors_hit else "",
                fix="Auto-retry" if self._errors_hit and result_text else "",
                tags=tags,
            )

            # Feed outcome back to conscience (Lux/Nox weight adjustment)
            conscience_outcome = "good" if outcome == "success" else ("bad" if outcome == "failure" else "neutral")
            self._conscience.record_outcome(conscience_outcome)

            # Queue deeper eval via batch API (50% cheaper, async)
            # Only for complex tasks — simple ones don't need LLM eval
            if len(self._tools_used) >= 3 or self._errors_hit:
                self._batch.queue(
                    task_type="self_eval",
                    prompt=(
                        f"Evaluate this task execution. Respond ONLY with JSON.\n"
                        f"Task: {self._last_user_msg[:200]}\n"
                        f"Tools used: {', '.join(dict.fromkeys(self._tools_used))}\n"
                        f"Errors: {'; '.join(self._errors_hit[:3]) if self._errors_hit else 'none'}\n"
                        f"Outcome: {outcome}\n"
                        f"Result preview: {result_text[:200]}\n\n"
                        f'Respond: {{"lesson": "...", "improvement": "...", "confidence": 0.0-1.0}}'
                    ),
                    model="claude-haiku-4-5-20251001",  # Cheapest model for eval
                    max_tokens=512,
                    temperature=0.3,
                    callback="eval_result",
                    metadata={"task": self._last_user_msg[:200], "outcome": outcome},
                )
                # Auto-submit if threshold reached
                if self._batch.should_submit():
                    asyncio.create_task(self._batch.submit_batch())

            self._history.add("self_eval", outcome, lesson[:100])
        except Exception as e:
            logger.debug("Self-eval failed: %s", e)

    async def query(self, messages: list[dict], context: ToolContext, system_prompt=None) -> QueryResult:
        result = QueryResult(messages=list(messages), history=self._history)
        async for event in self.query_streaming(messages, context, system_prompt):
            if event.type == "text":
                result.text += event.text
            elif event.type == "done":
                result.text = event.text or result.text
                if event.usage:
                    result.usage = event.usage
            elif event.type == "error":
                result.error = event.text
            elif event.type == "tool_call":
                result.tool_calls += 1
            elif event.type == "permission_denial" and event.denial:
                result.permission_denials.append(event.denial)
            elif event.type == "compact":
                result.was_compacted = True
            result.turn_count += 1 if event.type in ("tool_call",) else 0
        result.messages = messages
        return result

    async def _execute_tool(self, name: str, params: dict, context: ToolContext) -> ToolResult:
        tool = self.registry.get(name)
        if not tool:
            return ToolResult(error=f"Unknown: {name}", is_error=True)
        if tool.needs_confirmation(params, context.config):
            if context.permission_callback:
                approved = await context.permission_callback(name, params)
                if not approved:
                    return ToolResult(error=f"Denied: {name}", is_error=True)
        try:
            return await tool.execute(params, context)
        except Exception as e:
            logger.error(f"Tool {name}: {e}", exc_info=True)
            return ToolResult(error=f"Failed: {e}", is_error=True)

    async def run_agent(self, task: str, context: ToolContext) -> str:
        system = self._build_system(context.working_dir, task=task)
        messages = [{"role": "user", "content": task}]
        result = await self.query(messages, context, system)
        return result.text or result.error or "(no response)"

    async def chat(self, user_message: str, messages: list[dict], context: ToolContext) -> QueryResult:
        self._last_user_msg = user_message
        self._tools_used = []
        self._errors_hit = []
        messages.append({"role": "user", "content": user_message})
        self._transcript.append("user", user_message)
        result = await self.query(messages, context)
        # Feed completed chat into soul
        try:
            self._soul_bridge.on_chat_message(
                user_message, result.text,
                mood=self._get_current_mood(),
                conscience_voice=self._conscience.get_last_deliberation().winner if self._conscience.get_last_deliberation() else "",
            )
        except Exception:
            pass
        return result

    def _get_current_mood(self) -> dict:
        """Helper to get current mood state dict."""
        try:
            return get_mood_engine(self.config.data_dir if self.config else "").get_state()
        except Exception:
            return {}

    # ── Claw-code style accessors ──

    @property
    def history(self) -> HistoryLog:
        return self._history

    @property
    def transcript(self) -> TranscriptStore:
        return self._transcript

    @property
    def permission_gate(self) -> PermissionGate:
        return self._permission_gate

    @property
    def router(self) -> ModelRouter:
        return self._router

    @property
    def batch(self) -> BatchProcessor:
        return self._batch

    @property
    def conscience(self) -> Conscience:
        return self._conscience

    @property
    def palazzo(self) -> Palazzo:
        return self._palazzo

    def get_session_summary(self) -> str:
        """Get a full session summary — combines history + transcript + permissions + router stats."""
        lines = [
            "# Session Summary",
            "",
            self._history.as_compact_summary(),
            "",
            self._transcript.get_summary(),
            "",
            self._permission_gate.get_denial_summary(),
            "",
            self._router.format_stats(),
            "",
            self._batch.format_stats(),
        ]
        return "\n".join(lines)

    # ── Batch callbacks ──

    def _on_dream_result(self, task):
        """Process a completed dream batch task."""
        if task.completed and task.result:
            try:
                dw = get_dreamworld(self.config.data_dir if self.config else "")
                # Store dream result — dreamworld will parse it
                logger.debug(f"Batch dream result: {task.result[:100]}")
            except Exception as e:
                logger.debug(f"Dream callback failed: {e}")

    def _on_eval_result(self, task):
        """Process a completed eval batch task."""
        if task.completed and task.result:
            try:
                import json as _json
                data = _json.loads(task.result.strip().strip('`').strip())
                lesson = data.get("lesson", "")
                if lesson and task.metadata.get("task"):
                    self._exp.record_lesson(
                        task_summary=task.metadata["task"],
                        outcome=task.metadata.get("outcome", "partial"),
                        lesson=f"[batch-eval] {lesson}",
                        tags=["batch_eval"],
                    )
                    logger.debug(f"Batch eval recorded: {lesson[:80]}")
            except Exception as e:
                logger.debug(f"Eval callback parse failed: {e}")
