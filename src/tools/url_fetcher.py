"""
URL Fetcher - Auto-detect and fetch URLs from user messages
Useful for identifying internal company resources mentioned in messages

Features:
- LRU cache với TTL=300s (5 phút)
- Dùng html.parser thay regex (không cần thêm dependency)
- Cache key = URL hash
"""

import re
import asyncio
import time
import hashlib
import httpx
from typing import Optional, List, Dict, Any
from urllib.parse import urlparse
from html.parser import HTMLParser
import structlog

logger = structlog.get_logger()

URL_CACHE_TTL = 300  # 5 minutes


class _URLCacheEntry:
    """Cache entry với TTL"""
    def __init__(self, value: Any, expires_at: float):
        self.value = value
        self.expires_at = expires_at
    
    def is_expired(self) -> bool:
        return time.time() > self.expires_at


class _URLCache:
    """Simple LRU cache với TTL cho URL fetcher"""
    def __init__(self, max_size: int = 100, ttl: int = URL_CACHE_TTL):
        self._cache: Dict[str, _URLCacheEntry] = {}
        self._max_size = max_size
        self._ttl = ttl
    
    def _make_key(self, url: str) -> str:
        return hashlib.md5(url.encode()).hexdigest()
    
    def get(self, url: str) -> Optional[Any]:
        key = self._make_key(url)
        entry = self._cache.get(key)
        if entry and not entry.is_expired():
            return entry.value
        elif entry:
            del self._cache[key]
        return None
    
    def set(self, url: str, value: Any) -> None:
        key = self._make_key(url)
        if len(self._cache) >= self._max_size:
            oldest_key = min(self._cache.keys(), key=lambda k: self._cache[k].expires_at)
            del self._cache[oldest_key]
        self._cache[key] = _URLCacheEntry(value, time.time() + self._ttl)
    
    def clear_expired(self) -> None:
        expired_keys = [k for k, v in self._cache.items() if v.is_expired()]
        for k in expired_keys:
            del self._cache[k]


_url_fetch_cache = _URLCache()

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
            domain = (parsed.netloc or "").lower()
            
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
            domain = (parsed.netloc or "").lower()
            
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
    
    async def fetch_url(self, url: str, use_cache: bool = True) -> URLInfo:
        """Fetch single URL and extract metadata.
        
        Args:
            url: URL to fetch
            use_cache: Whether to use LRU cache (default: True)
        """
        global _url_fetch_cache
        
        if use_cache:
            cached_result = _url_fetch_cache.get(url)
            if cached_result is not None:
                logger.debug("url_fetcher_cache_hit", url=url)
                return cached_result
        
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
                    
                    if "text/html" in content_type:
                        html = response.text
                        url_info.title = self._extract_title(html)
                        url_info.description = self._extract_description(html)
                    else:
                        url_info.description = f"Content-Type: {content_type}"
                else:
                    url_info.fetch_error = f"HTTP {response.status_code}"
                    
        except httpx.TimeoutException:
            url_info.fetch_error = "Timeout"
        except httpx.RequestError as e:
            url_info.fetch_error = f"Request error: {str(e)[:50]}"
        except Exception as e:
            url_info.fetch_error = f"Error: {str(e)[:50]}"
        
        if use_cache:
            _url_fetch_cache.set(url, url_info)
        
        return url_info
    
    def _extract_title(self, html: str) -> Optional[str]:
        """Extract title from HTML using html.parser"""
        class TitleParser(HTMLParser):
            def __init__(self):
                super().__init__()
                self.title = None
                self.og_title = None
                self.current_tag = None
                self.current_attrs = {}
                
            def handle_starttag(self, tag: str, attrs: list[tuple[str, str]]) -> None:
                self.current_tag = tag
                self.current_attrs = dict(attrs)
                if tag == "title":
                    self.title = ""
                elif tag == "meta":
                    prop = self.current_attrs.get("property", "") or self.current_attrs.get("name", "")
                    if prop.lower() == "og:title":
                        content = self.current_attrs.get("content", "")
                        if content:
                            self.og_title = content.strip()
            
            def handle_endtag(self, tag: str) -> None:
                if tag == "title":
                    self.current_tag = None
            
            def handle_data(self, data: str) -> None:
                if self.current_tag == "title":
                    self.title = (self.title or "") + data
        
        try:
            parser = TitleParser()
            parser.feed(html)
            
            if parser.title:
                return parser.title.strip()
            if parser.og_title:
                return parser.og_title.strip()
            return None
        except Exception:
            return None
    
    def _extract_description(self, html: str) -> Optional[str]:
        """Extract description from HTML using html.parser"""
        class DescriptionParser(HTMLParser):
            def __init__(self):
                super().__init__()
                self.description = None
                self.og_description = None
                self.current_tag = None
                self.current_attrs = {}
                
            def handle_starttag(self, tag: str, attrs: list[tuple[str, str]]) -> None:
                self.current_tag = tag
                self.current_attrs = dict(attrs)
                if tag == "meta":
                    name = (self.current_attrs.get("name", "") or self.current_attrs.get("property", "")).lower()
                    content = self.current_attrs.get("content", "")
                    if name == "description" and content:
                        self.description = content.strip()
                    elif name == "og:description" and content:
                        self.og_description = content.strip()
            
            def handle_endtag(self, tag: str) -> None:
                self.current_tag = None
        
        try:
            parser = DescriptionParser()
            parser.feed(html)
            
            if parser.description:
                return parser.description[:200].strip()
            if parser.og_description:
                return parser.og_description[:200].strip()
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


def get_url_cache_stats() -> dict:
    """Get URL cache statistics."""
    global _url_fetch_cache
    return {
        "size": len(_url_fetch_cache._cache),
        "max_size": _url_fetch_cache._max_size,
        "ttl_seconds": _url_fetch_cache._ttl,
    }


def clear_url_cache() -> None:
    """Clear the URL fetch cache."""
    global _url_fetch_cache
    _url_fetch_cache._cache.clear()
    logger.info("url_cache_cleared")
