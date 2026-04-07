// K6 Load Test - Smoke Test
// Run: k6 run load_test/k6_smoke.js
// Install: k6 run --http-debug load_test/k6_smoke.js

import http from 'k6/http';
import { check, sleep } from 'k6';
import { Rate, Trend } from 'k6/metrics';

// Custom metrics
const errorRate = new Rate('errors');
const healthDuration = new Trend('health_duration');
const webhookDuration = new Trend('webhook_duration');

export const options = {
  scenarios: {
    smoke: {
      executor: 'constant-vus',
      vus: 1,
      duration: '30s',
    },
  },
  thresholds: {
    http_req_duration: ['p(95)<500'],
    http_req_failed: ['rate<0.01'],
    errors: ['rate<0.01'],
  },
};

const BASE_URL = __ENV.BASE_URL || 'http://localhost:8000';

export default function () {
  // Test 1: Health check
  const healthStart = Date.now();
  const healthRes = http.get(`${BASE_URL}/health`);
  healthDuration.add(Date.now() - healthStart);
  
  check(healthRes, {
    'health endpoint returns 200': (r) => r.status === 200,
    'health has version': (r) => JSON.parse(r.body).version !== undefined,
  });
  
  if (healthRes.status !== 200) {
    errorRate.add(1);
  }
  
  sleep(1);
  
  // Test 2: Ready check
  const readyRes = http.get(`${BASE_URL}/health/ready`);
  check(readyRes, {
    'ready endpoint returns 200': (r) => r.status === 200,
  });
  
  if (readyRes.status !== 200) {
    errorRate.add(1);
  }
  
  sleep(1);
  
  // Test 3: Metrics endpoint
  const metricsRes = http.get(`${BASE_URL}/metrics`);
  check(metricsRes, {
    'metrics endpoint returns 200': (r) => r.status === 200,
    'metrics has content': (r) => r.body.length > 0,
  });
  
  if (metricsRes.status !== 200) {
    errorRate.add(1);
  }
}
