#!/usr/bin/env python3
"""
Supervisor CLI Entry Point
Run with: python run_cli.py
         or: python -m src.cli.main
"""

import asyncio
import sys
import os

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


async def main():
    """Run the CLI"""
    from src.cli.main import SupervisorCLI
    
    cli = SupervisorCLI()
    await cli.run()


if __name__ == "__main__":
    asyncio.run(main())