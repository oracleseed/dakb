"""
DAKB Gateway Configuration

Configuration for the DAKB Gateway Service using environment variables.
Uses Pydantic BaseModel for validation (not pydantic-settings to avoid dependency).

Version: 1.1
Created: 2025-12-07
Author: Backend Agent (Claude Opus 4.5)

Environment Variables:
- DAKB_GATEWAY_HOST: Gateway bind host (default: 0.0.0.0)
- DAKB_GATEWAY_PORT: Gateway bind port (default: 3100)
- DAKB_INTERNAL_SECRET: Secret for gateway-embedding communication
- DAKB_JWT_SECRET: Secret for JWT token signing
- DAKB_JWT_ALGORITHM: JWT algorithm (default: HS256)
- DAKB_JWT_EXPIRY_HOURS: Token expiry in hours (default: 24)
- DAKB_EMBEDDING_URL: Internal embedding service URL
- MONGO_URI: MongoDB connection string
- DAKB_DB_NAME: Database name (default: dakb)
- DAKB_RATE_LIMIT_REQUESTS: Rate limit requests per window
- DAKB_RATE_LIMIT_WINDOW: Rate limit window in seconds
- DAKB_FAISS_DATA_DIR: FAISS data directory (default: data/dakb_faiss)
- DAKB_REDIS_URL: Redis connection URL (default: redis://localhost:6379/0)
- DAKB_MONGO_URI: Bridge MongoDB URI (default: mongodb://localhost:27017)
- DAKB_BRIDGE_ALLOW_AGENT_LAUNCH: Allow agent launch from chat (default: False)
- FILE_VAULT_BACKEND: Vault backend, 's3' or 'local' (default: local)
- FILE_VAULT_S3_ENDPOINT_URL: S3-compatible endpoint (default: http://localhost:9000)
- FILE_VAULT_S3_BUCKET: Vault bucket name (default: dakb-vault)
- FILE_VAULT_MAX_FILES / FILE_VAULT_MAX_SIZE: Per-entry vault limits
- FILE_VAULT_DELETE_TTL_DAYS: Soft-delete retention before purge (default: 30)
"""

import os

from pydantic import BaseModel, Field, field_validator


def _get_env(key: str, default: str | None = None) -> str | None:
    """Get environment variable with optional default."""
    return os.getenv(key, default)


def _get_env_int(key: str, default: int) -> int:
    """Get integer environment variable with default."""
    value = os.getenv(key)
    if value is None:
        return default
    try:
        return int(value)
    except ValueError:
        return default


def _get_env_float(key: str, default: float) -> float:
    """Get float environment variable with default."""
    value = os.getenv(key)
    if value is None:
        return default
    try:
        return float(value)
    except ValueError:
        return default


def _get_env_bool(key: str, default: bool) -> bool:
    """Get boolean environment variable with default."""
    value = os.getenv(key)
    if value is None:
        return default
    return value.lower() in ('true', '1', 'yes', 'on')


def _get_env_list(key: str, default: list[str]) -> list[str]:
    """Get list environment variable (comma-separated) with default."""
    value = os.getenv(key)
    if value is None:
        return default
    return [item.strip() for item in value.split(',') if item.strip()]


class VaultSettings(BaseModel):
    """File vault storage configuration.

    Defaults are deployment-neutral: the S3 backend points at a local
    S3-compatible endpoint (e.g. MinIO) and a generic bucket name. Override
    every value via the FILE_VAULT_* environment variables in production.
    """
    backend: str = Field(default="local", description="'s3' or 'local'")
    # S3 settings (defaults target a local S3-compatible endpoint such as MinIO)
    s3_endpoint_url: str | None = Field(None)
    s3_access_key: str | None = Field(None)
    s3_secret_key: str | None = Field(None)
    s3_bucket: str = Field(default="dakb-vault")
    s3_region: str = Field(default="us-east-1")
    # Local settings
    local_base_path: str = Field(default="./data/vault")
    # Limits
    max_files_per_entry: int = Field(default=10)
    max_total_size_bytes: int = Field(default=500 * 1024 * 1024)  # 500MB
    # Hold settings
    hold_ttl_seconds: int = Field(default=3600)  # 1 hour
    hold_base_path: str = Field(default="./data/vault_holds")
    # Cleanup
    soft_delete_ttl_days: int = Field(default=30)
    cleanup_interval_hours: int = Field(default=6)
    # Security
    signed_url_ttl_seconds: int = Field(default=900)  # 15 min
    # Broad allow-list — covers nearly all common non-executable formats.
    # Executable content is rejected by the upload route's executable detector
    # regardless of declared MIME type, so application/octet-stream is safe to
    # allow for ML data formats (parquet, npy, hdf5, etc.).
    allowed_mime_types: list[str] = Field(default=[
        # Documents
        "application/pdf",
        "application/msword",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/vnd.ms-excel",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "application/vnd.ms-powerpoint",
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        "application/vnd.oasis.opendocument.text",
        "application/vnd.oasis.opendocument.spreadsheet",
        "application/vnd.oasis.opendocument.presentation",
        "application/rtf",
        "application/epub+zip",
        # Text & code-as-text (storage only — not execution)
        "text/plain", "text/markdown", "text/csv", "text/tab-separated-values",
        "text/html", "text/xml", "text/css", "text/yaml", "text/x-yaml",
        "text/x-python", "text/x-c", "text/x-c++", "text/x-java", "text/x-go",
        "text/x-rust", "text/x-ruby", "text/x-php", "text/x-sql", "text/x-log",
        # Application data
        "application/json", "application/xml", "application/yaml",
        "application/x-yaml", "application/toml", "application/javascript",
        "application/x-ipynb+json", "application/sql",
        "application/x-sqlite3", "application/vnd.sqlite3",
        "application/x-hdf", "application/x-hdf5",
        "application/vnd.apache.parquet", "application/x-parquet",
        "application/x-numpy-data",
        "application/octet-stream",  # ML data catchall — guarded by executable detector
        # Images
        "image/png", "image/jpeg", "image/gif", "image/webp", "image/svg+xml",
        "image/bmp", "image/tiff", "image/x-icon", "image/vnd.microsoft.icon",
        "image/heic", "image/heif", "image/avif",
        # Audio
        "audio/mpeg", "audio/mp4", "audio/wav", "audio/x-wav", "audio/ogg",
        "audio/flac", "audio/x-flac", "audio/aac", "audio/webm",
        # Video
        "video/mp4", "video/webm", "video/x-matroska", "video/quicktime",
        "video/x-msvideo", "video/mpeg",
        # Archives
        "application/zip", "application/x-tar", "application/gzip",
        "application/x-gzip", "application/x-bzip2", "application/x-xz",
        "application/x-rar-compressed", "application/vnd.rar",
        "application/x-7z-compressed", "application/zstd", "application/x-zstd",
        # Fonts
        "font/ttf", "font/otf", "font/woff", "font/woff2",
    ])

    def to_backend_config(self) -> dict:
        """Convert to dict suitable for create_vault_backend()."""
        if self.backend == "s3":
            return {
                "backend": "s3",
                "endpoint_url": self.s3_endpoint_url,
                "access_key": self.s3_access_key,
                "secret_key": self.s3_secret_key,
                "bucket": self.s3_bucket,
                "region": self.s3_region,
            }
        return {
            "backend": "local",
            "base_path": self.local_base_path,
        }


class DAKBGatewaySettings(BaseModel):
    """
    Gateway configuration loaded from environment variables.

    All settings can be overridden via environment variables.
    Required settings will raise errors if not provided.
    """

    # ==========================================================================
    # Gateway Server Settings
    # ==========================================================================

    gateway_host: str = Field(
        default="0.0.0.0",
        description="Host to bind the gateway server"
    )

    gateway_port: int = Field(
        default=3100,
        description="Port for the gateway server",
        ge=1024,
        le=65535
    )

    debug: bool = Field(
        default=False,
        description="Enable debug mode"
    )

    # ==========================================================================
    # Security Settings
    # ==========================================================================

    internal_secret: str = Field(
        ...,  # Required
        description="Secret for gateway <-> embedding service communication",
        min_length=32
    )

    jwt_secret: str = Field(
        ...,  # Required
        description="Secret for JWT token signing",
        min_length=32
    )

    jwt_algorithm: str = Field(
        default="HS256",
        description="JWT signing algorithm"
    )

    jwt_expiry_hours: int = Field(
        default=24,
        description="JWT token expiry in hours",
        ge=1,
        le=720  # Max 30 days
    )

    # ==========================================================================
    # Internal Services
    # ==========================================================================

    embedding_service_url: str = Field(
        default="http://127.0.0.1:3101",
        description="Internal embedding service URL (loopback only)"
    )

    embedding_timeout: float = Field(
        default=30.0,
        description="Timeout for embedding service requests in seconds",
        ge=1.0,
        le=120.0
    )

    # ==========================================================================
    # MongoDB Settings
    # ==========================================================================

    mongo_uri: str | None = Field(
        default=None,
        description="MongoDB connection URI (falls back to project settings)"
    )

    db_name: str = Field(
        default="dakb",
        description="MongoDB database name"
    )

    # ==========================================================================
    # Rate Limiting
    # ==========================================================================

    rate_limit_enabled: bool = Field(
        default=True,
        description="Enable rate limiting"
    )

    rate_limit_requests: int = Field(
        default=100,
        description="Maximum requests per rate limit window",
        ge=1,
        le=10000
    )

    rate_limit_window: int = Field(
        default=60,
        description="Rate limit window in seconds",
        ge=1,
        le=3600
    )

    # ==========================================================================
    # CORS Settings
    # ==========================================================================

    cors_origins: list[str] = Field(
        default_factory=lambda: [
            "http://localhost:8000",
            "https://localhost:8000",
            "http://127.0.0.1:8000",
            "https://127.0.0.1:8000",
            # Add your LAN IPs here if needed, e.g.:
            # "http://192.168.1.100:8000",
        ],
        description="Allowed CORS origins"
    )

    cors_allow_credentials: bool = Field(
        default=True,
        description="Allow credentials in CORS requests"
    )

    # ==========================================================================
    # Logging Settings
    # ==========================================================================

    log_level: str = Field(
        default="INFO",
        description="Logging level"
    )

    log_format: str = Field(
        default="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        description="Log format string"
    )

    # ==========================================================================
    # Search Settings
    # ==========================================================================

    default_search_limit: int = Field(
        default=10,
        description="Default number of search results",
        ge=1,
        le=100
    )

    max_search_limit: int = Field(
        default=100,
        description="Maximum number of search results",
        ge=1,
        le=500
    )

    min_similarity_score: float = Field(
        default=0.3,
        description="Minimum similarity score for search results",
        ge=0.0,
        le=1.0
    )

    # ==========================================================================
    # Data Storage Settings
    # ==========================================================================

    faiss_data_dir: str = Field(
        default="data/dakb_faiss",
        description="Directory for FAISS index storage (relative to project root or absolute)"
    )

    # ==========================================================================
    # Redis Settings (Real-Time Communication)
    # ==========================================================================

    redis_url: str = Field(
        default="redis://localhost:6379/0",
        description="Redis connection URL for real-time communication"
    )

    # ==========================================================================
    # Bridge Settings (Chat Bridge / Session Bridge)
    # ==========================================================================

    bridge_mongo_uri: str = Field(
        default="mongodb://localhost:27017",
        description="MongoDB connection URI used by the Session Bridge"
    )

    bridge_allow_agent_launch: bool = Field(
        default=False,
        description=(
            "Allow launching agents from an external chat. DENY-BY-DEFAULT; "
            "enable only with an explicit allowlist."
        )
    )

    # ==========================================================================
    # File Vault Settings
    # ==========================================================================

    vault: VaultSettings = Field(
        default_factory=VaultSettings,
        description="File vault storage configuration"
    )

    @field_validator('internal_secret', 'jwt_secret')
    @classmethod
    def validate_secrets(cls, v: str) -> str:
        """Validate that secrets are strong enough."""
        if len(v) < 32:
            raise ValueError("Secret must be at least 32 characters")
        return v


def _load_settings_from_env() -> DAKBGatewaySettings:
    """
    Load settings from environment variables.

    Returns:
        DAKBGatewaySettings instance.

    Raises:
        ValueError: If required settings are missing.
    """
    # Get required settings
    internal_secret = _get_env("DAKB_INTERNAL_SECRET")
    jwt_secret = _get_env("DAKB_JWT_SECRET")

    if not internal_secret:
        raise ValueError("DAKB_INTERNAL_SECRET environment variable is required")
    if not jwt_secret:
        raise ValueError("DAKB_JWT_SECRET environment variable is required")

    return DAKBGatewaySettings(
        # Gateway settings
        gateway_host=_get_env("DAKB_GATEWAY_HOST", "0.0.0.0"),
        gateway_port=_get_env_int("DAKB_GATEWAY_PORT", 3100),
        debug=_get_env_bool("DAKB_DEBUG", False),

        # Security settings
        internal_secret=internal_secret,
        jwt_secret=jwt_secret,
        jwt_algorithm=_get_env("DAKB_JWT_ALGORITHM", "HS256"),
        jwt_expiry_hours=_get_env_int("DAKB_JWT_EXPIRY_HOURS", 24),

        # Internal services
        embedding_service_url=_get_env("DAKB_EMBEDDING_URL", "http://127.0.0.1:3101"),
        embedding_timeout=_get_env_float("DAKB_EMBEDDING_TIMEOUT", 30.0),

        # MongoDB settings
        mongo_uri=_get_env("MONGO_URI"),
        db_name=_get_env("DAKB_DB_NAME", "dakb"),

        # Rate limiting
        rate_limit_enabled=_get_env_bool("DAKB_RATE_LIMIT_ENABLED", True),
        rate_limit_requests=_get_env_int("DAKB_RATE_LIMIT_REQUESTS", 100),
        rate_limit_window=_get_env_int("DAKB_RATE_LIMIT_WINDOW", 60),

        # CORS settings
        cors_origins=_get_env_list("DAKB_CORS_ORIGINS", [
            "http://localhost:8000",
            "https://localhost:8000",
            "http://127.0.0.1:8000",
            "https://127.0.0.1:8000",
        ]),
        cors_allow_credentials=_get_env_bool("DAKB_CORS_CREDENTIALS", True),

        # Logging settings
        log_level=_get_env("DAKB_LOG_LEVEL", "INFO"),
        log_format=_get_env(
            "DAKB_LOG_FORMAT",
            "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
        ),

        # Search settings
        default_search_limit=_get_env_int("DAKB_DEFAULT_SEARCH_LIMIT", 10),
        max_search_limit=_get_env_int("DAKB_MAX_SEARCH_LIMIT", 100),
        min_similarity_score=_get_env_float("DAKB_MIN_SIMILARITY", 0.3),

        # Data storage settings
        faiss_data_dir=_get_env("DAKB_FAISS_DATA_DIR", "data/dakb_faiss"),

        # Redis settings (Real-Time Communication)
        redis_url=_get_env("DAKB_REDIS_URL", "redis://localhost:6379/0"),

        # Bridge settings (Chat Bridge / Session Bridge)
        bridge_mongo_uri=_get_env("DAKB_MONGO_URI", "mongodb://localhost:27017"),
        bridge_allow_agent_launch=_get_env_bool(
            "DAKB_BRIDGE_ALLOW_AGENT_LAUNCH", False
        ),

        # File Vault settings
        vault=VaultSettings(
            backend=_get_env("FILE_VAULT_BACKEND", "local"),
            s3_endpoint_url=_get_env("FILE_VAULT_S3_ENDPOINT_URL", "http://localhost:9000"),
            s3_access_key=_get_env("FILE_VAULT_S3_ACCESS_KEY"),
            s3_secret_key=_get_env("FILE_VAULT_S3_SECRET_KEY"),
            s3_bucket=_get_env("FILE_VAULT_S3_BUCKET", "dakb-vault"),
            s3_region=_get_env("FILE_VAULT_S3_REGION", "us-east-1"),
            local_base_path=_get_env("FILE_VAULT_LOCAL_BASE_PATH", "./data/vault"),
            max_files_per_entry=_get_env_int("FILE_VAULT_MAX_FILES", 10),
            max_total_size_bytes=_get_env_int("FILE_VAULT_MAX_SIZE", 500 * 1024 * 1024),
            hold_ttl_seconds=_get_env_int("FILE_VAULT_HOLD_TTL", 3600),
            hold_base_path=_get_env("FILE_VAULT_HOLD_PATH", "./data/vault_holds"),
            soft_delete_ttl_days=_get_env_int("FILE_VAULT_DELETE_TTL_DAYS", 30),
            cleanup_interval_hours=_get_env_int("FILE_VAULT_CLEANUP_HOURS", 6),
            signed_url_ttl_seconds=_get_env_int("FILE_VAULT_SIGNED_URL_TTL", 900),
            # Use VaultSettings class default — single source of truth
            allowed_mime_types=_get_env_list(
                "FILE_VAULT_ALLOWED_MIMES",
                VaultSettings.model_fields["allowed_mime_types"].default,
            ),
        ),
    )


# Cached settings instance
_settings: DAKBGatewaySettings | None = None


def get_settings() -> DAKBGatewaySettings:
    """
    Get settings instance.

    Uses module-level caching to ensure settings are only loaded once.

    Returns:
        DAKBGatewaySettings instance.

    Raises:
        ValueError: If required settings are missing or invalid.
    """
    global _settings
    if _settings is None:
        _settings = _load_settings_from_env()
    return _settings


def clear_settings_cache() -> None:
    """Clear the settings cache. Useful for testing."""
    global _settings
    _settings = None


def validate_settings() -> tuple[bool, list[str]]:
    """
    Validate that all required settings are configured.

    Returns:
        Tuple of (is_valid, list of error messages).
    """
    errors = []

    # Check required environment variables
    required_vars = [
        ("DAKB_INTERNAL_SECRET", "Internal secret for embedding service"),
        ("DAKB_JWT_SECRET", "JWT secret for token signing"),
    ]

    for var_name, description in required_vars:
        if not os.getenv(var_name):
            errors.append(f"Missing required environment variable: {var_name} ({description})")

    # Validate secrets are strong enough
    internal_secret = os.getenv("DAKB_INTERNAL_SECRET", "")
    if internal_secret and len(internal_secret) < 32:
        errors.append("DAKB_INTERNAL_SECRET must be at least 32 characters")

    jwt_secret = os.getenv("DAKB_JWT_SECRET", "")
    if jwt_secret and len(jwt_secret) < 32:
        errors.append("DAKB_JWT_SECRET must be at least 32 characters")

    return len(errors) == 0, errors
