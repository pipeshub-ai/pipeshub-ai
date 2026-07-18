"""Odoo DataSource — typed CRM module operations built on OdooClient.execute_kw.

Scope: CRM only, read-only. Covers:
  - crm.lead (leads/opportunities): full field set
  - crm.stage / crm.team / crm.tag / crm.lost.reason
  - res.partner (contacts) / res.users (salespersons)
  - mail.activity (activities scheduled on a lead)
  - mail.message (chatter notes/log — read; add_note()/create_activity() still write, they're not lead mutation)
  - utm.source / utm.medium / utm.campaign (marketing attribution)
  - ir.attachment (metadata + on-demand content fetch)

Other Odoo modules (Sales, Accounting, Inventory, ...) are still out of
scope; add them as new methods here (or split into per-module files under
this package if it grows the way app/sources/external/salesforce did).

Errors are not swallowed — every call goes straight through OdooClient, which
already raises ConnectionError/RuntimeError on failure. There's no separate
success/error envelope to remember to check.
"""

from __future__ import annotations

import logging
from typing import Any

from pydantic import BaseModel, ConfigDict

from app.sources.client.odoo.odoo import OdooClient

logger = logging.getLogger(__name__)

# "mobile" is deliberately absent from the default field lists below: it
# doesn't exist on crm.lead/res.partner on Odoo saas~19.4 (confirmed live via
# fields_get — search_read raises ValueError on an unknown field name, unlike
# a plain dict.get). The CrmLead/Partner models still declare it as optional
# for older Odoo versions that do have it; pass it explicitly via `fields=`
# if your instance supports it.

DEFAULT_LEAD_FIELDS = [
    "id", "name", "type", "partner_name", "email_from", "phone",
    "stage_id", "team_id", "user_id", "tag_ids",
    "expected_revenue", "probability", "description",
    "priority", "active",
    "date_open", "date_closed", "date_deadline", "lost_reason_id",
    "street", "city", "state_id", "country_id",
    "contact_name", "function", "website",
    "company_id", "referred", "source_id", "medium_id", "campaign_id",
    "create_date", "write_date",
]

DEFAULT_ACTIVITY_FIELDS = [
    "id", "res_id", "res_model", "activity_type_id",
    "summary", "note", "date_deadline", "user_id", "state",
]

DEFAULT_MESSAGE_FIELDS = [
    "id", "res_id", "model", "subject", "body",
    "date", "author_id", "message_type", "subtype_id",
]

DEFAULT_PARTNER_FIELDS = [
    "id", "name", "email", "phone", "function",
    "street", "city", "state_id", "country_id",
    "is_company", "parent_id", "write_date",
]

DEFAULT_USER_FIELDS = ["id", "name", "email", "login", "active"]

DEFAULT_ATTACHMENT_FIELDS = [
    "id", "name", "mimetype", "file_size", "res_id", "res_model",
    "create_date", "checksum",
]


# ---------------------------------------------------------------------------
# Pydantic models. extra="allow" because Odoo installs routinely add custom
# fields via Studio/modules.
#
# Odoo many2one fields (stage_id, team_id, user_id, ...) come back over
# XML-RPC as either [id, "Display Name"] or the literal False when unset —
# typed as `Any` rather than a narrower union, so validation doesn't reject
# either shape. Char fields Odoo may return as `False` when empty are typed
# `str | bool | None`.
# ---------------------------------------------------------------------------

class CrmLead(BaseModel):
    model_config = ConfigDict(extra="allow")
    id: int
    name: str = ""
    type: str = "lead"
    partner_name: str | bool | None = None
    email_from: str | bool | None = None
    phone: str | bool | None = None
    stage_id: Any = None
    team_id: Any = None
    user_id: Any = None
    tag_ids: list[int] = []
    expected_revenue: float = 0.0
    probability: float = 0.0
    description: str | bool | None = None
    priority: str = "0"
    active: bool = True
    date_open: str | bool | None = None
    date_closed: str | bool | None = None
    date_deadline: str | bool | None = None
    lost_reason_id: Any = None
    street: str | bool | None = None
    city: str | bool | None = None
    state_id: Any = None
    country_id: Any = None
    contact_name: str | bool | None = None
    function: str | bool | None = None
    mobile: str | bool | None = None
    website: str | bool | None = None
    company_id: Any = None
    referred: str | bool | None = None
    source_id: Any = None
    medium_id: Any = None
    campaign_id: Any = None
    create_date: str | None = None
    write_date: str | None = None


class CrmStage(BaseModel):
    model_config = ConfigDict(extra="allow")
    id: int
    name: str = ""
    sequence: int = 0
    is_won: bool = False


class CrmTeam(BaseModel):
    model_config = ConfigDict(extra="allow")
    id: int
    name: str = ""


class CrmTag(BaseModel):
    model_config = ConfigDict(extra="allow")
    id: int
    name: str = ""
    color: int = 0


class CrmLostReason(BaseModel):
    model_config = ConfigDict(extra="allow")
    id: int
    name: str = ""


class MailActivity(BaseModel):
    model_config = ConfigDict(extra="allow")
    id: int
    res_id: int | None = None
    res_model: str | None = None
    activity_type_id: Any = None
    summary: str | bool | None = None
    note: str | bool | None = None
    date_deadline: str | bool | None = None
    user_id: Any = None
    state: str | None = None


class MailMessage(BaseModel):
    model_config = ConfigDict(extra="allow")
    id: int
    res_id: int | None = None
    model: str | None = None
    subject: str | bool | None = None
    body: str | bool | None = None
    date: str | bool | None = None
    author_id: Any = None
    message_type: str | None = None
    subtype_id: Any = None


class UtmSource(BaseModel):
    model_config = ConfigDict(extra="allow")
    id: int
    name: str = ""


class UtmMedium(BaseModel):
    model_config = ConfigDict(extra="allow")
    id: int
    name: str = ""


class UtmCampaign(BaseModel):
    model_config = ConfigDict(extra="allow")
    id: int
    name: str = ""


class Partner(BaseModel):
    model_config = ConfigDict(extra="allow")
    id: int
    name: str = ""
    email: str | bool | None = None
    phone: str | bool | None = None
    mobile: str | bool | None = None
    function: str | bool | None = None
    street: str | bool | None = None
    city: str | bool | None = None
    state_id: Any = None
    country_id: Any = None
    is_company: bool = False
    parent_id: Any = None
    write_date: str | None = None


class ResUser(BaseModel):
    model_config = ConfigDict(extra="allow")
    id: int
    name: str = ""
    email: str | bool | None = None
    login: str = ""
    active: bool = True


class Attachment(BaseModel):
    model_config = ConfigDict(extra="allow")
    id: int
    name: str = ""
    mimetype: str | bool | None = None
    file_size: int = 0
    res_id: int | None = None
    res_model: str | None = None
    create_date: str | None = None
    checksum: str | bool | None = None


class OdooDataSource:
    """Typed CRM operations on top of a connected OdooClient."""

    def __init__(self, client: OdooClient) -> None:
        self._client = client

    def get_client(self) -> OdooClient:
        return self._client

    # -- Leads ---------------------------------------------------------

    def _lead_domain(
        self, lead_type: str | None, updated_since: str | None
    ) -> list[list[Any]]:
        domain: list[list[Any]] = []
        if lead_type is not None:
            domain.append(["type", "=", lead_type])
        if updated_since is not None:
            domain.append(["write_date", ">=", updated_since])
        return domain

    async def list_leads(
        self,
        *,
        lead_type: str | None = None,
        updated_since: str | None = None,
        include_archived: bool = False,
        limit: int = 100,
        offset: int = 0,
        fields: list[str] | None = None,
    ) -> list[CrmLead]:
        """List leads/opportunities, oldest-updated first (for incremental sync
        cursors). ``lead_type``: "lead", "opportunity", or None for both.
        ``include_archived``: also return leads with active=False (lost/archived)."""
        kwargs: dict[str, Any] = {
            "fields": fields or DEFAULT_LEAD_FIELDS,
            "limit": limit,
            "offset": offset,
            "order": "write_date asc",
        }
        if include_archived:
            kwargs["context"] = {"active_test": False}
        rows = await self._client.execute_kw(
            "crm.lead", "search_read", [self._lead_domain(lead_type, updated_since)], kwargs
        )
        return [CrmLead.model_validate(row) for row in rows]

    async def get_lead(
        self, lead_id: int, fields: list[str] | None = None
    ) -> CrmLead | None:
        rows = await self._client.execute_kw(
            "crm.lead", "read", [[lead_id]], {"fields": fields or DEFAULT_LEAD_FIELDS}
        )
        return CrmLead.model_validate(rows[0]) if rows else None

    async def count_leads(
        self, lead_type: str | None = None, updated_since: str | None = None
    ) -> int:
        return await self._client.execute_kw(
            "crm.lead", "search_count", [self._lead_domain(lead_type, updated_since)]
        )

    # -- Lookups ---------------------------------------------------------

    async def list_stages(self) -> list[CrmStage]:
        rows = await self._client.execute_kw(
            "crm.stage", "search_read", [[]], {"fields": ["name", "sequence", "is_won"]}
        )
        return [CrmStage.model_validate(row) for row in rows]

    async def list_teams(self) -> list[CrmTeam]:
        rows = await self._client.execute_kw(
            "crm.team", "search_read", [[]], {"fields": ["name"]}
        )
        return [CrmTeam.model_validate(row) for row in rows]

    async def list_tags(self) -> list[CrmTag]:
        rows = await self._client.execute_kw(
            "crm.tag", "search_read", [[]], {"fields": ["name", "color"]}
        )
        return [CrmTag.model_validate(row) for row in rows]

    async def list_lost_reasons(self) -> list[CrmLostReason]:
        rows = await self._client.execute_kw(
            "crm.lost.reason", "search_read", [[]], {"fields": ["name"]}
        )
        return [CrmLostReason.model_validate(row) for row in rows]

    async def list_utm_sources(self) -> list[UtmSource]:
        rows = await self._client.execute_kw(
            "utm.source", "search_read", [[]], {"fields": ["name"]}
        )
        return [UtmSource.model_validate(row) for row in rows]

    async def list_utm_mediums(self) -> list[UtmMedium]:
        rows = await self._client.execute_kw(
            "utm.medium", "search_read", [[]], {"fields": ["name"]}
        )
        return [UtmMedium.model_validate(row) for row in rows]

    async def list_utm_campaigns(self) -> list[UtmCampaign]:
        rows = await self._client.execute_kw(
            "utm.campaign", "search_read", [[]], {"fields": ["name"]}
        )
        return [UtmCampaign.model_validate(row) for row in rows]

    # -- Activities --------------------------------------------------------

    async def list_activities(
        self,
        res_model: str = "crm.lead",
        res_id: int | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[MailActivity]:
        domain: list[list[Any]] = [["res_model", "=", res_model]]
        if res_id is not None:
            domain.append(["res_id", "=", res_id])
        rows = await self._client.execute_kw(
            "mail.activity",
            "search_read",
            [domain],
            {
                "fields": DEFAULT_ACTIVITY_FIELDS,
                "limit": limit,
                "offset": offset,
                "order": "date_deadline asc",
            },
        )
        return [MailActivity.model_validate(row) for row in rows]

    async def create_activity(
        self,
        res_model: str,
        res_id: int,
        activity_type_id: int,
        summary: str | None = None,
        note: str | None = None,
        date_deadline: str | None = None,
        user_id: int | None = None,
    ) -> int:
        values: dict[str, Any] = {
            "res_model": res_model,
            "res_id": res_id,
            "activity_type_id": activity_type_id,
        }
        if summary is not None:
            values["summary"] = summary
        if note is not None:
            values["note"] = note
        if date_deadline is not None:
            values["date_deadline"] = date_deadline
        if user_id is not None:
            values["user_id"] = user_id
        return await self._client.execute_kw("mail.activity", "create", [values])

    # -- Notes & chatter -----------------------------------------------------

    async def list_messages(
        self,
        res_model: str = "crm.lead",
        res_id: int | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[MailMessage]:
        domain: list[list[Any]] = [["model", "=", res_model]]
        if res_id is not None:
            domain.append(["res_id", "=", res_id])
        rows = await self._client.execute_kw(
            "mail.message",
            "search_read",
            [domain],
            {
                "fields": DEFAULT_MESSAGE_FIELDS,
                "limit": limit,
                "offset": offset,
                "order": "date desc",
            },
        )
        return [MailMessage.model_validate(row) for row in rows]

    async def add_note(self, res_model: str, res_id: int, body: str) -> int:
        """Post a chatter note via the model's mail.thread mixin — the
        standard way to write a note in Odoo, works on any model."""
        return await self._client.execute_kw(res_model, "message_post", [[res_id]], {"body": body})

    # -- Contacts ------------------------------------------------------------

    async def list_partners(
        self,
        *,
        updated_since: str | None = None,
        limit: int = 100,
        offset: int = 0,
        fields: list[str] | None = None,
    ) -> list[Partner]:
        domain: list[list[Any]] = []
        if updated_since is not None:
            domain.append(["write_date", ">=", updated_since])
        rows = await self._client.execute_kw(
            "res.partner",
            "search_read",
            [domain],
            {
                "fields": fields or DEFAULT_PARTNER_FIELDS,
                "limit": limit,
                "offset": offset,
                "order": "write_date asc",
            },
        )
        return [Partner.model_validate(row) for row in rows]

    async def get_partner(
        self, partner_id: int, fields: list[str] | None = None
    ) -> Partner | None:
        rows = await self._client.execute_kw(
            "res.partner", "read", [[partner_id]], {"fields": fields or DEFAULT_PARTNER_FIELDS}
        )
        return Partner.model_validate(rows[0]) if rows else None

    async def count_partners(self, updated_since: str | None = None) -> int:
        domain: list[list[Any]] = []
        if updated_since is not None:
            domain.append(["write_date", ">=", updated_since])
        return await self._client.execute_kw("res.partner", "search_count", [domain])

    async def list_users(self, *, include_inactive: bool = False) -> list[ResUser]:
        """Salespersons — used to resolve a lead/activity's user_id to a
        real name/email instead of leaving it as a raw many2one tuple."""
        kwargs: dict[str, Any] = {"fields": DEFAULT_USER_FIELDS}
        if include_inactive:
            kwargs["context"] = {"active_test": False}
        rows = await self._client.execute_kw("res.users", "search_read", [[]], kwargs)
        return [ResUser.model_validate(row) for row in rows]

    # -- Attachments -----------------------------------------------------------

    async def list_attachments(
        self,
        res_model: str = "crm.lead",
        res_id: int | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[Attachment]:
        """Metadata only — no binary content (see get_attachment_content)."""
        domain: list[list[Any]] = [["res_model", "=", res_model]]
        if res_id is not None:
            domain.append(["res_id", "=", res_id])
        rows = await self._client.execute_kw(
            "ir.attachment",
            "search_read",
            [domain],
            {"fields": DEFAULT_ATTACHMENT_FIELDS, "limit": limit, "offset": offset},
        )
        return [Attachment.model_validate(row) for row in rows]

    async def get_attachment_content(self, attachment_id: int) -> str | None:
        """Base64-encoded file content, fetched on demand (not part of
        list_attachments — binary payloads shouldn't ride along on a listing call)."""
        rows = await self._client.execute_kw(
            "ir.attachment", "read", [[attachment_id]], {"fields": ["datas"]}
        )
        if not rows or not rows[0].get("datas"):
            return None
        return rows[0]["datas"]
