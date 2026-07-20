"""Unit tests for OdooDataSource (CRM module). execute_kw is mocked — these
tests only exercise domain-building and response parsing, not the transport
(that's covered by test_odoo_client.py)."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.sources.external.odoo.odoo import (
    DEFAULT_LEAD_FIELDS,
    Attachment,
    CrmLead,
    CrmLostReason,
    CrmStage,
    CrmTag,
    CrmTeam,
    MailActivity,
    MailFollower,
    MailMessage,
    OdooDataSource,
    Partner,
    ResUser,
    UtmCampaign,
    UtmMedium,
    UtmSource,
)


@pytest.fixture
def client():
    c = MagicMock()
    c.execute_kw = AsyncMock()
    return c


@pytest.fixture
def data_source(client):
    return OdooDataSource(client)


class TestListLeads:
    async def test_default_call_shape(self, data_source, client):
        client.execute_kw.return_value = []
        await data_source.list_leads()
        client.execute_kw.assert_awaited_once_with(
            "crm.lead",
            "search_read",
            [[]],
            {
                "fields": DEFAULT_LEAD_FIELDS,
                "limit": 100,
                "offset": 0,
                "order": "write_date asc",
            },
        )

    async def test_filters_by_type_and_updated_since(self, data_source, client):
        client.execute_kw.return_value = []
        await data_source.list_leads(lead_type="opportunity", updated_since="2026-01-01 00:00:00")
        args = client.execute_kw.await_args.args
        domain = args[2][0]
        assert ["type", "=", "opportunity"] in domain
        assert ["write_date", ">=", "2026-01-01 00:00:00"] in domain

    async def test_pagination_forwarded(self, data_source, client):
        client.execute_kw.return_value = []
        await data_source.list_leads(limit=25, offset=50)
        kwargs = client.execute_kw.await_args.args[3]
        assert kwargs["limit"] == 25
        assert kwargs["offset"] == 50

    async def test_parses_rows_into_crm_lead(self, data_source, client):
        client.execute_kw.return_value = [
            {
                "id": 1,
                "name": "Big Deal",
                "type": "opportunity",
                "partner_name": "Acme",
                "email_from": "buyer@acme.test",
                "phone": False,
                "stage_id": [3, "Qualified"],
                "team_id": [1, "Sales"],
                "user_id": False,
                "tag_ids": [7, 8],
                "expected_revenue": 5000.0,
                "probability": 30.0,
                "description": False,
                "create_date": "2026-01-01 00:00:00",
                "write_date": "2026-01-02 00:00:00",
            }
        ]
        leads = await data_source.list_leads()
        assert len(leads) == 1
        lead = leads[0]
        assert isinstance(lead, CrmLead)
        assert lead.id == 1
        assert lead.stage_id == [3, "Qualified"]
        assert lead.user_id is False
        assert lead.tag_ids == [7, 8]

    async def test_custom_fields_forwarded(self, data_source, client):
        client.execute_kw.return_value = []
        await data_source.list_leads(fields=["id", "name"])
        kwargs = client.execute_kw.await_args.args[3]
        assert kwargs["fields"] == ["id", "name"]

    async def test_include_archived_sets_active_test_context(self, data_source, client):
        client.execute_kw.return_value = []
        await data_source.list_leads(include_archived=True)
        kwargs = client.execute_kw.await_args.args[3]
        assert kwargs["context"] == {"active_test": False}

    async def test_default_excludes_archived_context(self, data_source, client):
        client.execute_kw.return_value = []
        await data_source.list_leads()
        kwargs = client.execute_kw.await_args.args[3]
        assert "context" not in kwargs

    async def test_parses_expanded_field_set(self, data_source, client):
        client.execute_kw.return_value = [
            {
                "id": 1,
                "name": "Deal",
                "priority": "2",
                "active": False,
                "date_deadline": "2026-02-01",
                "lost_reason_id": [5, "Too expensive"],
                "street": "1 Main St",
                "city": "Mumbai",
                "state_id": False,
                "country_id": [1, "India"],
                "contact_name": "Jane Doe",
                "function": "CTO",
                "mobile": "+91123456",
                "website": "https://acme.test",
                "company_id": [1, "My Company"],
                "referred": False,
                "source_id": [2, "Website"],
                "medium_id": False,
                "campaign_id": False,
            }
        ]
        lead = (await data_source.list_leads())[0]
        assert lead.priority == "2"
        assert lead.active is False
        assert lead.lost_reason_id == [5, "Too expensive"]
        assert lead.contact_name == "Jane Doe"
        assert lead.source_id == [2, "Website"]


class TestGetLead:
    async def test_found_returns_crm_lead(self, data_source, client):
        client.execute_kw.return_value = [{"id": 42, "name": "Solo Lead"}]
        lead = await data_source.get_lead(42)
        assert isinstance(lead, CrmLead)
        assert lead.id == 42
        client.execute_kw.assert_awaited_once()
        model, method, args, kwargs = client.execute_kw.await_args.args
        assert (model, method, args) == ("crm.lead", "read", [[42]])

    async def test_not_found_returns_none(self, data_source, client):
        client.execute_kw.return_value = []
        lead = await data_source.get_lead(999)
        assert lead is None


class TestCountLeads:
    async def test_count_passes_domain_through(self, data_source, client):
        client.execute_kw.return_value = 17
        count = await data_source.count_leads(lead_type="lead")
        assert count == 17
        model, method, args = client.execute_kw.await_args.args
        assert model == "crm.lead"
        assert method == "search_count"
        assert args == [[["type", "=", "lead"]]]

    async def test_no_filters_uses_empty_domain(self, data_source, client):
        client.execute_kw.return_value = 5
        await data_source.count_leads()
        args = client.execute_kw.await_args.args[2]
        assert args == [[]]


class TestLookupLists:
    async def test_list_stages(self, data_source, client):
        client.execute_kw.return_value = [
            {"id": 1, "name": "New", "sequence": 1, "is_won": False},
            {"id": 4, "name": "Won", "sequence": 4, "is_won": True},
        ]
        stages = await data_source.list_stages()
        assert [s.name for s in stages] == ["New", "Won"]
        assert all(isinstance(s, CrmStage) for s in stages)
        model, method, args, kwargs = client.execute_kw.await_args.args
        assert (model, method) == ("crm.stage", "search_read")

    async def test_list_teams(self, data_source, client):
        client.execute_kw.return_value = [{"id": 1, "name": "Sales"}]
        teams = await data_source.list_teams()
        assert teams == [CrmTeam(id=1, name="Sales")]

    async def test_list_tags(self, data_source, client):
        client.execute_kw.return_value = [{"id": 1, "name": "Hot", "color": 2}]
        tags = await data_source.list_tags()
        assert tags == [CrmTag(id=1, name="Hot", color=2)]

    async def test_list_lost_reasons(self, data_source, client):
        client.execute_kw.return_value = [{"id": 1, "name": "Too expensive"}]
        reasons = await data_source.list_lost_reasons()
        assert reasons == [CrmLostReason(id=1, name="Too expensive")]
        model, method, args, kwargs = client.execute_kw.await_args.args
        assert (model, method) == ("crm.lost.reason", "search_read")

    async def test_list_utm_sources(self, data_source, client):
        client.execute_kw.return_value = [{"id": 1, "name": "Website"}]
        sources = await data_source.list_utm_sources()
        assert sources == [UtmSource(id=1, name="Website")]

    async def test_list_utm_mediums(self, data_source, client):
        client.execute_kw.return_value = [{"id": 1, "name": "Email"}]
        mediums = await data_source.list_utm_mediums()
        assert mediums == [UtmMedium(id=1, name="Email")]

    async def test_list_utm_campaigns(self, data_source, client):
        client.execute_kw.return_value = [{"id": 1, "name": "Spring Promo"}]
        campaigns = await data_source.list_utm_campaigns()
        assert campaigns == [UtmCampaign(id=1, name="Spring Promo")]


class TestActivities:
    async def test_list_activities_for_a_lead(self, data_source, client):
        client.execute_kw.return_value = [
            {"id": 1, "res_id": 42, "res_model": "crm.lead", "summary": "Call back",
             "date_deadline": "2026-02-01", "state": "planned"}
        ]
        activities = await data_source.list_activities(res_model="crm.lead", res_id=42)
        assert len(activities) == 1
        assert isinstance(activities[0], MailActivity)
        args = client.execute_kw.await_args.args
        domain = args[2][0]
        assert ["res_model", "=", "crm.lead"] in domain
        assert ["res_id", "=", 42] in domain

    async def test_list_activities_without_res_id_scopes_by_model_only(self, data_source, client):
        client.execute_kw.return_value = []
        await data_source.list_activities(res_model="crm.lead")
        domain = client.execute_kw.await_args.args[2][0]
        assert domain == [["res_model", "=", "crm.lead"]]

    async def test_create_activity_minimal(self, data_source, client):
        client.execute_kw.return_value = 7
        activity_id = await data_source.create_activity("crm.lead", 42, activity_type_id=1)
        assert activity_id == 7
        client.execute_kw.assert_awaited_once_with(
            "mail.activity",
            "create",
            [{"res_model": "crm.lead", "res_id": 42, "activity_type_id": 1}],
        )

    async def test_create_activity_with_optional_fields(self, data_source, client):
        client.execute_kw.return_value = 8
        await data_source.create_activity(
            "crm.lead", 42, activity_type_id=1,
            summary="Follow up", note="Call about pricing",
            date_deadline="2026-02-05", user_id=3,
        )
        values = client.execute_kw.await_args.args[2][0]
        assert values["summary"] == "Follow up"
        assert values["note"] == "Call about pricing"
        assert values["date_deadline"] == "2026-02-05"
        assert values["user_id"] == 3


class TestMessages:
    async def test_list_messages_for_a_lead(self, data_source, client):
        client.execute_kw.return_value = [
            {"id": 1, "res_id": 42, "model": "crm.lead", "body": "<p>Note</p>"}
        ]
        messages = await data_source.list_messages(res_model="crm.lead", res_id=42)
        assert len(messages) == 1
        assert isinstance(messages[0], MailMessage)
        domain = client.execute_kw.await_args.args[2][0]
        assert ["model", "=", "crm.lead"] in domain
        assert ["res_id", "=", 42] in domain

    async def test_add_note_posts_via_message_post(self, data_source, client):
        client.execute_kw.return_value = 99
        message_id = await data_source.add_note("crm.lead", 42, "Called, left voicemail")
        assert message_id == 99
        client.execute_kw.assert_awaited_once_with(
            "crm.lead", "message_post", [[42]], {"body": "Called, left voicemail"}
        )


class TestPartners:
    async def test_list_partners_default(self, data_source, client):
        client.execute_kw.return_value = [{"id": 1, "name": "Acme"}]
        partners = await data_source.list_partners()
        assert len(partners) == 1
        assert isinstance(partners[0], Partner)
        args, kwargs = client.execute_kw.await_args.args[2], client.execute_kw.await_args.args[3]
        assert args == [[]]
        assert kwargs["limit"] == 100

    async def test_list_partners_filters_by_updated_since(self, data_source, client):
        client.execute_kw.return_value = []
        await data_source.list_partners(updated_since="2026-01-01 00:00:00")
        domain = client.execute_kw.await_args.args[2][0]
        assert ["write_date", ">=", "2026-01-01 00:00:00"] in domain

    async def test_get_partner_found(self, data_source, client):
        client.execute_kw.return_value = [{"id": 5, "name": "Bob"}]
        partner = await data_source.get_partner(5)
        assert partner == Partner.model_validate({"id": 5, "name": "Bob"})

    async def test_get_partner_not_found(self, data_source, client):
        client.execute_kw.return_value = []
        assert await data_source.get_partner(999) is None

    async def test_count_partners(self, data_source, client):
        client.execute_kw.return_value = 3
        count = await data_source.count_partners()
        assert count == 3
        model, method, args = client.execute_kw.await_args.args
        assert (model, method, args) == ("res.partner", "search_count", [[]])


class TestUsers:
    async def test_list_users_default(self, data_source, client):
        client.execute_kw.return_value = [
            {"id": 2, "name": "Anup Pradhan", "email": "anup@ruki.test", "login": "anup@ruki.test", "active": True}
        ]
        users = await data_source.list_users()
        assert len(users) == 1
        assert isinstance(users[0], ResUser)
        model, method, args, kwargs = client.execute_kw.await_args.args
        assert (model, method, args) == ("res.users", "search_read", [[]])
        assert "context" not in kwargs

    async def test_list_users_include_inactive_sets_context(self, data_source, client):
        client.execute_kw.return_value = []
        await data_source.list_users(include_inactive=True)
        kwargs = client.execute_kw.await_args.args[3]
        assert kwargs["context"] == {"active_test": False}


class TestFollowers:
    async def test_list_followers_bulk_by_res_ids(self, data_source, client):
        client.execute_kw.return_value = [
            {"id": 1, "res_id": 42, "partner_id": [7, "Anup Pradhan"]},
            {"id": 2, "res_id": 43, "partner_id": [9, "External Contact"]},
        ]
        followers = await data_source.list_followers("crm.lead", [42, 43])
        assert len(followers) == 2
        assert all(isinstance(f, MailFollower) for f in followers)
        assert followers[0].res_id == 42
        domain = client.execute_kw.await_args.args[2][0]
        assert ["res_model", "=", "crm.lead"] in domain
        assert ["res_id", "in", [42, 43]] in domain

    async def test_list_followers_empty_ids_skips_call(self, data_source, client):
        followers = await data_source.list_followers("crm.lead", [])
        assert followers == []
        client.execute_kw.assert_not_awaited()


class TestAttachments:
    async def test_list_attachments_for_a_lead(self, data_source, client):
        client.execute_kw.return_value = [
            {"id": 1, "name": "quote.pdf", "res_id": 42, "res_model": "crm.lead"}
        ]
        attachments = await data_source.list_attachments(res_model="crm.lead", res_id=42)
        assert len(attachments) == 1
        assert isinstance(attachments[0], Attachment)
        domain = client.execute_kw.await_args.args[2][0]
        assert ["res_model", "=", "crm.lead"] in domain
        assert ["res_id", "=", 42] in domain
        fields = client.execute_kw.await_args.args[3]["fields"]
        assert "datas" not in fields

    async def test_get_attachment_content_found(self, data_source, client):
        client.execute_kw.return_value = [{"datas": "base64stufff=="}]
        content = await data_source.get_attachment_content(1)
        assert content == "base64stufff=="
        client.execute_kw.assert_awaited_once_with(
            "ir.attachment", "read", [[1]], {"fields": ["datas"]}
        )

    async def test_get_attachment_content_missing_returns_none(self, data_source, client):
        client.execute_kw.return_value = [{"datas": False}]
        assert await data_source.get_attachment_content(1) is None

    async def test_get_attachment_content_not_found_returns_none(self, data_source, client):
        client.execute_kw.return_value = []
        assert await data_source.get_attachment_content(999) is None


class TestGetClient:
    def test_returns_underlying_client(self, data_source, client):
        assert data_source.get_client() is client
