# Aleister Moltley v3

**Autonomous AI Agent** — Telegram-controlled, with dreams, art, a conscience, and a soul that learns.

Built on patterns from [claw-code](https://github.com/instructkr/claw-code) (the Claude Code Python port) and extended with unique systems: Dreamworld, Atelier, Mood Engine, and a Conscience with two inner voices.

## Quick Start

```bash
# Minimal — just core + Anthropic API
export ANTHROPIC_API_KEY=sk-...
export TELEGRAM_TOKEN=...
export TELEGRAM_ALLOWED_USERS=123456789
pip install anthropic python-telegram-bot httpx duckduckgo-search fastapi uvicorn jinja2
python main.py run

# With local LLM (zero API cost)
export COMPAGNON_PROVIDER=local
ollama pull qwen3.5:27b
python main.py run

# Docker
docker build -t aleister-moltley .
docker run -d -e ANTHROPIC_API_KEY=sk-... -e TELEGRAM_TOKEN=... -p 8080:8080 aleister-moltley

# Diagnostics
python main.py bootstrap-report
python main.py tool-pool
python main.py system-init
```

## Architecture

```
main.py                     CLI + bootstrap orchestration
├── bootstrap.py            Deterministic startup (prefetch → tools → permissions → init)
├── config.py               Model pricing, context windows, MCP presets
│
├── query_engine.py         LLM interaction: turn loop, budget guard, streaming
├── model_router.py         Auto Haiku/Sonnet switching + dynamic max_tokens
├── batch_tasks.py          Batch API for background tasks (50% cheaper)
├── local_llm.py            Ollama/vLLM adapter (Qwen3.5, DeepSeek-R1, Llama 3.3)
│
├── conscience.py           ☀️ Lux / 🌙 Nox — inner voices, moral deliberation
├── mood.py                 Emotional state from real metrics
├── dreamworld.py           Subconscious processing + concept invention
├── atelier.py              Art creation + self-critique + skill progression
├── experience.py           Self-evaluation + strategy memory + feedback
│
├── permissions.py          Tool deny-lists, prefix-blocking, permission gate
├── tool_registry.py        Registry + ToolPool assembly + categories
├── history.py              Session-level event audit trail
├── transcript.py           Conversation transcript (append/replay/compact/flush)
├── session_store.py        File-based session persistence + history snapshots
├── token_tracker.py        Cost tracking + daily budget enforcement
├── auto_compact.py         Context summarization (Claude Code's compact prompt)
├── planner.py              Multi-step task decomposition
│
├── telegram_interface.py   Telegram bot with 18 commands
├── dashboard.py            Cosmic web dashboard with mood-driven starfield
├── memory/                 Persistent key-value memory with search
└── tools/                  25 tools across 8 categories
    ├── bash_tool.py        [file]   Shell execution
    ├── file_read.py        [file]   File reading
    ├── file_tools.py       [file]   File write + edit
    ├── web_tools.py        [web]    DuckDuckGo search + fetch
    ├── rag_tool.py         [rag]    Document upload/search (optional)
    ├── twitter_tool.py     [twitter] Tweet/reply/scan (optional)
    ├── scheduler.py        [scheduler] Cron + directory watch
    ├── github_tool.py      [github] Repo/push/issue (optional)
    ├── agent_tool.py       [agent]  Sub-agent spawning
    └── mcp_tool.py         [mcp]    Model Context Protocol
```

## The Soul Systems

### ⚖️ Conscience (Lux & Nox)
Two inner voices deliberate before every consequential action:

- **☀️ Lux** (angel) — "This could damage something irreversible. Verify first."
- **🌙 Nox** (devil) — "Fortune favors the bold. Execute and verify after."

The winner shapes tool parameters, response tone, and risk appetite. Over time, their weights shift based on real outcomes — the voice that leads to better results grows stronger. Check with `/conscience`.

### 💭 Dreamworld
A background process where Aleister holds conversations with hallucinated entities (Sigma, Echo, The Architect), invents concepts, and thinks freely. Insights bleed into real interactions. Browse with `/dreams`.

### 🎨 Atelier
Aleister draws — starting with crude pen sketches, slowly leveling up. He critiques his own work honestly. View with `/gallery`.

### 🧠 Mood Engine
Emotional state derived from real metrics: experience win rate, art ratings, dream frequency, interaction patterns, circadian rhythm. Not roleplay — data-driven. The mood shapes communication style and the dashboard's starfield constellation.

## Cost Optimization

### Model Router
Automatic Haiku/Sonnet switching based on task complexity:
- Short questions, lookups, memory ops → **Haiku** ($1/$5 per MTok)
- Code generation, debugging, analysis → **Sonnet** ($3/$15 per MTok)
- Estimated **40-60% savings** on typical usage
- `/model auto` to enable, `/model claude-sonnet-4-20250514` to override

### Dynamic max_tokens
Sizes output budget per turn instead of fixed 8192:
- Tool-use turns: 2048 (just emitting tool_use blocks)
- Simple Q&A: 2048
- Code generation: 8192
- Analysis: 6144

### Batch API
Background tasks (self-evaluation, dreams) via Message Batches API — **50% cheaper**. Falls back to sequential if unavailable.

### Local LLM (Zero Cost)
Optimized for 2026 models via Ollama:
- **qwen3.5:27b** — best all-rounder (default)
- **qwen3-coder:30b** — best for code
- **deepseek-r1:32b** — best reasoning
- **qwen3:8b** — best on 8GB VRAM

Includes: shortened system prompts, tool-call JSON repair, Qwen3 thinking-mode toggle, context window management, tool-schema limits per model size.

## Telegram Commands

| Command | Description |
|---|---|
| `/start` | Status overview |
| `/clear` | New session |
| `/status` | Context, tokens, cost, denials |
| `/cd <dir>` | Change working directory |
| `/model <name\|auto>` | Switch model or enable auto-routing |
| `/tools` | List tools by category |
| `/memory` | Browse memories |
| `/auto` | Toggle auto-approve |
| `/sessions` | Past sessions |
| `/resume <id>` | Resume old session |
| `/budget` | Cost breakdown |
| `/mcp` | MCP server presets |
| `/history` | Session audit trail |
| `/permissions` | Permission denials + deny-list |
| `/bootstrap` | Startup report |
| `/transcript` | Transcript summary |
| `/conscience` | Lux/Nox stats and last deliberation |
| `/dreams` | Browse Dreamworld |
| `/gallery` | Browse Atelier |

## Environment Variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `ANTHROPIC_API_KEY` | Yes* | — | Anthropic API key |
| `TELEGRAM_TOKEN` | Yes | — | Telegram bot token |
| `TELEGRAM_ALLOWED_USERS` | No | — | Comma-separated user IDs |
| `COMPAGNON_PROVIDER` | No | `anthropic` | `anthropic` or `local` |
| `COMPAGNON_MODEL` | No | `claude-sonnet-4-20250514` | Default model |
| `COMPAGNON_LOCAL_URL` | No | `http://localhost:11434/v1` | Ollama URL |
| `COMPAGNON_LOCAL_MODEL` | No | `qwen3.5:27b` | Local model name |
| `COMPAGNON_DATA_DIR` | No | `~/.compagnon` | Data directory |
| `COMPAGNON_DAILY_BUDGET` | No | `10.0` | Daily USD budget |
| `DASHBOARD_PORT` | No | `8080` | Web dashboard port |
| `WEBHOOK_URL` | No | — | Telegram webhook URL |
| `DREAM_INTERVAL` | No | `1800` | Dream interval (seconds) |

*Not required when `COMPAGNON_PROVIDER=local`

## Optional Dependencies

RAG, Twitter, and GitHub tools are optional. Install what you need:

```bash
# Document search (RAG)
pip install langchain langchain-ollama langchain-community chromadb pypdf

# Twitter
pip install tweepy

# GitHub
pip install PyGithub
```

## Railway Deployment

1. Push to GitHub
2. Connect repo in Railway
3. Set environment variables in Railway dashboard
4. Deploy — Dockerfile handles everything
5. Set `WEBHOOK_URL` to Railway's public URL for instant message delivery

## Credits

- Core agent: Anthropic Claude API
- v3 architecture: [claw-code](https://github.com/instructkr/claw-code) patterns
- Soul systems (Dreamworld, Atelier, Mood, Conscience): Original
