"""Where the tests find the integration stack's published ports when a run moves them.

The QA engine calls env(port, environ) after it remaps a managed stack's host ports.
port(default) gives the host port that now serves the CI file's default; environ is the
test process's environment. It returns the settings to override. A setting that points
somewhere other than this host (a remote share, a hosted endpoint) is left alone.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping

_LOCAL = re.compile(r"(?<=[/@])(localhost|127\.0\.0\.1)(?::\d+)?|^(localhost|127\.0\.0\.1)(?::\d+)?$")

# Settings that hold a URL or address: (published port, default when unset; None leaves it unset).
URLS: dict[str, tuple[int, str | None]] = {
    "PIPESHUB_API_URL": (3000, "http://localhost:3000/api/v1"),
    "PIPESHUB_CONNECTOR_URL": (8088, "http://localhost:8088"),
    "TEST_MONGO_URI": (27017, "mongodb://admin:password@localhost:27017/?authSource=admin"),
    "TEST_NEO4J_URI": (7687, None),
    "KAFKA_BOOTSTRAP_SERVERS": (29092, "localhost:29092"),
    "MAILPIT_URL": (8025, "http://localhost:8025"),
    "MINIO_TEST_ENDPOINT": (9000, "http://localhost:9000"),
    "MINIO_TEST_CONSOLE_URL": (9001, "http://localhost:9001"),
    "WEB_FIXTURES_TEST_URL": (8089, "http://localhost:8089"),
    "MCP_FIXTURE_TEST_URL": (8097, "http://localhost:8097"),
    "OPENAPI_FIXTURE_TEST_URL": (8098, "http://localhost:8098"),
    "NEXTCLOUD_TEST_URL": (8090, "http://localhost:8090"),
    "BOOKSTACK_TEST_URL": (8094, "http://localhost:8094"),
    "ZAMMAD_TEST_URL": (8096, "http://localhost:8096"),
    "TEST_OLLAMA_ENDPOINT": (11434, None),
    "CONFLUENCE_TEST_BASE_URL": (8101, None),
    "JIRA_TEST_BASE_URL": (8102, None),
    "NOTION_API_BASE_URL": (8103, None),
    "NOTION_TEST_RESET_URL": (8103, None),
    "GMAIL_SIM_URL": (8106, None),
}

# Settings that hold only a port: (published port, the setting that names its host).
PORTS: dict[str, tuple[int, str]] = {
    "REDIS_PORT": (6379, "REDIS_HOST"),
    "QDRANT_PORT": (6333, "QDRANT_HOST"),
    "POSTGRES_TEST_PORT": (5433, "POSTGRES_TEST_HOST"),
    "MARIADB_TEST_PORT": (3307, "MARIADB_TEST_HOST"),
    "BOOKSTACK_DB_PORT": (3308, "BOOKSTACK_DB_HOST"),
    "SMB_PORT": (1445, "SMB_HOST"),
}


def env(port: Callable[[int], int], environ: Mapping[str, str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for name, (published, default) in URLS.items():
        value = environ.get(name) or default
        if value and _LOCAL.search(value):
            out[name] = _LOCAL.sub(lambda m: f"{m.group(1) or m.group(2)}:{port(published)}", value, count=1)
    for name, (published, host) in PORTS.items():
        if environ.get(host, "localhost") in ("localhost", "127.0.0.1"):
            out[name] = str(port(published))
    return out
