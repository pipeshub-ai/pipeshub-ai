"""
Test configuration settings.

This module manages test environment configuration, loading from environment
variables and providing sensible defaults for different test scenarios.
"""

import os
from enum import Enum
from pathlib import Path
from typing import Dict, Optional

from pydantic import BaseModel, Field, field_validator  # type: ignore


class Environment(str, Enum):
    """Test environment types."""
    LOCAL = "local"
    DEV = "dev"
    STAGING = "staging"
    CI = "ci"


class APIConfig(BaseModel):
    """API configuration for tests."""
    
    base_url: str = Field(default="http://localhost:8000", description="Base API URL")
    timeout: int = Field(default=30, description="Request timeout in seconds")
    verify_ssl: bool = Field(default=False, description="Verify SSL certificates")
    max_retries: int = Field(default=3, description="Maximum number of retries")
    retry_delay: float = Field(default=1.0, description="Delay between retries in seconds")
    
    @field_validator("base_url")
    @classmethod
    def validate_base_url(cls, v: str) -> str:
        """Ensure base URL doesn't end with trailing slash."""
        return v.rstrip("/")


class DatabaseConfig(BaseModel):
    """Database configuration for tests."""
    
    host: str = Field(default="localhost", description="Database host")
    port: int = Field(default=5432, description="Database port")
    name: str = Field(default="pipeshub_test", description="Database name")
    user: str = Field(default="postgres", description="Database user")
    password: str = Field(default="postgres", description="Database password")
    
    @property
    def connection_string(self) -> str:
        """Get database connection string."""
        return f"postgresql://{self.user}:{self.password}@{self.host}:{self.port}/{self.name}"


class RedisConfig(BaseModel):
    """Redis configuration for tests."""
    
    host: str = Field(default="localhost", description="Redis host")
    port: int = Field(default=6379, description="Redis port")
    db: int = Field(default=0, description="Redis database number")
    password: Optional[str] = Field(default=None, description="Redis password")
    
    @property
    def connection_string(self) -> str:
        """Get Redis connection string."""
        if self.password:
            return f"redis://:{self.password}@{self.host}:{self.port}/{self.db}"
        return f"redis://{self.host}:{self.port}/{self.db}"


class AuthConfig(BaseModel):
    """Authentication configuration for tests."""
    
    admin_username: str = Field(default="admin", description="Admin username")
    admin_password: str = Field(default="admin123", description="Admin password")
    test_username: str = Field(default="testuser", description="Test user username")
    test_password: str = Field(default="testpass123", description="Test user password")
    jwt_secret: str = Field(default="test-secret-key", description="JWT secret key")
    token_expiry: int = Field(default=3600, description="Token expiry in seconds")


class TestDataConfig(BaseModel):
    """Test data configuration."""
    
    cleanup_after_tests: bool = Field(default=True, description="Clean up test data after tests")
    use_fixtures: bool = Field(default=True, description="Use fixture data")
    seed_database: bool = Field(default=False, description="Seed database with test data")
    fixtures_path: Path = Field(default=Path("tests/fixtures/data"), description="Path to fixture files")


class LoggingConfig(BaseModel):
    """Logging configuration for tests."""
    
    level: str = Field(default="DEBUG", description="Logging level")
    format: str = Field(
        default="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        description="Log format"
    )
    log_to_file: bool = Field(default=True, description="Log to file")
    log_file: Path = Field(default=Path("tests/logs/test.log"), description="Log file path")
    log_requests: bool = Field(default=True, description="Log HTTP requests")
    log_responses: bool = Field(default=True, description="Log HTTP responses")


class TestSettings(BaseModel):
    """
    Main test settings configuration.
    
    This class aggregates all test configurations and loads them from
    environment variables or uses defaults.
    """
    
    environment: Environment = Field(
        default=Environment.LOCAL,
        description="Test environment"
    )
    
    # Component configurations
    api: APIConfig = Field(default_factory=APIConfig)
    database: DatabaseConfig = Field(default_factory=DatabaseConfig)
    redis: RedisConfig = Field(default_factory=RedisConfig)
    auth: AuthConfig = Field(default_factory=AuthConfig)
    test_data: TestDataConfig = Field(default_factory=TestDataConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)
    
    # General test settings
    parallel_tests: bool = Field(default=False, description="Run tests in parallel")
    workers: int = Field(default=4, description="Number of parallel workers")
    fail_fast: bool = Field(default=False, description="Stop on first failure")
    verbose: bool = Field(default=True, description="Verbose output")
    
    # Feature flags for conditional test execution
    skip_slow_tests: bool = Field(default=False, description="Skip slow tests")
    skip_external_tests: bool = Field(default=False, description="Skip external API tests")
    skip_integration_tests: bool = Field(default=False, description="Skip integration tests")
    
    @classmethod
    def from_env(cls) -> "TestSettings":
        """
        Load settings from environment variables.
        
        Returns:
            TestSettings instance with values from environment
        """
        return cls(
            environment=Environment(os.getenv("TEST_ENV", "local")),
            api=APIConfig(
                base_url=os.getenv("API_BASE_URL", "http://localhost:8000"),
                timeout=int(os.getenv("API_TIMEOUT", "30")),
                verify_ssl=os.getenv("VERIFY_SSL", "false").lower() == "true",
                max_retries=int(os.getenv("MAX_RETRIES", "3")),
                retry_delay=float(os.getenv("RETRY_DELAY", "1.0")),
            ),
            database=DatabaseConfig(
                host=os.getenv("DB_HOST", "localhost"),
                port=int(os.getenv("DB_PORT", "5432")),
                name=os.getenv("DB_NAME", "pipeshub_test"),
                user=os.getenv("DB_USER", "postgres"),
                password=os.getenv("DB_PASSWORD", "postgres"),
            ),
            redis=RedisConfig(
                host=os.getenv("REDIS_HOST", "localhost"),
                port=int(os.getenv("REDIS_PORT", "6379")),
                db=int(os.getenv("REDIS_DB", "0")),
                password=os.getenv("REDIS_PASSWORD"),
            ),
            auth=AuthConfig(
                admin_username=os.getenv("ADMIN_USERNAME", "admin"),
                admin_password=os.getenv("ADMIN_PASSWORD", "admin123"),
                test_username=os.getenv("TEST_USERNAME", "testuser"),
                test_password=os.getenv("TEST_PASSWORD", "testpass123"),
                jwt_secret=os.getenv("JWT_SECRET", "test-secret-key"),
                token_expiry=int(os.getenv("TOKEN_EXPIRY", "3600")),
            ),
            test_data=TestDataConfig(
                cleanup_after_tests=os.getenv("CLEANUP_AFTER_TESTS", "true").lower() == "true",
                use_fixtures=os.getenv("USE_FIXTURES", "true").lower() == "true",
                seed_database=os.getenv("SEED_DATABASE", "false").lower() == "true",
            ),
            logging=LoggingConfig(
                level=os.getenv("LOG_LEVEL", "DEBUG"),
                log_to_file=os.getenv("LOG_TO_FILE", "true").lower() == "true",
                log_requests=os.getenv("LOG_REQUESTS", "true").lower() == "true",
                log_responses=os.getenv("LOG_RESPONSES", "true").lower() == "true",
            ),
            parallel_tests=os.getenv("PARALLEL_TESTS", "false").lower() == "true",
            workers=int(os.getenv("WORKERS", "4")),
            fail_fast=os.getenv("FAIL_FAST", "false").lower() == "true",
            verbose=os.getenv("VERBOSE", "true").lower() == "true",
            skip_slow_tests=os.getenv("SKIP_SLOW_TESTS", "false").lower() == "true",
            skip_external_tests=os.getenv("SKIP_EXTERNAL_TESTS", "false").lower() == "true",
            skip_integration_tests=os.getenv("SKIP_INTEGRATION_TESTS", "false").lower() == "true",
        )
    
    def to_dict(self) -> Dict:
        """
        Convert settings to dictionary.
        
        Returns:
            Dictionary representation of settings
        """
        return self.model_dump()
    
    def get_api_url(self, endpoint: str = "") -> str:
        """
        Get full API URL for an endpoint.
        
        Args:
            endpoint: API endpoint path
            
        Returns:
            Full URL
        """
        return f"{self.api.base_url}/{endpoint.lstrip('/')}"


# Global settings instance
_settings: Optional[TestSettings] = None


def get_settings() -> TestSettings:
    """
    Get test settings singleton.
    
    Returns:
        TestSettings instance
    """
    global _settings
    if _settings is None:
        _settings = TestSettings.from_env()
    return _settings


def reset_settings() -> None:
    """Reset settings singleton (useful for testing)."""
    global _settings
    _settings = None

