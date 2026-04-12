"""
CLI Banner - Welcome screen
"""

from rich.style import Style
from rich.text import Text


def get_banner() -> Text:
    """Get welcome banner"""
    banner = Text()
    
    banner.append("╔══════════════════════════════════════════════════════════════╗\n", Style(color="cyan"))
    banner.append("║                                                              ║\n", Style(color="cyan"))
    banner.append("║   🤖 SUPERVISOR AGENT v2.1                                   ║\n", Style(color="cyan", bold=True))
    banner.append("║                                                              ║\n", Style(color="cyan"))
    banner.append("║   Multi-Agent System for IT Service Management              ║\n", Style(color="white"))
    banner.append("║                                                              ║\n", Style(color="cyan"))
    banner.append("║   Commands: /help /status /clear /history /quit             ║\n", Style(color="dim"))
    banner.append("║                                                              ║\n", Style(color="cyan"))
    banner.append("╚══════════════════════════════════════════════════════════════╝", Style(color="cyan"))
    
    return banner


def get_subtitle() -> str:
    """Get subtitle"""
    return "[dim]Interactive CLI - Type your message or /help for commands[/dim]"