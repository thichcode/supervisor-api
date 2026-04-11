"""
URL Fetcher - Auto-detect and fetch URLs from user messages
Useful for identifying internal company resources mentioned in messages
"""

import re
import httpx
from typing import Optional, List, Dict, Any
from urllib.parse import urlparse
import structlog

logger = structlog.get_logger()

# Common URL patterns
URL_PATTERN = re.compile(
    r'https?://(?:[-\w.]|(?:%[\da-fA-F]{2}))+[^\s]*',
    re.IGNORECASE
)

# Internal company domains (configurable)
INTERNAL_DOMAINS = [
    "company.com",
    "internal.company.com",
    "wiki.company.com",
    "jira.company.com",
    "confluence.company.com",
    "sharepoint.company.com",
    "portal.company.com",
    # Add your internal domains here
]

# Trusted external domains
TRUSTED_DOMAINS = [
    "github.com",
    "gitlab.com",
    "stackoverflow.com",
    "docs.python.org",
    "docs.docker.com",
    "kubernetes.io",
    "aws.amazon.com",
    "azure.microsoft.com",
    "cloud.google.com",
]


class URLInfo:
    """Structured URL information"""
    
    def __init__(
        self,
        url: str,
        is_internal: bool = False,
        domain: str = "",
        title: Optional[str] = None,
        description: Optional[str] = None,
        status_code: Optional[int] = None,
        fetch_error: Optional[str] = None,
    ):
        self.url = url
        self.is_internal = is_internal
        self.domain = domain
        self.title = title
        self.description = description
        self.status_code = status_code
        self.fetch_error = fetch_error
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "url": self.url,
            "is_internal": self.is_internal,
            "domain": self.domain,
            "title": self.title,
            "description": self.description,
            "status_code": self.status_code,
            "fetch_error": self.fetch_error,
        }
    
    def get_summary(self) -> str:
        """Get human-readable summary"""
        location = "internal" if self.is_internal else "external"
        if self.fetch_error:
            return f"[{location}] {self.domain} - Error: {self.fetch_error}"
        if self.title:
            return f"[{location}] {self.title} ({self.domain})"
        return f"[{location}] {self.url}"


class URLFetcher:
    """
    Fetch and analyze URLs from user messages.
    Auto-detects internal vs external URLs.
    """
    
    def __init__(
        self,
        internal_domains: Optional[List[str]] = None,
        trusted_domains: Optional[List[str]] = None,
        timeout: int = 10,
        max_urls: int = 5,
    ):
        self.internal_domains = internal_domains or INTERNAL_DOMAINS
        self.trusted_domains = trusted_domains or TRUSTED_DOMAINS
        self.timeout = timeout
        self.max_urls = max_urls
    
    def detect_urls(self, text: str) -> List[str]:
        """Extract URLs from text"""
        urls = URL_PATTERN.findall(text)
        # Deduplicate while preserving order
        seen = set()
        unique_urls = []
        for url in urls:
            # Clean up URL (remove trailing punctuation)
            clean_url = url.rstrip('.,;:!?')
            if clean_url not in seen:
                seen.add(clean_url)
                unique_urls.append(clean_url)
        return unique_urls[:self.max_urls]
    
    def is_internal_url(self, url: str) -> bool:
        """Check if URL is internal (company network)"""
        try:
            parsed = urlparse(url)
            domain = parsed.netloc.lower()
            
            # Check against internal domains
            for internal_domain in self.internal_domains:
                if internal_domain.lower() in domain:
                    return True
            return False
        except Exception:
            return False
    
    def is_trusted_url(self, url: str) -> bool:
        """Check if URL is from a trusted external domain"""
        try:
            parsed = urlparse(url)
            domain = parsed.netloc.lower()
            
            for trusted_domain in self.trusted_domains:
                if trusted_domain.lower() in domain:
                    return True
            return False
        except Exception:
            return False
    
    def _should_fetch(self, url: str) -> bool:
        """Determine if URL should be fetched"""
        # Always fetch internal URLs
        if self.is_internal_url(url):
            return True
        
        # Only fetch trusted external URLs
        if self.is_trusted_url(url):
            return True
        
        return False
    
    async def fetch_url(self, url: str) -> URLInfo:
        """Fetch single URL and extract metadata"""
        parsed = urlparse(url)
        domain = parsed.netloc
        
        url_info = URLInfo(
            url=url,
            domain=domain,
            is_internal=self.is_internal_url(url),
        )
        
        if not self._should_fetch(url):
            url_info.fetch_error = "Domain not in trusted list"
            return url_info
        
        try:
            async with httpx.AsyncClient(
                timeout=self.timeout,
                follow_redirects=True,
                headers={
                    "User-Agent": "SupervisorBot/1.0 (+https://company.com/bot)"
                }
            ) as client:
                response = await client.get(url)
                url_info.status_code = response.status_code
                
                if response.status_code == 200:
                    content_type = response.headers.get("content-type", "")
                    
                    # Parse HTML content
                    if "text/html" in content_type:
                        html = response.text
                        url_info.title = self._extract_title(html)
                        url_info.description = self._extract_description(html)
                    else:
                        # For non-HTML, just note the content type
                        url_info.description = f"Content-Type: {content_type}"
                else:
                    url_info.fetch_error = f"HTTP {response.status_code}"
                    
        except httpx.TimeoutException:
            url_info.fetch_error = "Timeout"
        except httpx.RequestError as e:
            url_info.fetch_error = f"Request error: {str(e)[:50]}"
        except Exception as e:
            url_info.fetch_error = f"Error: {str(e)[:50]}"
        
        return url_info
    
    def _extract_title(self, html: str) -> Optional[str]:
        """Extract title from HTML"""
        try:
            # Simple regex for title (avoiding lxml/BeautifulSoup dependency)
            title_match = re.search(r'<title[^>]*>([^<]+)</title>', html, re.IGNORECASE)
            if title_match:
                return title_match.group(1).strip()
            
            # Fallback: og:title
            og_match = re.search(r'<meta[^>]+property=["\']og:title["\'][^>]+content=["\']([^"\']+)["\']', html, re.IGNORECASE)
            if og_match:
                return og_match.group(1).strip()
            
            return None
        except Exception:
            return None
    
    def _extract_description(self, html: str) -> Optional[str]:
        """Extract description from HTML"""
        try:
            # Meta description
            desc_match = re.search(
                r'<meta[^>]+name=["\']description["\'][^>]+content=["\']([^"\']+)["\']',
                html, re.IGNORECASE
            )
            if desc_match:
                return desc_match.group(1).strip()[:200]
            
            return None
        except Exception:
            return None
    
    async def fetch_all(self, text: str) -> List[URLInfo]:
        """Detect and fetch all URLs from text"""
        urls = self.detect_urls(text)
        
        if not urls:
            return []
        
        logger.info("URLs detected in message", count=len(urls), urls=urls)
        
        # Fetch all URLs concurrently
        import asyncio
        results = await asyncio.gather(
            *[self.fetch_url(url) for url in urls],
            return_exceptions=True
        )
        
        url_infos = []
        for result in results:
            if isinstance(result, URLInfo):
                url_infos.append(result)
            else:
                logger.warning("URL fetch exception", error=str(result))
        
        return url_infos
    
    def build_context(self, url_infos: List[URLInfo]) -> str:
        """Build context string from fetched URLs for LLM"""
        if not url_infos:
            return ""
        
        lines = ["\n## URLs mentioned in message:"]
        
        for i, info in enumerate(url_infos, 1):
            lines.append(f"\n{i}. {info.get_summary()}")
            if info.title:
                lines.append(f"   Title: {info.title}")
            if info.description:
                lines.append(f"   Description: {info.description[:100]}...")
        
        return "\n".join(lines)


# Global instance (can be configured)
url_fetcher = URLFetcher()
