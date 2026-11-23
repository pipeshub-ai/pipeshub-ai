"""What the ServiceNow connector and its permission reading (``app/ee/permission_sync/servicenow.py``) share."""

from dataclasses import dataclass

from app.config.constants.http_status_code import HttpStatusCode
from app.connectors.sources.servicenow.servicenow.constants import (
    ServiceNowDictKeys,
    ServiceNowTables,
)
from app.sources.client.servicenow.servicenow import ServiceNowRateLimited
from app.sources.external.servicenow.models import (
    ServiceNowAccessError,
    ServiceNowAPIError,
    ServiceNowAuthError,
)

# A knowledge base's criteria ids by kind, from these tables (see _kb_criteria)
_KB_MTOM = (
    (ServiceNowDictKeys.READ, ServiceNowTables.KB_UC_CAN_READ_MTOM),
    (ServiceNowDictKeys.CANNOT_READ, ServiceNowTables.KB_UC_CANNOT_READ_MTOM),
    (ServiceNowDictKeys.WRITE, ServiceNowTables.KB_UC_CAN_CONTRIBUTE_MTOM),
    (ServiceNowDictKeys.CANNOT_WRITE, ServiceNowTables.KB_UC_CANNOT_CONTRIBUTE_MTOM),
)

# Stands for "every user" where a knowledge base with no user criteria is open (see _kb_criteria)
_EVERYONE = "everyone"

@dataclass
class _Listing:
    """What one _iter_table listing saw: ``total`` rows counted before ACLs, ``seen`` shown."""
    total: int = 0
    seen: int = 0

    @property
    def partial(self) -> bool:
        return self.seen < self.total


class _CriteriaUnreadable(Exception):
    """A user criteria (Can Read or Cannot Read) could not be read or resolved to users;
    the grants it governs are left as stored."""


class _CriteriaHidden(_CriteriaUnreadable):
    """An ACL hides an allow criterion, or a knowledge base's criteria rows, from this account:
    unknown until the account may read them, not a failure the next run would get past."""


def _raise_if_run_stop(error: BaseException | None) -> None:
    """Raise the rate limit, auth failure or unreadable access table behind ``error``, if any.
    Every later call would fail the same way, so the run ends there (checkpoints unmoved, nothing
    unlinked) instead of failing item by item."""
    while error is not None:
        if isinstance(error, (ServiceNowRateLimited, ServiceNowAuthError, ServiceNowAccessError)) or (
            isinstance(error, ServiceNowAPIError) and error.status_code == HttpStatusCode.TOO_MANY_REQUESTS.value
        ):
            raise error
        error = error.__cause__
