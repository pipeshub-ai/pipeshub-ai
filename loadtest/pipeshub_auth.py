"""Password login for the load test, so runs stop depending on a pasted token.

PipesHub authenticates in two calls: `initAuth` hands back a single-use
`x-session-token`, and `authenticate` exchanges it plus the password for a
24h access token — long enough that a run never needs to refresh.

Shared by `locustfile_play.py` and `seed_users.py`; mirrors
`integration-tests/helper/local_auth.py`.
"""

from __future__ import annotations

import os

import requests

DEFAULT_TIMEOUT = 30


class AuthError(RuntimeError):
    pass


def login(base_url: str, email: str, password: str, timeout: int = DEFAULT_TIMEOUT) -> str:
    """Return an access token for `email`, or raise AuthError."""
    base_url = base_url.rstrip("/")
    session_token = _init_auth(base_url, email, timeout)
    return _authenticate(base_url, session_token, email, password, timeout)


def _init_auth(base_url: str, email: str, timeout: int) -> str:
    resp = requests.post(
        f"{base_url}/api/v1/userAccount/initAuth",
        json={"email": email},
        timeout=timeout,
    )
    if resp.status_code >= 400:
        raise AuthError(f"initAuth failed for {email}: HTTP {resp.status_code}: {resp.text[:200]}")
    session_token = resp.headers.get("x-session-token")
    if not session_token:
        raise AuthError(f"initAuth returned no x-session-token for {email}")
    return session_token


def _authenticate(base_url: str, session_token: str, email: str, password: str, timeout: int) -> str:
    resp = requests.post(
        f"{base_url}/api/v1/userAccount/authenticate",
        headers={"x-session-token": session_token},
        json={
            "method": "password",
            "credentials": {"password": password},
            "email": email,
        },
        timeout=timeout,
    )
    if resp.status_code >= 400:
        # Five wrong passwords block the account for 24h, so surface this loudly
        # rather than letting a retry loop burn the remaining attempts.
        raise AuthError(
            f"authenticate failed for {email}: HTTP {resp.status_code}: {resp.text[:200]}"
        )
    try:
        data = resp.json()
    except ValueError:
        raise AuthError(f"authenticate returned non-JSON for {email}") from None
    if not isinstance(data, dict):
        raise AuthError(
            f"authenticate returned non-object JSON for {email}: {type(data).__name__}"
        )
    token = data.get("accessToken")
    if not isinstance(token, str) or not token:
        # A multi-step org config answers with `nextStep` instead of a token.
        raise AuthError(
            f"authenticate returned no accessToken for {email} (keys: {sorted(data)})"
        )
    return token


def parse_credentials(users_env: str = "", emails_env: str = "", shared_password: str = "") -> list[tuple[str, str]]:
    """Read credentials from the two supported env formats.

    `PIPESHUB_USERS` is `email:password` pairs, comma separated. `PIPESHUB_EMAILS`
    is a comma-separated list that pairs each address with `PIPESHUB_PASSWORD`.
    """
    pairs: list[tuple[str, str]] = []

    for entry in (users_env or "").split(","):
        entry = entry.strip()
        if not entry:
            continue
        if ":" not in entry:
            raise AuthError(f"PIPESHUB_USERS entry {entry!r} is not 'email:password'")
        email, password = entry.split(":", 1)
        if not email.strip() or not password.strip():
            raise AuthError(f"PIPESHUB_USERS entry {entry!r} has an empty field")
        pairs.append((email.strip(), password.strip()))

    if not pairs and emails_env:
        if not shared_password:
            raise AuthError("PIPESHUB_EMAILS is set but PIPESHUB_PASSWORD is not")
        for email in emails_env.split(","):
            email = email.strip()
            if email:
                pairs.append((email, shared_password))

    return pairs


def credentials_from_env() -> list[tuple[str, str]]:
    return parse_credentials(
        users_env=os.environ.get("PIPESHUB_USERS", ""),
        emails_env=os.environ.get("PIPESHUB_EMAILS", ""),
        shared_password=os.environ.get("PIPESHUB_PASSWORD", ""),
    )


def resolve_tokens(base_url: str, credentials: list[tuple[str, str]]) -> list[str]:
    """Log every credential in. Raises on the first failure — a run with fewer
    identities than intended would quietly measure the wrong thing."""
    tokens = []
    for email, password in credentials:
        tokens.append(login(base_url, email, password))
    return tokens
