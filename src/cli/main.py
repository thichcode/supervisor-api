"""
Supervisor CLI - Main interactive terminal
"""

import os
import sys
import asyncio
import json
from typing import Optional, List, Dict, Any
from pathlib import Path

# Rich for UI
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.markdown import Markdown
from rich.syntax import Syntax
from rich.style import Style
from rich import box

# Prompt toolkit for interactive input
from prompt_toolkit import PromptSession
from prompt_toolkit.styles import Style as PtStyle
from prompt_toolkit.completion import WordCompleter
from prompt_toolkit.key_binding import KeyBindings

from .banner import get_banner
from .commands import COMMAND_REGISTRY, resolve_command, get_help_text
from .config import load_cli_config

console = Console()


class SupervisorCLI:
    """
    Interactive CLI for Supervisor Agent
    """
    
    def __init__(self):
        self.config = load_cli_config()
        self.console = console
        self.history: List[Dict[str, Any]] = []
        self.session_id = None
        self.running = False
        
        # Initialize prompt session
        self.prompt_session = self._create_prompt_session()
        
    def _create_prompt_session(self) -> PromptSession:
        """Create interactive prompt session"""
        command_names = [cmd.name for cmd in COMMAND_REGISTRY]
        command_completer = WordCompleter(command_names, sentence=True)
        
        style = PtStyle([
            ("prompt", "fg:cyan bold"),
            ("completion-menu", "bg:ansiblue"),
            ("completion-item", "fg:white"),
        ])
        
        return PromptSession(
            message="[bold cyan]supervisor[/bold cyan] > ",
            completer=command_completer,
            style=style,
            reserve_space_for_menu=5,
        )
    
    def show_banner(self):
        """Display welcome banner"""
        banner = get_banner()
        self.console.print(Panel(
            banner,
            border_style="cyan",
            box=box.DOUBLE,
            padding=(1, 2),
        ))
        
        # Show quick help
        self.console.print("\n[dim]Type /help for commands[/dim]\n")
    
    async def chat(self, message: str) -> str:
        """
        Send message to Supervisor agent
        Returns: agent response
        """
        from src.core.supervisor import Supervisor
        from src.db import async_session
        
        async with async_session() as session:
            supervisor = Supervisor()
            
            # Process through agents
            result = await supervisor.process(
                message=message,
                user_id="cli_user",
                session_id=self.session_id,
            )
            
            # Store in history
            self.history.append({
                "user": message,
                "response": result.get("response", ""),
                "metadata": result.get("metadata", {}),
            })
            
            return result.get("response", "No response")
    
    async def handle_command(self, command: str) -> bool:
        """
        Handle slash commands
        Returns: True if handled, False otherwise
        """
        if not command.startswith("/"):
            return False
        
        canonical, args = resolve_command(command)
        
        if canonical == "help":
            self.show_help()
        elif canonical == "status":
            await self.show_status()
        elif canonical == "clear":
            self.console.clear()
            self.show_banner()
        elif canonical == "history":
            self.show_history()
        elif canonical == "quit" or canonical == "exit":
            self.running = False
            self.console.print("[yellow]Goodbye![/yellow]")
            return "exit"
        else:
            self.console.print(f"[red]Unknown command: {command}[/red]")
            self.console.print("[dim]Type /help for available commands[/dim]")
        
        return True
    
    def show_help(self):
        """Display help"""
        table = Table(title="Available Commands", box=box.ROUNDED)
        table.add_column("Command", style="cyan")
        table.add_column("Description", style="white")
        table.add_column("Aliases", style="dim")
        
        for cmd in COMMAND_REGISTRY:
            aliases = ", ".join(cmd.aliases) if cmd.aliases else "-"
            table.add_row(f"/{cmd.name}", cmd.description, aliases)
        
        self.console.print(table)
    
    async def show_status(self):
        """Display system status"""
        from src.core.supervisor import Supervisor
        from src.db import async_session
        
        s = Supervisor()
        
        table = Table(title="System Status", box=box.ROUNDED)
        table.add_column("Component", style="cyan")
        table.add_column("Status", style="white")
        
        table.add_row("LLM", "✓" if s.llm_client else "✗")
        table.add_row("Database", "✓")
        table.add_row("Cache", "✓")
        table.add_row("BM25", "✓" if s.use_bm25 else "✗")
        table.add_row("URL Fetcher", "✓" if hasattr(s, "url_fetcher") else "✗")
        
        self.console.print(table)
    
    def show_history(self):
        """Display conversation history"""
        if not self.history:
            self.console.print("[dim]No conversation history[/dim]")
            return
        
        for i, item in enumerate(self.history[-10:], 1):
            self.console.print(f"\n[cyan]You:[/cyan] {item['user']}")
            self.console.print(f"[green]Bot:[/green] {item['response'][:200]}...")
    
    async def run(self):
        """Main CLI loop"""
        self.running = True
        self.show_banner()
        
        while self.running:
            try:
                # Get user input
                user_input = await self.prompt_session.prompt_async()
                
                if not user_input.strip():
                    continue
                
                # Handle commands
                if user_input.startswith("/"):
                    result = await self.handle_command(user_input)
                    if result == "exit":
                        break
                    continue
                
                # Process as chat message
                with self.console.status("[bold cyan]Processing..."):
                    response = await self.chat(user_input)
                
                # Display response
                self.console.print(f"\n[bold green]Answer:[/bold green]\n{response}\n")
                
            except KeyboardInterrupt:
                self.console.print("\n[yellow]Use /quit to exit[/yellow]")
            except Exception as e:
                self.console.print(f"[red]Error: {e}[/red]")


async def main():
    """Entry point"""
    cli = SupervisorCLI()
    await cli.run()


if __name__ == "__main__":
    asyncio.run(main())