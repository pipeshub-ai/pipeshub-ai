
import os
from typing import Dict, Optional

from app.services.featureflag.interfaces.config import IConfigProvider
from app.utils.logger import create_logger

logger = create_logger(__name__) # TODO fix logger


class EnvFileProvider(IConfigProvider):
    """
    Provider that reads feature flags from .env file
    Implements Single Responsibility Principle - only handles .env file reading
    """

    def __init__(self, env_file_path: str) -> None:
        self.env_file_path = env_file_path
        self._flags: Dict[str, bool] = {}
        self._load_env_file()

    def _load_env_file(self) -> None:
        """Load and parse .env file"""
        if not os.path.exists(self.env_file_path):
            logger.warning(f"Warning: .env file not found at {self.env_file_path}")
            return

        try:
            with open(self.env_file_path, 'r') as f:
                for line in f:
                    line = line.strip()

                    # Skip empty lines and comments
                    if not line or line.startswith('#'):
                        continue

                    # Parse key=value pairs
                    if '=' in line:
                        key, value = line.split('=', 1)
                        key = key.strip().upper()  # Normalize to uppercase
                        value = value.strip()

                        # Store boolean value
                        self._flags[key] = self._parse_bool(value)
        except IOError as e:
            logger.error(f"Error loading .env file: {e}")

    def _parse_bool(self, value: str) -> bool:
        """Parse string value to boolean"""
        return value.lower() in ('true', '1', 'yes', 'on', 'enabled')

    def get_flag_value(self, flag_name: str) -> Optional[bool]:
        """Get flag value by name"""
        return self._flags.get(flag_name.upper())

    def refresh(self) -> None:
        """Reload .env file"""
        self._flags.clear()
        self._load_env_file()
