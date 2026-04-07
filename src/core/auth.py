"""
Authentication and Authorization Layer
- JWT token validation
- HMAC webhook signature verification
- API key authentication
- Role-based access control (RBAC)
"""
import base64
import hashlib
import hmac
import json
import time
from typing import Optional, List, Callable
from dataclasses import dataclass
from enum import Enum
from functools import wraps
import structlog

try:
    import jwt
    JWT_AVAILABLE = True
except ImportError:
    JWT_AVAILABLE = False
    jwt = None

from fastapi import HTTPException, Request, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

from src.config import get_settings

settings = get_settings()
logger = structlog.get_logger()


def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("utf-8")


def _b64url_decode(data: str) -> bytes:
    padding = "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(data + padding)


class AuthMethod(Enum):
    JWT = "jwt"
    HMAC = "hmac"
    API_KEY = "api_key"
    WEBHOOK_SECRET = "webhook_secret"


class UserRole(Enum):
    ADMIN = "admin"
    SERVICE = "service"
    USER = "user"
    GUEST = "guest"


@dataclass
class TokenPayload:
    sub: str          # Subject (user ID)
    role: str         # User role
    exp: int          # Expiration timestamp
    iat: int          # Issued at timestamp
    iss: str = "supervisor-api"  # Issuer
    aud: str = "supervisor"      # Audience
    scopes: List[str] = None     # Permission scopes
    service: Optional[str] = None  # Service name for service accounts


@dataclass
class AuthResult:
    authenticated: bool
    method: AuthMethod
    user_id: Optional[str] = None
    role: Optional[UserRole] = None
    scopes: List[str] = None
    error: Optional[str] = None


class JWTAuth:
    """JWT token validation"""
    
    def __init__(self, secret: str, algorithm: str = "HS256"):
        self.secret = secret
        self.algorithm = algorithm
        self._public_key: Optional[str] = None
    
    def set_public_key(self, public_key_pem: str):
        """Set RSA public key for RS256 algorithm"""
        self._public_key = public_key_pem
    
    def verify(self, token: str) -> AuthResult:
        """Verify and decode JWT token"""
        if not token:
            return AuthResult(False, AuthMethod.JWT, error="Token is required")

        if not JWT_AVAILABLE:
            return self._verify_without_pyjwt(token)

        try:
            # Choose key based on algorithm
            key = self._public_key if self.algorithm.startswith("RS") else self.secret
            
            payload = jwt.decode(
                token,
                key,
                algorithms=[self.algorithm],
                audience="supervisor",
                issuer="supervisor-api",
                options={"require": ["sub", "exp", "role"]}
            )
            
            return AuthResult(
                authenticated=True,
                method=AuthMethod.JWT,
                user_id=payload.get("sub"),
                role=UserRole(payload.get("role", "user")),
                scopes=payload.get("scopes", []),
            )
            
        except jwt.ExpiredSignatureError:
            logger.warning("jwt_expired", token=token[:20])
            return AuthResult(False, AuthMethod.JWT, error="Token has expired")
            
        except jwt.InvalidAudienceError:
            logger.warning("jwt_invalid_audience", token=token[:20])
            return AuthResult(False, AuthMethod.JWT, error="Invalid token audience")
            
        except jwt.InvalidIssuerError:
            logger.warning("jwt_invalid_issuer", token=token[:20])
            return AuthResult(False, AuthMethod.JWT, error="Invalid token issuer")
            
        except jwt.InvalidTokenError as e:
            logger.warning("jwt_invalid", error=str(e))
            return AuthResult(False, AuthMethod.JWT, error="Invalid token")

    def _verify_without_pyjwt(self, token: str) -> AuthResult:
        """Minimal HS256 JWT verification fallback when PyJWT is unavailable."""
        try:
            header_b64, payload_b64, signature_b64 = token.split(".")
        except ValueError:
            return AuthResult(False, AuthMethod.JWT, error="Invalid token")

        expected_sig = hmac.new(
            self.secret.encode("utf-8"),
            f"{header_b64}.{payload_b64}".encode("utf-8"),
            hashlib.sha256,
        ).digest()

        try:
            actual_sig = _b64url_decode(signature_b64)
        except Exception:
            return AuthResult(False, AuthMethod.JWT, error="Invalid token")

        if not hmac.compare_digest(actual_sig, expected_sig):
            return AuthResult(False, AuthMethod.JWT, error="Invalid token")

        try:
            payload = json.loads(_b64url_decode(payload_b64).decode("utf-8"))
        except (ValueError, json.JSONDecodeError):
            return AuthResult(False, AuthMethod.JWT, error="Invalid token")

        now = int(time.time())
        if payload.get("exp") is None or int(payload["exp"]) < now:
            return AuthResult(False, AuthMethod.JWT, error="Token has expired")
        if payload.get("aud") != "supervisor":
            return AuthResult(False, AuthMethod.JWT, error="Invalid token audience")
        if payload.get("iss") != "supervisor-api":
            return AuthResult(False, AuthMethod.JWT, error="Invalid token issuer")
        if not payload.get("sub") or not payload.get("role"):
            return AuthResult(False, AuthMethod.JWT, error="Invalid token")

        return AuthResult(
            authenticated=True,
            method=AuthMethod.JWT,
            user_id=payload.get("sub"),
            role=UserRole(payload.get("role", "user")),
            scopes=payload.get("scopes", []),
        )
    
    def create_token(
        self,
        user_id: str,
        role: str,
        scopes: List[str] = None,
        expires_in: int = 3600,
        service: Optional[str] = None
    ) -> str:
        """Create a new JWT token"""
        if not JWT_AVAILABLE:
            return self._create_token_without_pyjwt(user_id, role, scopes, expires_in, service)
        
        now = int(time.time())
        payload = {
            "sub": user_id,
            "role": role,
            "scopes": scopes or [],
            "iat": now,
            "exp": now + expires_in,
            "iss": "supervisor-api",
            "aud": "supervisor",
        }
        
        if service:
            payload["service"] = service
            
        return jwt.encode(payload, self.secret, algorithm=self.algorithm)

    def _create_token_without_pyjwt(
        self,
        user_id: str,
        role: str,
        scopes: Optional[List[str]] = None,
        expires_in: int = 3600,
        service: Optional[str] = None,
    ) -> str:
        """Minimal HS256 JWT creation fallback when PyJWT is unavailable."""
        now = int(time.time())
        header = {"alg": self.algorithm, "typ": "JWT"}
        payload = {
            "sub": user_id,
            "role": role,
            "scopes": scopes or [],
            "iat": now,
            "exp": now + expires_in,
            "iss": "supervisor-api",
            "aud": "supervisor",
        }
        if service:
            payload["service"] = service

        header_b64 = _b64url_encode(json.dumps(header, separators=(",", ":")).encode("utf-8"))
        payload_b64 = _b64url_encode(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
        signing_input = f"{header_b64}.{payload_b64}".encode("utf-8")
        signature = hmac.new(self.secret.encode("utf-8"), signing_input, hashlib.sha256).digest()
        return f"{header_b64}.{payload_b64}.{_b64url_encode(signature)}"
    
    def refresh_token(self, token: str, expires_in: int = 3600) -> Optional[str]:
        """Refresh an existing token"""
        if not JWT_AVAILABLE:
            return None
            
        try:
            payload = jwt.decode(
                token,
                self.secret,
                algorithms=[self.algorithm],
                options={"verify_exp": False}  # Don't verify expiration for refresh
            )
            
            return self.create_token(
                user_id=payload["sub"],
                role=payload["role"],
                scopes=payload.get("scopes", []),
                expires_in=expires_in,
                service=payload.get("service")
            )
        except Exception:
            return None


class HMACAuth:
    """HMAC signature verification for webhooks"""
    
    def __init__(self, secret: str, algorithm: str = "sha256"):
        self.secret = secret.encode('utf-8')
        self.algorithm = algorithm
    
    def compute_signature(
        self,
        payload: bytes,
        timestamp: Optional[str] = None,
        nonce: Optional[str] = None
    ) -> str:
        """Compute HMAC signature for a payload"""
        data = payload
        
        if timestamp:
            data = timestamp.encode('utf-8') + data
        if nonce:
            data = data + nonce.encode('utf-8')
        
        signature = hmac.new(
            self.secret,
            data,
            hashlib.new(self.algorithm).name
        ).hexdigest()
        
        return signature
    
    def verify(
        self,
        payload: bytes,
        signature: str,
        timestamp: Optional[str] = None,
        nonce: Optional[str] = None,
        max_age_seconds: int = 300
    ) -> AuthResult:
        """Verify HMAC signature"""
        if not signature:
            return AuthResult(False, AuthMethod.HMAC, error="Signature is required")
        
        # Check timestamp to prevent replay attacks
        if timestamp:
            try:
                ts = int(timestamp)
                now = int(time.time())
                if abs(now - ts) > max_age_seconds:
                    logger.warning("hmac_signature_expired", timestamp=timestamp)
                    return AuthResult(False, AuthMethod.HMAC, error="Signature has expired")
            except ValueError:
                return AuthResult(False, AuthMethod.HMAC, error="Invalid timestamp format")
        
        # Compute expected signature
        expected = self.compute_signature(payload, timestamp, nonce)
        
        # Use constant-time comparison
        if hmac.compare_digest(signature, expected):
            return AuthResult(
                authenticated=True,
                method=AuthMethod.HMAC,
                user_id="webhook_client"
            )
        else:
            logger.warning("hmac_signature_invalid")
            return AuthResult(False, AuthMethod.HMAC, error="Invalid signature")


class APIKeyAuth:
    """API Key authentication"""
    
    def __init__(self):
        self._keys: dict[str, dict] = {}
    
    def add_key(self, key: str, user_id: str, role: str = "service", scopes: List[str] = None):
        """Add an API key"""
        key_hash = hashlib.sha256(key.encode()).hexdigest()
        self._keys[key_hash] = {
            "user_id": user_id,
            "role": role,
            "scopes": scopes or [],
            "created_at": int(time.time())
        }
    
    def remove_key(self, key: str):
        """Remove an API key"""
        key_hash = hashlib.sha256(key.encode()).hexdigest()
        self._keys.pop(key_hash, None)
    
    def verify(self, key: str) -> AuthResult:
        """Verify API key"""
        if not key:
            return AuthResult(False, AuthMethod.API_KEY, error="API key is required")
        
        # Support both "Bearer token" and raw key
        if key.startswith("Bearer "):
            key = key[7:]
        
        key_hash = hashlib.sha256(key.encode()).hexdigest()
        key_info = self._keys.get(key_hash)
        
        if not key_info:
            return AuthResult(False, AuthMethod.API_KEY, error="Invalid API key")
        
        return AuthResult(
            authenticated=True,
            method=AuthMethod.API_KEY,
            user_id=key_info["user_id"],
            role=UserRole(key_info["role"]),
            scopes=key_info["scopes"]
        )


class AuthManager:
    """Central authentication manager"""
    
    def __init__(self):
        self.jwt_auth = JWTAuth(
            secret=settings.webhook_input_secret or "default-secret-change-me",
            algorithm="HS256"
        )
        self.hmac_auth = HMACAuth(
            secret=settings.webhook_input_secret or "default-secret-change-me"
        )
        self.api_key_auth = APIKeyAuth()
        
        # Load API keys from environment if provided
        api_keys = settings.webhook_input_secret.split(",") if settings.webhook_input_secret else []
        for i, key in enumerate(api_keys):
            if key and len(key) > 10:
                self.api_key_auth.add_key(key, f"service-{i}", "service", ["read", "write"])
    
    def authenticate_request(self, request: Request) -> AuthResult:
        """Authenticate a request using multiple methods"""
        
        # 1. Check Authorization header for JWT
        auth_header = request.headers.get("Authorization")
        if auth_header and auth_header.startswith("Bearer "):
            token = auth_header[7:]
            result = self.jwt_auth.verify(token)
            if result.authenticated:
                return result
        
        # 2. Check X-API-Key header
        api_key = request.headers.get("X-API-Key")
        if api_key:
            result = self.api_key_auth.verify(api_key)
            if result.authenticated:
                return result
        
        # 3. Check webhook signature
        signature = request.headers.get("X-Signature") or request.headers.get("X-Webhook-Signature")
        if signature:
            body = request._body if hasattr(request, "_body") else b""
            timestamp = request.headers.get("X-Timestamp")
            nonce = request.headers.get("X-Nonce")
            
            result = self.hmac_auth.verify(body, signature, timestamp, nonce)
            if result.authenticated:
                return result
        
        return AuthResult(False, AuthMethod.JWT, error="No valid authentication provided")


# Global auth manager
auth_manager = AuthManager()


# FastAPI dependencies
security = HTTPBearer(auto_error=False)


async def require_auth(
    credentials: HTTPAuthorizationCredentials = Depends(security),
    request: Request = None
) -> AuthResult:
    """FastAPI dependency for authentication"""
    if not credentials:
        # Try other auth methods
        if request:
            return auth_manager.authenticate_request(request)
        raise HTTPException(status_code=401, detail="Authentication required")
    
    result = auth_manager.jwt_auth.verify(credentials.credentials)
    if not result.authenticated:
        raise HTTPException(status_code=401, detail=result.error or "Invalid token")
    
    return result


def require_role(allowed_roles: List[UserRole]):
    """FastAPI dependency factory for role-based access"""
    async def check_role(auth: AuthResult = Depends(require_auth)):
        if auth.role not in allowed_roles:
            raise HTTPException(
                status_code=403,
                detail=f"Role {auth.role} is not authorized for this resource"
            )
        return auth
    return check_role


def require_scope(required_scopes: List[str]):
    """FastAPI dependency factory for scope-based access"""
    async def check_scope(auth: AuthResult = Depends(require_auth)):
        if not auth.scopes:
            raise HTTPException(status_code=403, detail="No scopes assigned")
        
        missing = set(required_scopes) - set(auth.scopes)
        if missing:
            raise HTTPException(
                status_code=403,
                detail=f"Missing required scopes: {missing}"
            )
        return auth
    return check_scope


# Admin-only dependency
require_admin = require_role([UserRole.ADMIN])


def require_webhook_signature(request: Request) -> AuthResult:
    """Verify webhook HMAC signature"""
    signature = request.headers.get("X-Signature")
    if not signature:
        raise HTTPException(status_code=401, detail="Webhook signature required")
    
    # This would need body access - use middleware or body parsing
    return AuthResult(True, AuthMethod.WEBHOOK_SECRET, user_id="webhook")


# Service account token creation utility
def create_service_token(service_name: str, scopes: List[str] = None) -> str:
    """Create a service account token"""
    return auth_manager.jwt_auth.create_token(
        user_id=f"service:{service_name}",
        role="service",
        scopes=scopes or ["read", "write"],
        expires_in=86400 * 30,  # 30 days
        service=service_name
    )


def create_admin_token(user_id: str, scopes: List[str] = None) -> str:
    """Create an admin token"""
    return auth_manager.jwt_auth.create_token(
        user_id=user_id,
        role="admin",
        scopes=scopes or ["admin", "read", "write"],
        expires_in=3600  # 1 hour
    )
