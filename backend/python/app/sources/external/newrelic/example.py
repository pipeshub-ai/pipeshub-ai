# ruff: noqa
"""
NewRelic NerdGraph API Usage Examples

This example demonstrates how to use the NewRelic DataSource to interact
with the NewRelic NerdGraph (GraphQL) API, covering:
- Authentication (API Key)
- Initializing the Client and DataSource
- Listing accounts
- Executing NRQL queries
- Searching entities
- Listing dashboards, alert policies, synthetics monitors
- Getting APM application details

Prerequisites:
1. Generate a NewRelic API key at https://one.newrelic.com/api-keys
2. Set environment variables:
   - NEWRELIC_API_KEY: NewRelic API key (e.g., NRAK-XXXXX)
   - NEWRELIC_ACCOUNT_ID: (optional) NewRelic account ID for NRQL queries

NerdGraph Reference: https://docs.newrelic.com/docs/apis/nerdgraph/get-started/introduction-new-relic-nerdgraph/
"""

import asyncio
import json
import os

from app.sources.client.newrelic.newrelic import NewRelicClient, NewRelicApiKeyConfig
from app.sources.external.newrelic.newrelic import NewRelicDataSource
from app.sources.client.graphql.response import GraphQLResponse


def print_section(title: str):
    print(f"\n{'-'*80}")
    print(f"| {title}")
    print(f"{'-'*80}")


def print_result(name: str, response: GraphQLResponse, show_data: bool = True):
    if response.success:
        print(f"  {name}: Success")
        if show_data and response.data:
            print(f"   Data: {json.dumps(response.data, indent=2, default=str)[:500]}...")
    else:
        print(f"  {name}: Failed")
        if response.errors:
            for error in response.errors:
                print(f"   Error: {error.message}")
        if response.message:
            print(f"   Message: {response.message}")


async def main() -> None:
    """Example usage of NewRelic NerdGraph API."""
    API_KEY = os.getenv("NEWRELIC_API_KEY")
    ACCOUNT_ID = os.getenv("NEWRELIC_ACCOUNT_ID")

    if not API_KEY:
        print("Please set NEWRELIC_API_KEY environment variable")
        print("   Get a key from https://one.newrelic.com/api-keys")
        return

    # Initialize NewRelic client
    print_section("Initializing NewRelic Client")
    print("  Using API key authentication")
    config = NewRelicApiKeyConfig(api_key=API_KEY)
    client = NewRelicClient.build_with_config(config)
    data_source = NewRelicDataSource(client)
    print("  Client initialized successfully.")

    try:
        # 1. List accounts
        print_section("Accounts")
        accounts_resp = await data_source.list_accounts()
        print_result("List Accounts", accounts_resp)

        # Determine account ID for subsequent queries
        account_id = None
        if ACCOUNT_ID:
            account_id = int(ACCOUNT_ID)
        elif accounts_resp.success and accounts_resp.data:
            accounts = accounts_resp.data.get("actor", {}).get("accounts", [])
            if accounts:
                account_id = accounts[0].get("id")
                print(f"   Using first account: {accounts[0].get('name')} (ID: {account_id})")

        # 2. Get specific account
        if account_id:
            print_section(f"Account Details (ID: {account_id})")
            account_resp = await data_source.get_account(account_id)
            print_result("Get Account", account_resp)

            # 3. Execute NRQL query
            print_section("NRQL Query")
            nrql_resp = await data_source.nrql_query(
                account_id=account_id,
                nrql_query="SELECT count(*) FROM Transaction SINCE 1 day ago",
            )
            print_result("NRQL Query", nrql_resp)

            # 4. List alert policies
            print_section("Alert Policies")
            policies_resp = await data_source.list_alert_policies(account_id)
            print_result("List Alert Policies", policies_resp)

        # 5. Search entities
        print_section("Entity Search")
        entities_resp = await data_source.list_entities()
        print_result("List Entities", entities_resp)

        # 6. List dashboards
        print_section("Dashboards")
        dashboards_resp = await data_source.list_dashboards()
        print_result("List Dashboards", dashboards_resp)

        # 7. List synthetics monitors
        print_section("Synthetics Monitors")
        monitors_resp = await data_source.list_synthetics_monitors()
        print_result("List Synthetics Monitors", monitors_resp)

        # 8. Get specific entity (if we found any)
        if entities_resp.success and entities_resp.data:
            search_results = (
                entities_resp.data
                .get("actor", {})
                .get("entitySearch", {})
                .get("results", {})
                .get("entities", [])
            )
            if search_results:
                first_entity = search_results[0]
                entity_guid = first_entity.get("guid", "")
                if entity_guid:
                    print_section(f"Entity Details (GUID: {entity_guid[:30]}...)")
                    entity_resp = await data_source.get_entity(entity_guid)
                    print_result("Get Entity", entity_resp)

    finally:
        # Close the client
        await client.get_client().close()

    print("\n" + "=" * 80)
    print("  All NewRelic NerdGraph API operations tested!")
    print("=" * 80)


if __name__ == "__main__":
    asyncio.run(main())
