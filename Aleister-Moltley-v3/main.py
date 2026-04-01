"""Compagnon v3 — Autonomous AI Agent with structured bootstrap.

Startup sequence (from claw-code patterns):
1. Parse CLI args
2. Build config from env
3. Run bootstrap (prefetch memory/experience/MCP, load tools, check permissions)
4. Build system init message
5. Start dashboard
6. Start Telegram bot
"""
import asyncio
import argparse
import logging
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config import CompagnonConfig

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s", datefmt="%H:%M:%S")
logger = logging.getLogger("compagnon")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Compagnon v3 — Autonomous AI Agent")
    subparsers = parser.add_subparsers(dest="command")

    # Default: run the bot
    run_parser = subparsers.add_parser("run", help="Start the bot (default)")
    run_parser.add_argument("--model", help="Anthropic model")
    run_parser.add_argument("--work-dir", help="Working directory")
    run_parser.add_argument("--auto-approve", action="store_true", help="Auto-approve all")
    run_parser.add_argument("--debug", action="store_true", help="Debug logging")
    run_parser.add_argument("--budget", type=float, help="Daily budget in USD")
    run_parser.add_argument("--local", action="store_true", help="Use local LLM (Ollama)")
    run_parser.add_argument("--local-model", help="Local model name")
    run_parser.add_argument("--local-url", help="Local LLM API URL")

    # Diagnostics (from claw-code CLI pattern)
    subparsers.add_parser("bootstrap-report", help="Run bootstrap and show startup report")
    subparsers.add_parser("tool-pool", help="Show assembled tool pool")
    subparsers.add_parser("session-summary", help="Show session summary template")
    subparsers.add_parser("system-init", help="Show system init message")

    return parser


def apply_args_to_config(args, config: CompagnonConfig):
    """Apply CLI args to config."""
    if hasattr(args, "model") and args.model:
        config.model = args.model
    if hasattr(args, "work_dir") and args.work_dir:
        config.working_dir = os.path.realpath(args.work_dir)
    if hasattr(args, "auto_approve") and args.auto_approve:
        config.auto_approve_write = True
        config.auto_approve_bash_destructive = True
    if hasattr(args, "budget") and args.budget:
        config.daily_budget_usd = args.budget
    if hasattr(args, "local") and args.local:
        config.provider = "local"
    if hasattr(args, "local_model") and args.local_model:
        config.local_model = args.local_model
    if hasattr(args, "local_url") and args.local_url:
        config.local_base_url = args.local_url
    if hasattr(args, "debug") and args.debug:
        logging.getLogger().setLevel(logging.DEBUG)


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)

    # Default to "run" if no subcommand
    command = args.command or "run"

    config = CompagnonConfig.from_env()

    # Load MCP from env
    mcp_json = os.getenv("COMPAGNON_MCP_SERVERS")
    if mcp_json:
        import json
        try:
            config.mcp_servers = json.loads(mcp_json)
        except json.JSONDecodeError as e:
            logger.error(f"Bad MCP JSON: {e}")

    # Load custom instructions
    instructions_file = os.getenv("COMPAGNON_INSTRUCTIONS_FILE")
    if instructions_file and os.path.exists(instructions_file):
        config.custom_instructions = open(instructions_file).read()

    apply_args_to_config(args, config)

    # ── Diagnostic commands (from claw-code CLI pattern) ──

    if command == "bootstrap-report":
        from bootstrap import run_bootstrap
        report = run_bootstrap(config)
        print(report.as_markdown())
        return 0

    if command == "tool-pool":
        from bootstrap import run_bootstrap
        report = run_bootstrap(config)
        # Get registry from bootstrap
        from tool_registry import ToolRegistry
        from permissions import ToolPermissionContext
        registry = ToolRegistry()
        # Re-run tool loading for display
        _register_all_tools(registry, config)
        pool = registry.assemble_pool(
            permission_context=ToolPermissionContext.from_config(config)
        )
        print(pool.as_markdown())
        return 0

    if command == "system-init":
        from bootstrap import build_system_init_message
        from tool_registry import ToolRegistry
        registry = ToolRegistry()
        _register_all_tools(registry, config)
        print(build_system_init_message(config, registry))
        return 0

    if command == "session-summary":
        from history import HistoryLog
        from transcript import TranscriptStore
        from permissions import PermissionGate, ToolPermissionContext
        h = HistoryLog(session_id="demo")
        h.add("startup", "bootstrap complete", "all stages ok")
        h.add_routing(["bash", "web_search"], "demo query")
        t = TranscriptStore()
        t.append("user", "Hello")
        t.append("assistant", "Hi there!")
        print(h.as_markdown())
        print()
        print(t.get_summary())
        return 0

    # ── Run the bot ──

    # Validate
    if not config.is_local and not config.anthropic_api_key:
        logger.error("ANTHROPIC_API_KEY not set (use COMPAGNON_PROVIDER=local for local LLM)")
        sys.exit(1)
    if not config.telegram_token:
        logger.error("TELEGRAM_TOKEN not set")
        sys.exit(1)

    # Bootstrap
    from bootstrap import run_bootstrap
    report = run_bootstrap(config)
    logger.info("Bootstrap complete in %.0fms", report.startup_time_ms)
    for stage in report.stages:
        icon = "✓" if stage.success else "✗"
        logger.info(f"  {icon} {stage.name} ({stage.duration_ms:.0f}ms)")

    logger.info(f"Aleister Moltley awakening")
    logger.info(f"  Provider: {config.provider}")
    logger.info(f"  Model: {config.active_model}")
    if config.is_local:
        logger.info(f"  Local URL: {config.local_base_url}")
    logger.info(f"  Dir: {config.working_dir}")
    logger.info(f"  Budget: ${config.daily_budget_usd}/day")
    logger.info(f"  MCP: {list(config.mcp_servers.keys()) or 'none'}")
    logger.info(f"  Tools: {report.tool_count}")

    # Start web dashboard in background
    from dashboard import start_dashboard
    start_dashboard(config)

    # Start bot
    from telegram_interface import CompagnonBot
    bot = CompagnonBot(config)
    asyncio.run(bot.run())


def _register_all_tools(registry, config):
    """Helper to register all tools (used by diagnostics)."""
    try:
        from tools.bash_tool import BashTool
        from tools.file_read import FileReadTool
        from tools.file_tools import FileWriteTool, FileEditTool
        from tools.web_tools import WebSearchTool, WebFetchTool
        from tools.agent_tool import AgentTool
        from memory.memory import MemoryStore, MemoryReadTool, MemoryWriteTool

        for tool_cls in [BashTool, FileReadTool, FileWriteTool, FileEditTool,
                         WebSearchTool, WebFetchTool, AgentTool]:
            try:
                registry.register(tool_cls())
            except Exception:
                pass

        # Optional tools
        for module_name, class_names in [
            ("tools.github_tool", ["GitHubCreateRepoTool", "GitHubPushFileTool",
                                    "GitHubCreateIssueTool", "GitHubListReposTool", "GitHubReadFileTool"]),
            ("tools.rag_tool", ["RAGUploadTool", "RAGSearchTool", "RAGListTool", "RAGDeleteTool"]),
            ("tools.twitter_tool", ["TwitterSearchTool", "TwitterReplyTool",
                                     "TwitterPostTool", "TwitterScanTool"]),
            ("tools.scheduler", ["ScheduleAddTool", "ScheduleListTool",
                                  "ScheduleRemoveTool", "WatchDirTool"]),
        ]:
            try:
                import importlib
                mod = importlib.import_module(module_name)
                for cls_name in class_names:
                    cls = getattr(mod, cls_name, None)
                    if cls:
                        try:
                            registry.register(cls())
                        except Exception:
                            pass
            except ImportError:
                pass

        mem_store = MemoryStore(config.memory_dir)
        registry.register(MemoryReadTool(mem_store))
        registry.register(MemoryWriteTool(mem_store))
    except Exception as e:
        logger.warning(f"Tool registration error: {e}")


if __name__ == "__main__":
    main()
