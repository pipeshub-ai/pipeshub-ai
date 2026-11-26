"""Salesforce rows and ids that the connector and its permission reading (``app/ee/permission_sync/salesforce.py``)
both use."""

import re
from logging import Logger
from typing import Any, List, Optional

from pydantic import BaseModel, ConfigDict

from app.utils.time_conversion import parse_timestamp


def _parse_salesforce_timestamp(value: Optional[Any]) -> Optional[int]:
    """Parse a Salesforce date string (normalizes +0000 to +00:00) to epoch ms. Returns None if invalid or missing."""
    if value is None or not isinstance(value, str):
        return None
    try:
        s = value.replace("+0000", "+00:00")
        return parse_timestamp(s)
    except Exception:
        return None


# Salesforce IDs are exactly 15 or 18 alphanumeric characters.
_SF_ID_RE = re.compile(r'^[a-zA-Z0-9]{15}([a-zA-Z0-9]{3})?$')


def _sanitize_soql_id(value: str) -> str:
    """
    Validate a Salesforce record ID before it is interpolated into a SOQL string.

    Salesforce IDs are exactly 15 or 18 alphanumeric characters.  Any value
    that does not match this pattern cannot be a legitimate ID and is rejected
    to prevent SOQL injection.

    Raises:
        ValueError: if *value* is empty or does not match the expected format.
    """
    if not value or not _SF_ID_RE.match(value):
        raise ValueError(f"Invalid Salesforce ID for SOQL interpolation: {value!r}")
    return value


def _sanitize_soql_ids_batch(ids: List[str], logger: Logger, context: str = "") -> List[str]:
    """
    Validate a batch of Salesforce record IDs for SOQL interpolation.

    Each ID is run through `_sanitize_soql_id`; invalid values are dropped and
    logged as warnings.  Returns the subset of *ids* that passed validation.

    Args:
        ids:     The raw IDs to validate.
        logger:  Logger used to emit per-invalid-ID warnings.
        context: Optional human-readable label included in warning messages
                 (e.g. "opportunity account lookup") to aid debugging.
    """
    safe: List[str] = []
    label = f" ({context})" if context else ""
    for value in ids:
        try:
            safe.append(_sanitize_soql_id(value))
        except ValueError:
            logger.warning("Skipping invalid Salesforce ID%s: %r", label, value)
    return safe


class SalesforceUser(BaseModel):
    """A row from the Salesforce User object."""

    model_config = ConfigDict(extra="ignore")

    Id: Optional[str] = None
    Email: Optional[str] = None
    FirstName: Optional[str] = None
    LastName: Optional[str] = None
    Title: Optional[str] = None
    Phone: Optional[str] = None
    MobilePhone: Optional[str] = None
    IsActive: Optional[bool] = None
    UserType: Optional[str] = None
    UserRoleId: Optional[str] = None
    CreatedDate: Optional[str] = None
    LastModifiedDate: Optional[str] = None


class SalesforceRole(BaseModel):
    """A row from the Salesforce UserRole object."""

    model_config = ConfigDict(extra="ignore")

    Id: Optional[str] = None
    Name: Optional[str] = None
    ParentRoleId: Optional[str] = None
    SystemModstamp: Optional[str] = None


class SalesforceGroup(BaseModel):
    """A row from the Salesforce Group object (Public Group or Queue)."""

    model_config = ConfigDict(extra="ignore")

    Id: Optional[str] = None
    Name: Optional[str] = None
    Type: Optional[str] = None
    CreatedDate: Optional[str] = None
    LastModifiedDate: Optional[str] = None
