# Locust Load Test - Alternative to k6
# Run: locust -f load_test/locustfile.py --host=http://localhost:8000
# Web UI: http://localhost:8089

from locust import HttpUser, task, between, events
from locust.runners import MasterRunner
import random
import json
import hashlib
import hmac
import time


class SupervisorUser(HttpUser):
    """
    Simulates a user making requests to the supervisor API
    """
    wait_time = between(1, 3)  # Wait 1-3 seconds between tasks
    host = "http://localhost:8000"
    
    def on_start(self):
        """Called when a simulated user starts"""
        # Verify service is healthy
        response = self.client.get("/health")
        if response.status_code != 200:
            print(f"Health check failed: {response.status_code}")
    
    @task(10)
    def health_check(self):
        """Lightweight health check - most common"""
        with self.client.get("/health/ready", catch_response=True) as response:
            if response.status_code == 200:
                response.success()
            else:
                response.failure(f"Health check failed: {response.status_code}")
    
    @task(5)
    def metrics(self):
        """Get metrics"""
        self.client.get("/metrics")
    
    @task(1)
    def webhook_simple(self):
        """Simple webhook request"""
        payload = self._create_payload("faq")
        
        response = self.client.post(
            "/webhook/n8n",
            json=payload,
            headers={
                "Content-Type": "application/json",
                "X-Webhook-Secret": "test-secret",
            },
            catch_response=True
        )
        
        if response.status_code in [200, 202]:
            response.success()
        elif response.status_code == 429:
            response.failure("Rate limited")
        else:
            response.failure(f"Webhook failed: {response.status_code}")
    
    @task(2)
    def webhook_policy(self):
        """Policy-related webhook request"""
        payload = self._create_payload("policy")
        
        self.client.post(
            "/webhook/n8n",
            json=payload,
            headers={"X-Webhook-Secret": "test-secret"}
        )
    
    @task(1)
    def webhook_support_case(self):
        """Support case webhook request"""
        payload = self._create_payload("support_case")
        payload["case"] = {
            "case_id": f"CASE-{random.randint(1000, 9999)}",
            "priority": random.choice(["low", "medium", "high"])
        }
        
        self.client.post(
            "/webhook/n8n",
            json=payload,
            headers={"X-Webhook-Secret": "test-secret"}
        )
    
    @task(0.5)
    def webhook_analysis(self):
        """Analysis request - less common"""
        payload = self._create_payload("analysis")
        
        self.client.post(
            "/webhook/n8n",
            json=payload,
            headers={"X-Webhook-Secret": "test-secret"}
        )
    
    def _create_payload(self, intent="faq"):
        """Create a test payload"""
        messages = {
            "faq": [
                "How do I reset my password?",
                "What are the IT support hours?",
                "How do I request a new laptop?",
                "Where can I find the VPN client?",
            ],
            "policy": [
                "What is the remote work policy?",
                "Can I expense this software?",
                "What is the vacation policy?",
            ],
            "support_case": [
                "My laptop is not working properly",
                "I need access to a shared folder",
                "The printer is showing an error",
            ],
            "analysis": [
                "Generate a report on ticket resolution times",
                "What are the common IT issues this month?",
                "Show me the backup compliance report",
            ]
        }
        
        return {
            "request_id": f"locust-{time.time()}-{random.randint(1000, 9999)}",
            "source": "ms_teams",
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "user": {
                "id": f"user-{random.randint(1, 100)}",
                "display_name": f"Test User {random.randint(1, 100)}",
                "role": random.choice(["employee", "manager", "admin"]),
            },
            "conversation": {
                "thread_id": f"thread-{random.randint(1, 50)}",
                "message_id": f"msg-{time.time()}",
            },
            "message": {
                "text": random.choice(messages.get(intent, messages["faq"]))
            }
        }


class AdminUser(HttpUser):
    """
    Simulates an admin user checking system status
    """
    wait_time = between(5, 15)  # Less frequent
    host = "http://localhost:8000"
    
    def on_start(self):
        """Called when a simulated admin starts"""
        # Verify admin access
        response = self.client.get("/admin/errors/dlq")
        # May fail if not authenticated, that's OK for testing
    
    @task(3)
    def check_health(self):
        """Check system health"""
        self.client.get("/health")
    
    @task(2)
    def check_metrics(self):
        """Check metrics"""
        self.client.get("/metrics")
    
    @task(1)
    def check_dlq(self):
        """Check DLQ status (admin)"""
        self.client.get("/admin/errors/dlq")
    
    @task(1)
    def check_circuit_breakers(self):
        """Check circuit breaker status"""
        self.client.get("/admin/errors/circuit-breakers")


# Event handlers for custom reporting
@events.test_start.add_listener
def on_test_start(environment, **kwargs):
    print(f"Starting load test against {environment.host}")


@events.test_stop.add_listener
def on_test_stop(environment, **kwargs):
    print("Load test completed")
    
    # Print summary stats
    stats = environment.stats
    print(f"\nTotal requests: {stats.total.num_requests}")
    print(f"Failed requests: {stats.total.num_failures}")
    print(f"Average response time: {stats.total.avg_response_time:.2f}ms")
    print(f"95th percentile: {stats.total.get_response_time_percentile(0.95):.2f}ms")


@events.request.add_listener
def on_request(request_type, name, response_time, response_length, exception, **kwargs):
    """Hook for each request"""
    pass


@events.quitting.add_listener
def on_quitting(environment, **kwargs):
    """Called when the test is about to stop"""
    if environment.stats.total.fail_ratio > 0.05:
        print(f"\n⚠️  High failure rate: {environment.stats.total.fail_ratio:.2%}")
    else:
        print(f"\n✓ Failure rate within acceptable limits: {environment.stats.total.fail_ratio:.2%}")
