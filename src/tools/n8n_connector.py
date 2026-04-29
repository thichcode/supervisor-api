"""
n8n Connector - Connect to internal systems via n8n webhooks
Supports both query (read) and action (write/execute) operations
with approval workflow for actions
"""

import httpx
from typing import Optional, List, Dict, Any
from enum import Enum
from dataclasses import dataclass, field
from datetime import datetime, timezone
import structlog

logger = structlog.get_logger()


class ActionType(str, Enum):
    """Type of operation"""
    QUERY = "query"           # Read-only, no approval needed
    ACTION = "action"         # Write/execute, needs approval
    APPROVED = "approved"      # Action approved by human


class RiskLevel(str, Enum):
    """Risk level for actions"""
    LOW = "low"           # Safe operations
    MEDIUM = "medium"      # Moderate risk
    HIGH = "high"         # Risky operations
    CRITICAL = "critical" # Very risky, needs senior approval


@dataclass
class ActionRequest:
    """Action request that needs approval"""
    request_id: str
    action_type: str
    system: str
    action: str
    parameters: Dict[str, Any]
    risk_level: RiskLevel
    requested_by: str
    requested_at: datetime = field(default_factory=datetime.utcnow)
    status: str = "pending"  # pending, approved, rejected, executed
    approved_by: Optional[str] = None
    approved_at: Optional[datetime] = None
    result: Optional[Dict] = None


@dataclass
class SystemAction:
    """Definition of a system action"""
    name: str
    display_name: str
    system: str
    action_type: ActionType
    risk_level: RiskLevel
    webhook_path: str
    parameters: List[Dict[str, str]] = field(default_factory=list)
    description: str = ""
    approval_required: bool = True


# Predefined system actions
SYSTEM_ACTIONS: Dict[str, SystemAction] = {
    # Backup Service Actions
    "backup_status": SystemAction(
        name="backup_status",
        display_name="Kiểm tra trạng thái Backup",
        system="backup",
        action_type=ActionType.QUERY,
        risk_level=RiskLevel.LOW,
        webhook_path="/webhook/backup/status",
        description="Xem trạng thái backup của hệ thống"
    ),
    "backup_restore": SystemAction(
        name="backup_restore",
        display_name="Khôi phục từ Backup",
        system="backup",
        action_type=ActionType.ACTION,
        risk_level=RiskLevel.HIGH,
        webhook_path="/webhook/backup/restore",
        parameters=[
            {"name": "backup_id", "type": "string", "required": True},
            {"name": "target_server", "type": "string", "required": True},
        ],
        description="Khôi phục dữ liệu từ backup"
    ),
    
    # Monitoring Service Actions
    "monitor_status": SystemAction(
        name="monitor_status",
        display_name="Xem trạng thái Monitor",
        system="monitoring",
        action_type=ActionType.QUERY,
        risk_level=RiskLevel.LOW,
        webhook_path="/webhook/monitoring/status",
        description="Xem trạng thái giám sát hệ thống"
    ),
    "monitor_alert_ack": SystemAction(
        name="monitor_alert_ack",
        display_name="Acknowledge Alert",
        system="monitoring",
        action_type=ActionType.ACTION,
        risk_level=RiskLevel.MEDIUM,
        webhook_path="/webhook/monitoring/ack-alert",
        parameters=[
            {"name": "alert_id", "type": "string", "required": True},
            {"name": "note", "type": "string", "required": False},
        ],
        description="Acknowledge một alert"
    ),
    
    # IT Service / Ticket Actions
    "ticket_create": SystemAction(
        name="ticket_create",
        display_name="Tạo Ticket IT",
        system="itsm",
        action_type=ActionType.ACTION,
        risk_level=RiskLevel.LOW,
        webhook_path="/webhook/itsm/create-ticket",
        parameters=[
            {"name": "title", "type": "string", "required": True},
            {"name": "description", "type": "string", "required": True},
            {"name": "category", "type": "string", "required": True},
            {"name": "priority", "type": "string", "required": False},
        ],
        description="Tạo ticket IT mới"
    ),
    "ticket_update": SystemAction(
        name="ticket_update",
        display_name="Cập nhật Ticket",
        system="itsm",
        action_type=ActionType.ACTION,
        risk_level=RiskLevel.MEDIUM,
        webhook_path="/webhook/itsm/update-ticket",
        parameters=[
            {"name": "ticket_id", "type": "string", "required": True},
            {"name": "status", "type": "string", "required": False},
            {"name": "note", "type": "string", "required": False},
        ],
        description="Cập nhật trạng thái ticket"
    ),
    
    # Server Management Actions
    "server_restart": SystemAction(
        name="server_restart",
        display_name="Restart Server",
        system="infrastructure",
        action_type=ActionType.ACTION,
        risk_level=RiskLevel.CRITICAL,
        webhook_path="/webhook/infra/restart-server",
        parameters=[
            {"name": "server_name", "type": "string", "required": True},
            {"name": "reason", "type": "string", "required": True},
        ],
        description="Restart một server (NGUY HIỂM)"
    ),
    "server_status": SystemAction(
        name="server_status",
        display_name="Kiểm tra Server",
        system="infrastructure",
        action_type=ActionType.QUERY,
        risk_level=RiskLevel.LOW,
        webhook_path="/webhook/infra/server-status",
        description="Xem trạng thái server"
    ),
    
    # Account Management
    "account_unlock": SystemAction(
        name="account_unlock",
        display_name="Mở khóa tài khoản",
        system="iam",
        action_type=ActionType.ACTION,
        risk_level=RiskLevel.MEDIUM,
        webhook_path="/webhook/iam/unlock-account",
        parameters=[
            {"name": "username", "type": "string", "required": True},
        ],
        description="Mở khóa tài khoản bị lock"
    ),
    "account_reset_password": SystemAction(
        name="account_reset_password",
        display_name="Reset Password",
        system="iam",
        action_type=ActionType.ACTION,
        risk_level=RiskLevel.HIGH,
        webhook_path="/webhook/iam/reset-password",
        parameters=[
            {"name": "username", "type": "string", "required": True},
        ],
        description="Reset password của user"
    ),
    
    # =============================================================================
    # GitLab Integration
    # =============================================================================
    "gitlab_merge_requests": SystemAction(
        name="gitlab_merge_requests",
        display_name="GitLab Merge Requests",
        system="gitlab",
        action_type=ActionType.QUERY,
        risk_level=RiskLevel.LOW,
        webhook_path="/webhook/gitlab/merge-requests",
        parameters=[
            {"name": "project_id", "type": "string", "required": False},
            {"name": "state", "type": "string", "required": False},
            {"name": "limit", "type": "integer", "required": False},
        ],
        description="Lấy danh sách Merge Requests"
    ),
    "gitlab_issues": SystemAction(
        name="gitlab_issues",
        display_name="GitLab Issues",
        system="gitlab",
        action_type=ActionType.QUERY,
        risk_level=RiskLevel.LOW,
        webhook_path="/webhook/gitlab/issues",
        parameters=[
            {"name": "project_id", "type": "string", "required": False},
            {"name": "state", "type": "string", "required": False},
            {"name": "labels", "type": "string", "required": False},
        ],
        description="Lấy danh sách Issues"
    ),
    "gitlab_pipelines": SystemAction(
        name="gitlab_pipelines",
        display_name="GitLab Pipelines",
        system="gitlab",
        action_type=ActionType.QUERY,
        risk_level=RiskLevel.LOW,
        webhook_path="/webhook/gitlab/pipelines",
        parameters=[
            {"name": "project_id", "type": "string", "required": True},
            {"name": "status", "type": "string", "required": False},
        ],
        description="Lấy danh sách Pipelines"
    ),
    "gitlab_members": SystemAction(
        name="gitlab_members",
        display_name="GitLab Project Members",
        system="gitlab",
        action_type=ActionType.QUERY,
        risk_level=RiskLevel.LOW,
        webhook_path="/webhook/gitlab/members",
        parameters=[
            {"name": "project_id", "type": "string", "required": True},
        ],
        description="Lấy danh sách thành viên project"
    ),
    
    # =============================================================================
    # Active Directory (AD)
    # =============================================================================
    "ad_user_info": SystemAction(
        name="ad_user_info",
        display_name="AD User Info",
        system="ad",
        action_type=ActionType.QUERY,
        risk_level=RiskLevel.LOW,
        webhook_path="/webhook/ad/user-info",
        parameters=[
            {"name": "username", "type": "string", "required": True},
        ],
        description="Lấy thông tin user từ AD"
    ),
    "ad_group_members": SystemAction(
        name="ad_group_members",
        display_name="AD Group Members",
        system="ad",
        action_type=ActionType.QUERY,
        risk_level=RiskLevel.LOW,
        webhook_path="/webhook/ad/group-members",
        parameters=[
            {"name": "group_name", "type": "string", "required": True},
        ],
        description="Lấy danh sách thành viên group"
    ),
    "ad_locked_users": SystemAction(
        name="ad_locked_users",
        display_name="AD Locked Users",
        system="ad",
        action_type=ActionType.QUERY,
        risk_level=RiskLevel.LOW,
        webhook_path="/webhook/ad/locked-users",
        description="Lấy danh sách user bị lock"
    ),
    
    # =============================================================================
    # Zabbix Monitoring
    # =============================================================================
    "zabbix_alerts": SystemAction(
        name="zabbix_alerts",
        display_name="Zabbix Alerts",
        system="monitoring",
        action_type=ActionType.QUERY,
        risk_level=RiskLevel.LOW,
        webhook_path="/webhook/zabbix/alerts",
        parameters=[
            {"name": "host", "type": "string", "required": False},
            {"name": "severity", "type": "integer", "required": False},
            {"name": "limit", "type": "integer", "required": False},
        ],
        description="Lấy alerts đang active"
    ),
    "zabbix_hosts": SystemAction(
        name="zabbix_hosts",
        display_name="Zabbix Hosts",
        system="monitoring",
        action_type=ActionType.QUERY,
        risk_level=RiskLevel.LOW,
        webhook_path="/webhook/zabbix/hosts",
        parameters=[
            {"name": "group", "type": "string", "required": False},
            {"name": "status", "type": "integer", "required": False},
        ],
        description="Lấy danh sách hosts"
    ),
    "zabbix_triggers": SystemAction(
        name="zabbix_triggers",
        display_name="Zabbix Triggers",
        system="monitoring",
        action_type=ActionType.QUERY,
        risk_level=RiskLevel.LOW,
        webhook_path="/webhook/zabbix/triggers",
        parameters=[
            {"name": "host", "type": "string", "required": False},
            {"name": "status", "type": "string", "required": False},
        ],
        description="Lấy danh sách triggers"
    ),
    
    # =============================================================================
    # ITC / ServiceNow
    # =============================================================================
    "itc_incidents": SystemAction(
        name="itc_incidents",
        display_name="ITC Incidents",
        system="itc",
        action_type=ActionType.QUERY,
        risk_level=RiskLevel.LOW,
        webhook_path="/webhook/itc/incidents",
        parameters=[
            {"name": "state", "type": "string", "required": False},
            {"name": "assigned_to", "type": "string", "required": False},
            {"name": "limit", "type": "integer", "required": False},
        ],
        description="Lấy danh sách incidents"
    ),
    "itc_tickets": SystemAction(
        name="itc_tickets",
        display_name="ITC Tickets",
        system="itc",
        action_type=ActionType.QUERY,
        risk_level=RiskLevel.LOW,
        webhook_path="/webhook/itc/tickets",
        parameters=[
            {"name": "category", "type": "string", "required": False},
            {"name": "priority", "type": "string", "required": False},
        ],
        description="Lấy danh sách tickets"
    ),
    "itc_cmdb": SystemAction(
        name="itc_cmdb",
        display_name="ITC CMDB",
        system="itc",
        action_type=ActionType.QUERY,
        risk_level=RiskLevel.LOW,
        webhook_path="/webhook/itc/cmdb",
        parameters=[
            {"name": "ci_type", "type": "string", "required": False},
            {"name": "name", "type": "string", "required": False},
        ],
        description="Lấy thông tin từ CMDB"
    ),
    
    # =============================================================================
    # Jira
    # =============================================================================
    "jira_issues": SystemAction(
        name="jira_issues",
        display_name="Jira Issues",
        system="jira",
        action_type=ActionType.QUERY,
        risk_level=RiskLevel.LOW,
        webhook_path="/webhook/jira/issues",
        parameters=[
            {"name": "project", "type": "string", "required": False},
            {"name": "status", "type": "string", "required": False},
            {"name": "assignee", "type": "string", "required": False},
        ],
        description="Lấy danh sách issues"
    ),
    "jira_projects": SystemAction(
        name="jira_projects",
        display_name="Jira Projects",
        system="jira",
        action_type=ActionType.QUERY,
        risk_level=RiskLevel.LOW,
        webhook_path="/webhook/jira/projects",
        description="Lấy danh sách projects"
    ),
    
    # =============================================================================
    # Matomo Analytics
    # =============================================================================
    "matomo_visitors": SystemAction(
        name="matomo_visitors",
        display_name="Matomo Visitors",
        system="analytics",
        action_type=ActionType.QUERY,
        risk_level=RiskLevel.LOW,
        webhook_path="/webhook/matomo/visitors",
        parameters=[
            {"name": "period", "type": "string", "required": False},
            {"name": "date", "type": "string", "required": False},
            {"name": "limit", "type": "integer", "required": False},
        ],
        description="Lấy thông tin visitors"
    ),
    "matomo_pageviews": SystemAction(
        name="matomo_pageviews",
        display_name="Matomo Page Views",
        system="analytics",
        action_type=ActionType.QUERY,
        risk_level=RiskLevel.LOW,
        webhook_path="/webhook/matomo/pageviews",
        parameters=[
            {"name": "period", "type": "string", "required": False},
            {"name": "date", "type": "string", "required": False},
            {"name": "limit", "type": "integer", "required": False},
        ],
        description="Lấy thống kê page views"
    ),
    "matomo_analytics": SystemAction(
        name="matomo_analytics",
        display_name="Matomo Analytics Summary",
        system="analytics",
        action_type=ActionType.QUERY,
        risk_level=RiskLevel.LOW,
        webhook_path="/webhook/matomo/summary",
        parameters=[
            {"name": "period", "type": "string", "required": False},
            {"name": "date", "type": "string", "required": False},
        ],
        description="Lấy tổng quan analytics"
    ),
    
    # =============================================================================
    # UptimeRobot
    # =============================================================================
    "uptimerobot_monitors": SystemAction(
        name="uptimerobot_monitors",
        display_name="UptimeRobot Monitors",
        system="monitoring",
        action_type=ActionType.QUERY,
        risk_level=RiskLevel.LOW,
        webhook_path="/webhook/uptimerobot/monitors",
        parameters=[
            {"name": "status", "type": "string", "required": False},
        ],
        description="Lấy danh sách monitors"
    ),
    "uptimerobot_incidents": SystemAction(
        name="uptimerobot_incidents",
        display_name="UptimeRobot Incidents",
        system="monitoring",
        action_type=ActionType.QUERY,
        risk_level=RiskLevel.LOW,
        webhook_path="/webhook/uptimerobot/incidents",
        parameters=[
            {"name": "limit", "type": "integer", "required": False},
        ],
        description="Lấy incidents"
    ),
    
    # =============================================================================
    # Nginx Log
    # =============================================================================
    "nginx_access_log": SystemAction(
        name="nginx_access_log",
        display_name="Nginx Access Log",
        system="infrastructure",
        action_type=ActionType.QUERY,
        risk_level=RiskLevel.LOW,
        webhook_path="/webhook/nginx/access-log",
        parameters=[
            {"name": "ip", "type": "string", "required": False},
            {"name": "path", "type": "string", "required": False},
            {"name": "limit", "type": "integer", "required": False},
        ],
        description="Lấy access log"
    ),
    "nginx_error_log": SystemAction(
        name="nginx_error_log",
        display_name="Nginx Error Log",
        system="infrastructure",
        action_type=ActionType.QUERY,
        risk_level=RiskLevel.LOW,
        webhook_path="/webhook/nginx/error-log",
        parameters=[
            {"name": "level", "type": "string", "required": False},
            {"name": "limit", "type": "integer", "required": False},
        ],
        description="Lấy error log"
    ),
    "nginx_stats": SystemAction(
        name="nginx_stats",
        display_name="Nginx Statistics",
        system="infrastructure",
        action_type=ActionType.QUERY,
        risk_level=RiskLevel.LOW,
        webhook_path="/webhook/nginx/stats",
        description="Lấy thống kê Nginx"
    ),
    
    # =============================================================================
    # Domain Info
    # =============================================================================
    "domain_whois": SystemAction(
        name="domain_whois",
        display_name="Domain WHOIS",
        system="infrastructure",
        action_type=ActionType.QUERY,
        risk_level=RiskLevel.LOW,
        webhook_path="/webhook/domain/whois",
        parameters=[
            {"name": "domain", "type": "string", "required": True},
        ],
        description="Lấy thông tin WHOIS domain"
    ),
    "domain_dns": SystemAction(
        name="domain_dns",
        display_name="Domain DNS Records",
        system="infrastructure",
        action_type=ActionType.QUERY,
        risk_level=RiskLevel.LOW,
        webhook_path="/webhook/domain/dns",
        parameters=[
            {"name": "domain", "type": "string", "required": True},
            {"name": "record_type", "type": "string", "required": False},
        ],
        description="Lấy DNS records"
    ),
    "domain_expiry": SystemAction(
        name="domain_expiry",
        display_name="Domain Expiry Check",
        system="infrastructure",
        action_type=ActionType.QUERY,
        risk_level=RiskLevel.LOW,
        webhook_path="/webhook/domain/expiry",
        parameters=[
            {"name": "domain", "type": "string", "required": True},
        ],
        description="Kiểm tra ngày hết hạn domain"
    ),
    
    # =============================================================================
    # Cloudflare
    # =============================================================================
    "cloudflare_zones": SystemAction(
        name="cloudflare_zones",
        display_name="Cloudflare Zones",
        system="cloud",
        action_type=ActionType.QUERY,
        risk_level=RiskLevel.LOW,
        webhook_path="/webhook/cloudflare/zones",
        description="Lấy danh sách zones"
    ),
    "cloudflare_dns": SystemAction(
        name="cloudflare_dns",
        display_name="Cloudflare DNS Records",
        system="cloud",
        action_type=ActionType.QUERY,
        risk_level=RiskLevel.LOW,
        webhook_path="/webhook/cloudflare/dns",
        parameters=[
            {"name": "zone_id", "type": "string", "required": True},
        ],
        description="Lấy DNS records từ Cloudflare"
    ),
    "cloudflare_analytics": SystemAction(
        name="cloudflare_analytics",
        display_name="Cloudflare Analytics",
        system="cloud",
        action_type=ActionType.QUERY,
        risk_level=RiskLevel.LOW,
        webhook_path="/webhook/cloudflare/analytics",
        parameters=[
            {"name": "zone_id", "type": "string", "required": False},
            {"name": "period", "type": "string", "required": False},
        ],
        description="Lấy thống kê traffic"
    ),
    "cloudflare_stats": SystemAction(
        name="cloudflare_stats",
        display_name="Cloudflare Stats",
        system="cloud",
        action_type=ActionType.QUERY,
        risk_level=RiskLevel.LOW,
        webhook_path="/webhook/cloudflare/stats",
        parameters=[
            {"name": "zone_id", "type": "string", "required": True},
        ],
        description="Lấy stats Cloudflare"
    ),
    "cloudflare_firewall_rules": SystemAction(
        name="cloudflare_firewall_rules",
        display_name="Cloudflare Firewall Rules",
        system="cloud",
        action_type=ActionType.QUERY,
        risk_level=RiskLevel.LOW,
        webhook_path="/webhook/cloudflare/firewall",
        parameters=[
            {"name": "zone_id", "type": "string", "required": False},
        ],
        description="Lấy firewall rules"
    ),
    
    # =============================================================================
    # Veeam Backup Exec
    # =============================================================================
    "veeam_jobs": SystemAction(
        name="veeam_jobs",
        display_name="Veeam Backup Jobs",
        system="backup",
        action_type=ActionType.QUERY,
        risk_level=RiskLevel.LOW,
        webhook_path="/webhook/veeam/jobs",
        description="Lấy danh sách backup jobs"
    ),
    "veeam_sessions": SystemAction(
        name="veeam_sessions",
        display_name="Veeam Sessions",
        system="backup",
        action_type=ActionType.QUERY,
        risk_level=RiskLevel.LOW,
        webhook_path="/webhook/veeam/sessions",
        parameters=[
            {"name": "limit", "type": "integer", "required": False},
        ],
        description="Lấy các phiên backup"
    ),
    "veeam_backups": SystemAction(
        name="veeam_backups",
        display_name="Veeam Backups",
        system="backup",
        action_type=ActionType.QUERY,
        risk_level=RiskLevel.LOW,
        webhook_path="/webhook/veeam/backups",
        description="Lấy danh sách backups"
    ),
    "veeam_restore_points": SystemAction(
        name="veeam_restore_points",
        display_name="Veeam Restore Points",
        system="backup",
        action_type=ActionType.QUERY,
        risk_level=RiskLevel.LOW,
        webhook_path="/webhook/veeam/restore-points",
        parameters=[
            {"name": "backup_name", "type": "string", "required": False},
        ],
        description="Lấy restore points"
    ),
    "veeam_repositories": SystemAction(
        name="veeam_repositories",
        display_name="Veeam Repositories",
        system="backup",
        action_type=ActionType.QUERY,
        risk_level=RiskLevel.LOW,
        webhook_path="/webhook/veeam/repositories",
        description="Lấy danh sách repositories"
    ),
    
    # =============================================================================
    # Database Monitoring
    # =============================================================================
    "db_mysql_status": SystemAction(
        name="db_mysql_status",
        display_name="MySQL Status",
        system="database",
        action_type=ActionType.QUERY,
        risk_level=RiskLevel.LOW,
        webhook_path="/webhook/db/mysql/status",
        parameters=[
            {"name": "host", "type": "string", "required": False},
        ],
        description="Lấy MySQL status"
    ),
    "db_mysql_connections": SystemAction(
        name="db_mysql_connections",
        display_name="MySQL Connections",
        system="database",
        action_type=ActionType.QUERY,
        risk_level=RiskLevel.LOW,
        webhook_path="/webhook/db/mysql/connections",
        description="Lấy active connections"
    ),
    "db_mysql_size": SystemAction(
        name="db_mysql_size",
        display_name="MySQL Database Sizes",
        system="database",
        action_type=ActionType.QUERY,
        risk_level=RiskLevel.LOW,
        webhook_path="/webhook/db/mysql/size",
        description="Lấy kích thước databases"
    ),
    "db_pg_status": SystemAction(
        name="db_pg_status",
        display_name="PostgreSQL Status",
        system="database",
        action_type=ActionType.QUERY,
        risk_level=RiskLevel.LOW,
        webhook_path="/webhook/db/postgresql/status",
        parameters=[
            {"name": "host", "type": "string", "required": False},
        ],
        description="Lấy PostgreSQL status"
    ),
    "db_pg_connections": SystemAction(
        name="db_pg_connections",
        display_name="PostgreSQL Connections",
        system="database",
        action_type=ActionType.QUERY,
        risk_level=RiskLevel.LOW,
        webhook_path="/webhook/db/postgresql/connections",
        description="Lấy active connections"
    ),
    "db_pg_replication": SystemAction(
        name="db_pg_replication",
        display_name="PostgreSQL Replication",
        system="database",
        action_type=ActionType.QUERY,
        risk_level=RiskLevel.LOW,
        webhook_path="/webhook/db/postgresql/replication",
        description="Lấy trạng thái replication"
    ),
    "db_mongo_status": SystemAction(
        name="db_mongo_status",
        display_name="MongoDB Status",
        system="database",
        action_type=ActionType.QUERY,
        risk_level=RiskLevel.LOW,
        webhook_path="/webhook/db/mongodb/status",
        parameters=[
            {"name": "host", "type": "string", "required": False},
        ],
        description="Lấy MongoDB status"
    ),
    "db_mongo_connections": SystemAction(
        name="db_mongo_connections",
        display_name="MongoDB Connections",
        system="database",
        action_type=ActionType.QUERY,
        risk_level=RiskLevel.LOW,
        webhook_path="/webhook/db/mongodb/connections",
        description="Lấy active connections"
    ),
    "db_mongo_size": SystemAction(
        name="db_mongo_size",
        display_name="MongoDB Database Sizes",
        system="database",
        action_type=ActionType.QUERY,
        risk_level=RiskLevel.LOW,
        webhook_path="/webhook/db/mongodb/size",
        description="Lấy kích thước databases"
    ),
    "db_redis_info": SystemAction(
        name="db_redis_info",
        display_name="Redis Info",
        system="database",
        action_type=ActionType.QUERY,
        risk_level=RiskLevel.LOW,
        webhook_path="/webhook/db/redis/info",
        description="Lấy Redis info"
    ),
    "db_redis_keys": SystemAction(
        name="db_redis_keys",
        display_name="Redis Keys",
        system="database",
        action_type=ActionType.QUERY,
        risk_level=RiskLevel.LOW,
        webhook_path="/webhook/db/redis/keys",
        parameters=[
            {"name": "pattern", "type": "string", "required": False},
            {"name": "limit", "type": "integer", "required": False},
        ],
        description="Lấy keys trong Redis"
    ),
    "db_redis_memory": SystemAction(
        name="db_redis_memory",
        display_name="Redis Memory Usage",
        system="database",
        action_type=ActionType.QUERY,
        risk_level=RiskLevel.LOW,
        webhook_path="/webhook/db/redis/memory",
        description="Lấy memory usage"
    ),
    
    # =============================================================================
    # Kubernetes
    # =============================================================================
    "k8s_pods": SystemAction(
        name="k8s_pods",
        display_name="Kubernetes Pods",
        system="kubernetes",
        action_type=ActionType.QUERY,
        risk_level=RiskLevel.LOW,
        webhook_path="/webhook/k8s/pods",
        parameters=[
            {"name": "namespace", "type": "string", "required": False},
        ],
        description="Lấy danh sách pods"
    ),
    "k8s_services": SystemAction(
        name="k8s_services",
        display_name="Kubernetes Services",
        system="kubernetes",
        action_type=ActionType.QUERY,
        risk_level=RiskLevel.LOW,
        webhook_path="/webhook/k8s/services",
        parameters=[
            {"name": "namespace", "type": "string", "required": False},
        ],
        description="Lấy danh sách services"
    ),
    "k8s_nodes": SystemAction(
        name="k8s_nodes",
        display_name="Kubernetes Nodes",
        system="kubernetes",
        action_type=ActionType.QUERY,
        risk_level=RiskLevel.LOW,
        webhook_path="/webhook/k8s/nodes",
        description="Lấy danh sách nodes"
    ),
    "k8s_events": SystemAction(
        name="k8s_events",
        display_name="Kubernetes Events",
        system="kubernetes",
        action_type=ActionType.QUERY,
        risk_level=RiskLevel.LOW,
        webhook_path="/webhook/k8s/events",
        parameters=[
            {"name": "namespace", "type": "string", "required": False},
            {"name": "limit", "type": "integer", "required": False},
        ],
        description="Lấy events"
    ),
}


class N8NConnector:
    """
    Connector to n8n webhooks for internal system integration
    """
    
    def __init__(
        self,
        base_url: str = "http://localhost:5678",
        api_key: Optional[str] = None,
        timeout: int = 30,
        approval_store: Optional[Dict[str, ActionRequest]] = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout
        self.approval_store = approval_store or {}  # In-memory store
        
        # HTTP client
        self._client: Optional[httpx.AsyncClient] = None
    
    async def _get_client(self) -> httpx.AsyncClient:
        """Get or create HTTP client"""
        if self._client is None:
            headers = {}
            if self.api_key:
                headers["Authorization"] = f"Bearer {self.api_key}"
            
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                headers=headers,
                timeout=self.timeout,
            )
        return self._client
    
    async def close(self):
        """Close HTTP client"""
        if self._client:
            await self._client.aclose()
            self._client = None
    
    async def execute_query(
        self,
        action_name: str,
        parameters: Dict[str, Any],
        user_id: str = "system",
    ) -> Dict[str, Any]:
        """
        Execute a read-only query (no approval needed)
        """
        if action_name not in SYSTEM_ACTIONS:
            return {
                "success": False,
                "error": f"Unknown action: {action_name}",
            }
        
        action_def = SYSTEM_ACTIONS[action_name]
        
        if action_def.action_type != ActionType.QUERY:
            return {
                "success": False,
                "error": f"Action {action_name} requires approval. Use request_action() instead.",
            }
        
        return await self._execute_webhook(action_def, parameters, user_id)
    
    def request_action(
        self,
        action_name: str,
        parameters: Dict[str, Any],
        user_id: str,
        user_display_name: str = "Unknown",
    ) -> ActionRequest:
        """
        Request an action that requires approval
        Returns an ActionRequest that needs to be approved
        """
        if action_name not in SYSTEM_ACTIONS:
            raise ValueError(f"Unknown action: {action_name}")
        
        action_def = SYSTEM_ACTIONS[action_name]
        
        if action_def.action_type == ActionType.QUERY:
            raise ValueError(f"Action {action_name} is a query, no approval needed. Use execute_query() instead.")
        
        # Generate request ID
        import uuid
        request_id = str(uuid.uuid4())[:8]
        
        # Create approval request
        request = ActionRequest(
            request_id=request_id,
            action_type=action_name,
            system=action_def.system,
            action=action_def.display_name,
            parameters=parameters,
            risk_level=action_def.risk_level,
            requested_by=user_display_name,
        )
        
        # Store for later approval
        self.approval_store[request_id] = request

        # ← FIX: notify n8n immediately so external systems know a request is pending
        try:
            import asyncio
            loop = asyncio.get_running_loop()
            loop.create_task(self._notify_n8n_request(request_id, action_def, parameters, user_display_name))
        except RuntimeError:
            asyncio.get_event_loop().run_until_complete(
                self._notify_n8n_request(request_id, action_def, parameters, user_display_name)
            )

        logger.info("Action requested",
                   request_id=request_id,
                   action=action_name,
                   risk_level=action_def.risk_level.value,
                   requested_by=user_display_name)

        return request

    async def _notify_n8n_request(
        self,
        request_id: str,
        action_def,  # SystemAction
        parameters: Dict[str, Any],
        user_display_name: str,
    ):
        """Fire-and-forget notification to n8n that a request is pending."""
        try:
            import httpx
            async with httpx.AsyncClient(timeout=5.0) as client:
                await client.post(
                    f"{self.base_url}/webhook/n8n/action-requested",
                    json={
                        "request_id": request_id,
                        "action": action_def.name,
                        "system": action_def.system,
                        "parameters": parameters,
                        "requested_by": user_display_name,
                        "risk_level": action_def.risk_level.value,
                        "status": "pending_approval",
                    },
                )
        except Exception as e:
            logger.warning("n8n action-requested notification failed", error=str(e))
    
    def approve_action(
        self,
        request_id: str,
        approver_name: str = "Admin",
    ) -> ActionRequest:
        """
        Approve a pending action request
        """
        if request_id not in self.approval_store:
            raise ValueError(f"Request not found: {request_id}")
        
        request = self.approval_store[request_id]
        
        if request.status != "pending":
            raise ValueError(f"Request already {request.status}")
        
        # Get action definition
        action_def = SYSTEM_ACTIONS.get(request.action_type)
        if not action_def:
            raise ValueError(f"Unknown action type: {request.action_type}")
        
        # Mark as approved
        request.status = "approved"
        request.approved_by = approver_name
        request.approved_at = datetime.now(timezone.utc)
        
        logger.info("Action approved", 
                   request_id=request_id,
                   approver=approver_name)
        
        return request
    
    def reject_action(
        self,
        request_id: str,
        rejector_name: str = "Admin",
        reason: str = "",
    ) -> ActionRequest:
        """
        Reject a pending action request
        """
        if request_id not in self.approval_store:
            raise ValueError(f"Request not found: {request_id}")
        
        request = self.approval_store[request_id]
        
        if request.status != "pending":
            raise ValueError(f"Request already {request.status}")
        
        request.status = "rejected"
        request.result = {"rejected": True, "reason": reason}
        
        logger.info("Action rejected", 
                   request_id=request_id,
                   rejector=rejector_name,
                   reason=reason)
        
        return request
    
    async def execute_approved_action(
        self,
        request_id: str,
    ) -> Dict[str, Any]:
        """
        Execute an approved action
        """
        if request_id not in self.approval_store:
            return {
                "success": False,
                "error": f"Request not found: {request_id}",
            }
        
        request = self.approval_store[request_id]
        
        if request.status != "approved":
            return {
                "success": False,
                "error": f"Request not approved. Status: {request.status}",
            }
        
        # Get action definition
        action_def = SYSTEM_ACTIONS.get(request.action_type)
        if not action_def:
            return {
                "success": False,
                "error": f"Unknown action type: {request.action_type}",
            }
        
        # Execute webhook
        result = await self._execute_webhook(action_def, request.parameters, request.requested_by)
        
        # Store result
        request.result = result
        request.status = "executed"
        
        logger.info("Action executed",
                   request_id=request_id,
                   action=request.action_type,
                   success=result.get("success", False))
        
        return result
    
    async def _execute_webhook(
        self,
        action_def: SystemAction,
        parameters: Dict[str, Any],
        user_id: str,
    ) -> Dict[str, Any]:
        """Execute n8n webhook"""
        try:
            client = await self._get_client()
            
            # Build webhook URL
            url = f"{self.base_url}{action_def.webhook_path}"
            
            # Build payload
            payload = {
                "action": action_def.name,
                "system": action_def.system,
                "parameters": parameters,
                "triggered_by": user_id,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
            
            logger.debug("Executing webhook",
                        url=url,
                        action=action_def.name)
            
            # Execute
            response = await client.post(url, json=payload)
            
            if response.status_code == 200:
                return {
                    "success": True,
                    "data": response.json() if response.text else {},
                }
            else:
                return {
                    "success": False,
                    "error": f"HTTP {response.status_code}",
                    "details": response.text[:500],
                }
                
        except httpx.ConnectError:
            return {
                "success": False,
                "error": f"Cannot connect to n8n at {self.base_url}",
            }
        except Exception as e:
            logger.error("Webhook execution failed", error=str(e))
            return {
                "success": False,
                "error": str(e),
            }
    
    def get_pending_approvals(
        self,
        system: Optional[str] = None,
        risk_level: Optional[RiskLevel] = None,
    ) -> List[ActionRequest]:
        """Get all pending approval requests"""
        pending = [
            req for req in self.approval_store.values()
            if req.status == "pending"
        ]
        
        if system:
            pending = [req for req in pending if req.system == system]
        
        if risk_level:
            pending = [req for req in pending if req.risk_level == risk_level]
        
        return sorted(pending, key=lambda x: x.requested_at)
    
    def get_available_actions(self, action_type: Optional[ActionType] = None) -> List[SystemAction]:
        """Get list of available actions"""
        actions = list(SYSTEM_ACTIONS.values())
        
        if action_type:
            actions = [a for a in actions if a.action_type == action_type]
        
        return actions


# Global connector instance (lazy initialization)
_connector: Optional[N8NConnector] = None


def get_n8n_connector() -> N8NConnector:
    """Get or create global n8n connector"""
    global _connector
    if _connector is None:
        import os
        _connector = N8NConnector(
            base_url=os.getenv("N8N_BASE_URL", "http://localhost:5678"),
            api_key=os.getenv("N8N_API_KEY"),
        )
    return _connector
