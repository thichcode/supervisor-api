#!/bin/bash
# Start supervisor-api with llama.cpp on host

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo -e "${YELLOW}Starting supervisor-api...${NC}"

# Check if llama.cpp is running
if ! ss -tlnp | grep -q ":8088"; then
    echo -e "${YELLOW}Starting llama.cpp server...${NC}"
    cd /root/models
    nohup llama-server -m Llama-3.1-8B-Instruct-Q4_K_M.gguf --port 8088 --ctx-size 4096 --parallel 4 > /tmp/llama.log 2>&1 &
    echo -e "${YELLOW}Waiting for llama.cpp to load...${NC}"
    sleep 15
fi

# Check if llama.cpp is responding
if ! curl -s http://localhost:8088/v1/models > /dev/null 2>&1; then
    echo -e "${RED}ERROR: llama.cpp not responding${NC}"
    exit 1
fi
echo -e "${GREEN}llama.cpp is running${NC}"

# Set environment variables
export DB_HOST=localhost
export DB_PORT=5432
export DB_NAME=supervisor_db
export DB_USER=postgres
export DB_PASSWORD=postgres
export REDIS_HOST=localhost
export REDIS_PORT=6379
export REDIS_PASSWORD=
export LLM_PROVIDER=ollama
export LLM_MODEL=Llama-3.1-8B-Instruct-Q4_K_M.gguf
export OLLAMA_BASE_URL=http://localhost:8088
export OLLAMA_TIMEOUT=320
export LOG_LEVEL=INFO

# Check Docker services
if ! docker ps | grep -q postgres; then
    echo -e "${YELLOW}Starting Docker services (postgres, redis)...${NC}"
    cd /tmp/supervisor-api
    docker compose up -d postgres redis
    sleep 5
fi

# Check if supervisor is already running
if pgrep -f "uvicorn src.api.app" > /dev/null; then
    echo -e "${YELLOW}supervisor-api is already running${NC}"
else
    echo -e "${YELLOW}Starting supervisor-api...${NC}"
    cd /tmp/supervisor-api
    source /tmp/supervisor-venv/bin/activate
    nohup python -m uvicorn src.api.app:app --host 0.0.0.0 --port 8000 > /tmp/supervisor.log 2>&1 &
    sleep 5
fi

# Verify
echo ""
echo -e "${GREEN}=== Service Status ===${NC}"
echo -e "llama.cpp: $(ss -tlnp | grep -q ':8088' && echo -e "${GREEN}Running${NC}" || echo -e "${RED}Stopped${NC}")"
echo -e "supervisor-api: $(curl -s http://localhost:8000/health > /dev/null 2>&1 && echo -e "${GREEN}Running${NC}" || echo -e "${RED}Stopped${NC}")"
echo -e "PostgreSQL: $(docker ps | grep -q postgres && echo -e "${GREEN}Running${NC}" || echo -e "${RED}Stopped${NC}")"
echo -e "Redis: $(docker ps | grep -q redis && echo -e "${GREEN}Running${NC}" || echo -e "${RED}Stopped${NC}")"

echo ""
echo -e "API: ${GREEN}http://localhost:8000${NC}"
echo -e "Health: http://localhost:8000/health"
echo -e "Docs: http://localhost:8000/docs"
