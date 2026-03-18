# ruff: noqa

"""
Amplitude API Usage Examples

This example demonstrates how to use the Amplitude DataSource to interact with
the Amplitude Analytics API, covering:
- Authentication (API Key + Secret Key via Basic Auth)
- Initializing the Client and DataSource
- Listing Event Types (Taxonomy)
- Listing Cohorts
- Listing User Properties
- Listing Annotations

Prerequisites:
1. Create an Amplitude project at https://analytics.amplitude.com
2. Go to Settings > Projects > [Your Project] > General
3. Copy the API Key and Secret Key
4. Set environment variables:
   - AMPLITUDE_API_KEY: Your project's API key
   - AMPLITUDE_SECRET_KEY: Your project's secret key

API Reference: https://www.docs.developers.amplitude.com/analytics/apis/
"""

import asyncio
import json
import os

from app.sources.client.amplitude.amplitude import (
    AmplitudeApiKeyConfig,
    AmplitudeClient,
    AmplitudeResponse,
)
from app.sources.external.amplitude.amplitude import AmplitudeDataSource


def print_section(title: str):
    print(f"\n{'-'*80}")
    print(f"| {title}")
    print(f"{'-'*80}")


def print_result(name: str, response: AmplitudeResponse, show_data: bool = True):
    if response.success:
        print(f"  {name}: Success")
        if show_data and response.data:
            data = response.data
            if isinstance(data, dict):
                # Try to show a summary
                for key in ("data", "events", "cohorts", "annotations",
                            "releases", "matches", "userData"):
                    if key in data:
                        items = data[key]
                        if isinstance(items, list):
                            print(f"   Found {len(items)} {key}.")
                            if items:
                                print(f"   Sample: {json.dumps(items[0], indent=2, default=str)[:400]}...")
                        elif isinstance(items, dict):
                            print(f"   {key}: {json.dumps(items, indent=2, default=str)[:400]}...")
                        else:
                            print(f"   {key}: {items}")
                        return
                # Generic dict response
                print(f"   Data: {json.dumps(data, indent=2, default=str)[:500]}...")
            elif isinstance(data, list):
                print(f"   Found {len(data)} items.")
                if data:
                    print(f"   Sample: {json.dumps(data[0], indent=2, default=str)[:400]}...")
    else:
        print(f"  {name}: Failed")
        print(f"   Error: {response.error}")
        if response.message:
            print(f"   Message: {response.message}")


async def main() -> None:
    # 1. Initialize Client
    print_section("Initializing Amplitude Client")

    api_key = os.getenv("AMPLITUDE_API_KEY")
    secret_key = os.getenv("AMPLITUDE_SECRET_KEY")

    if not api_key:
        print("  AMPLITUDE_API_KEY environment variable is not set.")
        print("  Please set it to your Amplitude project's API key.")
        return

    if not secret_key:
        print("  AMPLITUDE_SECRET_KEY environment variable is not set.")
        print("  Please set it to your Amplitude project's secret key.")
        return

    config = AmplitudeApiKeyConfig(
        api_key=api_key,
        secret_key=secret_key,
    )
    client = AmplitudeClient.build_with_config(config)
    data_source = AmplitudeDataSource(client)
    print(f"  Client initialized successfully.")
    print(f"  Base URL (v2): {client.get_base_url()}")
    print(f"  Base URL (v3): {client.get_base_url_v3()}")

    try:
        # 2. List Event Types (Taxonomy)
        print_section("Event Types (Taxonomy)")
        event_types_resp = await data_source.list_event_types()
        print_result("List Event Types", event_types_resp)

        # 3. List Cohorts
        print_section("Cohorts")
        cohorts_resp = await data_source.list_cohorts()
        print_result("List Cohorts", cohorts_resp)

        # 4. List User Properties (Taxonomy)
        print_section("User Properties (Taxonomy)")
        user_props_resp = await data_source.list_user_properties()
        print_result("List User Properties", user_props_resp)

        # 5. List Event Properties (Taxonomy)
        print_section("Event Properties (Taxonomy)")
        event_props_resp = await data_source.list_event_properties()
        print_result("List Event Properties", event_props_resp)

        # 6. List Annotations
        print_section("Annotations")
        annotations_resp = await data_source.list_annotations()
        print_result("List Annotations", annotations_resp)

        # 7. List Releases
        print_section("Releases")
        releases_resp = await data_source.list_releases()
        print_result("List Releases", releases_resp)

    finally:
        # Cleanup: Close the HTTP client session
        print("\nClosing client connection...")
        inner_client = client.get_client()
        if hasattr(inner_client, "close"):
            await inner_client.close()

    print("\n" + "=" * 80)
    print("  All Amplitude API operations tested!")
    print("=" * 80)


if __name__ == "__main__":
    asyncio.run(main())
