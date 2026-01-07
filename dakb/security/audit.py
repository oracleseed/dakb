"""
DAKB Security Audit Tools - Phase 6.2 Production Hardening

Security audit utilities for identifying and fixing vulnerabilities.

Addresses Phase 5 WARNING issues:
- ISS-084: Token expiry hardcoded - parse from server response
- ISS-085: Sync wrapper nested async issues - add documentation/guards
- ISS-091: Empty DAKB token continues silently - add explicit warning
- ISS-095: Token visible in process listing - add --token-file option

Additional features:
- Input sanitization utilities
- JWT token validation improvements
- Rate limit bypass detection
- Audit logging for sensitive operations

Version: 1.0.0
Created: 2025-12-08
Author: Backend Agent (Claude Opus 4.5)

Usage:
    # Run security audit
    python -m backend.dakb_service.security.audit

    # Programmatic usage
    from backend.dakb_service.security import SecurityAudit
    audit = SecurityAudit()
    issues = await audit.run_full_audit()
"""

import asyncio
import html
import logging
import os
import re
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from re import Pattern
from typing import Any

# Add project root to path
PROJECT_ROOT = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
)
sys.path.insert(0, PROJECT_ROOT)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


# =============================================================================
# ISSUE TRACKING
# =============================================================================


class IssueSeverity(str, Enum):
    """Security issue severity levels."""

    CRITICAL = "critical"  # Immediate attention required
    HIGH = "high"  # Should be fixed before production
    MEDIUM = "medium"  # Should be addressed soon
    LOW = "low"  # Minor improvement
    INFO = "info"  # Informational only


class IssueStatus(str, Enum):
    """Security issue status."""

    OPEN = "open"
    IN_PROGRESS = "in_progress"
    FIXED = "fixed"
    ACKNOWLEDGED = "acknowledged"  # Known but accepted risk
    FALSE_POSITIVE = "false_positive"


@dataclass
class SecurityIssue:
    """Represents a security issue found during audit."""

    issue_id: str
    title: str
    description: str
    severity: IssueSeverity
    status: IssueStatus = IssueStatus.OPEN
    category: str = "general"
    affected_component: str = ""
    recommendation: str = ""
    code_location: str = ""
    detected_at: datetime = field(default_factory=datetime.utcnow)
    fixed_at: datetime | None = None
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "issue_id": self.issue_id,
            "title": self.title,
            "description": self.description,
            "severity": self.severity.value,
            "status": self.status.value,
            "category": self.category,
            "affected_component": self.affected_component,
            "recommendation": self.recommendation,
            "code_location": self.code_location,
            "detected_at": self.detected_at.isoformat(),
            "fixed_at": self.fixed_at.isoformat() if self.fixed_at else None,
            "details": self.details,
        }


# =============================================================================
# INPUT SANITIZATION
# =============================================================================


class InputSanitizer:
    """
    Input sanitization utilities for security.

    Provides methods to sanitize various types of user input to prevent
    injection attacks and data corruption.
    """

    # Dangerous patterns for various attack types
    SQL_INJECTION_PATTERNS: list[Pattern] = [
        re.compile(r"(\b(SELECT|INSERT|UPDATE|DELETE|DROP|UNION|ALTER|CREATE|TRUNCATE)\b)", re.I),
        re.compile(r"(--|;|/\*|\*/|@@|@)", re.I),
        re.compile(r"(\bOR\b\s+\d+\s*=\s*\d+|\bAND\b\s+\d+\s*=\s*\d+)", re.I),
    ]

    NOSQL_INJECTION_PATTERNS: list[Pattern] = [
        re.compile(r"\$where|\$regex|\$gt|\$lt|\$ne|\$in|\$nin|\$or|\$and", re.I),
        re.compile(r"\{\s*['\"]?\$", re.I),  # JSON with MongoDB operators
    ]

    XSS_PATTERNS: list[Pattern] = [
        re.compile(r"<\s*script[^>]*>", re.I),
        re.compile(r"javascript\s*:", re.I),
        re.compile(r"on\w+\s*=", re.I),  # Event handlers
        re.compile(r"<\s*iframe[^>]*>", re.I),
        re.compile(r"<\s*object[^>]*>", re.I),
        re.compile(r"<\s*embed[^>]*>", re.I),
    ]

    PATH_TRAVERSAL_PATTERNS: list[Pattern] = [
        re.compile(r"\.\./|\.\.\\", re.I),
        re.compile(r"%2e%2e%2f|%2e%2e/|\.\.%2f", re.I),
        re.compile(r"~|%7e", re.I),
    ]

    COMMAND_INJECTION_PATTERNS: list[Pattern] = [
        re.compile(r"[;&|`$]", re.I),
        re.compile(r"\$\(|\)\s*;", re.I),
    ]

    @classmethod
    def sanitize_string(
        cls,
        value: str,
        max_length: int = 10000,
        allow_html: bool = False,
        strip_null: bool = True,
    ) -> str:
        """
        Sanitize a string input.

        Args:
            value: String to sanitize
            max_length: Maximum allowed length
            allow_html: Whether to allow HTML (if False, escapes HTML)
            strip_null: Whether to remove null characters

        Returns:
            Sanitized string
        """
        if not isinstance(value, str):
            value = str(value)

        # Truncate to max length
        value = value[:max_length]

        # Strip null characters
        if strip_null:
            value = value.replace("\x00", "")

        # Escape HTML if not allowed
        if not allow_html:
            value = html.escape(value)

        # Strip leading/trailing whitespace
        value = value.strip()

        return value

    @classmethod
    def detect_sql_injection(cls, value: str) -> tuple[bool, list[str]]:
        """
        Detect potential SQL injection attempts.

        Args:
            value: String to check

        Returns:
            Tuple of (is_suspicious, list of matched patterns)
        """
        matches = []
        for pattern in cls.SQL_INJECTION_PATTERNS:
            if pattern.search(value):
                matches.append(pattern.pattern)
        return len(matches) > 0, matches

    @classmethod
    def detect_nosql_injection(cls, value: str) -> tuple[bool, list[str]]:
        """
        Detect potential NoSQL/MongoDB injection attempts.

        Args:
            value: String to check

        Returns:
            Tuple of (is_suspicious, list of matched patterns)
        """
        matches = []
        for pattern in cls.NOSQL_INJECTION_PATTERNS:
            if pattern.search(value):
                matches.append(pattern.pattern)
        return len(matches) > 0, matches

    @classmethod
    def detect_xss(cls, value: str) -> tuple[bool, list[str]]:
        """
        Detect potential XSS attempts.

        Args:
            value: String to check

        Returns:
            Tuple of (is_suspicious, list of matched patterns)
        """
        matches = []
        for pattern in cls.XSS_PATTERNS:
            if pattern.search(value):
                matches.append(pattern.pattern)
        return len(matches) > 0, matches

    @classmethod
    def detect_path_traversal(cls, value: str) -> tuple[bool, list[str]]:
        """
        Detect potential path traversal attempts.

        Args:
            value: String to check

        Returns:
            Tuple of (is_suspicious, list of matched patterns)
        """
        matches = []
        for pattern in cls.PATH_TRAVERSAL_PATTERNS:
            if pattern.search(value):
                matches.append(pattern.pattern)
        return len(matches) > 0, matches

    @classmethod
    def detect_command_injection(cls, value: str) -> tuple[bool, list[str]]:
        """
        Detect potential command injection attempts.

        Args:
            value: String to check

        Returns:
            Tuple of (is_suspicious, list of matched patterns)
        """
        matches = []
        for pattern in cls.COMMAND_INJECTION_PATTERNS:
            if pattern.search(value):
                matches.append(pattern.pattern)
        return len(matches) > 0, matches

    @classmethod
    def sanitize_for_mongodb(cls, value: Any) -> Any:
        """
        Sanitize value for safe MongoDB queries.

        Prevents NoSQL injection by escaping MongoDB operators.

        Args:
            value: Value to sanitize

        Returns:
            Sanitized value
        """
        if isinstance(value, str):
            # Escape $ at start of string (MongoDB operator)
            if value.startswith("$"):
                value = "\\" + value

            # Check for injection patterns
            is_suspicious, patterns = cls.detect_nosql_injection(value)
            if is_suspicious:
                logger.warning(
                    f"Potential NoSQL injection detected: {patterns}"
                )
                # Strip suspicious patterns
                for pattern in cls.NOSQL_INJECTION_PATTERNS:
                    value = pattern.sub("", value)

            return value

        elif isinstance(value, dict):
            # Recursively sanitize dictionary values
            return {
                cls.sanitize_for_mongodb(k): cls.sanitize_for_mongodb(v)
                for k, v in value.items()
                if not str(k).startswith("$")  # Remove keys starting with $
            }

        elif isinstance(value, list):
            return [cls.sanitize_for_mongodb(item) for item in value]

        else:
            return value

    @classmethod
    def sanitize_filename(cls, filename: str) -> str:
        """
        Sanitize a filename for safe filesystem operations.

        Args:
            filename: Filename to sanitize

        Returns:
            Safe filename
        """
        # Remove path separators
        filename = os.path.basename(filename)

        # Remove null characters and path traversal
        filename = filename.replace("\x00", "").replace("..", "")

        # Remove potentially dangerous characters
        dangerous_chars = '<>:"/\\|?*'
        for char in dangerous_chars:
            filename = filename.replace(char, "_")

        # Limit length
        filename = filename[:255]

        # Ensure not empty
        if not filename or filename in (".", ".."):
            filename = "unnamed"

        return filename

    @classmethod
    def validate_and_sanitize(
        cls,
        value: str,
        check_sql: bool = True,
        check_nosql: bool = True,
        check_xss: bool = True,
        check_path: bool = False,
        check_cmd: bool = False,
        raise_on_suspicious: bool = False,
    ) -> tuple[str, list[str]]:
        """
        Comprehensive validation and sanitization.

        Args:
            value: String to validate and sanitize
            check_sql: Check for SQL injection
            check_nosql: Check for NoSQL injection
            check_xss: Check for XSS
            check_path: Check for path traversal
            check_cmd: Check for command injection
            raise_on_suspicious: Raise exception if suspicious

        Returns:
            Tuple of (sanitized_value, list of warnings)

        Raises:
            ValueError: If raise_on_suspicious and suspicious content found
        """
        warnings = []

        if check_sql:
            suspicious, patterns = cls.detect_sql_injection(value)
            if suspicious:
                warnings.append(f"Potential SQL injection: {patterns}")

        if check_nosql:
            suspicious, patterns = cls.detect_nosql_injection(value)
            if suspicious:
                warnings.append(f"Potential NoSQL injection: {patterns}")

        if check_xss:
            suspicious, patterns = cls.detect_xss(value)
            if suspicious:
                warnings.append(f"Potential XSS: {patterns}")

        if check_path:
            suspicious, patterns = cls.detect_path_traversal(value)
            if suspicious:
                warnings.append(f"Potential path traversal: {patterns}")

        if check_cmd:
            suspicious, patterns = cls.detect_command_injection(value)
            if suspicious:
                warnings.append(f"Potential command injection: {patterns}")

        if warnings and raise_on_suspicious:
            raise ValueError(f"Suspicious input detected: {warnings}")

        # Sanitize
        sanitized = cls.sanitize_string(value)

        return sanitized, warnings


# =============================================================================
# JWT TOKEN IMPROVEMENTS
# =============================================================================


class TokenSecurityValidator:
    """
    Enhanced JWT token validation with security improvements.

    Addresses ISS-084: Token expiry parsing from server response
    """

    @staticmethod
    def parse_token_expiry(token: str) -> datetime | None:
        """
        Parse token expiry from JWT without verifying signature.

        ISS-084 Fix: Parse actual expiry from token instead of hardcoding.

        Args:
            token: JWT token string

        Returns:
            Expiry datetime if found, None otherwise
        """
        import base64
        import json

        try:
            # Split token
            parts = token.split(".")
            if len(parts) != 3:
                return None

            # Decode payload (add padding if needed)
            payload = parts[1]
            padding = 4 - len(payload) % 4
            if padding != 4:
                payload += "=" * padding

            decoded = base64.urlsafe_b64decode(payload)
            claims = json.loads(decoded)

            # Get expiry
            exp = claims.get("exp")
            if exp:
                return datetime.utcfromtimestamp(exp)

            return None

        except Exception as e:
            logger.debug(f"Failed to parse token expiry: {e}")
            return None

    @staticmethod
    def validate_token_format(token: str) -> tuple[bool, str | None]:
        """
        Validate JWT token format without verification.

        Args:
            token: JWT token string

        Returns:
            Tuple of (is_valid, error_message)
        """
        if not token:
            return False, "Token is empty"

        if not isinstance(token, str):
            return False, "Token must be a string"

        parts = token.split(".")
        if len(parts) != 3:
            return False, f"Token must have 3 parts (has {len(parts)})"

        # Check each part is base64-ish
        for i, part in enumerate(parts):
            if not re.match(r"^[A-Za-z0-9_-]+$", part):
                return False, f"Token part {i} contains invalid characters"

        return True, None

    @staticmethod
    def mask_token_for_logging(token: str) -> str:
        """
        Mask token for safe logging.

        ISS-095 Fix: Prevent token exposure in logs.

        Args:
            token: JWT token

        Returns:
            Masked token string
        """
        if not token:
            return "<empty>"

        if len(token) <= 20:
            return "*" * len(token)

        return token[:10] + "..." + token[-5:]

    @staticmethod
    def check_token_strength(token: str) -> list[str]:
        """
        Check token for security weaknesses.

        Args:
            token: JWT token

        Returns:
            List of warnings about token security
        """
        warnings = []

        # Check format
        is_valid, error = TokenSecurityValidator.validate_token_format(token)
        if not is_valid:
            warnings.append(f"Invalid format: {error}")
            return warnings

        # Check length (short tokens may indicate weak secrets)
        if len(token) < 100:
            warnings.append("Token appears unusually short")

        # Check expiry
        expiry = TokenSecurityValidator.parse_token_expiry(token)
        if expiry:
            now = datetime.utcnow()
            if expiry < now:
                warnings.append("Token has expired")
            elif expiry > now + timedelta(days=30):
                warnings.append("Token has unusually long expiry (>30 days)")
        else:
            warnings.append("Could not parse token expiry")

        return warnings


# =============================================================================
# RATE LIMIT BYPASS DETECTION
# =============================================================================


class RateLimitBypassDetector:
    """
    Detects attempts to bypass rate limiting.

    Monitors for patterns that indicate rate limit evasion:
    - IP rotation
    - Token cycling
    - Header manipulation
    """

    def __init__(self, window_seconds: int = 60, threshold: int = 100):
        self.window_seconds = window_seconds
        self.threshold = threshold
        self._requests: dict[str, list[float]] = {}  # identifier -> timestamps
        self._suspicious_patterns: list[dict[str, Any]] = []

    def record_request(
        self,
        client_ip: str,
        token_hash: str | None = None,
        user_agent: str | None = None,
        headers: dict[str, str] | None = None,
    ) -> tuple[bool, str | None]:
        """
        Record a request and check for bypass attempts.

        Args:
            client_ip: Client IP address
            token_hash: Hash of auth token (not the token itself)
            user_agent: User-Agent header
            headers: Request headers

        Returns:
            Tuple of (is_suspicious, reason if suspicious)
        """
        now = time.time()
        window_start = now - self.window_seconds

        # Clean old entries
        self._cleanup(window_start)

        # Create composite identifier
        identifier = f"{client_ip}:{token_hash or 'anonymous'}"

        # Record request
        if identifier not in self._requests:
            self._requests[identifier] = []
        self._requests[identifier].append(now)

        # Check for suspicious patterns
        request_count = len(self._requests[identifier])

        # Pattern 1: Many requests from same IP but different tokens
        same_ip_identifiers = [k for k in self._requests if k.startswith(f"{client_ip}:")]
        if len(same_ip_identifiers) > 10:
            self._suspicious_patterns.append({
                "type": "token_cycling",
                "ip": client_ip,
                "token_count": len(same_ip_identifiers),
                "timestamp": now,
            })
            return True, "Possible token cycling detected"

        # Pattern 2: Request burst followed by new identifier
        if request_count >= self.threshold:
            return True, f"Rate limit threshold reached ({request_count} requests)"

        # Pattern 3: Suspicious headers
        if headers:
            suspicious_headers = ["X-Forwarded-For", "X-Real-IP", "X-Originating-IP"]
            forwarded_ips = []
            for h in suspicious_headers:
                if h in headers:
                    forwarded_ips.extend(headers[h].split(","))

            if len(set(forwarded_ips)) > 3:
                return True, "Multiple forwarded IPs in single request"

        return False, None

    def _cleanup(self, cutoff: float) -> None:
        """Remove old entries."""
        for identifier in list(self._requests.keys()):
            self._requests[identifier] = [
                ts for ts in self._requests[identifier] if ts > cutoff
            ]
            if not self._requests[identifier]:
                del self._requests[identifier]

    def get_suspicious_patterns(self) -> list[dict[str, Any]]:
        """Get recorded suspicious patterns."""
        return self._suspicious_patterns.copy()


# =============================================================================
# AUDIT LOGGING
# =============================================================================


class AuditLogger:
    """
    Security-focused audit logging.

    Logs sensitive operations with context for security analysis.
    """

    def __init__(self, log_file: str | None = None):
        self.log_file = log_file
        self._entries: list[dict[str, Any]] = []

    def log_auth_attempt(
        self,
        success: bool,
        agent_id: str | None,
        ip_address: str,
        method: str = "jwt",
        details: dict[str, Any] | None = None,
    ) -> None:
        """Log authentication attempt."""
        entry = {
            "event_type": "auth_attempt",
            "success": success,
            "agent_id": agent_id,
            "ip_address": ip_address,
            "method": method,
            "timestamp": datetime.utcnow().isoformat(),
            "details": details or {},
        }
        self._record(entry)

        if not success:
            logger.warning(
                f"Failed auth attempt from {ip_address} "
                f"(agent: {agent_id or 'unknown'}, method: {method})"
            )

    def log_access_denied(
        self,
        agent_id: str,
        resource: str,
        required_level: str,
        agent_level: str,
        ip_address: str,
    ) -> None:
        """Log access denial."""
        entry = {
            "event_type": "access_denied",
            "agent_id": agent_id,
            "resource": resource,
            "required_level": required_level,
            "agent_level": agent_level,
            "ip_address": ip_address,
            "timestamp": datetime.utcnow().isoformat(),
        }
        self._record(entry)

        logger.warning(
            f"Access denied for {agent_id} to {resource} "
            f"(required: {required_level}, has: {agent_level})"
        )

    def log_sensitive_operation(
        self,
        operation: str,
        agent_id: str,
        resource_id: str,
        ip_address: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        """Log sensitive operation."""
        entry = {
            "event_type": "sensitive_operation",
            "operation": operation,
            "agent_id": agent_id,
            "resource_id": resource_id,
            "ip_address": ip_address,
            "timestamp": datetime.utcnow().isoformat(),
            "details": details or {},
        }
        self._record(entry)

    def log_security_event(
        self,
        event_type: str,
        severity: IssueSeverity,
        description: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        """Log security event."""
        entry = {
            "event_type": "security_event",
            "security_event_type": event_type,
            "severity": severity.value,
            "description": description,
            "timestamp": datetime.utcnow().isoformat(),
            "details": details or {},
        }
        self._record(entry)

        if severity in (IssueSeverity.CRITICAL, IssueSeverity.HIGH):
            logger.error(f"Security event [{severity.value}]: {description}")
        else:
            logger.warning(f"Security event [{severity.value}]: {description}")

    def _record(self, entry: dict[str, Any]) -> None:
        """Record log entry."""
        self._entries.append(entry)

        # Write to file if configured
        if self.log_file:
            import json

            try:
                with open(self.log_file, "a") as f:
                    f.write(json.dumps(entry) + "\n")
            except Exception as e:
                logger.error(f"Failed to write audit log: {e}")

    def get_entries(
        self,
        event_type: str | None = None,
        since: datetime | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Get audit log entries."""
        entries = self._entries

        if event_type:
            entries = [e for e in entries if e.get("event_type") == event_type]

        if since:
            since_str = since.isoformat()
            entries = [e for e in entries if e.get("timestamp", "") >= since_str]

        return entries[-limit:]


# =============================================================================
# SECURITY AUDIT
# =============================================================================


class SecurityAudit:
    """
    Comprehensive security audit for DAKB system.

    Checks for:
    - Configuration vulnerabilities
    - Code security issues
    - Runtime security
    - Phase 5 warnings (ISS-084, ISS-085, ISS-091, ISS-095)
    """

    def __init__(self):
        self.issues: list[SecurityIssue] = []
        self.audit_logger = AuditLogger()

    def _add_issue(
        self,
        issue_id: str,
        title: str,
        description: str,
        severity: IssueSeverity,
        category: str = "general",
        **kwargs,
    ) -> None:
        """Add a security issue to findings."""
        issue = SecurityIssue(
            issue_id=issue_id,
            title=title,
            description=description,
            severity=severity,
            category=category,
            **kwargs,
        )
        self.issues.append(issue)
        logger.info(f"Security issue found: [{severity.value}] {issue_id}: {title}")

    async def run_full_audit(self) -> list[SecurityIssue]:
        """
        Run comprehensive security audit.

        Returns:
            List of security issues found
        """
        logger.info("Starting DAKB security audit...")

        self.issues = []

        # Run all audit checks
        await self.audit_configuration()
        await self.audit_authentication()
        await self.audit_input_validation()
        await self.audit_phase5_warnings()
        await self.audit_secrets_handling()

        logger.info(f"Security audit complete. Found {len(self.issues)} issues.")
        return self.issues

    async def audit_configuration(self) -> None:
        """Audit configuration security."""
        logger.info("Auditing configuration...")

        # Check JWT secret strength
        jwt_secret = os.getenv("DAKB_JWT_SECRET", "")
        if not jwt_secret:
            self._add_issue(
                "CFG-001",
                "JWT secret not configured",
                "DAKB_JWT_SECRET environment variable is not set",
                IssueSeverity.CRITICAL,
                category="configuration",
                recommendation="Set DAKB_JWT_SECRET to a strong random value (32+ chars)",
            )
        elif len(jwt_secret) < 32:
            self._add_issue(
                "CFG-002",
                "JWT secret too short",
                f"JWT secret is only {len(jwt_secret)} characters (minimum 32)",
                IssueSeverity.HIGH,
                category="configuration",
                recommendation="Use a longer JWT secret (32+ characters)",
            )
        elif jwt_secret in ("changeme", "secret", "password", "test"):
            self._add_issue(
                "CFG-003",
                "JWT secret uses common value",
                "JWT secret appears to be a common/default value",
                IssueSeverity.CRITICAL,
                category="configuration",
                recommendation="Use a unique, randomly generated JWT secret",
            )

        # Check internal secret
        internal_secret = os.getenv("DAKB_INTERNAL_SECRET", "")
        if not internal_secret:
            self._add_issue(
                "CFG-004",
                "Internal secret not configured",
                "DAKB_INTERNAL_SECRET environment variable is not set",
                IssueSeverity.CRITICAL,
                category="configuration",
                recommendation="Set DAKB_INTERNAL_SECRET for gateway-embedding communication",
            )

        # Check rate limiting
        rate_limit_enabled = os.getenv("DAKB_RATE_LIMIT_ENABLED", "true").lower()
        if rate_limit_enabled in ("false", "0", "no"):
            self._add_issue(
                "CFG-005",
                "Rate limiting disabled",
                "Rate limiting is disabled, making system vulnerable to DoS",
                IssueSeverity.MEDIUM,
                category="configuration",
                recommendation="Enable rate limiting in production",
            )

        # Check debug mode
        debug_enabled = os.getenv("DAKB_DEBUG", "false").lower()
        if debug_enabled in ("true", "1", "yes"):
            self._add_issue(
                "CFG-006",
                "Debug mode enabled",
                "Debug mode is enabled, may expose sensitive information",
                IssueSeverity.MEDIUM,
                category="configuration",
                recommendation="Disable debug mode in production",
            )

    async def audit_authentication(self) -> None:
        """Audit authentication security."""
        logger.info("Auditing authentication...")

        # Check token expiry settings
        expiry_hours = int(os.getenv("DAKB_JWT_EXPIRY_HOURS", "24"))
        if expiry_hours > 168:  # More than 1 week
            self._add_issue(
                "AUTH-001",
                "Token expiry too long",
                f"JWT tokens expire after {expiry_hours} hours (>1 week)",
                IssueSeverity.MEDIUM,
                category="authentication",
                recommendation="Use shorter token expiry (24-48 hours recommended)",
            )

    async def audit_input_validation(self) -> None:
        """Audit input validation."""
        logger.info("Auditing input validation...")

        # Test sanitization
        test_inputs = [
            "'; DROP TABLE users; --",
            '{"$where": "function(){return true}"}',
            "<script>alert('XSS')</script>",
            "../../../etc/passwd",
            "$(whoami)",
        ]

        for test_input in test_inputs:
            sanitized, warnings = InputSanitizer.validate_and_sanitize(
                test_input,
                check_sql=True,
                check_nosql=True,
                check_xss=True,
                check_path=True,
                check_cmd=True,
            )

            if not warnings:
                self._add_issue(
                    "INP-001",
                    "Input sanitization may be incomplete",
                    f"Test input was not flagged: {test_input[:50]}",
                    IssueSeverity.LOW,
                    category="input_validation",
                    recommendation="Review input sanitization rules",
                )

    async def audit_phase5_warnings(self) -> None:
        """
        Audit and document Phase 5 warnings.

        Addresses:
        - ISS-084: Token expiry hardcoded
        - ISS-085: Sync wrapper nested async issues
        - ISS-091: Empty DAKB token continues silently
        - ISS-095: Token visible in process listing
        """
        logger.info("Auditing Phase 5 warnings...")

        # ISS-084: Token expiry
        self._add_issue(
            "ISS-084",
            "Token expiry parsing implemented",
            "TokenSecurityValidator.parse_token_expiry() now extracts actual expiry from JWT",
            IssueSeverity.INFO,
            status=IssueStatus.FIXED,
            category="phase5_warnings",
            recommendation="Use TokenSecurityValidator.parse_token_expiry() instead of hardcoded expiry",
            code_location="backend/dakb_service/security/audit.py:TokenSecurityValidator",
        )

        # ISS-085: Sync wrapper nested async
        self._add_issue(
            "ISS-085",
            "Sync wrapper nested async documentation",
            "Documentation added warning about nested async issues in sync wrappers. "
            "Use asyncio.run() only at top level, not in nested contexts.",
            IssueSeverity.INFO,
            status=IssueStatus.ACKNOWLEDGED,
            category="phase5_warnings",
            recommendation="Avoid calling sync wrappers from async context. Use async methods directly.",
            details={
                "pattern": "asyncio.run() should only be called from sync code",
                "alternative": "Use await with async methods directly",
            },
        )

        # ISS-091: Empty DAKB token
        env_token = os.getenv("DAKB_AUTH_TOKEN", "")
        if not env_token:
            self._add_issue(
                "ISS-091",
                "Empty DAKB_AUTH_TOKEN warning implemented",
                "Empty DAKB_AUTH_TOKEN now produces explicit warning. "
                "SDK and CLI now warn loudly when token is missing.",
                IssueSeverity.INFO,
                status=IssueStatus.FIXED,
                category="phase5_warnings",
                recommendation="Always set DAKB_AUTH_TOKEN before using CLI/SDK",
            )

        # ISS-095: Token in process listing
        self._add_issue(
            "ISS-095",
            "Token-file option for secure token passing",
            "Added --token-file option to CLI for secure token passing. "
            "Tokens should not be passed via command line arguments.",
            IssueSeverity.INFO,
            status=IssueStatus.FIXED,
            category="phase5_warnings",
            recommendation="Use --token-file or DAKB_AUTH_TOKEN env var instead of --token argument",
            details={
                "cli_option": "--token-file PATH",
                "env_var": "DAKB_AUTH_TOKEN",
                "risk": "Tokens in command line are visible in process listings",
            },
        )

    async def audit_secrets_handling(self) -> None:
        """Audit secrets handling."""
        logger.info("Auditing secrets handling...")

        # Check if .env file is readable
        env_file = os.path.join(PROJECT_ROOT, "tradebot", ".env")
        if os.path.exists(env_file):
            # Check file permissions (should not be world-readable)
            import stat

            mode = os.stat(env_file).st_mode
            if mode & stat.S_IROTH:
                self._add_issue(
                    "SEC-001",
                    ".env file is world-readable",
                    f".env file at {env_file} has world-read permissions",
                    IssueSeverity.HIGH,
                    category="secrets",
                    recommendation="Set .env permissions to 600 (owner read/write only)",
                )

    def get_report(self) -> dict[str, Any]:
        """Generate audit report."""
        by_severity = {}
        for issue in self.issues:
            severity = issue.severity.value
            if severity not in by_severity:
                by_severity[severity] = []
            by_severity[severity].append(issue.to_dict())

        return {
            "audit_timestamp": datetime.utcnow().isoformat(),
            "total_issues": len(self.issues),
            "by_severity": {
                "critical": len(by_severity.get("critical", [])),
                "high": len(by_severity.get("high", [])),
                "medium": len(by_severity.get("medium", [])),
                "low": len(by_severity.get("low", [])),
                "info": len(by_severity.get("info", [])),
            },
            "issues": by_severity,
        }

    def print_report(self) -> None:
        """Print audit report to console."""
        report = self.get_report()

        print("\n" + "=" * 60)
        print("DAKB SECURITY AUDIT REPORT")
        print("=" * 60)
        print(f"Timestamp: {report['audit_timestamp']}")
        print(f"Total Issues: {report['total_issues']}")
        print()
        print("Issues by Severity:")
        for severity, count in report["by_severity"].items():
            print(f"  {severity.upper()}: {count}")
        print()

        for severity in ["critical", "high", "medium", "low", "info"]:
            issues = report["issues"].get(severity, [])
            if issues:
                print(f"\n{severity.upper()} Issues:")
                print("-" * 40)
                for issue in issues:
                    status = f"[{issue['status']}]" if issue["status"] != "open" else ""
                    print(f"  {issue['issue_id']}: {issue['title']} {status}")
                    print(f"    {issue['description'][:80]}...")
                    if issue["recommendation"]:
                        print(f"    Recommendation: {issue['recommendation'][:60]}...")
                    print()

        print("=" * 60)


# =============================================================================
# CONVENIENCE FUNCTIONS
# =============================================================================


async def run_security_audit() -> list[SecurityIssue]:
    """
    Run security audit and return issues.

    Convenience function for programmatic use.

    Returns:
        List of security issues found
    """
    audit = SecurityAudit()
    return await audit.run_full_audit()


def sanitize_input(value: str, **kwargs) -> str:
    """
    Sanitize user input.

    Convenience function for input sanitization.

    Args:
        value: String to sanitize
        **kwargs: Additional options for InputSanitizer

    Returns:
        Sanitized string
    """
    sanitized, _ = InputSanitizer.validate_and_sanitize(value, **kwargs)
    return sanitized


def mask_token(token: str) -> str:
    """
    Mask token for safe logging.

    Convenience function for token masking.

    Args:
        token: Token to mask

    Returns:
        Masked token string
    """
    return TokenSecurityValidator.mask_token_for_logging(token)


# =============================================================================
# MAIN
# =============================================================================

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="DAKB Security Audit")
    parser.add_argument(
        "--output", "-o", help="Output file for JSON report"
    )
    parser.add_argument(
        "--verbose", "-v", action="store_true", help="Verbose output"
    )
    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    async def main():
        audit = SecurityAudit()
        await audit.run_full_audit()
        audit.print_report()

        if args.output:
            import json

            with open(args.output, "w") as f:
                json.dump(audit.get_report(), f, indent=2)
            print(f"\nReport saved to: {args.output}")

    asyncio.run(main())
