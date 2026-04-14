"""
Graph-based Agent Router using Dijkstra's algorithm
Routes user queries through optimal agent path for best response quality
"""

import heapq
from typing import Dict, List, Tuple, Set
from dataclasses import dataclass, field
from enum import Enum
import structlog

logger = structlog.get_logger()


class AgentType(Enum):
    """Available agent types in the system"""
    CONTEXT = "context"
    POLICY = "policy"
    KNOWLEDGE = "knowledge"
    DRAFT = "draft"
    QA = "qa"
    ESCALATION = "escalation"


@dataclass
class AgentNode:
    """Represents an agent in the routing graph"""
    agent_type: AgentType
    name: str
    description: str
    capabilities: List[str] = field(default_factory=list)
    avg_success_rate: float = 0.7  # Historical success rate
    avg_latency_ms: int = 100
    
    def __hash__(self):
        return hash(self.agent_type.value)


@dataclass
class Edge:
    """Represents a connection between agents"""
    from_agent: AgentType
    to_agent: AgentType
    weight: float  # Cost/distance (lower is better)
    conditions: List[str] = field(default_factory=list)
    
    def __lt__(self, other):
        return self.weight < other.weight


class AgentGraph:
    """
    Directed weighted graph for agent routing.
    Nodes = Agents, Edges = Possible transitions with costs.
    """
    
    def __init__(self):
        self.nodes: Dict[AgentType, AgentNode] = {}
        self.edges: Dict[AgentType, List[Edge]] = {}
        self._build_default_graph()
    
    def _build_default_graph(self):
        """Build default routing graph based on supervisor pipeline"""
        
        # Define agents
        self.nodes = {
            AgentType.CONTEXT: AgentNode(
                AgentType.CONTEXT, "ContextAgent",
                "Builds conversation context from memory",
                capabilities=["history", "user_info", "case_info"],
                avg_success_rate=0.85, avg_latency_ms=50
            ),
            AgentType.POLICY: AgentNode(
                AgentType.POLICY, "PolicyAgent",
                "Extracts relevant policies and guidelines",
                capabilities=["policy_lookup", "sop_steps", "guide_retrieval"],
                avg_success_rate=0.80, avg_latency_ms=80
            ),
            AgentType.KNOWLEDGE: AgentNode(
                AgentType.KNOWLEDGE, "KnowledgeAgent",
                "Retrieves knowledge base information",
                capabilities=["faq_lookup", "pattern_matching", "search"],
                avg_success_rate=0.75, avg_latency_ms=100
            ),
            AgentType.DRAFT: AgentNode(
                AgentType.DRAFT, "DraftAgent",
                "Generates response using LLM",
                capabilities=["text_generation", "summarization"],
                avg_success_rate=0.70, avg_latency_ms=500
            ),
            AgentType.QA: AgentNode(
                AgentType.QA, "QAAgent",
                "Validates and improves response quality",
                capabilities=["validation", "confidence_scoring", "review"],
                avg_success_rate=0.82, avg_latency_ms=80
            ),
            AgentType.ESCALATION: AgentNode(
                AgentType.ESCALATION, "EscalationAgent",
                "Handles cases requiring human intervention",
                capabilities=["routing", "priority_escalation"],
                avg_success_rate=0.90, avg_latency_ms=50
            ),
        }
        
        # Define edges with weights (cost = latency + failure penalty)
        self.edges = {
            AgentType.CONTEXT: [
                Edge(AgentType.CONTEXT, AgentType.POLICY, weight=80),
                Edge(AgentType.CONTEXT, AgentType.KNOWLEDGE, weight=100),
                Edge(AgentType.CONTEXT, AgentType.QA, weight=150),
            ],
            AgentType.POLICY: [
                Edge(AgentType.POLICY, AgentType.KNOWLEDGE, weight=80),
                Edge(AgentType.POLICY, AgentType.DRAFT, weight=500),
            ],
            AgentType.KNOWLEDGE: [
                Edge(AgentType.KNOWLEDGE, AgentType.DRAFT, weight=500),
                Edge(AgentType.KNOWLEDGE, AgentType.POLICY, weight=80),
            ],
            AgentType.DRAFT: [
                Edge(AgentType.DRAFT, AgentType.QA, weight=80),
                Edge(AgentType.DRAFT, AgentType.ESCALATION, weight=50),
            ],
            AgentType.QA: [
                Edge(AgentType.QA, AgentType.DRAFT, weight=500),  # Retry
                Edge(AgentType.QA, AgentType.ESCALATION, weight=50),
            ],
            AgentType.ESCALATION: [],  # Terminal node
        }
    
    def get_neighbors(self, agent: AgentType) -> List[Edge]:
        """Get all outgoing edges from an agent"""
        return self.edges.get(agent, [])
    
    def get_agent(self, agent_type: AgentType) -> AgentNode:
        """Get agent node by type"""
        return self.nodes.get(agent_type)
    
    def update_success_rate(self, agent_type: AgentType, success: bool) -> None:
        """Update historical success rate for an agent"""
        if agent_type in self.nodes:
            node = self.nodes[agent_type]
            # Exponential moving average
            if success:
                node.avg_success_rate = 0.9 * node.avg_success_rate + 0.1 * 1.0
            else:
                node.avg_success_rate = 0.9 * node.avg_success_rate + 0.1 * 0.0
    
    def recalculate_weights(self) -> None:
        """Recalculate edge weights based on agent performance"""
        for from_agent, edges in self.edges.items():
            for edge in edges:
                to_node = self.nodes.get(edge.to_agent)
                if to_node:
                    # Weight = latency + (1 - success_rate) * penalty
                    base_weight = to_node.avg_latency_ms
                    failure_penalty = (1 - to_node.avg_success_rate) * 200
                    edge.weight = base_weight + failure_penalty


class AgentRouter:
    """
    Routes queries through optimal agent path using Dijkstra's algorithm.
    Considers: latency, success rate, query complexity, and user context.
    """
    
    def __init__(self):
        self.graph = AgentGraph()
        self.query_history: List[Dict] = []
    
    def route(
        self,
        query: str,
        query_type: str = "general",
        user_context: Dict = None,
        complexity: str = "medium"
    ) -> List[AgentType]:
        """
        Find optimal agent path for the query.
        
        Args:
            query: User query
            query_type: Type of query (faq, support, policy, etc.)
            user_context: User information for personalization
            complexity: Query complexity (low, medium, high)
            
        Returns:
            List of agent types in optimal execution order
        """
        # Adjust graph based on query type
        self._adjust_for_query_type(query_type)
        
        # Determine start and end agents
        start = self._determine_start_agent(query_type)
        end = AgentType.QA  # Always end with QA for validation
        
        # Run Dijkstra's algorithm
        path = self._dijkstra(start, end, complexity)
        
        # Record routing decision
        self.query_history.append({
            "query": query[:100],
            "query_type": query_type,
            "complexity": complexity,
            "path": [a.value for a in path]
        })
        
        return path
    
    def _adjust_for_query_type(self, query_type: str):
        """Adjust routing based on query type"""
        self.graph.recalculate_weights()
        
        # Query-type specific adjustments
        if query_type == "policy":
            # Boost policy agent priority
            for edge in self.graph.edges.get(AgentType.CONTEXT, []):
                if edge.to_agent == AgentType.POLICY:
                    edge.weight *= 0.5
        elif query_type == "faq":
            # Boost knowledge agent
            for edge in self.graph.edges.get(AgentType.CONTEXT, []):
                if edge.to_agent == AgentType.KNOWLEDGE:
                    edge.weight *= 0.5
        elif query_type == "support":
            # Standard path with case context
            pass
    
    def _determine_start_agent(self, query_type: str) -> AgentType:
        """Determine starting agent based on query type"""
        start_agents = {
            "policy": AgentType.POLICY,
            "faq": AgentType.KNOWLEDGE,
            "guide": AgentType.POLICY,
            "support": AgentType.CONTEXT,
            "system_query": AgentType.CONTEXT,
            "general": AgentType.CONTEXT,
        }
        return start_agents.get(query_type, AgentType.CONTEXT)
    
    def _dijkstra(
        self,
        start: AgentType,
        end: AgentType,
        complexity: str
    ) -> List[AgentType]:
        """
        Dijkstra's algorithm to find shortest path.
        Considers edge weights (latency + failure cost).
        """
        # Priority queue: (cost, agent_type, path)
        pq = [(0, start, [start])]
        visited: Set[AgentType] = set()
        
        # Complexity adjustment
        complexity_multiplier = {
            "low": 0.8,
            "medium": 1.0,
            "high": 1.3
        }.get(complexity, 1.0)
        
        while pq:
            cost, current, path = heapq.heappop(pq)
            
            if current in visited:
                continue
            
            visited.add(current)
            
            if current == end:
                return path
            
            for edge in self.graph.get_neighbors(current):
                if edge.to_agent not in visited:
                    adjusted_weight = edge.weight * complexity_multiplier
                    new_cost = cost + adjusted_weight
                    new_path = path + [edge.to_agent]
                    heapq.heappush(pq, (new_cost, edge.to_agent, new_path))
        
        # Fallback to default path
        return [start, AgentType.DRAFT, AgentType.QA]
    
    def get_path_cost(self, path: List[AgentType]) -> Dict:
        """Calculate total cost and metrics for a path"""
        total_latency = 0
        total_success = 1.0
        
        for i, agent_type in enumerate(path):
            node = self.graph.get_agent(agent_type)
            if node:
                total_latency += node.avg_latency_ms
                total_success *= node.avg_success_rate
        
        return {
            "total_latency_ms": total_latency,
            "expected_success_rate": total_success,
            "path_length": len(path),
            "agents": [self.graph.get_agent(a).name for a in path]
        }
    
    def get_routing_stats(self) -> Dict:
        """Get routing statistics"""
        return {
            "total_routes": len(self.query_history),
            "query_types": self._count_query_types(),
            "avg_path_length": sum(len(h["path"]) for h in self.query_history) / max(1, len(self.query_history))
        }
    
    def _count_query_types(self) -> Dict[str, int]:
        """Count query types from history"""
        counts = {}
        for h in self.query_history:
            qt = h["query_type"]
            counts[qt] = counts.get(qt, 0) + 1
        return counts


class AdaptiveRouter:
    """
    Adaptive routing that learns from user feedback.
    Updates agent weights based on response quality.
    """
    
    def __init__(self):
        self.router = AgentRouter()
        self.feedback_history: List[Dict] = []
    
    def route_with_feedback(
        self,
        query: str,
        query_type: str = "general",
        user_context: Dict = None
    ) -> Tuple[List[AgentType], Dict]:
        """Route query and return path with metadata"""
        path = self.router.route(
            query=query,
            query_type=query_type,
            user_context=user_context,
            complexity=self._estimate_complexity(query)
        )
        
        cost_info = self.router.get_path_cost(path)
        
        return path, {
            "path": [a.value for a in path],
            "cost": cost_info,
            "query_complexity": self._estimate_complexity(query)
        }
    
    def record_feedback(
        self,
        query: str,
        path: List[str],
        user_satisfied: bool,
        issues: List[str] = None
    ) -> None:
        """Record user feedback to improve routing"""
        # Update agent success rates
        for agent_str in path:
            try:
                agent_type = AgentType(agent_str)
                self.router.graph.update_success_rate(agent_type, user_satisfied)
            except ValueError:
                pass
        
        self.feedback_history.append({
            "query": query,
            "path": path,
            "satisfied": user_satisfied,
            "issues": issues or []
        })
    
    def _estimate_complexity(self, query: str) -> str:
        """Estimate query complexity based on length and content"""
        length = len(query.split())
        
        # Complex indicators
        has_comparison = any(w in query.lower() for w in ["so sánh", "khác", "hơn", "tốt hơn"])
        has_multi = any(query.lower().__contains__(w) for w in ["và", "hoặc", "cả"])
        
        if length > 30 or (has_comparison and has_multi):
            return "high"
        elif length > 15:
            return "medium"
        else:
            return "low"
    
    def get_recommendations(self) -> Dict:
        """Get routing improvement recommendations"""
        if not self.feedback_history:
            return {"message": "Not enough feedback data"}
        
        satisfied = sum(1 for f in self.feedback_history if f["satisfied"])
        total = len(self.feedback_history)
        
        # Find problematic agents
        agent_issues: Dict[str, int] = {}
        for feedback in self.feedback_history:
            if not feedback["satisfied"]:
                for issue in feedback.get("issues", []):
                    agent_issues[issue] = agent_issues.get(issue, 0) + 1
        
        return {
            "overall_satisfaction": satisfied / total if total > 0 else 0,
            "total_feedback": total,
            "problematic_agents": agent_issues,
            "graph_stats": self.router.get_routing_stats()
        }


# Factory function
def create_router(router_type: str = "adaptive") -> AdaptiveRouter:
    """Create a router instance"""
    if router_type == "basic":
        router = AgentRouter()
        return router
    else:
        return AdaptiveRouter()


# Example usage
if __name__ == "__main__":
    print("=== Agent Router Demo ===\n")
    
    # Basic routing
    router = AgentRouter()
    
    test_queries = [
        ("Chính sách nghỉ phép năm 2024?", "policy"),
        ("Làm thế nào để reset password?", "faq"),
        ("Tôi cần hỗ trợ về laptop không hoạt động", "support"),
        ("Ai đang xử lý case của tôi?", "system_query"),
    ]
    
    for query, qtype in test_queries:
        print(f"Query: {query[:50]}...")
        print(f"Type: {qtype}")
        
        path = router.route(query, query_type=qtype)
        cost = router.get_path_cost(path)
        
        print(f"Path: {' -> '.join([a.value for a in path])}")
        print(f"Cost: {cost['total_latency_ms']}ms, Success: {cost['expected_success_rate']:.1%}")
        print()
    
    # Adaptive routing with feedback
    print("=== Adaptive Router Demo ===\n")
    adaptive = AdaptiveRouter()
    
    # Simulate routing
    path, info = adaptive.route_with_feedback(
        "Quy định về giờ làm việc là gì?",
        query_type="policy"
    )
    print(f"Path: {info['path']}")
    print(f"Cost: {info['cost']}")
    
    # Simulate feedback
    adaptive.record_feedback(
        "Quy định về giờ làm việc là gì?",
        path=info["path"],
        user_satisfied=True
    )
    
    # Get recommendations
    recs = adaptive.get_recommendations()
    print(f"\nRecommendations: {recs}")