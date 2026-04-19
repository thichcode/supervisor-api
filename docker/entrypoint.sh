#!/bin/sh
set -eu

APP_CMD="python -m uvicorn src.api.app:app --host 0.0.0.0 --port 8000"
if [ "$#" -gt 0 ]; then
  ENABLE_GATEWAY_DEFAULT="false"
else
  ENABLE_GATEWAY_DEFAULT="true"
fi
ENABLE_GATEWAY="${ENABLE_TELEGRAM_GATEWAY:-$ENABLE_GATEWAY_DEFAULT}"
GATEWAY_TOKEN="${TELEGRAM_BOT_TOKEN:-}"

cleanup() {
  if [ -n "${API_PID:-}" ] && kill -0 "$API_PID" 2>/dev/null; then
    kill "$API_PID" 2>/dev/null || true
  fi
  if [ -n "${GATEWAY_PID:-}" ] && kill -0 "$GATEWAY_PID" 2>/dev/null; then
    kill "$GATEWAY_PID" 2>/dev/null || true
  fi
}

trap cleanup INT TERM EXIT

if [ "$#" -gt 0 ]; then
  echo "[entrypoint] Executing: $*"
  exec "$@"
fi

# Default container mode: run API + optional Telegram gateway together.
echo "[entrypoint] Starting supervisor API"
sh -lc "$APP_CMD" &
API_PID=$!

if [ "$ENABLE_GATEWAY" = "true" ] && [ -n "$GATEWAY_TOKEN" ]; then
  echo "[entrypoint] Starting Telegram gateway"
  python -m src.gateway &
  GATEWAY_PID=$!
else
  echo "[entrypoint] Telegram gateway disabled or token missing"
fi

# Keep container alive while both processes are healthy.
while :; do
  if ! kill -0 "$API_PID" 2>/dev/null; then
    echo "[entrypoint] API process exited"
    exit 1
  fi

  if [ -n "${GATEWAY_PID:-}" ] && ! kill -0 "$GATEWAY_PID" 2>/dev/null; then
    echo "[entrypoint] Telegram gateway process exited"
    exit 1
  fi

  sleep 2
done
