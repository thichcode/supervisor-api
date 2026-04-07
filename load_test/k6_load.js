// K6 Load Test - Load Test
// Run: k6 run load_test/k6_load.js
// Install k6: https://k6.io/docs/getting-started/installation/

import http from 'k6/http';
import { check, sleep } from 'k6';
import { Rate, Trend, Counter, Gauge } from 'k6/metrics';

// Custom metrics
const errorRate = new Rate('errors');
const successRate = new Rate('success');
const healthDuration = new Trend('health_duration');
const webhookDuration = new Trend('webhook_duration');
const webhookErrorRate = new Rate('webhook_errors');
const concurrentRequests = new Gauge('concurrent_requests');

// Test configuration
export const options = {
  scenarios: {
    // Ramp up from 0 to 50 VUs over 2 minutes
    // Stay at 50 VUs for 5 minutes
    // Ramp down over 1 minute
    load_test: {
      executor: 'ramping-vus',
      startVUs: 0,
      stages: [
        { duration: '2m', target: 50 },
        { duration: '5m', target: 50 },
        { duration: '1m', target: 0 },
      ],
      gracefulRampDown: '30s',
    },
  },
  thresholds: {
    // Response time thresholds
    http_req_duration: ['p(50)<200', 'p(95)<500', 'p(99)<1000'],
    
    // Error rate threshold
    http_req_failed: ['rate<0.05'],
    
    // Custom thresholds
    errors: ['rate<0.05'],
    success: ['rate>0.90'],
    
    // Health check specific
    'health_duration': ['p(95)<300'],
    
    // Webhook specific
    'webhook_duration': ['p(95)<2000', 'p(99)<5000'],
  },
  
  // Summary
  summaryTrendStats: ['avg', 'min', 'max', 'p(50)', 'p(95)', 'p(99)'],
};

const BASE_URL = __ENV.BASE_URL || 'http://localhost:8000';
const WEBHOOK_SECRET = __ENV.WEBHOOK_SECRET || 'test-secret';

// Sample payloads
const samplePayloads = [
  {
    text: "Hello, I need help with password reset",
    intent: "faq",
  },
  {
    text: "What is the policy for annual leave?",
    intent: "policy",
  },
  {
    text: "I need to submit a support ticket for my laptop",
    intent: "support_case",
  },
  {
    text: "Can you generate a report on team performance?",
    intent: "analysis",
  },
];

function createWebhookPayload(intent = 'faq') {
  const payload = {
    request_id: `test-${Date.now()}-${Math.random().toString(36).substr(2, 9)}`,
    source: "ms_teams",
    timestamp: new Date().toISOString(),
    user: {
      id: `user-${Math.floor(Math.random() * 1000)}`,
      display_name: "Load Test User",
      role: "employee",
    },
    conversation: {
      thread_id: `thread-${Math.floor(Math.random() * 100)}`,
      message_id: `msg-${Date.now()}`,
    },
    message: {
      text: samplePayloads.find(p => p.intent === intent)?.text || "Help me",
    },
  };
  return payload;
}

function computeHMAC(payload, secret) {
  // Simple HMAC for testing
  const crypto = require('crypto');
  return crypto.createHmac('sha256', secret).update(JSON.stringify(payload)).digest('hex');
}

export function setup() {
  // Warm up the service
  console.log('Warming up service...');
  http.get(`${BASE_URL}/health`);
  http.get(`${BASE_URL}/health/ready`);
  
  return { startTime: Date.now() };
}

export default function (data) {
  // Track concurrent requests
  concurrentRequests.add(1);
  
  // Randomly pick what to test
  const testType = Math.random();
  
  if (testType < 0.05) {
    // 5% - Health checks
    const start = Date.now();
    const res = http.get(`${BASE_URL}/health`);
    healthDuration.add(Date.now() - start);
    
    check(res, {
      'health OK': (r) => r.status === 200,
    });
    
    if (res.status !== 200) errorRate.add(1);
    else successRate.add(1);
    
  } else if (testType < 0.10) {
    // 5% - Ready check
    const res = http.get(`${BASE_URL}/health/ready`);
    
    check(res, {
      'ready OK': (r) => r.status === 200,
    });
    
  } else if (testType < 0.15) {
    // 5% - Metrics
    const res = http.get(`${BASE_URL}/metrics`);
    
    check(res, {
      'metrics OK': (r) => r.status === 200,
    });
    
  } else {
    // 85% - Webhook requests
    const payload = createWebhookPayload();
    const payloadStr = JSON.stringify(payload);
    const signature = computeHMAC(payload, WEBHOOK_SECRET);
    
    const params = {
      headers: {
        'Content-Type': 'application/json',
        'X-Webhook-Secret': WEBHOOK_SECRET,
        'X-Signature': signature,
        'X-Timestamp': Math.floor(Date.now() / 1000).toString(),
      },
    };
    
    const start = Date.now();
    const res = http.post(`${BASE_URL}/webhook/n8n`, payloadStr, params);
    webhookDuration.add(Date.now() - start);
    
    const success = check(res, {
      'webhook 200': (r) => r.status === 200,
      'webhook 202': (r) => r.status === 202,
      'webhook response valid': (r) => {
        try {
          const body = JSON.parse(r.body);
          return body.status !== undefined;
        } catch (e) {
          return false;
        }
      },
    });
    
    if (!success) {
      errorRate.add(1);
      webhookErrorRate.add(1);
      console.log(`Webhook error: ${res.status} - ${res.body}`);
    } else {
      successRate.add(1);
    }
  }
  
  // Simulate realistic think time
  sleep(Math.random() * 2 + 0.5);
  
  concurrentRequests.add(-1);
}

export function handleSummary(data) {
  return {
    'stdout': textSummary(data, { indent: ' ', enableColors: true }),
    'summary.json': JSON.stringify(data),
  };
}

function textSummary(data, options) {
  const { metrics } = data;
  
  let summary = '\n';
  summary += '='.repeat(60) + '\n';
  summary += '  LOAD TEST SUMMARY\n';
  summary += '='.repeat(60) + '\n\n';
  
  // Duration
  const duration = Math.round(data.state.testRunDurationMs / 1000);
  summary += `Test Duration: ${Math.floor(duration / 60)}m ${duration % 60}s\n`;
  summary += `VUs Max: ${data.state.vusMax}\n\n`;
  
  // HTTP metrics
  summary += 'HTTP Metrics:\n';
  summary += `  Requests: ${metrics.http_reqs.values.count}\n`;
  summary += `  Failed: ${(metrics.http_req_failed.values.passes / metrics.http_reqs.values.count * 100).toFixed(2)}%\n`;
  summary += `  Duration (avg): ${metrics.http_req_duration.values.avg.toFixed(2)}ms\n`;
  summary += `  Duration (p95): ${metrics.http_req_duration.values['p(95)'].toFixed(2)}ms\n`;
  summary += `  Duration (p99): ${metrics.http_req_duration.values['p(99)'].toFixed(2)}ms\n\n`;
  
  // Custom metrics
  summary += 'Webhook Metrics:\n';
  if (metrics.webhook_duration) {
    summary += `  Avg: ${metrics.webhook_duration.values.avg.toFixed(2)}ms\n`;
    summary += `  p95: ${metrics.webhook_duration.values['p(95)'].toFixed(2)}ms\n`;
    summary += `  p99: ${metrics.webhook_duration.values['p(99)'].toFixed(2)}ms\n`;
  }
  if (metrics.webhook_errors) {
    const errRate = (metrics.webhook_errors.values.passes / metrics.http_reqs.values.count * 100).toFixed(2);
    summary += `  Error Rate: ${errRate}%\n`;
  }
  
  summary += '\n';
  
  // Thresholds
  summary += 'Thresholds:\n';
  for (const [name, threshold] of Object.entries(data.thresholds)) {
    const passed = threshold.ok ? '✓ PASS' : '✗ FAIL';
    summary += `  ${name}: ${passed}\n`;
  }
  
  summary += '\n' + '='.repeat(60) + '\n';
  
  return summary;
}
