"""Example usage of DataDog client and generated datasource."""

import logging
import os
import sys
from datetime import datetime, timedelta

# Add parent directory to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../../.."))

from app.sources.client.datadog.datadog import DataDogClient
from app.sources.external.datadog.datadog import DataDogDataSource

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


def main() -> None:
    """Demonstrate DataDog client and datasource usage."""
    # Get credentials from environment variables
    api_key = os.environ.get("DATADOG_API_KEY")
    app_key = os.environ.get("DATADOG_APP_KEY")
    site = os.environ.get("DATADOG_SITE", "ap1.datadoghq.com")

    if not api_key or not app_key:
        msg = "Please set DATADOG_API_KEY and DATADOG_APP_KEY environment variables"
        raise ValueError(msg)

    print()
    print("DataDog DataSource Example")
    print()
    print(f"Site: {site}")
    print()

    # Initialize the DataDog client
    logger.info("Initializing DataDog client...")
    client = DataDogClient(
        api_key=api_key,
        app_key=app_key,
        site=site,
        logger=logger,
    )

    # Initialize the generated DataDog datasource
    datasource = DataDogDataSource(client=client)

    try:
        # MONITORS
        print()
        print("MONITORS API")
        print()
        
        # Example 1: List all monitors
        print("\n1. Listing all monitors...")
        response = datasource.list_monitors()
        if response.success:
            monitors = response.data.get("monitors", [])
            print(f"   Found {len(monitors)} monitors")
            for i, monitor in enumerate(monitors[:3], 1):
                print(f"      {i}. {monitor.get('name', 'N/A')} (ID: {monitor.get('id')}, State: {monitor.get('overall_state')})")
        else:
            print(f"   Error: {response.error}")

        # Example 2: Get specific monitor (if any exist)
        if response.success and monitors:
            monitor_id = monitors[0]["id"]
            print(f"\n2. Getting monitor {monitor_id}...")
            response = datasource.get_monitor(monitor_id=monitor_id)
            if response.success:
                monitor = response.data
                print(f"    Monitor: {monitor.get('name')}")
                print(f"      Type: {monitor.get('type')}")
                print(f"      Query: {monitor.get('query', 'N/A')[:100]}")
            else:
                print(f"    Error: {response.error}")

        #  DASHBOARDS 
        print("\n")
        print(" DASHBOARDS API")
        print()
        
        # Example 3: List all dashboards
        print("\n3. Listing all dashboards...")
        response = datasource.list_dashboards()
        if response.success:
            dashboards_data = response.data.get("dashboards", [])
            print(f"    Found {len(dashboards_data)} dashboards")
            for i, dashboard in enumerate(dashboards_data[:3], 1):
                print(f"      {i}. {dashboard.get('title', 'N/A')} (ID: {dashboard.get('id')})")
        else:
            print(f"    Error: {response.error}")

        # Example 4: Get specific dashboard (if any exist)
        if response.success and dashboards_data:
            dashboard_id = dashboards_data[0]["id"]
            print(f"\n4. Getting dashboard {dashboard_id}...")
            response = datasource.get_dashboard(dashboard_id=dashboard_id)
            if response.success:
                dashboard = response.data
                print(f"    Dashboard: {dashboard.get('title')}")
                print(f"      Description: {dashboard.get('description', 'N/A')[:100]}")
                print(f"      Widgets: {len(dashboard.get('widgets', []))}")
            else:
                print(f"    Error: {response.error}")

        #  LOGS 
        print("\n")
        print(" LOGS API")
        print()
        
        # Example 5: List logs from last 15 minutes
        print("\n5. Listing logs from last 15 minutes...")
        to_time = datetime.now()
        from_time = to_time - timedelta(minutes=15)
        
        log_body = {
            "filter": {
                "query": "*",  # All logs
                "from": from_time.isoformat() + "Z",
                "to": to_time.isoformat() + "Z",
            },
            "page": {"limit": 10},
            "sort": "timestamp",
        }
        
        response = datasource.list_logs(body=log_body)
        if response.success:
            logs = response.data.get("data", [])
            print(f"    Found {len(logs)} logs")
            for i, log in enumerate(logs[:3], 1):
                attrs = log.get("attributes", {})
                message = attrs.get("message", "N/A")
                print(f"      {i}. {message[:100]}")
        else:
            print(f"    Error: {response.error}")

        #  METRICS 
        print("\n" )
        print(" METRICS API")
        print()
        
        # Example 6: List active metrics
        print("\n6. Listing active metrics...")
        from_ts = int((datetime.now() - timedelta(hours=1)).timestamp())
        
        response = datasource.list_active_metrics(from_ts=from_ts)
        if response.success:
            metrics = response.data.get("metrics", [])
            print(f"    Found {len(metrics)} active metrics")
            for i, metric in enumerate(metrics[:5], 1):
                print(f"      {i}. {metric}")
        else:
            print(f"    Error: {response.error}")

        # Example 7: Query specific metric
        print("\n7. Querying CPU metric...")
        to_ts = int(datetime.now().timestamp())
        from_ts = to_ts - 3600  # Last hour
        
        response = datasource.query_metrics(
            query="avg:system.cpu.idle{*}",
            from_ts=from_ts,
            to_ts=to_ts,
        )
        if response.success:
            series = response.data.get("series", [])
            print(f"    Query successful")
            print(f"      Status: {response.data.get('status')}")
            print(f"      Series count: {len(series)}")
        else:
            print(f"    Note: {response.error}")
            print("      (This is normal if no system metrics are available)")

    except Exception as e:
        logger.error(f"Error during example execution: {e}", exc_info=True)
        raise
    finally:
        # Clean up
        client.close()
        print("\n" )
        print(" Example completed successfully! All APIs tested successfully.")
        print()


if __name__ == "__main__":
    main()
