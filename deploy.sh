#!/bin/bash
# Deploy supervisor-api to production server

set -e

echo "=== Supervisor-API Deploy Script ==="

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

# Check if running as root or with sudo
if [ "$EUID" -ne 0 ]; then
    echo -e "${YELLOW}Warning: Not running as root. Some operations may fail.${NC}"
fi

# 1. Install system dependencies
echo -e "\n${GREEN}[1/6] Installing system dependencies...${NC}"
if command -v apt-get &> /dev/null; then
    sudo apt-get update
    sudo apt-get install -y python3.11 python3-pip git curl docker.io docker-compose
elif command -v yum &> /dev/null; then
    sudo yum install -y python3 git curl docker
fi

# 2. Setup Python virtual environment
echo -e "\n${GREEN}[2/6] Setting up Python environment...${NC}"
python3 -m venv venv
source venv/bin/activate
pip install -e .

# 3. Copy environment file
echo -e "\n${GREEN}[3/6] Setting up environment...${NC}"
if [ ! -f .env ]; then
    cp .env.example .env
    echo -e "${YELLOW}Created .env from template. Please edit with your settings!${NC}"
fi

# 4. Start Docker services
echo -e "\n${GREEN}[4/6] Starting Docker services (PostgreSQL, Redis)...${NC}"
docker compose up -d postgres redis

# 5. Download and start llama.cpp
echo -e "\n${GREEN}[5/6] Setting up llama.cpp...${NC}"
if [ ! -d "llama.cpp" ]; then
    git clone https://github.com/ggerganov/llama.cpp.git
fi
cd llama.cpp
make
cd ..

# Check if model exists
MODEL_PATH="/root/models/Llama-3.1-8B-Instruct-Q4_K_M.gguf"
if [ ! -f "$MODEL_PATH" ]; then
    echo -e "${YELLOW}Model not found at $MODEL_PATH${NC}"
    echo -e "${YELLOW}Please download model manually from HuggingFace:${NC}"
    echo "  https://huggingface.co/bartowski/Llama-3.1-8B-Instruct-GGUF"
fi

# 6. Start the application
echo -e "\n${GREEN}[6/6] Starting supervisor-api...${NC}"

# Start llama.cpp in background
nohup llama-server \
    -m "$MODEL_PATH" \
    --port 8088 \
    --ctx-size 4096 \
    --parallel 4 \
    --host 0.0.0.0 \
    > /tmp/llama.log 2>&1 &

echo "Waiting for llama.cpp to load..."
sleep 30

# Verify llama.cpp
if curl -s http://localhost:8088/v1/models > /dev/null 2>&1; then
    echo -e "${GREEN}llama.cpp is running!${NC}"
else
    echo -e "${RED}Warning: llama.cpp may not be ready yet${NC}"
fi

# Start supervisor-api
echo -e "\n${GREEN}Starting supervisor-api...${NC}"
source venv/bin/activate
nohup python -m src.api.app > /tmp/supervisor.log 2>&1 &

sleep 5

# Health check
echo -e "\n${GREEN}Checking health...${NC}"
if curl -s http://localhost:8000/health > /dev/null 2>&1; then
    echo -e "${GREEN}✅ supervisor-api is running!${NC}"
else
    echo -e "${YELLOW}⚠️  supervisor-api may still be starting. Check logs:${NC}"
    echo "  tail -f /tmp/supervisor.log"
fi

echo -e "\n${GREEN}=== Deploy Complete! ===${NC}"
echo ""
echo "Services:"
echo "  - supervisor-api: http://localhost:8000"
echo "  - llama.cpp:      http://localhost:8088"
echo "  - PostgreSQL:     localhost:5432"
echo "  - Redis:          localhost:6379"
echo ""
echo "Logs:"
echo "  - tail -f /tmp/supervisor.log"
echo "  - tail -f /tmp/llama.log"
echo ""
echo "To stop: docker compose down"
