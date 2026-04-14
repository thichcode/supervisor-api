"""
Notification - Multi-channel notification sender
Supports: Email, Telegram, Slack, Teams, SMS, Webhook
"""

import re
from typing import Optional, List, Dict, Any
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import structlog

logger = structlog.get_logger()

# Try importing libraries
HTTPX_AVAILABLE = False
try:
    import httpx
    HTTPX_AVAILABLE = True
except ImportError:
    pass

SMTP_AVAILABLE = False
try:
    import smtplib
    SMTP_AVAILABLE = True
except ImportError:
    pass


class Channel(str, Enum):
    """Notification channels"""
    EMAIL = "email"
    TELEGRAM = "telegram"
    SLACK = "slack"
    TEAMS = "teams"
    SMS = "sms"
    WEBHOOK = "webhook"


@dataclass
class NotificationMessage:
    """Notification message"""
    channel: Channel
    subject: Optional[str] = None
    body: str = ""
    html: Optional[str] = None
    recipients: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)
    priority: str = "normal"  # low, normal, high, urgent
    sent_at: Optional[datetime] = None
    status: str = "pending"  # pending, sent, failed


@dataclass
class ChannelConfig:
    """Channel-specific configuration"""
    # Email
    smtp_host: Optional[str] = None
    smtp_port: int = 587
    smtp_user: Optional[str] = None
    smtp_password: Optional[str] = None
    smtp_use_tls: bool = True
    from_email: Optional[str] = None
    
    # Telegram
    telegram_bot_token: Optional[str] = None
    telegram_parse_mode: str = "Markdown"
    
    # Slack
    slack_webhook_url: Optional[str] = None
    slack_channel: Optional[str] = None
    
    # Teams
    teams_webhook_url: Optional[str] = None
    
    # SMS
    sms_api_key: Optional[str] = None
    sms_api_url: Optional[str] = None
    sms_sender: Optional[str] = None
    
    # Generic Webhook
    webhook_url: Optional[str] = None
    webhook_headers: Dict[str, str] = field(default_factory=dict)


class TemplateRenderer:
    """Simple template renderer for notifications"""
    
    def __init__(self):
        self._templates: Dict[str, str] = {}
    
    def register(self, name: str, template: str):
        """Register a template"""
        self._templates[name] = template
    
    def render(self, template_name: str, **kwargs) -> str:
        """Render a template with variables"""
        template = self._templates.get(template_name)
        if not template:
            raise ValueError(f"Template not found: {template_name}")
        
        # Simple variable substitution
        result = template
        for key, value in kwargs.items():
            result = result.replace(f"{{{key}}}", str(value))
            result = result.replace(f"{{{{{key}}}}}", str(value))
        
        return result
    
    def render_string(self, template: str, **kwargs) -> str:
        """Render a template string"""
        return self.render("_inline_", template, **kwargs)


class NotificationSender:
    """
    Multi-channel notification sender
    """
    
    def __init__(self, config: Optional[ChannelConfig] = None):
        self.config = config or ChannelConfig()
        self.renderer = TemplateRenderer()
        self._http_client: Optional[httpx.AsyncClient] = None
        self._sent_log: List[NotificationMessage] = []
        
        # Register default templates
        self._register_default_templates()
    
    def _register_default_templates(self):
        """Register default notification templates"""
        self.renderer.register("incident_alert", """🚨 *INCIDENT ALERT*

*{title}*
Severity: {severity}
Status: {status}

📋 Description:
{description}

⏰ Time: {timestamp}
📎 Link: {link}
""")
        
        self.renderer.register("ticket_created", """🎫 *Ticket Created*

ID: {ticket_id}
Title: {title}
Priority: {priority}
Category: {category}

📝 Description:
{description}

👤 Created by: {created_by}
📅 Due: {due_date}
🔗 Link: {link}
""")
        
        self.renderer.register("backup_report", """💾 *Backup Report*

📊 Status: {status}
Backup: {backup_name}
Size: {size}

⏱️ Duration: {duration}
✅ Files: {files_count}
🔄 Retention: {retention}

📅 {timestamp}
""")
        
        self.renderer.register("system_health", """🏥 *System Health Report*

⏰ {timestamp}

📊 Services:
{summary}

🔴 Critical: {critical_count}
🟡 Warning: {warning_count}
🟢 Healthy: {healthy_count}
""")
        
        self.renderer.register("approval_request", """⚠️ *Approval Required*

Action: {action}
Requested by: {requested_by}
Risk Level: {risk_level}

📋 Details:
{details}

⏰ Requested at: {requested_at}

🔗 Approve: {approve_link}
🚫 Reject: {reject_link}
""")
    
    async def _get_client(self) -> httpx.AsyncClient:
        """Get HTTP client"""
        if self._http_client is None:
            self._http_client = httpx.AsyncClient(timeout=30.0)
        return self._http_client
    
    async def close(self):
        """Close HTTP client"""
        if self._http_client:
            await self._http_client.aclose()
    
    async def send(self, message: NotificationMessage) -> bool:
        """Send notification via specified channel"""
        try:
            if message.channel == Channel.EMAIL:
                return await self._send_email(message)
            elif message.channel == Channel.TELEGRAM:
                return await self._send_telegram(message)
            elif message.channel == Channel.SLACK:
                return await self._send_slack(message)
            elif message.channel == Channel.TEAMS:
                return await self._send_teams(message)
            elif message.channel == Channel.SMS:
                return await self._send_sms(message)
            elif message.channel == Channel.WEBHOOK:
                return await self._send_webhook(message)
            else:
                logger.error("Unknown channel", channel=message.channel)
                return False
        except Exception as e:
            logger.error("Send failed", channel=message.channel, error=str(e))
            message.status = "failed"
            return False
    
    async def send_multi(
        self,
        channel: Channel,
        recipients: List[str],
        subject: Optional[str],
        body: str,
        html: Optional[str] = None,
        **kwargs
    ) -> Dict[str, bool]:
        """Send to multiple recipients"""
        results = {}
        
        for recipient in recipients:
            message = NotificationMessage(
                channel=channel,
                subject=subject,
                body=body,
                html=html,
                recipients=[recipient],
                **kwargs
            )
            results[recipient] = await self.send(message)
        
        return results
    
    async def _send_email(self, message: NotificationMessage) -> bool:
        """Send email via SMTP"""
        if not SMTP_AVAILABLE:
            logger.error("smtplib not available")
            return False
        
        if not self.config.smtp_host:
            logger.error("SMTP not configured")
            return False
        
        try:
            msg = MIMEMultipart('alternative')
            msg['Subject'] = message.subject or "Notification"
            msg['From'] = self.config.from_email or self.config.smtp_user
            msg['To'] = ", ".join(message.recipients)
            
            # Plain text
            msg.attach(MIMEText(message.body, 'plain'))
            
            # HTML
            if message.html:
                msg.attach(MIMEText(message.html, 'html'))
            
            # Send
            server = smtplib.SMTP(self.config.smtp_host, self.config.smtp_port)
            if self.config.smtp_use_tls:
                server.starttls()
            
            if self.config.smtp_user and self.config.smtp_password:
                server.login(self.config.smtp_user, self.config.smtp_password)
            
            server.sendmail(msg['From'], message.recipients, msg.as_string())
            server.quit()
            
            message.status = "sent"
            message.sent_at = datetime.utcnow()
            self._sent_log.append(message)
            
            logger.info("Email sent", recipients=message.recipients)
            return True
            
        except Exception as e:
            logger.error("Email send failed", error=str(e))
            message.status = "failed"
            return False
    
    async def _send_telegram(self, message: NotificationMessage) -> bool:
        """Send Telegram message"""
        if not self.config.telegram_bot_token:
            logger.error("Telegram bot token not configured")
            return False
        
        try:
            client = await self._get_client()
            
            for recipient in message.recipients:
                # Get chat_id (could be user ID or channel)
                chat_id = recipient if recipient.startswith('-') else f"@{recipient}"
                
                payload = {
                    "chat_id": chat_id,
                    "text": message.body,
                    "parse_mode": self.config.telegram_parse_mode,
                }
                
                response = await client.post(
                    f"https://api.telegram.org/bot{self.config.telegram_bot_token}/sendMessage",
                    json=payload
                )
                
                if response.status_code != 200:
                    logger.error("Telegram send failed", 
                                status=response.status_code,
                                response=response.text)
                    return False
            
            message.status = "sent"
            message.sent_at = datetime.utcnow()
            self._sent_log.append(message)
            
            logger.info("Telegram sent", recipients=message.recipients)
            return True
            
        except Exception as e:
            logger.error("Telegram send failed", error=str(e))
            message.status = "failed"
            return False
    
    async def _send_slack(self, message: NotificationMessage) -> bool:
        """Send Slack message"""
        if not self.config.slack_webhook_url:
            logger.error("Slack webhook not configured")
            return False
        
        try:
            client = await self._get_client()
            
            payload = {
                "text": message.body,
            }
            
            if message.html:
                # Convert HTML to Slack blocks
                payload["blocks"] = [{
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": message.body
                    }
                }]
            
            if self.config.slack_channel:
                payload["channel"] = self.config.slack_channel
            
            response = await client.post(
                self.config.slack_webhook_url,
                json=payload
            )
            
            if response.status_code != 200:
                logger.error("Slack send failed", 
                            status=response.status_code,
                            response=response.text)
                return False
            
            message.status = "sent"
            message.sent_at = datetime.utcnow()
            self._sent_log.append(message)
            
            logger.info("Slack sent")
            return True
            
        except Exception as e:
            logger.error("Slack send failed", error=str(e))
            message.status = "failed"
            return False
    
    async def _send_teams(self, message: NotificationMessage) -> bool:
        """Send Microsoft Teams message"""
        if not self.config.teams_webhook_url:
            logger.error("Teams webhook not configured")
            return False
        
        try:
            client = await self._get_client()
            
            payload = {
                "@type": "MessageCard",
                "@context": "http://schema.org/extensions",
                "themeColor": self._get_priority_color(message.priority),
                "summary": message.subject or "Notification",
                "sections": [{
                    "activityTitle": message.subject,
                    "activitySubtitle": "",
                    "text": message.body,
                }],
            }
            
            response = await client.post(
                self.config.teams_webhook_url,
                json=payload
            )
            
            if response.status_code not in (200, 201):
                logger.error("Teams send failed",
                           status=response.status_code,
                           response=response.text)
                return False
            
            message.status = "sent"
            message.sent_at = datetime.utcnow()
            self._sent_log.append(message)
            
            logger.info("Teams sent")
            return True
            
        except Exception as e:
            logger.error("Teams send failed", error=str(e))
            message.status = "failed"
            return False
    
    async def _send_sms(self, message: NotificationMessage) -> bool:
        """Send SMS via API"""
        if not self.config.sms_api_url or not self.config.sms_api_key:
            logger.error("SMS not configured")
            return False
        
        try:
            client = await self._get_client()
            
            for recipient in message.recipients:
                # Clean phone number
                phone = re.sub(r'[^\d+]', '', recipient)
                
                payload = {
                    "api_key": self.config.sms_api_key,
                    "sender": self.config.sms_sender or "SUPERVISOR",
                    "phone": phone,
                    "message": message.body[:160],  # SMS limit
                }
                
                response = await client.post(
                    self.config.sms_api_url,
                    json=payload
                )
                
                if response.status_code != 200:
                    logger.error("SMS send failed",
                                status=response.status_code,
                                phone=phone)
                    return False
            
            message.status = "sent"
            message.sent_at = datetime.utcnow()
            self._sent_log.append(message)
            
            logger.info("SMS sent", recipients=message.recipients)
            return True
            
        except Exception as e:
            logger.error("SMS send failed", error=str(e))
            message.status = "failed"
            return False
    
    async def _send_webhook(self, message: NotificationMessage) -> bool:
        """Send via generic webhook"""
        if not self.config.webhook_url:
            logger.error("Webhook URL not configured")
            return False
        
        try:
            client = await self._get_client()
            
            payload = {
                "channel": message.channel.value,
                "subject": message.subject,
                "body": message.body,
                "recipients": message.recipients,
                "priority": message.priority,
                "metadata": message.metadata,
                "timestamp": datetime.utcnow().isoformat(),
            }
            
            headers = dict(self.config.webhook_headers)
            headers["Content-Type"] = "application/json"
            
            response = await client.post(
                self.config.webhook_url,
                json=payload,
                headers=headers
            )
            
            if response.status_code not in (200, 201, 202):
                logger.error("Webhook send failed",
                            status=response.status_code,
                            response=response.text)
                return False
            
            message.status = "sent"
            message.sent_at = datetime.utcnow()
            self._sent_log.append(message)
            
            logger.info("Webhook sent")
            return True
            
        except Exception as e:
            logger.error("Webhook send failed", error=str(e))
            message.status = "failed"
            return False
    
    def _get_priority_color(self, priority: str) -> str:
        """Get color for priority"""
        colors = {
            "low": "36a64f",      # Green
            "normal": "2196f3",   # Blue
            "high": "ff9800",     # Orange
            "urgent": "f44336",   # Red
        }
        return colors.get(priority, "2196f3")
    
    def get_sent_log(
        self,
        channel: Optional[Channel] = None,
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        """Get sent notification history"""
        log = self._sent_log
        
        if channel:
            log = [m for m in log if m.channel == channel]
        
        return [
            {
                "channel": m.channel.value,
                "subject": m.subject,
                "recipients": m.recipients,
                "status": m.status,
                "sent_at": m.sent_at.isoformat() if m.sent_at else None,
                "priority": m.priority,
            }
            for m in log[-limit:]
        ]


# Factory functions
def create_telegram_sender(bot_token: str) -> NotificationSender:
    """Create Telegram notification sender"""
    config = ChannelConfig(telegram_bot_token=bot_token)
    return NotificationSender(config)


def create_slack_sender(webhook_url: str, channel: Optional[str] = None) -> NotificationSender:
    """Create Slack notification sender"""
    config = ChannelConfig(slack_webhook_url=webhook_url, slack_channel=channel)
    return NotificationSender(config)


def create_teams_sender(webhook_url: str) -> NotificationSender:
    """Create Teams notification sender"""
    config = ChannelConfig(teams_webhook_url=webhook_url)
    return NotificationSender(config)


def create_email_sender(
    smtp_host: str,
    smtp_user: str,
    smtp_password: str,
    from_email: Optional[str] = None,
) -> NotificationSender:
    """Create email notification sender"""
    config = ChannelConfig(
        smtp_host=smtp_host,
        smtp_user=smtp_user,
        smtp_password=smtp_password,
        from_email=from_email or smtp_user,
    )
    return NotificationSender(config)


# Global sender instance
_notification_sender: Optional[NotificationSender] = None


def get_notification_sender(config: Optional[ChannelConfig] = None) -> NotificationSender:
    """Get or create global notification sender"""
    global _notification_sender
    if _notification_sender is None:
        _notification_sender = NotificationSender(config)
    return _notification_sender
