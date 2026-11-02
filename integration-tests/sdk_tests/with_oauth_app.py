#!/usr/bin/env python3
"""Run a command with a new OAuth app of the provisioned org in its environment.

The client secret goes only to the command's environment, never to a file or the log.
The command also gets a session JWT (PIPESHUB_SESSION_TOKEN) and keeps the admin login:
the OAuth app and personal access token routes accept only an interactive session, and
the sign-in tests sign in with the login.

Usage: with_oauth_app.py <command> [arg ...]

Env vars:
    PIPESHUB_BASE_URL            default http://localhost:3000
    PIPESHUB_TEST_USER_EMAIL     required
    PIPESHUB_TEST_USER_PASSWORD  required
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "helper"))

from local_auth import obtain_local_oauth_credentials, obtain_user_session_token


def main(command: list[str]) -> None:
    if not command:
        raise SystemExit(__doc__)
    base_url = (os.getenv("PIPESHUB_BASE_URL") or "http://localhost:3000").rstrip("/")
    client_id, client_secret = obtain_local_oauth_credentials(base_url)
    env = dict(os.environ)
    env.update(
        PIPESHUB_API_URL=f"{base_url}/api/v1",
        PIPESHUB_TOKEN_URL=f"{base_url}/api/v1/oauth2/token",
        PIPESHUB_CLIENT_ID=client_id,
        PIPESHUB_CLIENT_SECRET=client_secret,
        PIPESHUB_SESSION_TOKEN=obtain_user_session_token(base_url),
    )
    os.execvpe(command[0], command, env)


if __name__ == "__main__":
    main(sys.argv[1:])
