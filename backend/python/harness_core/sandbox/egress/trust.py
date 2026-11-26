"""What a program in a sandbox needs to reach a credentialed host through the egress proxy (OWD-1, WP-06).

Its HTTP(S) goes to the proxy (``proxy_env``); for HTTPS to a host the proxy puts a credential on, the proxy ends the
TLS with its own authority, so the program must trust that certificate too. ``TRUST_SCRIPT`` (standard-library
Python, run first in the sandbox) reads the certificate from the proxy and writes ``TRUST_BUNDLE``: the image's own
authorities plus the proxy's. ``trust_env`` points the usual TLS stacks at it.
"""

from __future__ import annotations

from typing import Final

from harness_core.sandbox.egress.types import (
    EGRESS_CA_PATH,
    EGRESS_PROXY_HOST,
    EGRESS_PROXY_PORT,
)

PROXY_URL: Final = f"http://{EGRESS_PROXY_HOST}:{EGRESS_PROXY_PORT}"
TRUST_DIR: Final = "/tmp/pxh-egress"
TRUST_BUNDLE: Final = f"{TRUST_DIR}/ca-bundle.pem"
TRUST_SCRIPT_ENV: Final = "PXH_EGRESS_TRUST"
_SYSTEM_BUNDLES: Final = (
    "/etc/ssl/certs/ca-certificates.crt",
    "/etc/pki/tls/certs/ca-bundle.crt",
    "/etc/ssl/cert.pem",
)
TRUST_SCRIPT: Final = (
    "import os, urllib.request as u\n"
    "opener = u.build_opener(u.ProxyHandler({}))\n"
    f"ca = opener.open('{PROXY_URL}{EGRESS_CA_PATH}', timeout=20).read()\n"
    "if b'BEGIN CERTIFICATE' not in ca:\n"
    "    raise SystemExit('the egress proxy offers no certificate')\n"
    "own = b''\n"
    f"for path in {_SYSTEM_BUNDLES!r}:\n"
    "    if os.path.exists(path):\n"
    "        own = open(path, 'rb').read()\n"
    "        break\n"
    f"os.makedirs('{TRUST_DIR}', exist_ok=True)\n"
    f"open('{TRUST_BUNDLE}', 'wb').write(own + b'\\n' + ca)\n"
)


def proxy_env() -> dict[str, str]:
    return {
        "HTTP_PROXY": PROXY_URL,
        "http_proxy": PROXY_URL,
        "HTTPS_PROXY": PROXY_URL,
        "https_proxy": PROXY_URL,
        "NO_PROXY": "",
        "no_proxy": "",
        # Node 22's own fetch reads the proxy variables only when asked to.
        "NODE_USE_ENV_PROXY": "1",
    }


def trust_env() -> dict[str, str]:
    return {
        "SSL_CERT_FILE": TRUST_BUNDLE,
        "REQUESTS_CA_BUNDLE": TRUST_BUNDLE,
        "CURL_CA_BUNDLE": TRUST_BUNDLE,
        "PIP_CERT": TRUST_BUNDLE,
        "NODE_EXTRA_CA_CERTS": TRUST_BUNDLE,
        "npm_config_cafile": TRUST_BUNDLE,
        "GIT_SSL_CAINFO": TRUST_BUNDLE,
        TRUST_SCRIPT_ENV: TRUST_SCRIPT,
    }


def trusted_command(command: list[str]) -> list[str]:
    """``command`` (``sh -c SCRIPT ARG...``) that first writes the trust bundle, and fails if it cannot."""
    if len(command) < 3 or command[1] != "-c":
        raise ValueError("the trust step wraps an `sh -c` command")
    return [command[0], "-c", f'python3 -c "${TRUST_SCRIPT_ENV}" || exit $?; {command[2]}', *command[3:]]


__all__ = [
    "PROXY_URL",
    "TRUST_BUNDLE",
    "TRUST_DIR",
    "TRUST_SCRIPT",
    "TRUST_SCRIPT_ENV",
    "proxy_env",
    "trust_env",
    "trusted_command",
]
