# Supervisor → Hermes-Style Agent Roadmap

## Overview
Mở rộng Supervisor API thành interactive agent giống Hermes, giữ nguyên ticket processing capability.

---

## Phase 1: CLI Mode (Immediate)

### Target
Thêm interactive terminal interface cho Supervisor.

### Components
1. **Rich UI** - Colorful terminal output với panels, tables
2. **Prompt Toolkit** - Interactive input với autocomplete
3. **Command registry** - Slash commands (/help, /status, /clear)

### Implementation
```
src/cli/
├── __init__.py
├── main.py          # HermesCLI class
├── banner.py        # Welcome banner
├── commands.py      # Slash command registry
├── completions.py   # Autocomplete
└── config.py        # CLI config
```

### Commands
- `/help` - Show help
- `/status` - System status
- `/clear` - Clear screen
- `/history` - Chat history
- `/quit` - Exit

---

## Phase 2: Expand Toolset (Week 1-2)

### Target
Thêm common tools giống Hermes.

### Tools to Add
1. **File Tools**
   - `read_file` - Read files with pagination
   - `write_file` - Write files
   - `search_files` - Grep-style search
   - `list_files` - Directory listing

2. **Terminal Tools**
   - `terminal` - Execute commands
   - `process` - Background process management

3. **Web Tools**
   - `web_search` - Search web
   - `fetch_url` - Fetch URL content
   - `browser` - Browser automation (optional)

4. **Code Tools**
   - `execute_code` - Python sandbox
   - `delegate_task` - Spawn subagents

---

## Phase 3: Skill System (Week 2-3)

### Target
Plugin architecture cho reusable workflows.

### Components
```
~/.hermes/skills/
├── SKILL.md          # Skill definition
├── references/       # Documentation
├── templates/        # Reusable templates
├── scripts/          # Helper scripts
└── assets/           # Static assets
```

### Commands
- `/skills` - List all skills
- `/skill load <name>` - Load a skill
- `/skill create <name>` - Create new skill

### Built-in Skills
- `it-report` - IT service report generation
- `data-pipeline` - Data processing
- `translation` - Vietnamese translation

---

## Phase 4: Multi-Platform (Week 3-4)

### Target
Hỗ trợ nhiều messaging platforms.

### Adapters
1. **Telegram** - Bot commands, inline queries
2. **Discord** - Slash commands, buttons
3. **Slack** - App home, modals

### Architecture
```
gateway/
├── run.py           # Main entry
├── session.py       # Session store
├── platforms/
│   ├── telegram.py
│   ├── discord.py
│   └── slack.py
└── handlers/        # Event handlers
```

---

## Phase 5: Advanced Features (Week 4+)

### 1. Subagent Delegation
- Spawn autonomous agents for complex tasks
- Parallel execution of subtasks
- Result aggregation

### 2. MCP Integration
- Model Context Protocol support
- Connect to external MCP servers
- Tool discovery from MCP

### 3. Enhanced Memory
- **fact_store** - Structured memory with queries
- **trajectory** - Conversation logging
- **holographic** - Proactive fact storage

---

## Technical Notes

### Shared Components
Supervisor và Hermes CLI sẽ share:
- LLM client (multi-provider)
- Tool registry
- Memory system
- Database models

### Config Location
```
~/.supervisor/          # Supervisor config
~/.hermes/              # Hermes config (new)
```

### Environment
```bash
# Supervisor mode
SUPERVISOR_MODE=web     # API mode (default)

# Hermes mode
SUPERVISOR_MODE=cli     # Interactive CLI
SUPERVISOR_MODE=telegram # Telegram bot
```

---

## Success Metrics

| Phase | Metric | Target |
|-------|--------|--------|
| Phase 1 | CLI startup | < 2s |
| Phase 2 | Tool coverage | 20+ tools |
| Phase 3 | Skill plugins | 5+ built-in |
| Phase 4 | Platform support | 3 platforms |
| Phase 5 | Subagent latency | < 5s |

---

## Timeline

- **Phase 1**: Week 0 (1-2 days)
- **Phase 2**: Week 1-2
- **Phase 3**: Week 2-3
- **Phase 4**: Week 3-4
- **Phase 5**: Week 4+

**Total: ~4-5 weeks for full Hermes-style agent**