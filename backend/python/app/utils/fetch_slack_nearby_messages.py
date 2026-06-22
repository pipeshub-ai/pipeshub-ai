"""Fetch nearby Slack channel messages for the chatbot agent.

Calls the Slack live API for up to ``limit`` channel messages immediately
before or after an ISO anchor timestamp. Results include ISO timestamps so
follow-up calls can paginate using a message from the prior batch.

For full thread context use ``fetch_slack_thread`` instead.
"""
from __future__ import annotations

import re
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Literal, Optional, TYPE_CHECKING, Union
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from langchain_core.tools import tool
from pydantic import BaseModel, Field

from app.sources.client.slack.slack import SlackClient
from app.sources.external.slack.slack import SlackDataSource
from app.utils.logger import create_logger

if TYPE_CHECKING:
    from app.config.configuration_service import ConfigurationService

logger = create_logger("fetch_slack_nearby_messages")

_USER_MENTION_RE = re.compile(r"<@([UW]\w+)(?:\|([^>]+))?>")
_BROADCAST_MENTION_RE = re.compile(r"<!(channel|here|everyone)>")

_DEFAULT_NEARBY_MESSAGE_LIMIT = 5
_MAX_NEARBY_MESSAGE_LIMIT = 50

NearbyDirection = Literal["before", "after"]


class SlackTimestampError(ValueError):
    """Raised when an ISO timestamp or timezone cannot be parsed."""


def _coerce_message_limit(message_limit: int) -> int:
    """Clamp the requested Slack page size to a safe range."""
    try:
        n = int(message_limit)
    except (TypeError, ValueError):
        n = _DEFAULT_NEARBY_MESSAGE_LIMIT
    return max(1, min(n, _MAX_NEARBY_MESSAGE_LIMIT))


def _slack_ts_to_iso(ts: str) -> Optional[str]:
    """Convert a Slack ``ts`` string to UTC ISO-8601 (``...Z``), or None."""
    try:
        seconds = float(ts)
    except (TypeError, ValueError):
        return None
    if seconds <= 0:
        return None
    try:
        dt = datetime.fromtimestamp(seconds, tz=timezone.utc)
    except (OverflowError, OSError, ValueError):
        return None
    return dt.isoformat().replace("+00:00", "Z")


def _parse_iso_timestamp(timestamp: str, timezone_name: str | None = None) -> datetime:
    """Parse an ISO-8601 timestamp, applying *timezone_name* when naive."""
    if not timestamp or not isinstance(timestamp, str):
        raise SlackTimestampError(
            "timestamp is required. Use ISO 8601 like '2023-11-14T22:13:20Z'."
        )
    s = timestamp.strip()
    if not s:
        raise SlackTimestampError(
            "timestamp is required. Use ISO 8601 like '2023-11-14T22:13:20Z'."
        )
    if s.endswith(("Z", "z")):
        s = s[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(s)
    except ValueError as e:
        raise SlackTimestampError(
            f"Invalid timestamp '{timestamp}'. Expected ISO 8601 like "
            f"'YYYY-MM-DDTHH:MM:SSZ' or 'YYYY-MM-DDTHH:MM:SS+05:30'."
        ) from e
    if dt.tzinfo is None:
        tz_name = (timezone_name or "").strip()
        if tz_name:
            try:
                dt = dt.replace(tzinfo=ZoneInfo(tz_name))
            except ZoneInfoNotFoundError as e:
                raise SlackTimestampError(
                    f"Unknown timezone '{timezone_name}'. Use an IANA name like "
                    f"'America/New_York' or 'UTC'."
                ) from e
        else:
            dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _iso_to_slack_ts(timestamp: str, timezone_name: str | None = None) -> str:
    """Convert an ISO timestamp to Slack ``oldest`` / ``latest`` format."""
    dt = _parse_iso_timestamp(timestamp, timezone_name)
    return f"{dt.timestamp():.6f}"


class FetchSlackNearbyMessagesArgs(BaseModel):
    """Arguments for ``fetch_slack_nearby_messages``."""

    timestamp: str = Field(
        ...,
        description=(
            "ISO-8601 anchor time for nearby fetch, e.g. '2023-11-14T22:13:20Z'. "
            "For indexed Slack records use 'Start Message ID' / 'End Message ID' "
            "from context. For pagination after a prior call, reuse "
            "'new_anchor_iso_timestamp' from that response."
        ),
    )
    timezone: str | None = Field(
        default=None,
        description=(
            "IANA timezone (e.g. 'America/New_York', 'UTC') applied when "
            "timestamp has no offset. Ignored when timestamp already includes "
            "Z or +/-HH:MM."
        ),
    )
    direction: NearbyDirection = Field(
        ...,
        description=(
            "'before': messages immediately before timestamp. "
            "'after': messages immediately after timestamp."
        ),
    )
    channel_id: str = Field(
        ...,
        description=(
            "Slack channel id (e.g. 'C1234567890'). For indexed records this is "
            "the record's external group id when recordGroupType is SLACK_CHANNEL."
        ),
    )
    connector_id: str = Field(
        ...,
        description=(
            "Slack connector instance id from the indexed record context "
            "(Connector ID in Message Details). Required to authenticate with "
            "the same Slack workspace that indexed the message."
        ),
    )
    limit: int = Field(
        default=_DEFAULT_NEARBY_MESSAGE_LIMIT,
        ge=1,
        le=_MAX_NEARBY_MESSAGE_LIMIT,
        description=(
            "Maximum number of messages to fetch from Slack for this call (1-50). "
            "Use 5 for a quick peek; call again with new_anchor_iso_timestamp "
            "to page further in the same direction."
        ),
    )
    reason: str = Field(
        default="Fetching nearby Slack messages for surrounding channel context",
        description="Brief explanation of why nearby messages are needed.",
    )


class SlackNearbyApiParams(BaseModel):
    """Slack ``conversations.history`` pagination params."""

    inclusive: bool = True
    limit: int
    latest: str | None = None
    oldest: str | None = None


class SlackConversationFetchParams(BaseModel):
    """Keyword arguments for a nearby Slack channel history request."""

    channel: str
    inclusive: bool = True
    limit: int
    latest: str | None = None
    oldest: str | None = None


class SlackNearbyFormattedMessage(BaseModel):
    """One Slack message formatted for agent tool output."""

    timestamp: str | None = None
    iso_timestamp: str | None = None
    text: str = ""
    user: str = ""
    user_display_name: str | None = None
    thread_ts: str | None = None
    subtype: str | None = None
    reply_count: int | None = None
    channel_id: str
    weburl: str


class FetchSlackNearbyMessagesError(BaseModel):
    ok: Literal[False] = False
    error: str


class FetchSlackNearbyMessagesSuccess(BaseModel):
    ok: Literal[True] = True
    messages: list[SlackNearbyFormattedMessage]
    message_count: int
    anchor_iso_timestamp: str | None
    new_anchor_iso_timestamp: str | None
    channel_id: str
    direction: NearbyDirection
    limit: int
    connector_id: str
    source: Literal["slack_api"] = "slack_api"


FetchSlackNearbyMessagesResult = Union[
    FetchSlackNearbyMessagesError,
    FetchSlackNearbyMessagesSuccess,
]


async def _resolve_user_display_names(
    data_source: SlackDataSource,
    user_ids: set[str],
) -> dict[str, str]:
    """Map Slack user ids to display names via ``users.info`` (best-effort)."""
    id_to_name: dict[str, str] = {}
    for uid in sorted(user_ids):
        if not uid or not uid.startswith("U"):
            continue
        try:
            resp = await data_source.users_info(user=uid)
        except Exception as e:
            logger.debug("users.info(%s) failed: %s", uid, e)
            continue
        if not resp.success or not resp.data:
            continue
        user_obj = resp.data.get("user") if isinstance(resp.data, dict) else None
        if not isinstance(user_obj, dict):
            continue
        profile = user_obj.get("profile") or {}
        id_to_name[uid] = (
            profile.get("display_name")
            or profile.get("real_name")
            or user_obj.get("name")
            or uid
        )
    return id_to_name


def _extract_user_mention_ids_from_text(text: str) -> set[str]:
    """Collect Slack user ids from ``<@U…>`` / ``<@W…>`` mention syntax in *text*."""
    if not text or "<@" not in text:
        return set()
    return {m.group(1) for m in _USER_MENTION_RE.finditer(text)}


def _replace_mentions_in_text(text: str, user_id_to_name: dict[str, str]) -> str:
    """Replace Slack user and broadcast mentions with readable ``@name`` tokens."""
    if not text:
        return text

    def replace_user_mention(match: re.Match[str]) -> str:
        user_id = match.group(1)
        embedded_name = match.group(2)
        if embedded_name:
            return f"@{embedded_name}"
        name = user_id_to_name.get(user_id)
        if name:
            return f"@{name}"
        return f"@{user_id}"

    text = _USER_MENTION_RE.sub(replace_user_mention, text)
    return _BROADCAST_MENTION_RE.sub(r"@\1", text)


def _slack_message_weburl(
    channel_id: str,
    ts: str,
    *,
    workspace_domain: Optional[str] = None,
    team_id: Optional[str] = None,
) -> str:
    """Build a Slack channel archive deep link for a message."""
    clean = ts.replace(".", "")
    if workspace_domain:
        return f"https://{workspace_domain}.slack.com/archives/{channel_id}/p{clean}"
    return f"https://app.slack.com/client/{team_id or ''}/{channel_id}/p{clean}"


async def _resolve_slack_workspace_info(
    data_source: SlackDataSource,
) -> tuple[Optional[str], Optional[str]]:
    """Return ``(workspace_domain, team_id)`` for permalink building (one API call)."""
    workspace_domain: Optional[str] = None
    team_id: Optional[str] = None
    try:
        resp = await data_source.team_info()
        if resp.success and isinstance(resp.data, dict):
            team = resp.data.get("team")
            if isinstance(team, dict):
                workspace_domain = team.get("domain") or workspace_domain
                team_id = team.get("id") or team_id
    except Exception as e:
        logger.debug("team.info failed for weburl building: %s", e)
    if not team_id:
        try:
            resp = await data_source.auth_test()
            if resp.success and isinstance(resp.data, dict):
                team_id = resp.data.get("team_id") or team_id
        except Exception as e:
            logger.debug("auth.test failed for weburl building: %s", e)
    return workspace_domain, team_id


def _slack_nearby_api_params(
    *,
    anchor_ts: str,
    direction: NearbyDirection,
    message_limit: int,
) -> SlackNearbyApiParams:
    """Build Slack ``oldest`` / ``latest`` / ``limit`` for count-based nearby fetch.

    Slack returns messages newest-first. ``before`` uses ``latest=anchor_ts`` plus
    ``limit`` for the messages closest before the anchor. ``after`` uses
    ``oldest=anchor_ts`` plus ``limit`` for the messages closest after it.
    """
    limit = _coerce_message_limit(message_limit)
    if direction == "before":
        return SlackNearbyApiParams(inclusive=True, limit=limit, latest=anchor_ts)
    return SlackNearbyApiParams(inclusive=True, limit=limit, oldest=anchor_ts)


async def _fetch_messages_from_slack(
    data_source: SlackDataSource,
    *,
    channel_id: str,
    anchor_ts: str,
    direction: NearbyDirection,
    message_limit: int,
    workspace_domain: Optional[str] = None,
    team_id: Optional[str] = None,
) -> tuple[list[SlackNearbyFormattedMessage], Optional[str]]:
    """Fetch up to *message_limit* channel messages before/after *anchor_ts*."""
    effective_limit = _coerce_message_limit(message_limit)
    nearby_params = _slack_nearby_api_params(
        anchor_ts=anchor_ts,
        direction=direction,
        message_limit=effective_limit,
    )
    fetch_params = SlackConversationFetchParams(
        channel=channel_id,
        inclusive=nearby_params.inclusive,
        limit=nearby_params.limit,
        latest=nearby_params.latest,
        oldest=nearby_params.oldest,
    )
    api_kwargs = fetch_params.model_dump(exclude_none=True)
    response = await data_source.conversations_history(**api_kwargs)

    if not response.success:
        return [], response.error or "Slack API request failed"

    data = response.data or {}
    raw_messages = data.get("messages") if isinstance(data, dict) else None
    if not isinstance(raw_messages, list):
        return [], None

    user_ids: set[str] = set()
    for m in raw_messages:
        if not isinstance(m, dict):
            continue
        if m.get("user"):
            user_ids.add(str(m.get("user")))
        text = m.get("text")
        if isinstance(text, str):
            user_ids.update(_extract_user_mention_ids_from_text(text))
    user_names = await _resolve_user_display_names(data_source, user_ids)

    formatted: list[SlackNearbyFormattedMessage] = []
    for msg in raw_messages:
        if not isinstance(msg, dict) or not msg.get("ts"):
            continue
        try:
            msg_ts = msg["ts"]
        except (TypeError, ValueError):
            continue

        user_id = str(msg.get("user") or msg.get("bot_id") or "")
        ts = str(msg_ts)
        msg_thread_ts = msg.get("thread_ts")
        iso_timestamp = _slack_ts_to_iso(ts)
        subtype = msg.get("subtype")
        reply_count = msg.get("reply_count")
        raw_text = str(msg.get("text") or "")
        formatted.append(
            SlackNearbyFormattedMessage(
                timestamp=iso_timestamp,
                iso_timestamp=iso_timestamp,
                text=_replace_mentions_in_text(raw_text, user_names),
                user=user_id,
                user_display_name=user_names.get(user_id) if user_id else None,
                thread_ts=str(msg_thread_ts) if msg_thread_ts else None,
                subtype=str(subtype) if subtype is not None else None,
                reply_count=int(reply_count) if isinstance(reply_count, int) else None,
                channel_id=channel_id,
                weburl=_slack_message_weburl(
                    channel_id,
                    ts,
                    workspace_domain=workspace_domain,
                    team_id=team_id,
                ),
            )
        )

    formatted.sort(key=lambda m: m.iso_timestamp or "")
    return formatted, None


def _new_anchor_iso_timestamp(
    messages: list[SlackNearbyFormattedMessage],
    direction: NearbyDirection,
) -> Optional[str]:
    """Return the ISO timestamp to use when paging further in the same direction."""
    if not messages:
        return None
    if direction == "before":
        return messages[0].iso_timestamp
    return messages[-1].iso_timestamp


async def _fetch_nearby_messages_impl(
    timestamp: str,
    direction: NearbyDirection,
    channel_id: str,
    connector_id: str,
    *,
    timezone_name: str | None = None,
    limit: int = _DEFAULT_NEARBY_MESSAGE_LIMIT,
    config_service: Optional["ConfigurationService"] = None,
) -> FetchSlackNearbyMessagesResult:
    """Core implementation: resolve Slack client and fetch nearby messages."""
    if not config_service:
        return FetchSlackNearbyMessagesError(
            error=(
                "Slack nearby-messages tool requires config_service to build a Slack client."
            ),
        )

    effective_connector_id = (connector_id or "").strip()
    if not effective_connector_id:
        return FetchSlackNearbyMessagesError(
            error=(
                "connector_id is required. Use the Connector ID from the indexed "
                "Slack record context."
            ),
        )

    channel = (channel_id or "").strip()
    if not channel:
        return FetchSlackNearbyMessagesError(error="channel_id is required.")

    try:
        anchor_ts = _iso_to_slack_ts(timestamp, timezone_name)

    except SlackTimestampError as e:
        return FetchSlackNearbyMessagesError(error=str(e))

    try:
        slack_client = await SlackClient.build_from_services(
            logger=logger,
            config_service=config_service,
            connector_instance_id=effective_connector_id,
        )
    except Exception as e:
        logger.error(
            "Failed to build Slack client for connector %s: %s",
            effective_connector_id,
            e,
        )
        return FetchSlackNearbyMessagesError(
            error=f"Failed to connect to Slack: {e}",
        )

    data_source = SlackDataSource(slack_client)
    effective_limit = _coerce_message_limit(limit)
    workspace_domain, team_id = await _resolve_slack_workspace_info(data_source)
    messages, api_error = await _fetch_messages_from_slack(
        data_source,
        channel_id=channel,
        anchor_ts=anchor_ts,
        direction=direction,
        message_limit=effective_limit,
        workspace_domain=workspace_domain,
        team_id=team_id,
    )
    if api_error:
        return FetchSlackNearbyMessagesError(error=api_error)

    return FetchSlackNearbyMessagesSuccess(
        messages=messages,
        message_count=len(messages),
        anchor_iso_timestamp=timestamp,
        new_anchor_iso_timestamp=_new_anchor_iso_timestamp(messages, direction),
        channel_id=channel,
        direction=direction,
        limit=effective_limit,
        connector_id=effective_connector_id,
    )


def create_fetch_slack_nearby_messages_tool(
    config_service: Optional["ConfigurationService"] = None,
) -> Callable:
    """Return a LangChain tool with Slack client dependencies bound."""

    @tool("fetch_slack_nearby_messages", args_schema=FetchSlackNearbyMessagesArgs)
    async def fetch_slack_nearby_messages_tool(
        timestamp: str,
        direction: NearbyDirection,
        channel_id: str,
        connector_id: str,
        timezone: str | None = None,
        limit: int = _DEFAULT_NEARBY_MESSAGE_LIMIT,
        reason: str = "Fetching nearby Slack messages for surrounding channel context",
    ) -> FetchSlackNearbyMessagesResult:
        """Fetch Slack channel messages adjacent to an ISO anchor time.

        ``before`` loads messages immediately before ``timestamp``; ``after`` loads
        messages immediately after it. Each message includes ``timestamp`` /
        ``iso_timestamp`` in UTC. To page further in the same direction, call again
        with ``timestamp`` set to ``new_anchor_iso_timestamp`` from the prior
        response. Scoped to ``channel_id`` only (channel-level history).

        Pass ``connector_id`` from the indexed Slack record's Connector ID in context.
        Call when retrieval lacks surrounding channel conversation. For full thread
        context use ``fetch_slack_thread`` instead.
        """
        try:
            return await _fetch_nearby_messages_impl(
                timestamp=timestamp,
                direction=direction,
                channel_id=channel_id,
                connector_id=connector_id,
                timezone_name=timezone,
                limit=limit,
                config_service=config_service,
            )
        except Exception as e:
            logger.exception("fetch_slack_nearby_messages_tool failed")
            return FetchSlackNearbyMessagesError(
                error=f"Failed to fetch nearby Slack messages: {e}",
            )

    return fetch_slack_nearby_messages_tool
