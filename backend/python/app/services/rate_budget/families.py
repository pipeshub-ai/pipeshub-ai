"""Which vendor quota a connector or an agent toolset spends, and how large that quota is (G-31).

A connector's sync and an agent's toolset reach the same vendor under different names: the Slack connector is
``Slack`` or ``Slack Workspace``, the toolset is ``slack``; Notion sync is ``Notion`` or ``Notion Personal``, the
toolset ``notion``. Both sides name their quota here, so a budget is shared by everything that reaches one vendor
for one org. Only a family whose sync side also takes from the budget is listed: a toolset alone gains nothing
from a shared bucket but a second limit to configure. So the Google Calendar, Docs and Sheets toolsets and the
Microsoft Teams toolset have none (no connector syncs those APIs), and neither do Jira and Confluence Data Center,
whose limits are whatever the customer's admin set on their own server (off by default): there is no documented
quota to default to.

A family is the vendor's own quota group, not the product: Google meters each API separately per Cloud project
(Drive and Gmail are two quotas), OneDrive and SharePoint both spend SharePoint Online's resource units per app per
tenant, and Outlook mail and calendar spend the Exchange limits.

Rates are requests per second across every process of the org, set below the vendor's documented limit:

- Slack: tiers 2-4 at once, about 2.8/s per workspace. Notion: 3/s average per integration.
- Jira and Confluence Cloud (developer.atlassian.com/cloud/{jira/platform,confluence}/rate-limiting): an hourly
  points quota, 65,000 points an hour in the default global pool (a request costs at least one point, an issue or a
  user more), and burst limits per tenant per endpoint (Jira: GET and POST 100/s, PUT and DELETE 50/s). 429 with
  Retry-After; past the hourly quota every request is refused until the hour resets. 10/s is 36,000 requests an
  hour, inside the quota at the point costs of a sync's pages.
- Google Drive (developers.google.com/workspace/drive/api/guides/limits): 1,000,000 quota units a minute per
  project and 325,000 per user; projects that used the API before May 2026 keep 12,000 queries a minute (200/s)
  per project. Over quota answers 403 (rate limit exceeded) or 429.
- Gmail (developers.google.com/workspace/gmail/api/reference/quota): 1,200,000 units a minute per project and 6,000
  per user; messages.get costs 20 units, messages.list 5, history.list 2, so a project allows about 1,000 message
  reads a second and one mailbox about 5. A per-user 403 (``userRateLimitExceeded``) stays with googleapiclient's
  own retry rather than pausing the org.
- SharePoint and OneDrive (learn.microsoft.com/sharepoint/dev/general-development/how-to-avoid-getting-throttled-or-
  blocked-in-sharepoint-online): per app per tenant 1,250 resource units a minute and 1,200,000 a day in the
  smallest license band (up to 1,000 licenses; five times that past 50,000), about 2 units a request. 429 or 503,
  both with Retry-After. 10/s is the smallest band's minute limit; a larger tenant raises it by override.
- Outlook (learn.microsoft.com/graph/throttling-limits): 10,000 requests per 10 minutes and four at once per app per
  mailbox, under Graph's global 130,000 per 10 s per app. A sync reads many mailboxes at once, so the org's budget
  is a few mailboxes' worth.

``CONNECTOR_RATE_BUDGETS`` overrides them, as JSON: ``{"slack": {"rate": 2, "burst": 6}}``.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from enum import StrEnum

from harness_core.priority import PriorityClass, class_for_run_kind

logger = logging.getLogger(__name__)

OVERRIDES_ENV = "CONNECTOR_RATE_BUDGETS"


class RatePriority(StrEnum):
    """Who is waiting for the call: a person in a chat, a sync, or an agent nobody is watching."""

    INTERACTIVE = "interactive"
    SYNC = "sync"
    BACKGROUND = "background"


# Priority, as two numbers per class. The reserve is the share of the bucket a class must leave behind: an
# interactive call may drain it, a sync leaves a quarter, a background agent half. The floor is the share of the
# rate a waiting class is owed however busy the classes above it are, so a sync under a stream of agent calls
# still finishes, and so does a background agent under a sync.
_RESERVE_SHARE: dict[RatePriority, float] = {
    RatePriority.INTERACTIVE: 0.0,
    RatePriority.SYNC: 0.25,
    RatePriority.BACKGROUND: 0.5,
}
_FLOOR_SHARE: dict[RatePriority, float] = {
    RatePriority.INTERACTIVE: 0.0,
    RatePriority.SYNC: 0.3,
    RatePriority.BACKGROUND: 0.1,
}


@dataclass(frozen=True, slots=True)
class BudgetLimits:
    rate: float
    burst: float

    def __post_init__(self) -> None:
        if self.rate <= 0 or self.burst < 1:
            raise ValueError(f"a rate budget needs rate > 0 and burst >= 1, got {self.rate}/{self.burst}")

    def reserve(self, priority: RatePriority) -> float:
        return self.burst * _RESERVE_SHARE[priority]

    def floor(self, priority: RatePriority) -> float:
        return self.rate * _FLOOR_SHARE[priority]


_DEFAULT_LIMITS: dict[str, BudgetLimits] = {
    "slack": BudgetLimits(rate=2.5, burst=10),
    "notion": BudgetLimits(rate=2.5, burst=6),
    "jira": BudgetLimits(rate=10, burst=20),
    "confluence": BudgetLimits(rate=10, burst=20),
    "google_drive": BudgetLimits(rate=100, burst=200),
    "gmail": BudgetLimits(rate=100, burst=200),
    "microsoft_sharepoint": BudgetLimits(rate=10, burst=40),
    "microsoft_outlook": BudgetLimits(rate=50, burst=100),
}

# Keyed by the normalized ``Connectors`` value a connector's app carries.
_CONNECTOR_FAMILIES: dict[str, str] = {
    "slack": "slack",
    "slackworkspace": "slack",
    "notion": "notion",
    "notionpersonal": "notion",
    "jira": "jira",
    "jirapersonal": "jira",
    "jiracloudpersonal": "jira",
    "confluence": "confluence",
    "drive": "google_drive",
    "driveworkspace": "google_drive",
    "gmail": "gmail",
    "gmailworkspace": "gmail",
    "onedrive": "microsoft_sharepoint",
    "sharepointonline": "microsoft_sharepoint",
    "outlook": "microsoft_outlook",
    "outlookpersonal": "microsoft_outlook",
}

# Keyed by the toolset registry's normalized name (``@ToolsetBuilder("Drive")`` is ``drive``).
_TOOLSET_FAMILIES: dict[str, str] = {
    "slack": "slack",
    "notion": "notion",
    "jira": "jira",
    "confluence": "confluence",
    "drive": "google_drive",
    "gmail": "gmail",
    "onedrive": "microsoft_sharepoint",
    "sharepoint": "microsoft_sharepoint",
    "outlook": "microsoft_outlook",
}


def _normalized(name: str) -> str:
    return "".join(ch for ch in name.lower() if ch.isalnum())


def family_for_connector(app_name: object) -> str | None:
    """``app_name`` is a ``Connectors`` member (not a str-Enum: its str() is ``Connectors.JIRA``) or its value."""
    name = getattr(app_name, "value", app_name)
    return _CONNECTOR_FAMILIES.get(_normalized(name)) if isinstance(name, str) else None


def family_for_toolset(toolset_name: str | None) -> str | None:
    return _TOOLSET_FAMILIES.get(_normalized(toolset_name or ""))


def limits_for(family: str) -> BudgetLimits | None:
    limits = _DEFAULT_LIMITS.get(family)
    raw = os.getenv(OVERRIDES_ENV, "").strip()
    if not raw:
        return limits
    try:
        override = json.loads(raw).get(family)
        if override is None:
            return limits
        base = limits or BudgetLimits(rate=1.0, burst=1)
        return BudgetLimits(
            rate=float(override.get("rate", base.rate)), burst=float(override.get("burst", base.burst))
        )
    except (ValueError, TypeError, AttributeError) as exc:
        # A bad override must not take the default budget away with it.
        logger.warning("Ignoring %s for %s: %s", OVERRIDES_ENV, family, exc)
        return limits


# G-45: the vendor budget priority each run class draws at (``priority-classes.json`` ``ratePriority``). A person
# waits on an interactive run, and on a lower-class run they just answered.
_CLASS_RATE_PRIORITY: dict[PriorityClass, RatePriority] = {
    "interactive": RatePriority.INTERACTIVE,
    "approval_resume": RatePriority.INTERACTIVE,
    "background": RatePriority.BACKGROUND,
    "batch": RatePriority.BACKGROUND,
}


def rate_priority_for_class(priority_class: PriorityClass) -> RatePriority:
    return _CLASS_RATE_PRIORITY[priority_class]


def priority_for_run_kind(run_kind: str | None) -> RatePriority:
    """For a claim that names no class (an older Node): the class its run kind gives. A child is interactive here,
    as deep work routes interactive turns to children; Node gives it its parent's class."""
    return rate_priority_for_class(class_for_run_kind(run_kind))


__all__ = [
    "OVERRIDES_ENV",
    "BudgetLimits",
    "RatePriority",
    "family_for_connector",
    "family_for_toolset",
    "limits_for",
    "priority_for_run_kind",
    "rate_priority_for_class",
]
