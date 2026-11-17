"""Asking users to connect their own account to a per-user connector.

Each request is an in-app notification that opens the connect dialog, and an email with a link
that connects without signing in to PipesHub first (``user_access.create_link``). The email is best
effort: without a mail server the notification still goes.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any
from urllib.parse import quote

import aiohttp

from app.config.constants.service import DefaultEndpoints, config_node_constants
from app.connectors.core.base.user_access import LINK_TTL_DAYS, create_link
from app.services.notification.types import (
    NotificationOrigin,
    NotificationSeverity,
    NotificationType,
)
from app.utils.jwt import generate_jwt
from app.utils.oauth_redirect import get_configured_frontend_base_url
from app.utils.request_context import inject_request_headers

if TYPE_CHECKING:
    from logging import Logger

    from app.config.configuration_service import ConfigurationService
    from app.services.notification.notification_service import NotificationService

EMAIL_TEMPLATE = "connectorAccountConnect"
_MAIL_ROUTE = "/api/v1/mail/emails/sendEmail"
_MAIL_TIMEOUT_S = 15
_OK = 200


@dataclass
class ConnectRequest:
    """Who to ask, about which connector."""

    org_id: str
    connector_id: str
    connector_name: str
    source_name: str
    user_id: str
    email: str
    reconnect: bool = False
    inviter_name: str | None = None
    org_name: str | None = None


def connect_page_link(connector_id: str) -> str:
    """The in-app page that opens the connect dialog (relative, like other connector notices)."""
    return f"workspace/connectors/personal/?connect={quote(connector_id)}"


async def send_connect_request(
    request: ConnectRequest,
    *,
    config_service: ConfigurationService,
    notification_service: NotificationService | None,
    logger: Logger,
    now_ms: int,
) -> tuple[bool, bool]:
    """Notify the user in the app and by email. Returns (notified, emailed); never raises."""
    source = request.source_name
    title = f"Reconnect your {source} account" if request.reconnect else f"Connect your {source} account"
    message = (
        f"Your {source} connection stopped working. Reconnect to keep seeing your {source} pages in PipesHub."
        if request.reconnect
        else f"Connect your {source} account to see your {source} pages in PipesHub search and chat."
    )
    notified = False
    if notification_service is not None:
        notified = await notification_service.publish_notification(
            org_id=request.org_id,
            origin=NotificationOrigin.CONNECTOR,
            type=NotificationType.CONNECTOR_WARNING if request.reconnect else NotificationType.CONNECTOR_INFO,
            severity=NotificationSeverity.WARNING if request.reconnect else NotificationSeverity.INFO,
            title=title,
            message=message,
            payload={"connectorId": request.connector_id, "action": "connect_account"},
            redirect_link=connect_page_link(request.connector_id),
            recipient_user_ids=[request.user_id],
        )
    emailed = False
    try:
        token = await create_link(config_service, request.connector_id, request.user_id, request.org_id, now_ms)
        base = await get_configured_frontend_base_url(config_service)
        link = f"{base}/connect-account?token={quote(token)}"
        emailed = await _send_email(
            config_service,
            request.org_id,
            request.email,
            subject=f"{title} to PipesHub",
            template_data={
                "orgName": request.org_name,
                "connectorName": request.connector_name,
                "sourceName": source,
                "link": link,
                "optOutLink": f"{link}&optOut=1",
                "reconnect": request.reconnect,
                "inviterName": request.inviter_name,
                "expiresInDays": LINK_TTL_DAYS,
            },
            logger=logger,
        )
    except Exception as e:
        logger.warning("Could not email a connect link for connector %s: %s", request.connector_id, e)
    return notified, emailed


async def _send_email(
    config_service: ConfigurationService,
    org_id: str,
    to: str,
    *,
    subject: str,
    template_data: dict[str, Any],
    logger: Logger,
) -> bool:
    endpoints = await config_service.get_config(config_node_constants.ENDPOINTS.value)
    node = ((endpoints or {}).get("cm") or {}).get("endpoint") or DefaultEndpoints.NODEJS_ENDPOINT.value
    token = await generate_jwt(config_service, {"orgId": org_id, "scopes": ["mail:send"]})
    body = {
        "orgId": org_id,
        "emailTemplateType": EMAIL_TEMPLATE,
        "sendEmailTo": [to],
        "subject": subject,
        "isAutoEmail": True,
        "templateData": template_data,
    }
    headers = inject_request_headers({"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=_MAIL_TIMEOUT_S)) as session, \
            session.post(f"{node.rstrip('/')}{_MAIL_ROUTE}", json=body, headers=headers) as response:
        if response.status == _OK:
            return True
        # The mail route's answer says why (often: no mail server set up); it never holds the link.
        logger.info("Connect email not sent (HTTP %s): %s", response.status, (await response.text())[:200])
        return False
