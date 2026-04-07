#!/bin/bash
# Quick load test script
# Run: ./load_test/run_tests.sh

set -e

BASE_URL="${BASE_URL:-http://localhost:8000}"
DURATION="${DURATION:-60s}"
VUS="${VUS:-50}"

echo "================================================"
echo "  Supervisor API Load Test"
echo "================================================"
echo "Base URL: $BASE_URL"
echo "Duration: $DURATION"
echo "VUs: $VUS"
echo ""

# Check if k6 is installed
if command -v k6 &> /dev/null; then
    echo "Using k6 for load testing..."
    
    # Run load test
    k6 run \
        --env BASE_URL="$BASE_URL" \
        --env WEBHOOK_SECRET="test-secret" \
        --duration "$DURATION" \
        --vus "$VUS" \
        load_test/k6_load.js
    
elif command -v locust &> /dev/null; then
    echo "Using Locust for load testing..."
    
    # Run locust in headless mode
    locust \
        --host="$BASE_URL" \
        --users="$VUS" \
        --spawn-rate=10 \
        --run-time="$DURATION" \
        --headless \
        --only-summary \
        -f load_test/locustfile.py
    
else
    echo "Neither k6 nor locust found!"
    echo "Installing k6: https://k6.io/docs/getting-started/installation/"
    echo "Installing locust: pip install locust"
    echo ""
    echo "Running basic curl tests instead..."
    
    # Basic smoke test with curl
    echo ""
    echo "1. Health Check..."
    curl -s "$BASE_URL/health" | jq . || echo "Health check failed"
    
    echo ""
    echo "2. Ready Check..."
    curl -s "$BASE_URL/health/ready" | jq . || echo "Ready check failed"
    
    echo ""
    echo "3. Metrics..."
    curl -s "$BASE_URL/metrics" | head -20 || echo "Metrics failed"
    
    echo ""
    echo "4. Webhook Test..."
    PAYLOAD='{
        "request_id": "curl-test-1",
        "source": "ms_teams",
        "timestamp": "'$(date -u +%Y-%m-%dT%H:%M:%SZ)'",
        "user": {"id": "user-1", "display_name": "Test User", "role": "employee"},
        "conversation": {"thread_id": "thread-1", "message_id": "msg-1"},
        "message": {"text": "Hello, I need help"}
    }'
    
    curl -s -X POST "$BASE_URL/webhook/n8n" \
        -H "Content-Type: application/json" \
        -H "X-Webhook-Secret: test-secret" \
        -d "$PAYLOAD" | jq . || echo "Webhook test failed"
    
    echo ""
    echo "================================================"
    echo "  Basic smoke test completed"
    echo "================================================"
fi
