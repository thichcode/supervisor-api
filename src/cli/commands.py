"""
CLI Commands - Command registry
"""

from dataclasses import dataclass
from typing import List, Tuple, Optional


@dataclass
class CommandDef:
    """Command definition"""
    name: str
    description: str
    category: str
    aliases: Tuple[str, ...] = ()
    args_hint: str = ""
    cli_only: bool = True
    gateway_only: bool = False


# Command registry
COMMAND_REGISTRY = [
    CommandDef(
        "help",
        "Show available commands",
        "Info",
        aliases=("h", "?"),
        args_hint="",
    ),
    CommandDef(
        "status",
        "Show system status",
        "Info",
        aliases=("st",),
        args_hint="",
    ),
    CommandDef(
        "clear",
        "Clear screen",
        "Session",
        aliases=("cl",),
        args_hint="",
    ),
    CommandDef(
        "history",
        "Show conversation history",
        "Session",
        aliases=("hist",),
        args_hint="",
    ),
    CommandDef(
        "quit",
        "Exit the CLI",
        "Exit",
        aliases=("q", "exit"),
        args_hint="",
    ),
    # Knowledge commands
    CommandDef(
        "knowledge",
        "Search knowledge base",
        "Knowledge",
        aliases=("kb", "search"),
        args_hint="<query>",
    ),
    CommandDef(
        "policies",
        "List company policies",
        "Knowledge",
        aliases=(),
        args_hint="[category]",
    ),
    # IT Service commands
    CommandDef(
        "ticket",
        "Create or check ticket",
        "Service",
        aliases=(),
        args_hint="<action> [id]",
    ),
    CommandDef(
        "report",
        "Generate IT report",
        "Service",
        aliases=(),
        args_hint="<type>",
    ),
]


def resolve_command(command: str) -> Tuple[str, str]:
    """
    Resolve command alias to canonical name
    Returns: (canonical_name, arguments)
    """
    parts = command.split(None, 1)
    cmd = parts[0].lstrip("/").lower()
    args = parts[1] if len(parts) > 1 else ""
    
    # Find canonical name
    for cmd_def in COMMAND_REGISTRY:
        if cmd == cmd_def.name.lower() or cmd in [a.lower() for a in cmd_def.aliases]:
            return cmd_def.name, args
    
    # Unknown command
    return cmd, args


def get_help_text() -> str:
    """Get help text"""
    lines = ["Available commands:"]
    
    categories = {}
    for cmd in COMMAND_REGISTRY:
        if cmd.category not in categories:
            categories[cmd.category] = []
        categories[cmd.category].append(cmd)
    
    for category, commands in categories.items():
        lines.append(f"\n[{category}]")
        for cmd in commands:
            aliases = f" ({', '.join(cmd.aliases)})" if cmd.aliases else ""
            lines.append(f"  /{cmd.name}{aliases} - {cmd.description}")
    
    return "\n".join(lines)