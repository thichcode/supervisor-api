"""
Enhanced Knowledge Base Search with Context-Aware and Domain-Specific loading.
Improves KB search precision by loading system context and domain skills before searching.

Workflow:
1. Load system context (alerts, incidents, user profile) 
2. Detect domain (database, gitlab, ad, monitoring...)
3. Load domain-specific skill/knowledge
4. Search KB with enriched context
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Optional

import structlog

logger = structlog.get_logger()


# =============================================================================
# Domain Detection - Detect which system domain the user query relates to
# =============================================================================

DOMAIN_KEYWORDS: dict[str, list[str]] = {
    "database": [
        "database", "db", "mysql", "postgresql", "postgres", "mongodb", "redis", 
        "sql", "connection", "replication", "slow query", "database size", "db status"
    ],
    "gitlab": [
        "gitlab", "merge request", "mr", "issue", "pipeline", "commit", "branch", 
        "git", "merge", "code review", "gitlab ci"
    ],
    "active_directory": [
        "ad", "active directory", "user", "account", "password", "login", 
        "group", "member", "locked", "unlock", "domain"
    ],
    "monitoring": [
        "monitor", "alert", "zabbix", "uptimerobot", "grafana", "prometheus", 
        "alert", "problem", "trigger", "notification"
    ],
    "backup": [
        "backup", "veeam", "restore", "recovery", "snapshot", "replication"
    ],
    "network": [
        "network", "vpn", "firewall", "dns", "domain", "ssl", "certificate",
        "cloudflare", "nginx", "apache", "proxy"
    ],
    "kubernetes": [
        "k8s", "kubernetes", "pod", "service", "deployment", "namespace",
        "container", "docker", "helm"
    ],
    "itc": [
        "ticket", "incident", "request", "itsm", "service desk", "cmdb",
        "serviceNow", "jira service"
    ],
    "jira": [
        "jira", "issue", "project", "sprint", "bug"
    ],
    "analytics": [
        "analytics", "matomo", "visitors", "pageview", "traffic", "analytics"
    ],
    "email": [
        "email", "mail", "outlook", "smtp", "imap", "gmail"
    ],
}


def detect_domain(query: str) -> Optional[str]:
    """
    Detect which domain the query relates to based on keywords.
    
    Args:
        query: User's message text
        
    Returns:
        Domain name or None if no match
    """
    query_lower = (query or "").lower()()
    
    for domain, keywords in DOMAIN_KEYWORDS.items():
        for keyword in keywords:
            if keyword in query_lower:
                logger.debug("Domain detected", domain=domain, keyword=keyword)
                return domain
    
    return None


# =============================================================================
# System Context - Current state of various systems
# =============================================================================

@dataclass
class SystemContext:
    """Current context from various systems."""
    active_alerts: int = 0
    recent_incidents: int = 0
    open_tickets: int = 0
    failed_backups: int = 0
    critical_systems: list[str] = field(default_factory=list)
    user_info: dict = field(default_factory=dict)
    timestamp: datetime = field(default_factory=datetime.utcnow)
    
    def to_dict(self) -> dict:
        return {
            "active_alerts": self.active_alerts,
            "recent_incidents": self.recent_incidents,
            "open_tickets": self.open_tickets,
            "failed_backups": self.failed_backups,
            "critical_systems": self.critical_systems,
            "user_info": self.user_info,
            "timestamp": self.timestamp.isoformat(),
        }
    
    def to_search_context(self) -> str:
        """Convert to search context string."""
        parts = []
        if self.active_alerts > 0:
            parts.append(f"Active alerts: {self.active_alerts}")
        if self.recent_incidents > 0:
            parts.append(f"Recent incidents: {self.recent_incidents}")
        if self.open_tickets > 0:
            parts.append(f"Open tickets: {self.open_tickets}")
        if self.failed_backups > 0:
            parts.append(f"Failed backups: {self.failed_backups}")
        if self.critical_systems:
            parts.append(f"Critical systems: {', '.join(self.critical_systems)}")
        return "; ".join(parts)


# =============================================================================
# Domain Skills - Predefined knowledge for each domain
# =============================================================================

DOMAIN_SKILLS: dict[str, dict] = {
    "database": {
        "description": "Database troubleshooting and management",
        "common_issues": [
            "Slow queries - Check with EXPLAIN, add index",
            "Connection pool exhausted - Increase pool size or check leaks",
            "Replication lag - Check replication status",
            "Lock contention - Check long running transactions"
        ],
        "commands": {
            "mysql": ["SHOW PROCESSLIST", "SHOW STATUS", "EXPLAIN"],
            "postgresql": ["SELECT * FROM pg_stat_activity", "EXPLAIN ANALYZE"],
        }
    },
    "gitlab": {
        "description": "GitLab CI/CD and repository management",
        "common_issues": [
            "Pipeline failed - Check .gitlab-ci.yml and logs",
            "MR blocked - Need approval",
            "Merge conflict - Rebase branch"
        ],
    },
    "active_directory": {
        "description": "Active Directory user management",
        "common_issues": [
            "Account locked - Unlock with ADUC or PowerShell",
            "Password expired - Reset via self-service",
            "Group membership - Add via ADUC"
        ],
    },
    "monitoring": {
        "description": "System monitoring and alerts",
        "common_issues": [
            "Alert storm - Check templates",
            "Host down - Check network and agent",
            "False positives - Adjust trigger"
        ],
    },
    "backup": {
        "description": "Backup and recovery",
        "common_issues": [
            "Backup failed - Check logs and storage",
            "Restore test - Verify backup integrity",
            "Retention policy - Check configuration"
        ],
    },
    "network": {
        "description": "Network and security",
        "common_issues": [
            "SSL expired - Renew and deploy",
            "DNS issue - Check records and propagation",
            "Firewall block - Check rules"
        ],
    },
}


def get_domain_skill(domain: str) -> Optional[dict]:
    """Get predefined skill/knowledge for a domain."""
    return DOMAIN_SKILLS.get(domain)


def get_domain_context(domain: str) -> str:
    """
    Get domain-specific context for KB search.
    
    Args:
        domain: Detected domain name
        
    Returns:
        Context string to prepend to KB search
    """
    skill = get_domain_skill(domain)
    if not skill:
        return ""
    
    parts = [f"Domain: {skill.get('description', domain)}"]
    
    if "common_issues" in skill:
        parts.append("Common issues:")
        for issue in skill["common_issues"]:
            parts.append(f"  - {issue}")
    
    return "\n".join(parts)


# =============================================================================
# Context Loader - Load current system state
# =============================================================================

async def load_system_context(limit_alerts: int = 5) -> SystemContext:
    """
    Load current system context from DB and monitoring systems.
    
    Args:
        limit_alerts: Max recent alerts to consider
        
    Returns:
        SystemContext with current system state
    """
    context = SystemContext()
    
    try:
        from src.db import async_session
        from src.db.models import Alert, InteractionLog
        from sqlalchemy import select, func
        
        async with async_session() as session:
            # Count active alerts
            alert_result = await session.execute(
                select(func.count(Alert.id))
                .where(Alert.status == "active")
            )
            context.active_alerts = alert_result.scalar() or 0
            
            # Count recent incidents (last 24h)
            cutoff = datetime.utcnow() - timedelta(hours=24)
            incident_result = await session.execute(
                select(func.count(InteractionLog.id))
                .where(
                    InteractionLog.created_at >= cutoff,
                    InteractionLog.traffic_class == "service_like"
                )
            )
            context.recent_incidents = incident_result.scalar() or 0
            
            # Get critical systems from recent failures
            fail_result = await session.execute(
                select(InteractionLog.output_text)
                .where(InteractionLog.created_at >= cutoff)
                .where(InteractionLog.outcome_status == "needs_review")
                .limit(3)
            )
            if fail_result.scalars().all():
                context.critical_systems = ["review_needed"]
                
    except Exception as e:
        logger.warning("Failed to load system context", error=str(e))
    
    return context


# =============================================================================
# Enhanced KB Search
# =============================================================================

@dataclass
class EnhancedSearchResult:
    """Enhanced search result with context."""
    title: str
    text: str
    score: float
    domain: Optional[str] = None
    context_used: Optional[str] = None


async def enhanced_kb_search(
    query: str,
    domain: Optional[str] = None,
    use_context: bool = True,
    use_domain: bool = True,
    user_id: Optional[str] = None,
    llm: Optional[Any] = None,
) -> dict:
    """
    Enhanced KB search with context and domain awareness.
    
    Args:
        query: User's search query
        domain: Optional pre-detected domain
        use_context: Whether to load system context
        use_domain: Whether to use domain skills
        user_id: Optional user ID for personalization
        
    Returns:
        Dictionary with search results and context metadata
    """
    results = {
        "query": query,
        "detected_domain": None,
        "system_context": None,
        "domain_context": None,
        "search_results": [],
        "enrichment": {
            "context_loaded": False,
            "domain_skill_used": False,
        }
    }
    
    # Step 1: Detect domain if not provided
    detected_domain = domain
    if not detected_domain:
        detected_domain = detect_domain(query)
    results["detected_domain"] = detected_domain
    
    # Step 2: Load system context
    if use_context:
        try:
            system_context = await load_system_context()
            results["system_context"] = system_context.to_dict()
            results["enrichment"]["context_loaded"] = True
        except Exception as e:
            logger.warning("Failed to load context", error=str(e))
    
    # Step 3: Get domain-specific context
    if use_domain and detected_domain:
        domain_ctx = get_domain_context(detected_domain)
        if domain_ctx:
            results["domain_context"] = domain_ctx
            results["enrichment"]["domain_skill_used"] = True
    
    # Step 4: Perform actual KB search with enriched context
    try:
        from src.knowledge.service import KnowledgeRetrievalService
        from src.db import async_session
        
        # Build enriched query with context
        enriched_query = query
        context_parts = []
        
        if results.get("domain_context"):
            context_parts.append(f"[Domain: {detected_domain}]\n{results['domain_context']}")
        
        if results.get("system_context"):
            sys_ctx = SystemContext(**results["system_context"])
            search_ctx = sys_ctx.to_search_context()
            if search_ctx:
                context_parts.append(f"[System State]\n{search_ctx}")
        
        # Only enrich query if we have context
        if context_parts:
            enriched_query = f"{query}\n\nContext:\n" + "\n\n".join(context_parts)
        
        # Call KnowledgeRetrievalService with search_type="all" for comprehensive search
        async with async_session() as session:
            kb_service = KnowledgeRetrievalService(session, llm=llm)
            # Use search_with_llm_enhancement if LLM is available, else basic search
            if llm:
                search_result = await kb_service.search_with_llm_enhancement(enriched_query, search_type="all")
            else:
                search_result = await kb_service.search(enriched_query, search_type="all")
        
        # Convert results to serializable format
        results["search_results"] = [
            {
                "id": r.id,
                "title": r.title,
                "content": (r.content or "")[:500],  # Truncate for response
                "similarity": r.similarity,
                "source": r.metadata.get("source", ""),
                "category": r.category,
                "kb_type": r.knowledge_type.value if hasattr(r.knowledge_type, "value") else str(r.knowledge_type),
            }
            for r in search_result.results
        ]
        results["total_results"] = search_result.total
        results["template_id"] = search_result.template_id
        results["template_label"] = search_result.template_label
        results["clarification"] = getattr(search_result, "clarification", {})
        
        logger.info(
            "Enhanced KB search completed",
            query=query,
            domain=detected_domain,
            results_count=len(results["search_results"]),
            context_used=bool(context_parts)
        )
        
    except Exception as e:
        logger.error("Enhanced KB search failed", error=str(e))
        results["error"] = str(e)
    
    return results


# =============================================================================
# Utilities
# =============================================================================

def format_context_for_prompt(context: SystemContext, domain_context: str) -> str:
    """
    Format context as prompt enhancement.
    
    Args:
        context: System context
        domain_context: Domain-specific context
        
    Returns:
        Formatted string for LLM prompt
    """
    parts = []
    
    if context.active_alerts > 0:
        parts.append(f"⚠️ {context.active_alerts} active alerts")
    if context.recent_incidents > 0:
        parts.append(f"📋 {context.recent_incidents} recent incidents")
    if context.failed_backups > 0:
        parts.append(f"❌ {context.failed_backups} failed backups")
        
    system_info = context.to_search_context()
    if system_info:
        parts.append(f"\nSystem state: {system_info}")
    
    if domain_context:
        parts.append(f"\nDomain knowledge:\n{domain_context}")
    
    return "\n".join(parts)