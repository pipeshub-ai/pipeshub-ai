"""
Odoo Data Source Example

Demonstrates how to use OdooDataSource to connect to a real Odoo instance
and read CRM data (leads, stages, teams, contacts, activities, notes...).

Usage:
    cd backend/python
    python -m app.sources.external.odoo.example

Environment Variables (Required):
    ODOO_URL          - Odoo instance URL (e.g., https://mycompany.odoo.com)
    ODOO_DB           - Odoo database name
    ODOO_USERNAME     - Odoo login/email
    ODOO_API_KEY      - Odoo API key (Settings > Users > Account Security > API Keys)

Falls back to ODOO_TEST_BASE_URL / ODOO_TEST_DB / ODOO_TEST_USERNAME /
ODOO_TEST_API_KEY (the integration-tests/.env.local names) if the plain
names above aren't set, so this can reuse the same local credentials.
"""

import asyncio
import os

from app.sources.client.odoo.odoo import OdooClient
from app.sources.external.odoo.odoo import OdooDataSource

URL = os.getenv("ODOO_URL") or os.getenv("ODOO_TEST_BASE_URL")
DB = os.getenv("ODOO_DB") or os.getenv("ODOO_TEST_DB")
USERNAME = os.getenv("ODOO_USERNAME") or os.getenv("ODOO_TEST_USERNAME")
API_KEY = os.getenv("ODOO_API_KEY") or os.getenv("ODOO_TEST_API_KEY")


def print_header(title: str) -> None:
    print(f"\n{'=' * 60}")
    print(f"  {title}")
    print(f"{'=' * 60}")


def print_subheader(title: str) -> None:
    print(f"\n  --- {title} ---")


async def main() -> None:
    print_header("Odoo Data Source Example")

    if not all([URL, DB, USERNAME, API_KEY]):
        print("\n[ERROR] Missing required environment variables.")
        print("Set: ODOO_URL, ODOO_DB, ODOO_USERNAME, ODOO_API_KEY")
        print("(or ODOO_TEST_BASE_URL / ODOO_TEST_DB / ODOO_TEST_USERNAME / ODOO_TEST_API_KEY)")
        return

    print("\nConnection Details:")
    print(f"  URL: {URL}")
    print(f"  DB: {DB}")
    print(f"  Username: {USERNAME}")

    print_header("Step 1: Connecting")
    client = OdooClient(url=URL, db=DB, username=USERNAME, api_key=API_KEY)
    try:
        await client.connect()
        print(f"[OK] Connected — {client.get_connection_info()}")
    except ConnectionError as e:
        print(f"[ERROR] Failed to connect: {e}")
        return

    data_source = OdooDataSource(client)

    print_header("Step 2: CRM Lookups (stages / teams / tags / lost reasons)")
    stages = await data_source.list_stages()
    print(f"Stages ({len(stages)}): {[s.name for s in stages]}")
    teams = await data_source.list_teams()
    print(f"Teams ({len(teams)}): {[t.name for t in teams]}")
    tags = await data_source.list_tags()
    print(f"Tags ({len(tags)}): {[t.name for t in tags]}")
    lost_reasons = await data_source.list_lost_reasons()
    print(f"Lost reasons ({len(lost_reasons)}): {[r.name for r in lost_reasons]}")

    print_header("Step 3: Marketing Attribution (UTM)")
    sources = await data_source.list_utm_sources()
    print(f"UTM sources ({len(sources)}): {[s.name for s in sources]}")
    mediums = await data_source.list_utm_mediums()
    print(f"UTM mediums ({len(mediums)}): {[m.name for m in mediums]}")
    campaigns = await data_source.list_utm_campaigns()
    print(f"UTM campaigns ({len(campaigns)}): {[c.name for c in campaigns]}")

    print_header("Step 4: Salespersons (res.users)")
    users = await data_source.list_users()
    print(f"Users ({len(users)}):")
    for u in users:
        print(f"  - {u.name} <{u.email}>")

    print_header("Step 5: Leads / Opportunities")
    lead_count = await data_source.count_leads()
    print(f"Total leads/opportunities: {lead_count}")
    leads = await data_source.list_leads(limit=5)
    print(f"\nShowing first {len(leads)}:")
    for lead in leads:
        print(f"  - [{lead.id}] {lead.name} ({lead.type}, stage={lead.stage_id})")

    if leads:
        first = leads[0]
        print_subheader(f"Details for lead #{first.id}: {first.name}")
        print(f"  Contact: {first.contact_name} / {first.function}")
        print(f"  Address: {first.street}, {first.city}")
        print(f"  Priority: {first.priority}  Probability: {first.probability}")

        print_subheader("Activities")
        activities = await data_source.list_activities(res_model="crm.lead", res_id=first.id)
        for a in activities:
            print(f"  - {a.summary} (deadline={a.date_deadline}, state={a.state})")
        if not activities:
            print("  (none)")

        print_subheader("Chatter / Notes")
        messages = await data_source.list_messages(res_model="crm.lead", res_id=first.id, limit=5)
        for m in messages:
            print(f"  - [{m.date}] {m.subject or '(no subject)'}")
        if not messages:
            print("  (none)")

        print_subheader("Attachments")
        attachments = await data_source.list_attachments(res_model="crm.lead", res_id=first.id)
        for att in attachments:
            print(f"  - {att.name} ({att.mimetype}, {att.file_size} bytes)")
        if not attachments:
            print("  (none)")

    print_header("Step 6: Contacts (res.partner)")
    partner_count = await data_source.count_partners()
    print(f"Total partners: {partner_count}")
    partners = await data_source.list_partners(limit=5)
    for p in partners:
        print(f"  - [{p.id}] {p.name} <{p.email}>")

    print_header("Cleanup")
    await client.close()
    print("[OK] Connection closed")

    print_header("Done")


if __name__ == "__main__":
    asyncio.run(main())
