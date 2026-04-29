#!/bin/bash
# Deploy supervisor-api với Hindsight integration
set -e

echo "=== Supervisor-API + Hindsight Deploy ==="

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

# 1. Setup environment
echo -e "\n${GREEN}[1/7] Setup environment...${NC}"
if [ ! -f .env ]; then
    cp .env.example .env
    echo -e "${YELLOW}Created .env - please edit with your settings!${NC}"
fi

# Load env vars
set -a && source .env && set +a

# 2. Start Docker services
echo -e "\n${GREEN}[2/7] Starting PostgreSQL, Redis, Hindsight...${NC}"
docker compose -f docker-compose.prod.yml up -d postgres redis hindsight-postgres

# Wait for databases
echo "Waiting for databases to start..."
sleep 10

# Start Hindsight
docker compose -f docker-compose.prod.yml up -d hindsight

echo -e "${GREEN}Waiting for Hindsight to initialize...${NC}"
sleep 30

# 3. Download llama.cpp if needed
echo -e "\n${GREEN}[3/7] Setting up llama.cpp...${NC}"
if [ ! -d "llama.cpp" ]; then
    git clone https://github.com/ggerganov/llama.cpp.git
    cd llama.cpp && make && cd ..
fi

# 4. Download model if needed
MODEL_PATH="${MODEL_PATH:-/root/models/Llama-3.1-8B-Instruct-Q4_K_M.gguf}"
if [ ! -f "$MODEL_PATH" ]; then
    echo -e "${YELLOW}Model not found. Downloading...${NC}"
    mkdir -p /root/models
    # Example: download from HuggingFace (thay bằng link thực tế)
    # wget -O "$MODEL_PATH" <model-url>
    echo -e "${YELLOW}Please download model manually from:${NC}"
    echo "  https://huggingface.co/bartowski/Llama-3.1-8B-Instruct-GGUF"
fi

# 5. Start llama.cpp
echo -e "\n${GREEN}[4/7] Starting llama.cpp...${NC}"
if ! pgrep -f "llama-server" > /dev/null; then
    nohup llama-server \
        -m "$MODEL_PATH" \
        --port 8088 \
        --ctx-size 4096 \
        --parallel 4 \
        --host 0.0.0.0 \
        > /tmp/llama.log 2>&1 &
    echo "Waiting for llama.cpp to load model..."
    sleep 60
fi

# Verify llama.cpp
if curl -s http://localhost:8088/v1/models > /dev/null 2>&1; then
    echo -e "${GREEN}✅ llama.cpp running${NC}"
else
    echo -e "${RED}⚠️ llama.cpp not responding yet${NC}"
fi

# 6. Build supervisor-api
echo -e "\n${GREEN}[5/7] Building supervisor-api...${NC}"
docker compose -f docker-compose.prod.yml build supervisor

# 7. Start supervisor-api
echo -e "\n${GREEN}[6/7] Starting supervisor-api...${NC}"
docker compose -f docker-compose.prod.yml up -d supervisor

# 8. Health check
echo -e "\n${GREEN}[7/7] Health check...${NC}"
sleep 10

echo ""
echo "=== Services Status ==="
docker compose -f docker-compose.prod.yml ps

echo ""
echo "=== Testing Hindsight ==="
if curl -s http://localhost:8888/health > /dev/null 2>&1; then
    echo -e "${GREEN}✅ Hindsight API running${NC}"
else
    echo -e "${YELLOW}⚠️ Hindsight not ready${NC}"
fi

echo ""
echo -e "${GREEN}=== Deploy Complete! ===${NC}"
echo ""
echo "Services:"
echo "  - supervisor-api:  http://localhost:8000"
echo "  - Hindsight API:  http://localhost:8888"
echo "  - Hindsight UI:   http://localhost:9999"
echo "  - llama.cpp:       http://localhost:8088"
echo "  - PostgreSQL:      localhost:5432 (supervisor), 5433 (hindsight)"
echo "  - Redis:           localhost:6379"
echo ""
echo "Logs:"
echo "  docker compose -f docker-compose.prod.yml logs -f"
