"""Centralized configuration constants for integration tests.

Reads from environment variables with sensible defaults for local development.
"""

from __future__ import annotations

import os


def _get_env(key: str, default: str = "") -> str:
    return os.getenv(key, default).strip()


# ------------------------------------------------------------------ #
# MongoDB
# ------------------------------------------------------------------ #
MONGO_URI: str = _get_env("TEST_MONGO_URI", "mongodb://admin:password@localhost:27017/?authSource=admin")
MONGO_DB_NAME: str = _get_env("TEST_MONGO_DB_NAME", "es")

# ------------------------------------------------------------------ #
# Test user
# ------------------------------------------------------------------ #
# Password used when seeding userCredentials via direct MongoDB write.
# Must satisfy the backend's password policy (min 8 chars, upper+lower+digit+special).
TEST_USER_PASSWORD: str = "TestPass123!"
