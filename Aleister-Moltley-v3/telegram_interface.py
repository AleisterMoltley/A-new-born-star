"""
Telegram Interface v3 — With History, Permissions, Transcript, Bootstrap integration.

New from claw-code patterns:
- /history — show session history audit trail
- /permissions — show permission denials + manage deny-lists
- /bootstrap — show last bootstrap report
- /transcript — show transcript summary
- permission_denial stream events shown in chat
- History saved alongside sessions
- Transcript turns tracked in session metadata
"""
from __future__ import annotations
import asyncio
import html
import json
import logging
import os
import time
import uuid
from typing import Optional

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, ContextTypes, filters
from telegram.constants import ParseMode, ChatAction

from config import CompagnonConfig, MCP_PRESETS
from tool_registry import ToolRegistry, ToolContext
from query_engine import QueryEngine, StreamEvent
from token_tracker import CostTracker, TokenUsage
from session_store import Session, SessionStore
from memory.memory import MemoryStore, MemoryReadTool, MemoryWriteTool
from permissions import ToolPermissionContext, PermissionGate
from history import HistoryLog
from transcript import TranscriptStore
from conscience import get_conscience
from palazzo import get_palazzo
from soul import get_impression_engine, get_personality, get_encounter_engine, get_world, get_journal
from soul.weather import get_weather_now
from soul.identity import IDENTITY
from soul_bridge import get_soul_bridge
from tools.bash_tool import BashTool
from tools.file_read import FileReadTool
from tools.file_tools import FileWriteTool, FileEditTool
from tools.web_tools import WebSearchTool, WebFetchTool
from tools.agent_tool import AgentTool
from tools.mcp_tool import MCPManager, MCPServerConfig

# Optional tools — guarded imports (don't crash if deps missing)
try:
    from tools.rag_tool import RAGUploadTool, RAGSearchTool, RAGListTool, RAGDeleteTool
    HAS_RAG = True
except ImportError:
    HAS_RAG = False
    logger.info("RAG tools unavailable (install langchain chromadb pypdf)")

try:
    from tools.twitter_tool import TwitterSearchTool, TwitterReplyTool, TwitterPostTool, TwitterScanTool
    HAS_TWITTER = True
except ImportError:
    HAS_TWITTER = False
    logger.info("Twitter tools unavailable (install tweepy)")

try:
    from tools.scheduler import ScheduleAddTool, ScheduleListTool, ScheduleRemoveTool, WatchDirTool, get_scheduler
    HAS_SCHEDULER = True
except ImportError:
    HAS_SCHEDULER = False
    logger.info("Scheduler tools unavailable")

try:
    from tools.github_tool import GitHubCreateRepoTool, GitHubPushFileTool, GitHubCreateIssueTool, GitHubListReposTool, GitHubReadFileTool
    HAS_GITHUB = True
except ImportError:
    HAS_GITHUB = False
    logger.info("GitHub tools unavailable (install PyGithub)")

logger = logging.getLogger(__name__)
MAX_TG_LEN = 4000
TOOL_ICONS = {"bash": "💻", "file_read": "📖", "file_write": "📝", "file_edit": "✂️",
              "web_search": "🔍", "web_fetch": "🌐", "agent": "🤖", "memory_read": "🧠",
              "memory_write": "💾", "rag_upload": "📄", "rag_search": "🔎", "rag_list": "📚",
              "rag_delete": "🗑️", "twitter_search": "🐦", "twitter_reply": "💬",
              "twitter_post": "📢", "twitter_scan": "📡", "schedule_add": "⏰",
              "schedule_list": "📋", "schedule_remove": "🚫", "watch_dir": "👁️",
              "github_create_repo": "🏗️", "github_push_file": "📤",
              "github_create_issue": "🎫", "github_list_repos": "📂",
              "github_read_file": "📥"}


class CompagnonBot:
    def __init__(self, config: CompagnonConfig):
        self.config = config
        self.registry = ToolRegistry()
        self.memory_store = MemoryStore(config.memory_dir)
        self.session_store = SessionStore(config.session_dir)
        self.cost_tracker = CostTracker(config.data_dir)
        self.mcp_manager = MCPManager()
        self._sessions: dict[int, Session] = {}
        self._pending: dict[str, asyncio.Future] = {}
        self._last_bootstrap_report = None  # Store bootstrap report
        self._last_engine: dict[int, QueryEngine] = {}  # Per-user engine ref for history access
        self._setup_tools()

    def _setup_tools(self):
        self.registry.register(BashTool())
        self.registry.register(FileReadTool())
        self.registry.register(FileWriteTool())
        self.registry.register(FileEditTool())
        self.registry.register(WebSearchTool())
        self.registry.register(WebFetchTool())
        self.registry.register(MemoryReadTool(self.memory_store))
        self.registry.register(MemoryWriteTool(self.memory_store))

        if HAS_RAG:
            self.registry.register(RAGUploadTool())
            self.registry.register(RAGSearchTool())
            self.registry.register(RAGListTool())
            self.registry.register(RAGDeleteTool())

        if HAS_TWITTER:
            self.registry.register(TwitterSearchTool())
            self.registry.register(TwitterReplyTool())
            self.registry.register(TwitterPostTool())
            self.registry.register(TwitterScanTool())

        if HAS_SCHEDULER:
            self.registry.register(ScheduleAddTool())
            self.registry.register(ScheduleListTool())
            self.registry.register(ScheduleRemoveTool())
            self.registry.register(WatchDirTool())

        if HAS_GITHUB:
            self.registry.register(GitHubCreateRepoTool())
            self.registry.register(GitHubPushFileTool())
            self.registry.register(GitHubCreateIssueTool())
            self.registry.register(GitHubListReposTool())
            self.registry.register(GitHubReadFileTool())

        def factory():
            return QueryEngine(config=self.config, registry=self.registry,
                               memory_context=self.memory_store.get_prompt_context(),
                               cost_tracker=self.cost_tracker)
        self.registry.register(AgentTool(query_engine_factory=factory))

    async def _setup_mcp(self):
        for name, cfg in self.config.mcp_servers.items():
            sc = MCPServerConfig(name=name, command=cfg.get("command"), args=cfg.get("args", []),
                                 env=cfg.get("env", {}), url=cfg.get("url"), transport=cfg.get("transport", "stdio"))
            for tool in await self.mcp_manager.connect_server(sc):
                self.registry.register_mcp(tool)

    def _get_session(self, uid: int) -> Session:
        if uid not in self._sessions:
            saved = self.session_store.load_latest(uid)
            if saved:
                self._sessions[uid] = saved
            else:
                self._sessions[uid] = Session(user_id=uid, working_dir=self.config.working_dir)
        return self._sessions[uid]

    def _save_session(self, session: Session, engine: QueryEngine = None):
        """Save session with optional history log (claw-code pattern)."""
        history_log = engine.history if engine else None
        if engine:
            session.transcript_turns = engine.transcript.turn_count
            session.permission_denials = len(engine.permission_gate.denials)
            session.history_summary = engine.history.as_compact_summary()
        self.session_store.save(session, history_log)
        self.session_store.cleanup_old(session.user_id)

    def _authorized(self, uid: int) -> bool:
        return not self.config.telegram_allowed_users or uid in self.config.telegram_allowed_users

    def _create_engine(self) -> QueryEngine:
        return QueryEngine(config=self.config, registry=self.registry,
                           memory_context=self.memory_store.get_prompt_context(),
                           cost_tracker=self.cost_tracker)

    # ── Commands ──────────────────────────────────────────────────

    async def cmd_start(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        if not self._authorized(update.effective_user.id): return
        s = self._get_session(update.effective_user.id)
        daily = self.cost_tracker.format_daily_summary()
        tools_by_cat = self.registry.get_categories()
        cat_summary = " | ".join(f"{cat}:{len(names)}" for cat, names in sorted(tools_by_cat.items()))
        await update.message.reply_text(
            f"🤖 <b>Compagnon v3</b>\n\n"
            f"Session: <code>{s.session_id}</code>\n"
            f"Model: <code>{self.config.active_model}</code>\n"
            f"Tools: {len(self.registry.list_names())} ({cat_summary})\n"
            f"Dir: <code>{s.working_dir}</code>\n"
            f"💰 {daily}\n\n"
            f"/clear — New session\n/status — Info\n/cd — Change dir\n"
            f"/model — Switch model\n/tools — List tools\n/memory — Memories\n"
            f"/auto — Toggle auto-approve\n/sessions — Past sessions\n"
            f"/resume &lt;id&gt; — Resume session\n/budget — Cost info\n"
            f"/mcp — MCP servers\n"
            f"/history — Session audit trail\n"
            f"/permissions — Permission denials\n"
            f"/bootstrap — Startup report\n"
            f"/transcript — Transcript summary\n"
            f"/conscience — Inner voices (Lux/Nox)\n"
            f"/palazzo — Physical location & body state\n"
            f"/explore — Discover Cefalù\n"
            f"/meet — Meet someone new\n"
            f"/journal — Diary entries\n"
            f"/anima — Soul, personality, weather",
            parse_mode=ParseMode.HTML)

    async def cmd_clear(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        if not self._authorized(update.effective_user.id): return
        s = self._get_session(update.effective_user.id)
        engine = self._last_engine.get(update.effective_user.id)
        self._save_session(s, engine)
        s.clear()
        self._last_engine.pop(update.effective_user.id, None)
        await update.message.reply_text(f"🗑️ New session: <code>{s.session_id}</code>", parse_mode=ParseMode.HTML)

    async def cmd_status(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        if not self._authorized(update.effective_user.id): return
        s = self._get_session(update.effective_user.id)
        from auto_compact import estimate_token_count
        from config import get_autocompact_threshold
        tokens = estimate_token_count(s.messages)
        threshold = get_autocompact_threshold(self.config.model)
        pct = (tokens / threshold * 100) if threshold > 0 else 0
        # Show permission + transcript info
        perm_info = f"Denials: {s.permission_denials}" if s.permission_denials else "Denials: 0"
        transcript_info = f"Turns: {s.transcript_turns}" if s.transcript_turns else ""
        await update.message.reply_text(
            f"📊 <b>Status</b>\n"
            f"Session: <code>{s.session_id}</code> ({len(s.messages)} msgs)\n"
            f"Context: ~{tokens:,}/{threshold:,} tokens ({pct:.0f}%)\n"
            f"Dir: <code>{s.working_dir}</code>\n"
            f"Model: <code>{self.config.active_model}</code>\n"
            f"Cost: ${s.total_cost_usd:.4f} | Tools: {s.tool_calls}\n"
            f"Compactions: {s.compactions} | {perm_info}\n"
            f"Auto-approve: {'⚡ ON' if self.config.auto_approve_write else '🛡️ OFF'}\n"
            f"{transcript_info}\n"
            f"💰 {self.cost_tracker.format_daily_summary()}",
            parse_mode=ParseMode.HTML)

    async def cmd_cd(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        if not self._authorized(update.effective_user.id): return
        s = self._get_session(update.effective_user.id)
        args = update.message.text.split(maxsplit=1)
        if len(args) < 2:
            await update.message.reply_text(f"📂 <code>{s.working_dir}</code>", parse_mode=ParseMode.HTML); return
        d = os.path.realpath(os.path.expanduser(args[1].strip()) if os.path.isabs(args[1].strip()) else os.path.join(s.working_dir, args[1].strip()))
        if os.path.isdir(d):
            s.working_dir = d; await update.message.reply_text(f"📂 <code>{d}</code>", parse_mode=ParseMode.HTML)
        else:
            await update.message.reply_text(f"❌ Not a directory: {d}")

    async def cmd_model(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        if not self._authorized(update.effective_user.id): return
        args = update.message.text.split(maxsplit=1)
        if len(args) < 2:
            # Show current model + router stats
            uid = update.effective_user.id
            engine = self._last_engine.get(uid)
            router_info = engine.router.format_stats() if engine else "No router stats yet"
            await update.message.reply_text(
                f"Model: <code>{self.config.active_model}</code>\n"
                f"Router: {router_info}\n\n"
                f"<code>/model auto</code> — enable auto-routing (Haiku/Sonnet)\n"
                f"<code>/model claude-haiku-4-5-20251001</code> — force Haiku\n"
                f"<code>/model claude-sonnet-4-20250514</code> — force Sonnet",
                parse_mode=ParseMode.HTML); return
        model_arg = args[1].strip()
        if model_arg == "auto":
            self.config.model = "claude-sonnet-4-20250514"
            # Clear force on all active engines
            for engine in self._last_engine.values():
                engine.router.set_force_model("")
            await update.message.reply_text("✅ Auto-routing enabled (Haiku for simple, Sonnet for complex)", parse_mode=ParseMode.HTML)
        else:
            self.config.model = model_arg
            for engine in self._last_engine.values():
                engine.router.set_force_model(model_arg)
            await update.message.reply_text(f"✅ Model: <code>{model_arg}</code> (router bypassed)", parse_mode=ParseMode.HTML)

    async def cmd_tools(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        if not self._authorized(update.effective_user.id): return
        tools = self.registry.get_all_enabled()
        # Group by category
        cats: dict[str, list] = {}
        for t in tools:
            cats.setdefault(t.category, []).append(t)
        lines = [f"🔧 <b>{len(tools)} tools:</b>\n"]
        for cat in sorted(cats.keys()):
            lines.append(f"\n<b>[{cat}]</b>")
            for t in cats[cat]:
                icon = TOOL_ICONS.get(t.name, "🔧" if not t.is_read_only else "📖")
                lines.append(f"  {icon} <code>{t.name}</code>")
        await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)

    async def cmd_memory(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        if not self._authorized(update.effective_user.id): return
        m = self.memory_store.list_all()
        if not m: await update.message.reply_text("📭 No memories."); return
        lines = [f"🧠 <b>{len(m)} memories:</b>\n"]
        for x in m:
            lines.append(f"• <b>{html.escape(x['key'])}</b> [{','.join(x.get('tags',[]))}]")
        await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)

    async def cmd_auto(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        if not self._authorized(update.effective_user.id): return
        self.config.auto_approve_write = not self.config.auto_approve_write
        self.config.auto_approve_bash_destructive = self.config.auto_approve_write
        st = "ON ⚡" if self.config.auto_approve_write else "OFF 🛡️"
        await update.message.reply_text(f"Auto-approve: <b>{st}</b>", parse_mode=ParseMode.HTML)

    async def cmd_sessions(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        if not self._authorized(update.effective_user.id): return
        sessions = self.session_store.list_sessions(update.effective_user.id, limit=10)
        if not sessions: await update.message.reply_text("No past sessions."); return
        lines = [f"📋 <b>Sessions:</b>\n"]
        for s in sessions:
            import datetime
            ts = datetime.datetime.fromtimestamp(s["updated_at"]).strftime("%m/%d %H:%M") if s["updated_at"] else "?"
            denials = f" 🚫{s['permission_denials']}" if s.get("permission_denials") else ""
            lines.append(f"• <code>{s['session_id']}</code> {ts} — {s['messages']} msgs, ${s['cost_usd']:.4f}{denials}")
        lines.append(f"\n/resume &lt;id&gt; to continue a session")
        await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)

    async def cmd_resume(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        if not self._authorized(update.effective_user.id): return
        args = update.message.text.split(maxsplit=1)
        if len(args) < 2: await update.message.reply_text("Usage: /resume <session_id>"); return
        sid = args[1].strip()
        loaded = self.session_store.load(update.effective_user.id, sid)
        if not loaded: await update.message.reply_text(f"Session {sid} not found."); return
        self._sessions[update.effective_user.id] = loaded
        await update.message.reply_text(
            f"▶️ Resumed <code>{sid}</code> ({len(loaded.messages)} msgs, ${loaded.total_cost_usd:.4f})",
            parse_mode=ParseMode.HTML)

    async def cmd_budget(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        if not self._authorized(update.effective_user.id): return
        t = self.cost_tracker.today
        pct = (t.total_cost_usd / self.config.daily_budget_usd * 100) if self.config.daily_budget_usd > 0 else 0
        bar_len = 20
        filled = int(pct / 100 * bar_len)
        bar = "█" * min(filled, bar_len) + "░" * max(0, bar_len - filled)
        await update.message.reply_text(
            f"💰 <b>Budget</b>\n\n"
            f"[{bar}] {pct:.1f}%\n"
            f"${t.total_cost_usd:.4f} / ${self.config.daily_budget_usd:.2f}\n\n"
            f"API calls: {t.api_calls}\n"
            f"Tool calls: {t.tool_calls}\n"
            f"Tokens: ↓{t.total_input:,} ↑{t.total_output:,}",
            parse_mode=ParseMode.HTML)

    async def cmd_mcp(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        if not self._authorized(update.effective_user.id): return
        lines = ["🔌 <b>MCP Server Presets:</b>\n"]
        for name in MCP_PRESETS:
            lines.append(f"• <code>{name}</code>")
        lines.append(f"\nActive: {list(self.config.mcp_servers.keys()) or 'none'}")
        lines.append(f"\nSet COMPAGNON_MCP_SERVERS env var to activate.")
        await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)

    # ── New commands from claw-code patterns ─────────────────────

    async def cmd_history(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        """Show session audit trail (from claw-code HistoryLog)."""
        if not self._authorized(update.effective_user.id): return
        uid = update.effective_user.id
        engine = self._last_engine.get(uid)
        if engine and engine.history.events:
            text = engine.history.as_markdown()
            if len(text) > MAX_TG_LEN:
                text = text[:MAX_TG_LEN - 20] + "\n\n(truncated)"
            await update.message.reply_text(f"<pre>{html.escape(text)}</pre>", parse_mode=ParseMode.HTML)
        else:
            # Try loading from disk
            s = self._get_session(uid)
            saved = self.session_store.load_history(uid, s.session_id)
            if saved and saved.get("events"):
                lines = [f"📜 <b>History ({s.session_id})</b>\n"]
                for e in saved["events"][-20:]:
                    lines.append(f"  [{e['category']}] {html.escape(e['title'])}")
                    if e.get("detail"):
                        lines.append(f"    {html.escape(e['detail'][:80])}")
                await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)
            else:
                await update.message.reply_text("📜 No history for current session. Start a conversation first.")

    async def cmd_permissions(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        """Show permission denials and current deny-list."""
        if not self._authorized(update.effective_user.id): return
        uid = update.effective_user.id
        engine = self._last_engine.get(uid)
        perm_ctx = ToolPermissionContext.from_config(self.config)
        lines = ["🔒 <b>Permissions</b>\n"]
        lines.append(f"Destructive allowed: {'yes' if perm_ctx.allow_destructive else 'no'}")
        if perm_ctx.deny_names:
            lines.append(f"Deny list: {', '.join(sorted(perm_ctx.deny_names))}")
        if perm_ctx.deny_prefixes:
            lines.append(f"Deny prefixes: {', '.join(perm_ctx.deny_prefixes)}")
        if engine:
            denials = engine.permission_gate.denials
            if denials:
                lines.append(f"\n<b>Session denials ({len(denials)}):</b>")
                for d in denials[-10:]:
                    lines.append(f"  🚫 {html.escape(d.tool_name)}: {html.escape(d.reason[:80])}")
            else:
                lines.append(f"\nNo denials in current session.")
        lines.append(f"\nUse /auto to toggle destructive command approval.")
        await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)

    async def cmd_bootstrap(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        """Show last bootstrap report."""
        if not self._authorized(update.effective_user.id): return
        if self._last_bootstrap_report:
            text = self._last_bootstrap_report.as_markdown()
            if len(text) > MAX_TG_LEN:
                text = text[:MAX_TG_LEN - 20] + "\n\n(truncated)"
            await update.message.reply_text(f"<pre>{html.escape(text)}</pre>", parse_mode=ParseMode.HTML)
        else:
            await update.message.reply_text("No bootstrap report available. Restart the bot to generate one.")

    async def cmd_transcript(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        """Show transcript summary for current session."""
        if not self._authorized(update.effective_user.id): return
        uid = update.effective_user.id
        engine = self._last_engine.get(uid)
        if engine:
            summary = engine.transcript.get_summary()
            user_msgs = engine.transcript.replay_messages()
            lines = [f"📝 <b>Transcript</b>\n", summary]
            if user_msgs:
                lines.append(f"\n<b>User messages ({len(user_msgs)}):</b>")
                for i, msg in enumerate(user_msgs[-5:], 1):
                    lines.append(f"  {i}. {html.escape(msg[:80])}")
            await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)
        else:
            s = self._get_session(uid)
            await update.message.reply_text(
                f"📝 Session {s.session_id}: {s.transcript_turns} turns recorded. "
                f"Start a new conversation to see live transcript."
            )

    async def cmd_conscience(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        """Show Aleister's conscience — the inner voices of Lux and Nox."""
        if not self._authorized(update.effective_user.id): return
        conscience = get_conscience(self.config.data_dir)
        stats = conscience.get_stats()
        lines = [
            f"⚖️ <b>Conscience — The Inner Voices</b>\n",
            f"☀️ <b>Lux</b> (the angel) — caution, trust, verification",
            f"   Wins: {stats['lux_wins']} | Weight: {stats['lux_weight']:.0%} | Win rate: {stats['lux_win_rate']:.0%}",
            f"🌙 <b>Nox</b> (the devil) — boldness, speed, action",
            f"   Wins: {stats['nox_wins']} | Weight: {stats['nox_weight']:.0%} | Win rate: {stats['nox_win_rate']:.0%}",
            f"\nBalance: <b>{stats['balance'].upper()}</b>",
            f"Total deliberations: {stats['total_deliberations']}",
            f"Ledger size: {stats['ledger_size']}",
        ]
        outcomes = stats.get("recent_outcomes", {})
        if outcomes.get("good") or outcomes.get("bad"):
            lines.append(f"\nRecent outcomes: ✓{outcomes.get('good',0)} ✗{outcomes.get('bad',0)} ○{outcomes.get('neutral',0)}")

        # Show last deliberation
        last = conscience.get_last_deliberation()
        if last:
            lines.append(f"\n<b>Last deliberation:</b>")
            lines.append(f"  ☀️ Lux: <i>{html.escape(last.lux_argument[:100])}</i>")
            lines.append(f"  🌙 Nox: <i>{html.escape(last.nox_argument[:100])}</i>")
            lines.append(f"  → {last.winner.capitalize()} won ({last.confidence:.0%})")

        await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)

    async def cmd_palazzo(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        """Show Aleister's physical location and body state."""
        if not self._authorized(update.effective_user.id): return
        palazzo = get_palazzo(self.config.data_dir)
        state = palazzo.get_dashboard_state()
        log = palazzo.get_recent_log(limit=8)
        from palazzo import ROOMS
        room = ROOMS.get(state["room_id"])

        lines = [
            f"🏰 <b>Palazzo Moltley — Cefalù, Sicily</b>\n",
            f"📍 <b>{state['room']}</b> ({state['floor']})",
        ]
        if room:
            lines.append(f"<i>{room.description[:120]}...</i>")
            lines.append(f"🔊 {room.ambient}")
            lines.append(f"💡 {room.lighting} | 🌡️ {room.temperature}")

        lines.extend([
            f"\n<b>Body State:</b>",
            f"  🧍 {html.escape(state['posture'])}",
            f"  🫁 {state['breathing']}",
            f"  ⚡ Energy: {state['energy']:.0%}",
            f"  🏠 Rooms today: {state['rooms_today']}",
        ])
        if state["needs"]:
            lines.append(f"  ⚠️ {html.escape(state['needs'])}")
        if state["activity"] != "idle":
            lines.append(f"  🎯 Currently: {state['activity']}")

        if log:
            lines.append(f"\n<b>Recent movements:</b>")
            for entry in log[-6:]:
                lines.append(f"  → {html.escape(entry['action'][:60])}")

        await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)
        if not self._authorized(update.effective_user.id): return
        from dreamworld import get_dreamworld
        dw = get_dreamworld(self.config.data_dir)
        stats = dw.get_stats()
        dreams = dw.list_dreams(limit=5)
        lines = [f"💭 <b>Dreamworld — Aleister's Inner World</b>\n"]
        lines.append(f"Dreams: {stats['total_dreams']} | Insights: {stats['total_insights']} | Concepts: {stats['invented_concepts']}")
        if stats.get("recent_insights"):
            lines.append(f"\n<b>Recent Insights:</b>")
            for i in stats["recent_insights"]:
                lines.append(f"  💡 {html.escape(i)}")
        if dreams:
            lines.append(f"\n<b>Recent Dreams:</b>")
            for d in dreams:
                personas = ", ".join(d.get("personas", []))
                lines.append(f"  🌀 {d['timestamp'][:16]} — {html.escape(d['seed'][:50])}...")
                if personas:
                    lines.append(f"     with: {html.escape(personas)}")
        await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)

    async def cmd_gallery(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        if not self._authorized(update.effective_user.id): return
        from atelier import get_atelier
        atelier = get_atelier(self.config.data_dir)
        stats = atelier.get_stats()
        works = atelier.list_works(limit=5)
        lines = [f"🎨 <b>Atelier — Aleister's Gallery</b>\n"]
        lines.append(f"Works: {stats['total_works']} | Level: {stats['level']} ({stats['level_name']})")
        lines.append(f"Avg rating: {stats['avg_rating']}/10 | Next level in: {stats['works_until_next']} works")
        if stats.get("last_critique"):
            lines.append(f"\n<b>Last self-critique:</b>")
            lines.append(f"<i>{html.escape(stats['last_critique'])}</i>")
        if works:
            lines.append(f"\n<b>Recent works:</b>")
            for w in works:
                lines.append(f"  🖼️ #{w['work_num']} '{html.escape(w['subject'])}' ({w['level_name']}, {w['rating']}/10)")
        await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)

    async def cmd_journal(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        """Show Aleister's diary entries."""
        if not self._authorized(update.effective_user.id): return
        journal = get_journal(self.config.data_dir)
        palazzo = get_palazzo(self.config.data_dir)
        from mood import get_mood_engine
        mood = get_mood_engine(self.config.data_dir).get_state()
        conscience_stats = get_conscience(self.config.data_dir).get_stats()
        weather = get_weather_now()
        entry = journal.write_entry(
            palazzo_log=palazzo.get_recent_log(8),
            mood=mood, conscience_stats=conscience_stats, weather=weather,
        )
        entries = journal.get_entries(limit=5)
        lines = [f"📓 <b>Aleister's Journal</b>\n"]
        if entry:
            preview = entry[:600].replace("\n", "\n  ")
            lines.append(f"<b>Today:</b>")
            lines.append(f"<pre>{html.escape(preview)}</pre>")
        if len(entries) > 1:
            lines.append(f"\n<b>Previous entries:</b>")
            for e in entries[1:4]:
                lines.append(f"  📅 {e['date']}: {html.escape(e['content'][:80])}...")
        await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)

    async def cmd_anima(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        """Show Aleister's emergent soul — personality, impressions, world."""
        if not self._authorized(update.effective_user.id): return
        weather = get_weather_now()
        personality = get_personality(self.config.data_dir)
        impressions = get_impression_engine(self.config.data_dir)
        encounters = get_encounter_engine(self.config.data_dir)
        world = get_world(self.config.data_dir)

        p_stats = personality.get_stats()
        i_stats = impressions.get_stats()
        e_stats = encounters.get_stats()
        w_stats = world.get_stats()

        lines = [
            f"🌊 <b>Cefalù, Sicily — {weather['season'].capitalize()}</b>\n",
            f"🌡️ {weather['temperature']:.0f}°C | ☁️ {weather['sky']}",
            f"💨 {weather['wind']} | 🌊 {weather['sea']}",
            f"\n<b>🧬 Personality (emergent):</b>",
            f"<i>{html.escape(p_stats['trait_summary'][:200])}</i>",
            f"\n<b>💫 Impressions:</b> {i_stats['total_impressions']} total, "
            f"{i_stats['preferences']} preferences, {i_stats['aversions']} aversions",
            f"<b>👥 People known:</b> {e_stats['total_known']}",
            f"<b>🗺️ Places discovered:</b> {w_stats['places_discovered']}",
            f"\n💰 Wallet: <code>{IDENTITY['wallet']['solana']}</code>",
        ]

        # Show top preferences if any
        prefs = impressions.get_preferences(limit=3)
        if prefs:
            items = ", ".join(f"{p['subject']}" for p in prefs)
            lines.append(f"\n❤️ Likes: {items}")

        avers = impressions.get_aversions(limit=3)
        if avers:
            items = ", ".join(f"{a['subject']}" for a in avers)
            lines.append(f"👎 Avoids: {items}")

        await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)

    async def cmd_explore(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        """Explore Cefalù — discover new places."""
        if not self._authorized(update.effective_user.id): return
        args = update.message.text.split(maxsplit=1)
        bridge = get_soul_bridge(self.config.data_dir)
        world = get_world(self.config.data_dir)
        mood_state = None
        uid = update.effective_user.id
        engine = self._last_engine.get(uid)
        if engine:
            try:
                mood_state = engine._get_current_mood()
            except Exception:
                pass

        if len(args) < 2:
            # Show known places
            places = world.list_discovered(limit=10)
            lines = [f"🗺️ <b>Known Places in Cefalù</b> ({len(places)})\n"]
            for p in places:
                lines.append(f"  📍 {html.escape(p.name)} ({p.place_type}) — {p.visit_count} visits")
            lines.append(f"\nUse <code>/explore [query]</code> to discover new places.")
            lines.append(f"Example: <code>/explore cathedral</code> or <code>/explore café</code>")
            await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)
            return

        query = args[1].strip()
        await ctx.bot.send_chat_action(update.effective_chat.id, "typing")

        # Use web_search to find real places, or generate contextually
        # For now, create a discovery based on the query
        place = bridge.on_explore_place(
            name=query.title(),
            place_type="discovered",
            lat=38.038 + hash(query) % 100 * 0.0001,  # Slight variation around Cefalù center
            lon=14.023 + hash(query) % 100 * 0.0001,
            notes=f"Discovered while looking for: {query}",
            mood=mood_state,
        )
        await update.message.reply_text(
            f"📍 <b>Discovered: {html.escape(place.name)}</b>\n"
            f"Type: {place.place_type}\n"
            f"<i>A new impression forms...</i>",
            parse_mode=ParseMode.HTML)

    async def cmd_meet(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        """Meet someone new in Cefalù."""
        if not self._authorized(update.effective_user.id): return
        args = update.message.text.split(maxsplit=1)
        bridge = get_soul_bridge(self.config.data_dir)
        encounters = get_encounter_engine(self.config.data_dir)

        mood_state = None
        uid = update.effective_user.id
        engine = self._last_engine.get(uid)
        if engine:
            try:
                mood_state = engine._get_current_mood()
            except Exception:
                pass

        if len(args) >= 2 and args[1].strip():
            # Look up known person
            name = args[1].strip()
            npc = encounters.find_by_name(name)
            if npc:
                lines = [
                    f"👤 <b>{html.escape(npc.full_name)}</b>\n",
                    f"Age: {npc.age} | {npc.occupation}",
                    f"Appearance: {html.escape(npc.appearance)}",
                    f"Quirk: {html.escape(npc.quirk)}",
                    f"Trust: {npc.memory.trust_level:+.1f} | Met {npc.memory.familiarity}x",
                ]
                if npc.memory.interactions:
                    last = npc.memory.interactions[-1]
                    lines.append(f"\nLast interaction: {html.escape(last.get('what', '')[:80])}")
                await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)
                return

        # Meet someone new
        palazzo = get_palazzo(self.config.data_dir)
        location = palazzo.body.current_room
        from palazzo import ROOMS
        room = ROOMS.get(location)
        location_name = room.name if room else "Cefalù"

        npc = bridge.on_meet_person(location=location_name, mood=mood_state)

        lines = [
            f"👋 <b>New encounter!</b>\n",
            f"You meet <b>{html.escape(npc.full_name)}</b>, {npc.age}.",
            f"{html.escape(npc.occupation).capitalize()}.",
            f"<i>{html.escape(npc.appearance)}</i>",
            f"Quirk: {html.escape(npc.quirk)}",
            f"\nMet at: {html.escape(location_name)}",
            f"\n<i>A first impression forms, colored by your current mood...</i>",
        ]

        # Show feeling
        feeling = bridge._impressions.get_feeling_about(npc.full_name)
        if feeling:
            val = feeling["valence"]
            feel_word = "positive" if val > 0.2 else "negative" if val < -0.2 else "neutral"
            lines.append(f"First impression: {feel_word} ({val:+.1f})")

        await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)

    async def cmd_dreams(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        if not self._authorized(update.effective_user.id): return
        from dreamworld import get_dreamworld
        dw = get_dreamworld(self.config.data_dir)
        stats = dw.get_stats()
        dreams = dw.list_dreams(limit=5)
        lines = [f"💭 <b>Dreamworld — Aleister's Inner World</b>\n"]
        lines.append(f"Dreams: {stats['total_dreams']} | Insights: {stats['total_insights']} | Concepts: {stats['invented_concepts']}")
        if stats.get("recent_insights"):
            lines.append(f"\n<b>Recent Insights:</b>")
            for i in stats["recent_insights"]:
                lines.append(f"  💡 {html.escape(i)}")
        if dreams:
            lines.append(f"\n<b>Recent Dreams:</b>")
            for d in dreams:
                personas = ", ".join(d.get("personas", []))
                lines.append(f"  🌀 {d['timestamp'][:16]} — {html.escape(d['seed'][:50])}...")
                if personas:
                    lines.append(f"     with: {html.escape(personas)}")
        await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)

    # ── Message Handler with Streaming ───────────────────────────
        if not self._authorized(update.effective_user.id): return
        user_msg = update.message.text or ""
        if not user_msg.strip(): return

        uid = update.effective_user.id
        session = self._get_session(uid)
        chat_id = update.effective_chat.id
        await ctx.bot.send_chat_action(chat_id, ChatAction.TYPING)

        status_msg = await update.message.reply_text("⏳ Working...")
        display_lines: list[str] = []
        text_buffer = ""
        last_edit_time = 0.0
        tool_count = 0
        denial_count = 0

        async def update_status(force: bool = False):
            nonlocal last_edit_time
            now = time.time()
            if not force and (now - last_edit_time) < self.config.stream_update_interval:
                return
            last_edit_time = now
            content = "\n".join(display_lines[-30:])
            if len(content) > MAX_TG_LEN:
                content = content[-MAX_TG_LEN:]
            if not content.strip():
                content = "⏳ Working..."
            try:
                await status_msg.edit_text(content, parse_mode=ParseMode.HTML)
            except Exception:
                try:
                    await status_msg.edit_text(content)
                except Exception:
                    pass

        async def permission_cb(tool_name: str, params: dict) -> bool:
            cid = uuid.uuid4().hex[:8]
            future = asyncio.get_event_loop().create_future()
            self._pending[cid] = future
            preview = json.dumps(params, ensure_ascii=False)
            if len(preview) > 500: preview = preview[:500] + "..."
            kb = InlineKeyboardMarkup([[
                InlineKeyboardButton("✅", callback_data=f"y_{cid}"),
                InlineKeyboardButton("❌", callback_data=f"n_{cid}"),
            ]])
            await ctx.bot.send_message(chat_id,
                f"⚠️ <code>{html.escape(tool_name)}</code>\n<pre>{html.escape(preview)}</pre>",
                parse_mode=ParseMode.HTML, reply_markup=kb)
            try:
                return await asyncio.wait_for(future, timeout=300)
            except asyncio.TimeoutError:
                self._pending.pop(cid, None); return False

        engine = self._create_engine()
        self._last_engine[uid] = engine  # Store for /history, /transcript, /permissions access

        tool_ctx = ToolContext(working_dir=session.working_dir, config=self.config,
                               session_id=session.session_id, permission_callback=permission_cb)

        session.messages.append({"role": "user", "content": user_msg})

        try:
            async for event in engine.query_streaming(session.messages, tool_ctx):
                if event.type == "text":
                    text_buffer += event.text

                elif event.type == "tool_call":
                    tool_count += 1
                    icon = TOOL_ICONS.get(event.tool_name, "🔧")
                    param_preview = json.dumps(event.tool_params, ensure_ascii=False)
                    if len(param_preview) > 80: param_preview = param_preview[:80] + "…"
                    display_lines.append(f"{icon} <code>{html.escape(event.tool_name)}</code> {html.escape(param_preview)}")
                    await update_status()

                elif event.type == "tool_result":
                    tr = event.tool_result
                    status = "✅" if not tr.is_error else "❌"
                    preview = (tr.output or tr.error)[:80].replace("\n", " ")
                    # Show timing from ToolExecution if available
                    timing = ""
                    if event.tool_execution and event.tool_execution.duration_ms > 0:
                        timing = f" ({event.tool_execution.duration_ms:.0f}ms)"
                    display_lines.append(f"  {status} {html.escape(preview)}{timing}")
                    await update_status()

                elif event.type == "permission_denial":
                    denial_count += 1
                    denial = event.denial
                    display_lines.append(f"  🚫 <b>Denied:</b> {html.escape(event.tool_name)} — {html.escape(denial.reason[:60] if denial else 'blocked')}")
                    await update_status(force=True)

                elif event.type == "conscience":
                    # Show conscience deliberation (inner voices)
                    display_lines.append(f"⚖️ <i>{html.escape(event.text[:120])}</i>")
                    await update_status()

                elif event.type == "compact":
                    display_lines.append(f"📦 {event.text}")
                    session.compactions += 1
                    await update_status(force=True)

                elif event.type == "error":
                    display_lines.append(f"❌ {html.escape(event.text)}")
                    await update_status(force=True)

                elif event.type == "done":
                    if event.usage:
                        session.total_input_tokens += event.usage.input_tokens
                        session.total_output_tokens += event.usage.output_tokens
                        session.total_cost_usd += event.usage.cost_usd(self.config.model)
                    session.tool_calls += tool_count

            try: await status_msg.delete()
            except Exception: pass

            parts = []
            if display_lines:
                parts.append("\n".join(display_lines))
                parts.append("")
            if text_buffer.strip():
                parts.append(text_buffer.strip())

            # Footer with denial info + router stats
            cost = session.total_cost_usd
            daily = self.cost_tracker.today.total_cost_usd
            denial_str = f" | 🚫 {denial_count}" if denial_count else ""
            history_str = f" | 📜 {engine.history.as_compact_summary()}" if engine.history.events else ""
            router_stats = engine.router.get_stats()
            router_str = f" | 🔀 {router_stats['haiku_pct']}% Haiku" if router_stats['total_routed'] > 0 else ""
            parts.append(f"\n<i>🔧 {tool_count} tools{denial_str}{router_str} | 💰 ${cost:.4f} / ${daily:.4f}{history_str}</i>")

            response = "\n".join(parts)
            await self._send_long(ctx.bot, chat_id, response)

            # Feedback buttons
            if tool_count > 0:
                fb_id = uuid.uuid4().hex[:8]
                if not hasattr(self, '_pending_feedback'):
                    self._pending_feedback = {}
                self._pending_feedback[fb_id] = user_msg[:200]
                try:
                    await ctx.bot.send_message(
                        chat_id, " ",
                        reply_markup=InlineKeyboardMarkup([[
                            InlineKeyboardButton("👍", callback_data=f"fb+_{fb_id}"),
                            InlineKeyboardButton("👎", callback_data=f"fb-_{fb_id}"),
                        ]]),
                    )
                except Exception:
                    pass

            if not session.title and len(session.messages) >= 2:
                session.title = user_msg[:60]

            self._save_session(session, engine)

        except Exception as e:
            logger.error(f"Error: {e}", exc_info=True)
            try: await status_msg.edit_text(f"❌ {e}")
            except: await ctx.bot.send_message(chat_id, f"❌ {e}")

    async def _send_long(self, bot, chat_id: int, text: str):
        if len(text) <= MAX_TG_LEN:
            try: await bot.send_message(chat_id, text, parse_mode=ParseMode.HTML)
            except: await bot.send_message(chat_id, text)
            return
        while text:
            if len(text) <= MAX_TG_LEN:
                chunk, text = text, ""
            else:
                split = text.rfind("\n", 0, MAX_TG_LEN)
                if split < MAX_TG_LEN // 2: split = MAX_TG_LEN
                chunk, text = text[:split], text[split:].lstrip("\n")
            try: await bot.send_message(chat_id, chunk, parse_mode=ParseMode.HTML)
            except: await bot.send_message(chat_id, chunk)

    # ── Callback (permission + feedback buttons) ─────────────────

    async def handle_callback(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        q = update.callback_query; await q.answer()
        if not q.data: return

        if q.data.startswith("fb_"):
            parts = q.data.split("_", 2)
            if len(parts) == 3:
                feedback = "good" if parts[1] == "good" else "bad"
                fb_id = parts[2]
                fb_data = getattr(self, '_pending_feedback', {}).get(fb_id, {})
                task = fb_data.get("task", "")
                from experience import get_experience_store
                exp = get_experience_store(self.config.data_dir)
                exp.record_feedback(task, feedback)
                emoji = "👍" if feedback == "good" else "👎"
                try:
                    await q.edit_message_text(f"{emoji} Noted — learning from this.")
                except Exception:
                    pass
                return

        action, cid = q.data.split("_", 1)
        fut = self._pending.pop(cid, None)
        if fut and not fut.done():
            approved = action == "y"
            fut.set_result(approved)
            await q.edit_message_text(q.message.text + f"\n\n<b>{'✅ Approved' if approved else '❌ Denied'}</b>",
                                      parse_mode=ParseMode.HTML)

    # ── Run ──────────────────────────────────────────────────────

    async def run(self):
        await self._setup_mcp()
        app = Application.builder().token(self.config.telegram_token).build()

        for cmd, handler in [
            ("start", self.cmd_start), ("clear", self.cmd_clear), ("status", self.cmd_status),
            ("cd", self.cmd_cd), ("model", self.cmd_model), ("tools", self.cmd_tools),
            ("memory", self.cmd_memory), ("auto", self.cmd_auto), ("sessions", self.cmd_sessions),
            ("resume", self.cmd_resume), ("budget", self.cmd_budget), ("mcp", self.cmd_mcp),
            ("dreams", self.cmd_dreams), ("gallery", self.cmd_gallery),
            # New claw-code commands
            ("history", self.cmd_history), ("permissions", self.cmd_permissions),
            ("bootstrap", self.cmd_bootstrap), ("transcript", self.cmd_transcript),
            ("conscience", self.cmd_conscience), ("palazzo", self.cmd_palazzo),
            ("journal", self.cmd_journal), ("anima", self.cmd_anima),
            ("explore", self.cmd_explore), ("meet", self.cmd_meet),
        ]:
            app.add_handler(CommandHandler(cmd, handler))

        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_message))
        app.add_handler(CallbackQueryHandler(self.handle_callback))

        logger.info(f"Compagnon v3 — {self.config.active_model} — {len(self.registry.list_names())} tools")

        # ── Scheduler ──
        scheduler = None
        scheduler_task = None
        if HAS_SCHEDULER:
            scheduler = get_scheduler(self.config.data_dir)

            async def execute_scheduled_task(task_id: str, description: str) -> str:
                engine = self._create_engine()
                tool_ctx = ToolContext(working_dir=self.config.working_dir, config=self.config, session_id=f"sched_{task_id}")
                return await engine.run_agent(task=description, context=tool_ctx)

            async def notify_user(text: str):
                if self.config.telegram_allowed_users:
                    uid = self.config.telegram_allowed_users[0]
                elif self._sessions:
                    uid = next(iter(self._sessions))
                else:
                    return
                try:
                    await app.bot.send_message(uid, text, parse_mode=ParseMode.HTML)
                except Exception:
                    try:
                        await app.bot.send_message(uid, text)
                    except Exception as e:
                        logger.warning("Scheduler notify failed: %s", e)

            scheduler.on_task_execute = execute_scheduled_task
            scheduler.on_notify = notify_user
            scheduler_task = asyncio.create_task(scheduler.run())

        # ── Dreamworld ──
        from dreamworld import get_dreamworld
        async def _dream_llm_callback(prompt: str) -> str:
            engine = self._create_engine()
            if engine.client:
                resp = engine.client.messages.create(
                    model=self.config.active_model, max_tokens=2048,
                    messages=[{"role": "user", "content": prompt}], temperature=0.9,
                )
                return "".join(b.text for b in resp.content if hasattr(b, "text"))
            elif engine._local_client:
                resp = engine._local_client.create(
                    model=self.config.active_model, max_tokens=2048,
                    messages=[{"role": "user", "content": prompt}], temperature=0.9,
                )
                return "".join(b.text for b in resp.content if hasattr(b, "text"))
            return ""

        dreamworld = get_dreamworld(self.config.data_dir, llm_callback=_dream_llm_callback)
        dreamworld_task = asyncio.create_task(dreamworld.run())

        # Daily journal writer (runs every hour, writes once per day)
        async def _journal_loop():
            while True:
                try:
                    await asyncio.sleep(3600)  # Check every hour
                    bridge = get_soul_bridge(self.config.data_dir)
                    palazzo = get_palazzo(self.config.data_dir)
                    from mood import get_mood_engine
                    mood = get_mood_engine(self.config.data_dir).get_state()
                    conscience_stats = get_conscience(self.config.data_dir).get_stats()
                    bridge.write_daily_journal(
                        palazzo_log=palazzo.get_recent_log(10),
                        mood=mood, conscience_stats=conscience_stats,
                    )
                except asyncio.CancelledError:
                    break
                except Exception as e:
                    logger.debug(f"Journal loop: {e}")
        journal_task = asyncio.create_task(_journal_loop())

        webhook_url = os.getenv("WEBHOOK_URL", "")
        webhook_port = int(os.getenv("WEBHOOK_PORT", "8443"))

        async with app:
            await app.start()
            if webhook_url:
                webhook_path = f"/webhook/{self.config.telegram_token}"
                full_url = f"{webhook_url}{webhook_path}"
                await app.bot.set_webhook(url=full_url)
                logger.info(f"Webhook mode: {full_url}")
                await app.updater.start_webhook(listen="0.0.0.0", port=webhook_port, url_path=webhook_path, webhook_url=full_url)
            else:
                await app.updater.start_polling(poll_interval=0.5, drop_pending_updates=True)
                logger.info("Polling mode (set WEBHOOK_URL for instant delivery)")
            try:
                while True:
                    await asyncio.sleep(1)
            except (KeyboardInterrupt, asyncio.CancelledError):
                pass
            finally:
                if scheduler:
                    scheduler.stop()
                if scheduler_task:
                    scheduler_task.cancel()
                dreamworld.stop()
                dreamworld_task.cancel()
                journal_task.cancel()
                await app.updater.stop()
                await app.stop()
                await self.mcp_manager.disconnect_all()
                for uid, s in self._sessions.items():
                    engine = self._last_engine.get(uid)
                    self._save_session(s, engine)
