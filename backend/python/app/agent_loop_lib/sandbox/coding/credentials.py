"""Credentials an operator configures for the legacy code sandbox: injected by the egress proxy, never held (OWD-1).

A registry URL (``SANDBOX_PIP_INDEX_URL``, ``SANDBOX_NPM_REGISTRY``) or a database URL (``SANDBOX_PG_URL``) may carry
a user name and password. A sandbox never receives the password, in any form: not in its command line, environment
or files (SEC2 O-3, O-4 first moved it out of argv and env into a file; WP-06 takes it out of the sandbox).

- A registry install runs on the broker with an egress allowlist of the registry's host. The sandbox's pip or npm
  sends a placeholder as the Basic password; the proxy, holding the credential sealed in the route, puts the real
  one on the request (inside TLS it ends with its own authority, which the install trusts).
- A PostgreSQL run reaches the database through the proxy under the name ``DATABASE_HOST``; the proxy signs in, so
  psql is never asked for a password.

Where no proxy can inject (``SANDBOX_MODE=docker``), a credentialed registry or database is refused with the reason.
A URL without a password is used exactly as before.
"""

from __future__ import annotations

import base64
import json
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Final
from urllib.parse import SplitResult, parse_qsl, quote, unquote, urlsplit, urlunsplit

from harness_core.sandbox.egress.policy import POSTGRES_PORT
from harness_core.sandbox.egress.trust import proxy_env, trust_env, trusted_command
from harness_core.sandbox.types import (
    CredentialInjection,
    EgressDestination,
    EgressPolicy,
    TokenRoute,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

    from harness_core.sandbox.egress.sealed import CredentialSealer

__all__ = [
    "DATABASE_HOST",
    "PGPASS_ENV",
    "PLACEHOLDER_USER",
    "PSQL_FORGET",
    "Credential",
    "Database",
    "ProxiedDatabase",
    "ProxiedRegistry",
    "Registry",
    "credentialed_needs_broker",
    "database",
    "pgpass",
    "proxied_database",
    "proxied_registry",
    "registry",
]

# The name a sandbox's PostgreSQL client connects to: the proxy, under the broker's hosts entry for it.
DATABASE_HOST: Final = "pxh-database"
# The Basic user name a sandbox sends with its placeholder; the proxy replaces the whole credential.
PLACEHOLDER_USER: Final = "pxh"
# Local mode only (no sandbox: psql runs in this service): psql reads the password file when it connects, before the
# script; this line removes it before user SQL, whose `\!` would otherwise find it.
PSQL_FORGET: Final = '\\! rm -f "$PGPASSFILE"\n'
PGPASS_ENV: Final = "PGPASSFILE"


@dataclass(frozen=True, slots=True)
class Credential:
    user: str
    password: str = field(repr=False)


@dataclass(frozen=True, slots=True)
class Registry:
    """``url`` without its secret; ``credential`` is what the proxy injects, ``None`` when the URL had none."""

    url: str
    credential: Credential | None = None


@dataclass(frozen=True, slots=True)
class Database:
    """A libpq connection string split: ``url`` without the password, the parts the proxy needs, the password."""

    url: str
    host: str = ""
    port: int = 5432
    dbname: str = ""
    user: str = ""
    sslmode: str = "prefer"
    password: str | None = field(default=None, repr=False)


def _without_userinfo(split: SplitResult, *, keep_user: bool) -> str:
    host = split.hostname or ""
    if ":" in host:
        host = f"[{host}]"
    netloc = host + (f":{split.port}" if split.port is not None else "")
    if keep_user and split.username:
        netloc = f"{split.username}@{netloc}"
    return urlunsplit((split.scheme, netloc, split.path, split.query, split.fragment))


def registry(url: str) -> Registry:
    split = urlsplit(url)
    if split.username is None and split.password is None:
        return Registry(url)
    credential = Credential(unquote(split.username or ""), unquote(split.password or ""))
    return Registry(_without_userinfo(split, keep_user=False), credential)


_DSN_PAIR = re.compile(r"\s*([A-Za-z_]+)\s*=\s*('(?:[^'\\]|\\.)*'|\S*)")


def _dsn(dsn: str) -> Database:
    pairs: list[tuple[str, str]] = []
    values: dict[str, str] = {}
    position = 0
    while position < len(dsn.rstrip()):
        match = _DSN_PAIR.match(dsn, position)
        if match is None:
            return Database(dsn)
        key, raw = match.group(1), match.group(2)
        position = match.end()
        values[key] = re.sub(r"\\(.)", r"\1", raw[1:-1]) if raw.startswith("'") else raw
        if key != "password":
            pairs.append((key, raw))
    port = values.get("port", "5432")
    return Database(
        " ".join(f"{k}={v}" for k, v in pairs) if "password" in values else dsn,
        host=values.get("host", ""),
        port=int(port) if port.isdigit() else 5432,
        dbname=values.get("dbname", ""),
        user=values.get("user", ""),
        sslmode=values.get("sslmode", "prefer"),
        password=values.get("password"),
    )


def database(url: str) -> Database:
    """A libpq connection string (URL or ``key=value`` form) without its password, and its parts."""
    split = urlsplit(url)
    if split.scheme not in ("postgres", "postgresql"):
        return _dsn(url)
    query = parse_qsl(split.query, keep_blank_values=True)
    in_query = [v for k, v in query if k == "password"]
    password = unquote(split.password) if split.password is not None else (in_query[-1] if in_query else None)
    options = dict(query)
    kept = urlencode_pairs([(k, v) for k, v in query if k != "password"])
    public = _without_userinfo(split._replace(query=kept), keep_user=True) if password is not None else url
    return Database(
        public,
        host=split.hostname or options.get("host", ""),
        port=split.port or 5432,
        dbname=unquote(split.path.lstrip("/")) or options.get("dbname", ""),
        user=unquote(split.username or "") or options.get("user", ""),
        sslmode=options.get("sslmode", "prefer"),
        password=password,
    )


def urlencode_pairs(pairs: list[tuple[str, str]]) -> str:
    return "&".join(f"{quote(k, safe='')}={quote(v, safe='')}" for k, v in pairs)


def pgpass(password: str) -> bytes:
    """A libpq password file entry for whatever the connection names (local mode only)."""
    field_ = password.replace("\\", "\\\\").replace(":", "\\:")
    return f"*:*:*:*:{field_}\n".encode()


@dataclass(frozen=True, slots=True)
class ProxiedRegistry:
    """What an install sandbox gets for a credentialed registry: its allowlist, environment and the URL it uses."""

    egress: EgressPolicy
    env: Mapping[str, str]
    url: str
    placeholder: str = field(repr=False)

    def npm_auth_flag(self) -> str:
        """npm's scoped ``_auth`` for this registry, the placeholder as the password."""
        split = urlsplit(self.url)
        scope = "//" + split.netloc + (split.path.rstrip("/") or "") + "/"
        token = base64.b64encode(f"{PLACEHOLDER_USER}:{self.placeholder}".encode()).decode()
        return f"--{scope}:_auth={token}"

    def pip_url(self) -> str:
        split = urlsplit(self.url)
        return urlunsplit(split._replace(netloc=f"{PLACEHOLDER_USER}:{self.placeholder}@{split.netloc}"))

    def command(self, command: list[str]) -> list[str]:
        return trusted_command(command)


def proxied_registry(found: Registry, sealer: CredentialSealer, placeholder: str) -> ProxiedRegistry:
    if found.credential is None:
        raise ValueError("a registry without a credential needs no proxy")
    split = urlsplit(found.url)
    host = split.hostname or ""
    port = split.port or (443 if split.scheme == "https" else 80)
    sealed = sealer.seal(placeholder, f"{found.credential.user}:{found.credential.password}")
    egress = EgressPolicy(
        destinations=(EgressDestination(host, (port,)),),
        token_routes=(TokenRoute(placeholder, host, sealed, CredentialInjection.BASIC),),
    )
    return ProxiedRegistry(egress, {**proxy_env(), **trust_env()}, found.url, placeholder)


@dataclass(frozen=True, slots=True)
class ProxiedDatabase:
    """What a PostgreSQL run's sandbox gets: its allowlist (the proxy's database route) and the URL psql uses."""

    egress: EgressPolicy
    url: str


def proxied_database(found: Database, sealer: CredentialSealer, placeholder: str) -> ProxiedDatabase:
    if not found.host or "," in found.host or found.host.startswith("/"):
        raise ValueError("the database URL must name one TCP host for the egress proxy to reach")
    host = f"[{found.host}]" if ":" in found.host else found.host
    user = f"{quote(found.user, safe='')}@" if found.user else ""
    upstream = f"postgres://{user}{host}:{found.port}/?sslmode={quote(found.sslmode, safe='')}"
    sealed = sealer.seal(placeholder, json.dumps({"user": found.user, "password": found.password or ""}))
    egress = EgressPolicy(
        destinations=(EgressDestination(DATABASE_HOST, (POSTGRES_PORT,), upstream=upstream),),
        token_routes=(TokenRoute(placeholder, DATABASE_HOST, sealed, CredentialInjection.POSTGRES),),
    )
    dbname = quote(found.dbname, safe="")
    # The sandbox's leg ends at the proxy, on the sandbox's own network; the proxy's leg keeps the operator's sslmode.
    return ProxiedDatabase(egress, f"postgresql://{user}{DATABASE_HOST}:{POSTGRES_PORT}/{dbname}?sslmode=disable")


def credentialed_needs_broker(what: str) -> str:
    return (
        f"The {what} is configured with a password, which never enters a sandbox: the egress proxy adds it on the "
        "way out. That needs SANDBOX_MODE=broker with the egress proxy; this sandbox runs without one."
    )
