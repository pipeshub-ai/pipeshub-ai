"""Custom errors for configuration management."""


class ConfigurationError(Exception):
    """Base class for configuration-related errors."""

    def __init__(self, message: str, config_key: str = None) -> None:
        self.message = message
        self.config_key = config_key
        super().__init__(self.message)

    def to_dict(self) -> dict:
        """Convert error to dictionary for API responses."""
        return {
            "error": "ConfigurationError",
            "message": self.message,
            "config_key": self.config_key,
        }


class ConfigurationNotFoundError(ConfigurationError):
    """Raised when required configuration is not found in the KV store."""

    def __init__(self, config_key: str, suggestion: str = None) -> None:
        self.suggestion = suggestion or "Please reconfigure this setting in the admin panel."
        message = (
            f"Configuration not found for key '{config_key}'. "
            f"The configuration may have been lost during migration or was never set. "
            f"{self.suggestion}"
        )
        super().__init__(message, config_key)

    def to_dict(self) -> dict:
        """Convert error to dictionary for API responses."""
        return {
            "error": "ConfigurationNotFoundError",
            "message": self.message,
            "config_key": self.config_key,
            "suggestion": self.suggestion,
            "action_required": True,
        }


class ConfigurationMigrationError(ConfigurationError):
    """Raised when migration from etcd to Redis fails or is incomplete."""

    def __init__(self, message: str, failed_keys: list = None) -> None:
        self.failed_keys = failed_keys or []
        full_message = (
            f"Configuration migration error: {message}. "
            "Please ensure etcd is running and perform migration, or reconfigure the application."
        )
        super().__init__(full_message)

    def to_dict(self) -> dict:
        """Convert error to dictionary for API responses."""
        return {
            "error": "ConfigurationMigrationError",
            "message": self.message,
            "failed_keys": self.failed_keys,
            "action_required": True,
        }


class ConfigurationInvalidError(ConfigurationError):
    """Raised when configuration value is invalid or corrupted."""

    def __init__(self, config_key: str, reason: str = None) -> None:
        self.reason = reason or "The configuration value is invalid or corrupted."
        message = (
            f"Invalid configuration for key '{config_key}'. {self.reason} "
            "Please reconfigure this setting."
        )
        super().__init__(message, config_key)

    def to_dict(self) -> dict:
        """Convert error to dictionary for API responses."""
        return {
            "error": "ConfigurationInvalidError",
            "message": self.message,
            "config_key": self.config_key,
            "reason": self.reason,
            "action_required": True,
        }
