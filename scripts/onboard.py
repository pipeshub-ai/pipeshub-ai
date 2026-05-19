#!/usr/bin/env python3
"""
PipesHub AI — Developer Onboarding Automation Script
=====================================================
Automates the full post-install onboarding flow:
  1. Create org + admin account (skipped if org already exists)
  2. Authenticate and obtain access token
  3. Configure LLM (AI model)
  4. Configure embedding model
  5. Configure SMTP
  6. Mark onboarding as "configured"

Usage:
  python scripts/onboard.py                          # uses onboarding.config.yml
  python scripts/onboard.py --config path/to/my.yml  # custom config file
  python scripts/onboard.py --dry-run                # print plan without making changes

Requirements: pip install requests pyyaml
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path
from typing import Any

try:
    import requests
    import yaml
except ImportError:
    print(
        "Missing dependencies. Run:  pip install requests pyyaml",
        file=sys.stderr,
    )
    sys.exit(1)


# ---------------------------------------------------------------------------
# Colour helpers (gracefully degrade on Windows without ANSI support)
# ---------------------------------------------------------------------------

RESET = "\033[0m"
BOLD = "\033[1m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
RED = "\033[31m"
CYAN = "\033[36m"
DIM = "\033[2m"


def _c(colour: str, text: str) -> str:
    return f"{colour}{text}{RESET}"


def info(msg: str) -> None:
    print(_c(CYAN, "  →") + f" {msg}")


def ok(msg: str) -> None:
    print(_c(GREEN, "  ✓") + f" {msg}")


def warn(msg: str) -> None:
    print(_c(YELLOW, "  ⚠") + f" {msg}")


def err(msg: str) -> None:
    print(_c(RED, "  ✗") + f" {msg}")


def section(title: str) -> None:
    print()
    print(_c(BOLD, f"── {title} {'─' * max(0, 55 - len(title))}"))


# ---------------------------------------------------------------------------
# Config loading & validation
# ---------------------------------------------------------------------------

DEFAULT_CONFIG_FILE = Path(__file__).parent / "onboarding.config.yml"

REQUIRED_ADMIN_KEYS = {"email", "password", "full_name"}


def load_config(path: Path) -> dict[str, Any]:
    if not path.exists():
        err(f"Config file not found: {path}")
        err("Copy scripts/onboarding.config.example.yml → scripts/onboarding.config.yml and fill in your values.")
        sys.exit(1)

    with path.open() as fh:
        cfg = yaml.safe_load(fh)

    if not isinstance(cfg, dict):
        err("Config file must be a YAML mapping at the top level.")
        sys.exit(1)

    missing = REQUIRED_ADMIN_KEYS - set((cfg.get("admin") or {}).keys())
    if missing:
        err(f"Config is missing required admin fields: {', '.join(sorted(missing))}")
        sys.exit(1)

    return cfg


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------

class APIError(Exception):
    def __init__(self, status: int, body: Any) -> None:
        self.status = status
        self.body = body
        super().__init__(f"HTTP {status}: {body}")


def _raise_for(resp: requests.Response) -> None:
    if not resp.ok:
        try:
            body = resp.json()
        except Exception:
            body = resp.text
        raise APIError(resp.status_code, body)


class PipesHubClient:
    def __init__(self, base_url: str, dry_run: bool = False) -> None:
        self.base = base_url.rstrip("/")
        self.dry_run = dry_run
        self._session = requests.Session()
        self._session.headers.update({"Content-Type": "application/json"})

    def _url(self, path: str) -> str:
        return f"{self.base}/api/v1{path}"

    def set_token(self, token: str) -> None:
        self._session.headers["Authorization"] = f"Bearer {token}"

    def get(self, path: str) -> Any:
        resp = self._session.get(self._url(path))
        _raise_for(resp)
        return resp.json()

    def post(self, path: str, body: dict[str, Any]) -> Any:
        if self.dry_run:
            info(f"[dry-run] POST {path}  body={body}")
            return {}
        resp = self._session.post(self._url(path), json=body)
        _raise_for(resp)
        try:
            return resp.json()
        except Exception:
            return {}

    def put(self, path: str, body: dict[str, Any]) -> Any:
        if self.dry_run:
            info(f"[dry-run] PUT  {path}  body={body}")
            return {}
        resp = self._session.put(self._url(path), json=body)
        _raise_for(resp)
        try:
            return resp.json()
        except Exception:
            return {}


# ---------------------------------------------------------------------------
# Step implementations
# ---------------------------------------------------------------------------

def wait_for_api(client: PipesHubClient, retries: int = 10, delay: float = 3.0) -> None:
    """Poll /api/v1/org/exists until the server is up."""
    for attempt in range(1, retries + 1):
        try:
            client._session.get(client._url("/org/exists"), timeout=5)
            return
        except requests.exceptions.ConnectionError:
            if attempt == retries:
                err("Server is not reachable. Is PipesHub running?")
                sys.exit(1)
            info(f"Waiting for server… attempt {attempt}/{retries}")
            time.sleep(delay)


def ensure_org(client: PipesHubClient, admin_cfg: dict[str, Any]) -> bool:
    """Create org + admin if none exists. Returns True if created."""
    resp = client._session.get(client._url("/org/exists"))
    data = resp.json() if resp.ok else {}
    if data.get("exists"):
        ok("Org already exists — skipping creation.")
        return False

    info("No org found — creating org and admin account…")
    account_type = admin_cfg.get("account_type", "individual")
    body: dict[str, Any] = {
        "accountType": account_type,
        "contactEmail": admin_cfg["email"],
        "adminFullName": admin_cfg["full_name"],
        "password": admin_cfg["password"],
    }
    if account_type == "business":
        body["registeredName"] = admin_cfg.get("org_name", admin_cfg["full_name"])
        if admin_cfg.get("org_short_name"):
            body["shortName"] = admin_cfg["org_short_name"]

    client.post("/org", body)
    ok(f"Org created (account_type={account_type}).")
    return True


def authenticate(client: PipesHubClient, admin_cfg: dict[str, Any]) -> str:
    """Run the two-step auth flow and return an access token."""
    info("Authenticating…")

    # Step 1 – initAuth to get session token
    init_resp = client._session.post(
        client._url("/userAccount/initAuth"),
        json={"email": admin_cfg["email"]},
    )
    _raise_for(init_resp)
    session_token = init_resp.headers.get("x-session-token", "")

    # Step 2 – authenticate with password
    auth_resp = client._session.post(
        client._url("/userAccount/authenticate"),
        json={
            "method": "password",
            "credentials": {"password": admin_cfg["password"]},
            "email": admin_cfg["email"],
        },
        headers={"x-session-token": session_token},
    )
    _raise_for(auth_resp)
    token_data = auth_resp.json()
    access_token = token_data.get("accessToken") or token_data.get("access_token")
    if not access_token:
        err(f"Unexpected auth response — no accessToken: {token_data}")
        sys.exit(1)

    client.set_token(access_token)
    ok("Authenticated successfully.")
    return access_token


def configure_llm(client: PipesHubClient, llm_cfg: dict[str, Any]) -> None:
    if llm_cfg.get("skip"):
        warn("LLM config: skip=true — skipping.")
        return

    # Check if already configured
    try:
        data = client.get("/configurationManager/ai-models/llm")
        if data.get("models"):
            ok("LLM already configured — skipping.")
            return
    except APIError:
        pass

    provider = llm_cfg.get("provider")
    if not provider:
        warn("LLM config: no provider specified — skipping.")
        return

    configuration: dict[str, Any] = {}
    for field in ("apiKey", "model", "endpoint", "deploymentName", "apiVersion", "modelFriendlyName"):
        snake = _to_snake(field)
        val = llm_cfg.get(snake) or llm_cfg.get(field)
        if val:
            configuration[field] = val

    body: dict[str, Any] = {
        "modelType": "llm",
        "provider": provider,
        "configuration": configuration,
        "isDefault": True,
        "isMultimodal": llm_cfg.get("is_multimodal", False),
        "isReasoning": llm_cfg.get("is_reasoning", False),
    }
    if llm_cfg.get("context_length"):
        body["contextLength"] = llm_cfg["context_length"]

    info(f"Configuring LLM — provider={provider}, model={configuration.get('model', '?')}")
    client.post("/configurationManager/ai-models/providers", body)
    ok("LLM configured.")


def configure_embedding(client: PipesHubClient, embed_cfg: dict[str, Any]) -> None:
    provider = embed_cfg.get("provider_type") or embed_cfg.get("providerType", "default")

    if embed_cfg.get("skip") or provider == "default":
        warn("Embedding config: using system default — skipping custom provider.")
        return

    # Check if already configured
    try:
        data = client.get("/configurationManager/ai-models/embedding")
        if data.get("models"):
            ok("Embedding model already configured — skipping.")
            return
    except APIError:
        pass

    configuration: dict[str, Any] = {}
    for field in ("apiKey", "model", "endpoint"):
        snake = _to_snake(field)
        val = embed_cfg.get(snake) or embed_cfg.get(field)
        if val:
            configuration[field] = val

    body: dict[str, Any] = {
        "modelType": "embedding",
        "provider": provider,
        "configuration": configuration,
        "isDefault": True,
        "isMultimodal": embed_cfg.get("is_multimodal", False),
    }

    info(f"Configuring embedding — provider={provider}, model={configuration.get('model', '?')}")
    client.post("/configurationManager/ai-models/providers", body)
    ok("Embedding model configured.")


def configure_smtp(client: PipesHubClient, smtp_cfg: dict[str, Any]) -> None:
    if smtp_cfg.get("skip"):
        warn("SMTP config: skip=true — skipping.")
        return

    # Check if already configured
    try:
        data = client.get("/configurationManager/smtpConfig")
        if data.get("host"):
            ok("SMTP already configured — skipping.")
            return
    except APIError:
        pass

    host = smtp_cfg.get("host")
    port = smtp_cfg.get("port")
    from_email = smtp_cfg.get("from_email")

    if not all([host, port, from_email]):
        warn("SMTP config: host/port/from_email are required — skipping.")
        return

    body: dict[str, Any] = {
        "host": host,
        "port": int(port),
        "fromEmail": from_email,
    }
    if smtp_cfg.get("username"):
        body["username"] = smtp_cfg["username"]
    if smtp_cfg.get("password"):
        body["password"] = smtp_cfg["password"]

    info(f"Configuring SMTP — host={host}:{port}, from={from_email}")
    client.post("/configurationManager/smtpConfig", body)
    ok("SMTP configured.")


def mark_onboarding_done(client: PipesHubClient) -> None:
    try:
        data = client.get("/org/onboarding-status")
        if data.get("status") == "configured":
            ok("Onboarding already marked as configured.")
            return
    except APIError:
        pass

    client.put("/org/onboarding-status", {"status": "configured"})
    ok("Onboarding status set to 'configured'.")


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------

def _to_snake(camel: str) -> str:
    """Convert camelCase → snake_case."""
    import re
    return re.sub(r"(?<!^)(?=[A-Z])", "_", camel).lower()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="PipesHub AI — automated developer onboarding"
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=DEFAULT_CONFIG_FILE,
        help="Path to YAML config file (default: scripts/onboarding.config.yml)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would happen without making any changes",
    )
    args = parser.parse_args()

    print()
    print(_c(BOLD, "PipesHub AI — Developer Onboarding Script"))
    if args.dry_run:
        print(_c(YELLOW, "  [DRY RUN — no changes will be made]"))

    cfg = load_config(args.config)
    base_url = cfg.get("api_base_url", "http://localhost:3000")
    admin_cfg: dict[str, Any] = cfg.get("admin", {})
    llm_cfg: dict[str, Any] = cfg.get("llm", {})
    embed_cfg: dict[str, Any] = cfg.get("embedding", {})
    smtp_cfg: dict[str, Any] = cfg.get("smtp", {})

    client = PipesHubClient(base_url, dry_run=args.dry_run)

    # ── 0. Health check ───────────────────────────────────────────────────
    section("0 · Connecting to API")
    info(f"Target: {base_url}")
    if not args.dry_run:
        wait_for_api(client)
    ok("Server is reachable.")

    # ── 1. Org + admin creation ──────────────────────────────────────────
    section("1 · Org & Admin Account")
    ensure_org(client, admin_cfg)

    # ── 2. Authentication ────────────────────────────────────────────────
    section("2 · Authentication")
    authenticate(client, admin_cfg)

    # ── 3. LLM ──────────────────────────────────────────────────────────
    section("3 · LLM (AI Model)")
    configure_llm(client, llm_cfg)

    # ── 4. Embedding ─────────────────────────────────────────────────────
    section("4 · Embedding Model")
    configure_embedding(client, embed_cfg)

    # ── 5. SMTP ──────────────────────────────────────────────────────────
    section("5 · SMTP")
    configure_smtp(client, smtp_cfg)

    # ── 6. Finish onboarding ─────────────────────────────────────────────
    section("6 · Finalise Onboarding")
    if not args.dry_run:
        mark_onboarding_done(client)
    else:
        warn("[dry-run] Would mark onboarding as configured.")

    print()
    print(_c(GREEN + BOLD, "  All done! PipesHub AI is ready to use."))
    print(_c(DIM, f"  Open the dashboard at {base_url.replace('3000', '3001')} (or your configured frontend URL)"))
    print()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nInterrupted.")
        sys.exit(130)
    except APIError as exc:
        err(f"API error {exc.status}: {exc.body}")
        sys.exit(1)
    except Exception as exc:  # noqa: BLE001
        err(f"Unexpected error: {exc}")
        raise
