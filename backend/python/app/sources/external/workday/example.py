# backend/python/app/sources/external/workday/example.py
"""Mock example usage of WorkdayClient and WorkdayDataSource (no real token required)."""

from app.sources.client.workday.workday import WorkdayClient, WorkdayTokenConfig
from app.sources.external.workday.workday import WorkdayDataSource


def main() -> None:
    # Fake configuration for testing only
    config = WorkdayTokenConfig(
        base_url="https://mock.workday.com/ccx/api/v1/test_tenant",
        access_token="FAKE_ACCESS_TOKEN"
    )

    client = WorkdayClient.build_with_config(config)
    datasource = WorkdayDataSource(client)

    # Since we don't have a real Workday environment, just mock data
    print("✅ Workday client initialized (mock mode)")
    print("🧱 Base URL:", config.base_url)
    print("🔑 Token:", config.access_token[:5] + "...")


if __name__ == "__main__":
    main()
