"""Odoo external data source package."""
from app.sources.external.odoo.odoo import (
    Attachment,
    CrmLead,
    CrmLostReason,
    CrmStage,
    CrmTag,
    CrmTeam,
    MailActivity,
    MailMessage,
    OdooDataSource,
    Partner,
    ResUser,
    UtmCampaign,
    UtmMedium,
    UtmSource,
)

__all__ = [
    "OdooDataSource",
    "CrmLead",
    "CrmStage",
    "CrmTeam",
    "CrmTag",
    "CrmLostReason",
    "MailActivity",
    "MailMessage",
    "UtmSource",
    "UtmMedium",
    "UtmCampaign",
    "Partner",
    "ResUser",
    "Attachment",
]
