# ...existing code...
"""Mock / real example usage of WorkdayClient and WorkdayDataSource.

This example prefers real configuration from environment variables:
- WORKDAY_BASE_URL
- WORKDAY_ACCESS_TOKEN

If those are not set it falls back to the previous mock values for quick testing.
"""

import os
from typing import Optional

from app.sources.client.workday.workday import WorkdayClient, WorkdayTokenConfig
from app.sources.external.workday.workday import WorkdayDataSource

# ...existing code...
def load_config_from_env() -> WorkdayTokenConfig:
    """Load WorkdayTokenConfig from environment, with safe defaults for local testing."""
    base_url = os.getenv("WORKDAY_BASE_URL") or "https://mock.workday.com/ccx/api/v1/test_tenant"
    access_token = os.getenv("WORKDAY_ACCESS_TOKEN") or "FAKE_ACCESS_TOKEN"
    return WorkdayTokenConfig(base_url=base_url, access_token=access_token)

# ...existing code...
def main() -> None:
    config = load_config_from_env()

    client = WorkdayClient.build_with_config(config)
    datasource = WorkdayDataSource(client)

    print("✅ Workday client initialized")
    print("🧱 Base URL:", config.base_url)
    print("🔑 Token preview:", (config.access_token[:5] + "...") if config.access_token else "(no token)")

    # Try a few common method names that a datasource might implement.
    tried: Optional[str] = None
    for method_name in ("fetch", "list", "list_workers", "get_workers", "query", "ping"):
        if hasattr(datasource, method_name):
            tried = method_name
            method = getattr(datasource, method_name)
            try:
                result = method()  # call without args; if real API needs args adjust accordingly
                print(f"ℹ️ Called datasource.{method_name}(), result type: {type(result).__name__}")
                # For safety, print a short repr
                print("Result preview:", repr(result)[:1000])
            except TypeError:
                print(f"ℹ️ datasource.{method_name} exists but requires parameters — adjust call in this example.")
            except Exception as exc:
                print(f"⚠️ datasource.{method_name} raised an exception: {exc!r}")
            break

    if not tried:
        print("ℹ️ No common fetch method found on datasource. DataSource initialized for manual calls.")
        print("You can call methods on the datasource or use the client stored in `client` for lower-level requests.")

if __name__ == "__main__":
    main()
# ...existing code...
