"""
Knowledge Base API helpers for response-validation integration tests.
"""
from __future__ import annotations

import requests


def session_headers(access_token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
    }


def create_kb(
    base_url: str,
    access_token: str,
    kb_name: str,
    timeout: int = 30,
) -> requests.Response:
    """POST /api/v1/knowledgeBase and return the raw response."""
    return requests.post(
        f"{base_url}/api/v1/knowledgeBase",
        headers=session_headers(access_token),
        json={"kbName": kb_name},
        timeout=timeout,
    )


def delete_kb(
    base_url: str,
    access_token: str,
    kb_id: str,
    timeout: int = 30,
) -> requests.Response:
    """DELETE /api/v1/knowledgeBase/{kbId} and return the raw response."""
    return requests.delete(
        f"{base_url}/api/v1/knowledgeBase/{kb_id}",
        headers=session_headers(access_token),
        timeout=timeout,
    )
