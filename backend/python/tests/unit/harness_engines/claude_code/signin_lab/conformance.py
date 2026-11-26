"""SP-1..SP-7 (SIGNIN-1): what the plan assumes about Claude Code's login, checked on one lab run.

The recorded fixture and a live run against the fake (Docker lane) go through the same checks, so a new Claude Code
version that changes any of it fails before the product reads it wrong.
"""

from __future__ import annotations

import json
import re
import urllib.parse
from collections.abc import Callable
from typing import Any

PROMPT = "Paste code here if prompted > "
TOKEN = re.compile(r"sk-ant-[a-z]{2,4}\d{0,2}-")
CREDENTIAL_KEYS = {
    "accessToken",
    "refreshToken",
    "expiresAt",
    "refreshTokenExpiresAt",
    "scopes",
    "subscriptionType",
    # 2.1.319 records where the plan came from; the product reads the plan from ``claude auth status`` only.
    "subscriptionTypeSource",
    "rateLimitTier",
}


def _first(requests: list[dict[str, Any]], path: str) -> dict[str, Any]:
    return next(r for r in requests if r["path"] == path)


def _files(tree: list[dict[str, Any]]) -> dict[str, str]:
    return {row["path"]: row["mode"] for row in tree if not row["dir"]}


def sp1_login_link(run: dict[str, Any]) -> None:
    login = run["login"]
    assert login["rc"] == 0, login
    url = urllib.parse.urlsplit(login["url"])
    assert (url.scheme, url.netloc, url.path) == (
        "https",
        "claude.com",
        "/cai/oauth/authorize",
    )
    query = urllib.parse.parse_qs(url.query)
    assert query["response_type"] == ["code"]
    assert query["code"] == ["true"]
    assert query["code_challenge_method"] == ["S256"]
    assert query["redirect_uri"] == ["https://platform.claude.com/oauth/code/callback"]
    assert {"client_id", "scope", "code_challenge", "state"} <= set(query)
    assert "user:inference" in query["scope"][0].split()
    # Over plain pipes (the broker's exec): the link, the prompt, then the result line.
    assert login["stdout"].startswith("Opening browser to sign in")
    assert (
        f"If the browser didn't open, visit: {login['url']}\n{PROMPT}Login successful.\n"
        in login["stdout"]
    )


def sp2_pasted_code(run: dict[str, Any]) -> None:
    bad = run["login_bad_code"]
    assert bad["rc"] == 1
    # The product reads only the exit code; the wording is pinned so a change is seen (2.1.319's, after 2.1.294's
    # "Request failed with status code 401").
    assert bad["stderr"] == (
        "Login failed: That code wasn't accepted. It may be incomplete, expired, already used, or from an earlier"
        " sign-in attempt. Sign in again to get a new code.\n"
    )
    without_state = run["login_code_without_state"]
    # A code without its "#state" is refused locally and the prompt waits on: the caller must check the shape.
    assert without_state["rc"] == "still_running_after_40s"
    assert (
        without_state["stderr"]
        == "Invalid code. Please make sure the full code was copied.\n"
    )
    # Telemetry of an earlier step can arrive first: the exchange is found, not indexed.
    exchange = _first(run["login_requests"], "/v1/oauth/token")
    assert exchange["grant_type"] == "authorization_code"
    assert exchange["body"] == [
        "client_id",
        "code",
        "code_verifier",
        "grant_type",
        "redirect_uri",
        "state",
    ]


def sp3_files(run: dict[str, Any]) -> None:
    files = _files(run["login"]["tree"])
    assert files[".credentials.json"] == "0o600"
    assert files[".claude.json"] == "0o600"
    assert set(run["login"]["credentials_shape"]) == {"claudeAiOauth"}
    assert set(run["login"]["credentials_shape"]["claudeAiOauth"]) == CREDENTIAL_KEYS
    account = run["login"]["oauth_account"]
    assert account["emailAddress"] == "ada@example.com"
    assert not TOKEN.search(json.dumps(account))
    # Nothing else under the config dir holds the login: everything but .credentials.json is profile and caches.
    assert [p for p in files if "credential" in p] == [".credentials.json"]


def sp4_status(run: dict[str, Any]) -> None:
    out = run["status_signed_out"]["json"]
    assert out["rc"] == 1
    signed_out = json.loads(out["stdout"])
    assert (signed_out["loggedIn"], signed_out["authMethod"]) == (False, "none")
    # The text form also names the lab's CA (NODE_EXTRA_CA_CERTS); the product reads only the JSON form.
    assert run["status_signed_out"]["text"]["stdout"].endswith(
        "Not logged in. Run claude auth login to authenticate.\n"
    )
    signed_in = run["status_signed_in"]["json"]
    assert signed_in["rc"] == 0
    status = json.loads(signed_in["stdout"])
    assert status["loggedIn"] is True
    assert status["authMethod"] == "claude.ai"
    assert status["email"] == "ada@example.com"
    assert {"orgId", "orgName", "subscriptionType"} <= set(status)
    assert {p: s["subscriptionType"] for p, s in run["plans"].items()} == {
        "claude_pro": "pro",
        "claude_max": "max",
        "claude_team": "team",
        "claude_enterprise": "enterprise",
    }
    for step in ("status_signed_in", "status_signed_out"):
        assert not TOKEN.search(json.dumps(run[step]))


def sp5_logout(run: dict[str, Any]) -> None:
    logout = run["logout"]
    assert (logout["rc"], logout["stdout"]) == (
        0,
        "Successfully logged out from your Anthropic account.\n",
    )
    assert ".credentials.json" not in _files(logout["tree"])
    revoke = _first(run["logout_requests"], "/v1/oauth/token/revoke")
    assert (revoke["host"], revoke["method"], revoke["path"]) == (
        "platform.claude.com",
        "POST",
        "/v1/oauth/token/revoke",
    )
    assert revoke["body"] == ["client_id", "token", "token_type_hint"]
    assert json.loads(run["status_after_logout"]["json"]["stdout"])["loggedIn"] is False
    # The revoke failed at the vendor, and logout still says it succeeded: PipesHub cannot learn it from the CLI.
    failed = run["logout_revoke_failed"]
    assert (failed["rc"], failed["stdout"]) == (
        0,
        "Successfully logged out from your Anthropic account.\n",
    )
    assert _first(run["logout_revoke_failed_requests"], "/v1/oauth/token/revoke")["host"] == "platform.claude.com"
    assert (
        json.loads(run["status_after_failed_revoke"]["json"]["stdout"])["loggedIn"]
        is False
    )


def _events(run: dict[str, Any], step: str) -> list[dict[str, Any]]:
    return run[step]["events"]


def sp6_errors(run: dict[str, Any]) -> None:
    expired = _events(run, "prompt_expired")
    assert {
        (e["error_status"], e["error"]) for e in expired if e["type"] == "system"
    } == {(401, "authentication_failed")}
    assert [e["error"] for e in expired if e["type"] == "assistant"] == [
        "authentication_failed"
    ]
    assert [e["api_error_status"] for e in expired if e["type"] == "result"] == [401]
    assert any(
        r["grant_type"] == "refresh_token" for r in run["prompt_expired_requests"]
    )
    limit = _events(run, "prompt_limit")
    info = next(e["rate_limit_info"] for e in limit if e["type"] == "rate_limit_event")
    assert info["status"] == "rejected"
    assert {"resetsAt", "rateLimitType"} <= set(info)
    assert [e["error"] for e in limit if e["type"] == "assistant"] == ["rate_limit"]
    assert [e["api_error_status"] for e in limit if e["type"] == "result"] == [429]
    signed_out = _events(run, "prompt_signed_out")
    assert [e["error"] for e in signed_out if e["type"] == "assistant"] == [
        "authentication_failed"
    ]
    result = next(e for e in signed_out if e["type"] == "result")
    assert result["api_error_status"] is None
    assert result["result"].startswith("Not logged in")
    for step in ("prompt_expired", "prompt_limit", "prompt_signed_out"):
        assert (
            next(e for e in _events(run, step) if e["type"] == "result")[
                "total_cost_usd"
            ]
            == 0
        )


def sp7_hosts(run: dict[str, Any]) -> None:
    """The sandbox reaches only these two; claude.com is the person's browser's, never the sandbox's."""
    contacted = {
        r["host"]
        for key in ("login_requests", "prompt_expired_requests", "logout_requests")
        for r in run[key]
    }
    assert contacted == {"api.anthropic.com", "platform.claude.com"}
    printed = json.dumps({k: v for k, v in run.items() if not k.endswith("_requests")})
    assert not TOKEN.search(printed)


Check = Callable[[dict[str, Any]], None]

CHECKS: tuple[Check, ...] = (
    sp1_login_link,
    sp2_pasted_code,
    sp3_files,
    sp4_status,
    sp5_logout,
    sp6_errors,
    sp7_hosts,
)
