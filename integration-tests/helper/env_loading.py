"""Shared `.env` loading for pytest (root conftest) and standalone tools
(the FRAMES benchmark CLI), so both resolve credentials the same way."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv


def load_test_env(base_dir: Path) -> None:
    """
    Load .env first (typically only PIPESHUB_TEST_ENV=local or prod).
    Then load the matching env file so credentials stay out of .env:
    - PIPESHUB_TEST_ENV=local  -> .env.local
    - PIPESHUB_TEST_ENV=prod   -> .env.prod
    """
    env_path = base_dir / ".env"
    if env_path.exists():
        load_dotenv(dotenv_path=env_path, override=True)

    test_env = os.getenv("PIPESHUB_TEST_ENV", "").strip().lower()
    if test_env == "local":
        local_env = base_dir / ".env.local"
        if local_env.exists():
            load_dotenv(dotenv_path=local_env, override=True)
        os.environ.pop("PIPESHUB_USER_BEARER_TOKEN", None)
    elif test_env == "prod":
        prod_env = base_dir / ".env.prod"
        if prod_env.exists():
            load_dotenv(dotenv_path=prod_env, override=True)

    refresh_tokens_env = base_dir / ".env.refresh_tokens"
    if refresh_tokens_env.exists():
        load_dotenv(dotenv_path=refresh_tokens_env, override=True)
