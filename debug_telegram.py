#!/usr/bin/env python3
"""
Debug Telegram bot issues
Run: python debug_telegram.py
"""
import os
import sys
import asyncio
import httpx

# Load .env
from dotenv import load_dotenv
load_dotenv()

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
API_BASE = f"https://api.telegram.org/bot{BOT_TOKEN}" if BOT_TOKEN else ""

async def check_bot():
    """Check if bot token is valid and get bot info"""
    if not BOT_TOKEN:
        print("❌ TELEGRAM_BOT_TOKEN not set in .env")
        return False
    
    print(f"🔍 Checking bot token...")
    async with httpx.AsyncClient() as client:
        try:
            resp = await client.get(f"{API_BASE}/getMe", timeout=10)
            if resp.status_code == 200:
                data = resp.json()
                if data.get("ok"):
                    bot_info = data.get("result", {})
                    print(f"✅ Bot found: @{bot_info.get('username')} ({bot_info.get('first_name')})")
                    return True
            print(f"❌ Invalid bot token or API error: {resp.status_code}")
            print(f"   Response: {resp.text[:200]}")
        except Exception as e:
            print(f"❌ Error connecting to Telegram: {e}")
    return False

async def check_commands():
    """Check registered commands"""
    print(f"\n🔍 Checking registered commands...")
    async with httpx.AsyncClient() as client:
        try:
            resp = await client.get(f"{API_BASE}/getMyCommands", timeout=10)
            if resp.status_code == 200:
                data = resp.json()
                if data.get("ok"):
                    commands = data.get("result", [])
                    if commands:
                        print(f"✅ Commands registered ({len(commands)}):")
                        for cmd in commands:
                            print(f"   /{cmd['command']} - {cmd['description']}")
                    else:
                        print("⚠️  No commands registered")
                    return
            print(f"❌ Failed to get commands: {resp.status_code}")
        except Exception as e:
            print(f"❌ Error: {e}")

async def check_polling():
    """Check if bot is receiving updates (polling)"""
    print(f"\n🔍 Checking for recent updates (polling status)...")
    async with httpx.AsyncClient() as client:
        try:
            resp = await client.get(f"{API_BASE}/getUpdates", params={"timeout": 5}, timeout=10)
            if resp.status_code == 200:
                data = resp.json()
                if data.get("ok"):
                    updates = data.get("result", [])
                    print(f"✅ Bot is receiving updates (last {len(updates)} messages in queue)")
                    if updates:
                        last = updates[-1]
                        print(f"   Last update: {last.get('update_id')}")
                    return
            print(f"❌ Failed to check updates: {resp.status_code}")
        except Exception as e:
            print(f"❌ Error: {e}")

async def test_send(chat_id: str = None):
    """Test sending a message"""
    if not chat_id:
        print("\n⚠️  Skipping send test (no chat_id provided)")
        print("   Run: python debug_telegram.py --chat-id YOUR_CHAT_ID")
        return
    
    print(f"\n🔍 Testing send message to {chat_id}...")
    async with httpx.AsyncClient() as client:
        try:
            resp = await client.post(
                f"{API_BASE}/sendMessage",
                json={"chat_id": chat_id, "text": "✅ Bot is working! (debug test)"},
                timeout=10
            )
            if resp.status_code == 200:
                data = resp.json()
                if data.get("ok"):
                    print(f"✅ Test message sent successfully!")
                    return
            print(f"❌ Failed to send: {resp.status_code}")
            print(f"   Response: {resp.text[:200]}")
        except Exception as e:
            print(f"❌ Error: {e}")

async def main():
    print("=" * 60)
    print("Telegram Bot Debug Tool")
    print("=" * 60)
    
    if not BOT_TOKEN:
        print("\n❌ TELEGRAM_BOT_TOKEN not found in .env")
        print("   Please add: TELEGRAM_BOT_TOKEN=your_token_here")
        sys.exit(1)
    
    # Check bot info
    ok = await check_bot()
    if not ok:
        sys.exit(1)
    
    # Check commands
    await check_commands()
    
    # Check polling
    await check_polling()
    
    # Test send if chat_id provided
    chat_id = None
    if len(sys.argv) > 2 and sys.argv[1] == "--chat-id":
        chat_id = sys.argv[2]
    await test_send(chat_id)
    
    print("\n" + "=" * 60)
    print("Debug complete!")
    print("=" * 60)

if __name__ == "__main__":
    asyncio.run(main())
