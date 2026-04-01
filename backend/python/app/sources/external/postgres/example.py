"""
PostgreSQL Data Source Example

Demonstrates how to use PostgreSQLDataSource to connect and fetch metadata from PostgreSQL.

Usage:
    cd backend/python
    python -m app.sources.external.postgres.example

Environment Variables (Required):
    POSTGRES_HOST            - PostgreSQL server host (e.g., localhost, 192.168.1.1)
    POSTGRES_DATABASE        - Database name to connect to
    POSTGRES_USER            - Username for authentication
    POSTGRES_PASSWORD        - Password for authentication
    
Optional:
    POSTGRES_PORT            - PostgreSQL server port (default: 5432)
    POSTGRES_SSLMODE         - SSL mode (default: prefer)
                               Options: disable, allow, prefer, require, verify-ca, verify-full
"""

import asyncio
import json
import os
import sys
from datetime import datetime
from pathlib import Path

# Add backend/python to path for imports
if __name__ == "__main__":
    backend_path = Path(__file__).parent.parent.parent.parent
    sys.path.insert(0, str(backend_path))

from app.sources.client.postgres.postgres import PostgreSQLClient, PostgreSQLConfig
from app.sources.external.postgres.postgres_ import PostgreSQLDataSource


# --- Configuration from environment ---
HOST = os.getenv("POSTGRES_HOST")
PORT = int(os.getenv("POSTGRES_PORT", "5432"))
DATABASE = os.getenv("POSTGRES_DATABASE")
USER = os.getenv("POSTGRES_USER")
PASSWORD = os.getenv("POSTGRES_PASSWORD", "")
SSLMODE = os.getenv("POSTGRES_SSLMODE", "prefer")


def print_header(title: str) -> None:
    """Print a formatted section header."""
    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}")


def print_subheader(title: str) -> None:
    """Print a formatted subsection header."""
    print(f"\n  --- {title} ---")


async def main() -> None:
    """Main example function."""
    print_header("PostgreSQL Data Source Example")
    print(f"Started: {datetime.now().isoformat()}")
    
    # Validate required environment variables
    if not HOST:
        print("\n[ERROR] POSTGRES_HOST is required")
        print("\nPlease set the following environment variables:")
        print("  POSTGRES_HOST     - PostgreSQL server host (e.g., localhost)")
        print("  POSTGRES_DATABASE - Database name to connect to")
        print("  POSTGRES_USER     - Username for authentication")
        print("  POSTGRES_PASSWORD - Password for authentication")
        print("\nOptional:")
        print("  POSTGRES_PORT     - Server port (default: 5432)")
        print("  POSTGRES_SSLMODE  - SSL mode (default: prefer)")
        return
    
    if not DATABASE:
        print("\n[ERROR] POSTGRES_DATABASE is required")
        print("Please specify which database to connect to")
        return
    
    if not USER:
        print("\n[ERROR] POSTGRES_USER is required")
        return
    
    print(f"\nConnection Details:")
    print(f"  Host: {HOST}")
    print(f"  Port: {PORT}")
    print(f"  Database: {DATABASE}")
    print(f"  User: {USER}")
    print(f"  SSL Mode: {SSLMODE}")
    
    # Create PostgreSQL client configuration
    print_header("Step 1: Creating PostgreSQL Client")
    
    config = PostgreSQLConfig(
        host=HOST,
        port=PORT,
        database=DATABASE,
        user=USER,
        password=PASSWORD,
        sslmode=SSLMODE,
    )
    
    client = config.create_client()
    print("[OK] PostgreSQL client created")
    
    # Create data source
    print_header("Step 2: Creating Data Source")
    data_source = PostgreSQLDataSource(client)
    print("[OK] Data source created")
    
    # Test connection
    print_header("Step 3: Testing Connection")
    try:
        client.connect()
        print("[OK] Successfully connected to PostgreSQL")
        
        # Get connection info
        conn_info = client.get_connection_info()
        print(f"\nConnection Info:")
        print(f"  Host: {conn_info['host']}")
        print(f"  Port: {conn_info['port']}")
        print(f"  Database: {conn_info['database']}")
        print(f"  User: {conn_info['user']}")
        
        # Test with version query
        response = await data_source.test_connection()
        if response.success:
            print(f"\n[OK] Connection test successful")
            if response.data:
                print(f"  PostgreSQL Version: {response.data.get('version', 'Unknown')}")
                print(f"  Current Database: {response.data.get('database', 'Unknown')}")
                print(f"  Current User: {response.data.get('user', 'Unknown')}")
        else:
            print(f"\n[ERROR] Connection test failed: {response.error}")
            return
            
    except Exception as e:
        print(f"\n[ERROR] Failed to connect: {e}")
        print("\nTroubleshooting:")
        print("  1. Check if PostgreSQL server is running")
        print("  2. Verify host and port are correct")
        print("  3. Ensure user has access to the database")
        print("  4. Check firewall settings")
        return
    
    # List databases
    print_header("Step 4: Listing Databases")
    response = await data_source.list_databases()
    if response.success and response.data:
        print(f"\nFound {len(response.data)} databases:")
        for db in response.data:
            print(f"  - {db['name']} (Size: {db.get('size', 'Unknown')})")
    else:
        print(f"[ERROR] Failed to list databases: {response.error}")
    
    # List schemas
    print_header("Step 5: Listing Schemas")
    response = await data_source.list_schemas()
    if response.success and response.data:
        print(f"\nFound {len(response.data)} schemas:")
        for schema in response.data:
            print(f"  - {schema['name']} (Owner: {schema.get('owner', 'Unknown')})")
    else:
        print(f"[ERROR] Failed to list schemas: {response.error}")
    
    # List tables in public schema
    print_header("Step 6: Listing Tables in 'public' Schema")
    response = await data_source.list_tables(schema="public")
    if response.success and response.data:
        print(f"\nFound {len(response.data)} tables:")
        for table in response.data:
            print(f"  - {table['name']} (Type: {table.get('type', 'Unknown')})")
            
            # Get detailed info for first table
            if len(response.data) > 0 and table == response.data[0]:
                print_subheader(f"Table Details: {table['name']}")
                table_info_response = await data_source.get_table_info("public", table['name'])
                if table_info_response.success and table_info_response.data:
                    columns = table_info_response.data.get('columns', [])
                    print(f"\n  Columns ({len(columns)}):")
                    for col in columns:
                        nullable = "NULL" if col.get('is_nullable') == 'YES' else "NOT NULL"
                        data_type = col.get('data_type', 'unknown')
                        max_length = col.get('character_maximum_length')
                        if max_length:
                            data_type += f"({max_length})"
                        print(f"    - {col['name']}: {data_type} ({nullable})")
                    
                    # Get foreign keys
                    fk_response = await data_source.get_foreign_keys("public", table['name'])
                    if fk_response.success and fk_response.data:
                        print(f"\n  Foreign Keys ({len(fk_response.data)}):")
                        for fk in fk_response.data:
                            print(f"    - {fk['column_name']} -> {fk['foreign_table_schema']}.{fk['foreign_table_name']}.{fk['foreign_column_name']}")
    else:
        print(f"[INFO] No tables found in public schema or error: {response.error}")
    
    # List views in public schema
    print_header("Step 7: Listing Views in 'public' Schema")
    response = await data_source.list_views(schema="public")
    if response.success and response.data:
        print(f"\nFound {len(response.data)} views:")
        for view in response.data:
            print(f"  - {view['name']}")
            if view.get('definition'):
                # Print first 100 chars of definition
                definition = view['definition'][:100].replace('\n', ' ')
                print(f"    Definition: {definition}...")
    else:
        print(f"[INFO] No views found in public schema or error: {response.error}")
    
    # Execute custom query
    print_header("Step 8: Executing Custom Query")
    custom_query = """
        SELECT 
            schemaname as schema,
            tablename as table,
            pg_size_pretty(pg_total_relation_size(schemaname||'.'||tablename)) as size
        FROM pg_tables
        WHERE schemaname = 'public'
        ORDER BY pg_total_relation_size(schemaname||'.'||tablename) DESC
        LIMIT 5;
    """
    response = await data_source.execute_query(custom_query)
    if response.success and response.data:
        print(f"\nTop 5 largest tables in public schema:")
        for row in response.data:
            print(f"  - {row['table']}: {row['size']}")
    else:
        print(f"[INFO] No results or error: {response.error}")
    
    # Close connection
    print_header("Cleanup")
    client.close()
    print("[OK] Connection closed")
    
    print_header("Done")
    print(f"Finished: {datetime.now().isoformat()}")


if __name__ == "__main__":
    asyncio.run(main())
