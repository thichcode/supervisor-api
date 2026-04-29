#!/bin/bash
# Entrypoint for supervisor container - starts both API and Telegram gateway

set -e

echo "🚀 Starting Supervisor services..."

# Start Telegram gateway in background (if token is set)
if [ -n "$TELEGRAM_BOT_TOKEN" ]; then
    echo "📱 Starting Telegram gateway..."
    python -m src.gateway &
    TG_PID=$!
    echo "   Telegram gateway PID: $TG_PID"
else
    echo "⚠️  TELEGRAM_BOT_TOKEN not set, skipping Telegram gateway"
fi

# Start FastAPI server (foreground)
echo "🌐 Starting FastAPI server on port $APP_PORT..."
uvicorn src.api.app:app --host 0.0.0.0 --port ${APP_PORT:-8000} --workers 1

# If we get here, wait for Telegram gateway to finish
if [ -n "$TG_PID" ]; then
    wait $TG_PID
fi
