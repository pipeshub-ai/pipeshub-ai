# ruff: noqa
"""
ServiceNow Table API Usage Examples

This example demonstrates how to use the ServiceNowTableAPI DataSource to interact with
the ServiceNow Table API, covering:
- Retrieving records from tables
- Creating records
- Updating records
- Deleting records

Prerequisites:
- Set SERVICENOW_INSTANCE_URL environment variable (e.g., https://dev12345.service-now.com)
- Set SERVICENOW_USERNAME environment variable
- Set SERVICENOW_PASSWORD environment variable
"""

import asyncio
import os

from app.sources.client.servicenow.servicenow import (
    ServiceNowUsernamePasswordConfig,
    ServiceNowClient
)
from app.sources.external.servicenow.servicenow import ServiceNowDataSource

# Environment variables
INSTANCE_URL = os.getenv("SERVICENOW_INSTANCE_URL")  # e.g., https://dev12345.service-now.com
USERNAME = os.getenv("SERVICENOW_USERNAME")
PASSWORD = os.getenv("SERVICENOW_PASSWORD")


async def main() -> None:
    """Simple example of using ServiceNowTableAPI to call the Table API."""
    # Configure and build the ServiceNow client
    config = ServiceNowUsernamePasswordConfig(
        instance_url=INSTANCE_URL,
        username=USERNAME,
        password=PASSWORD
    )
    client = ServiceNowClient.build_with_config(config)
    
    # Create the data source
    data_source = ServiceNowDataSource(client)

    # Example 1: Get all records from incident table
    print("Fetching incidents from ServiceNow:")
    incidents_response = await data_source.get_now_table_tableName(
        tableName="evaluation",
        sysparm_limit=10,
    )
    
    if incidents_response.success:
        print(f"Successfully retrieved incidents: {incidents_response.data}")
    else:
        print(f"Error: {incidents_response.error} - {incidents_response.message}")

if __name__ == "__main__":
    asyncio.run(main())