# ruff: noqa
"""
ClickHouse API example demonstrating:
1. Connection & Server Info (ping, version, min_version check)
2. Database Management (CREATE DATABASE, list databases)
3. Table Management (CREATE TABLE, list tables)
4. INSERT operations (Create)
5. SELECT operations (Read - basic, filtered, aggregations)
6. UPDATE operations (ALTER TABLE mutations)
7. DELETE operations (ALTER TABLE DELETE)
8. Raw Query operations (JSON, CSV formats)
9. Streaming Query operations
10. Role Management (CREATE ROLE, list roles)
11. User Management (CREATE USER, list users)
12. Grant Roles to Users
13. Cleanup (DROP users, roles, tables, databases)
14. Connection close
"""
import os
from datetime import datetime
from typing import Any, Dict, List, Optional

from app.sources.client.clickhouse.clickhouse import (
    ClickHouseClient,
    ClickHouseClientViaCredentials,
    ClickHouseResponse,
)
from app.sources.external.clickhouse.clickhouse import ClickHouseDataSource


# ---------------------------------------------------------------------------
# 1. Connection & Server Info
# ---------------------------------------------------------------------------

def ping_server(ds: ClickHouseDataSource) -> ClickHouseResponse:
    """Ping ClickHouse server to verify connection."""
    print("\n" + "=" * 60)
    print("1. CONNECTION & SERVER INFO - Ping Server")
    print("=" * 60)

    response = ds.ping()

    if response.success:
        print("  Server is alive and responding")
    else:
        print(f"  Ping failed: {response.error}")

    return response


def get_server_version(ds: ClickHouseDataSource) -> Optional[str]:
    """Get ClickHouse server version via SQL query."""
    print("\n" + "=" * 60)
    print("1. CONNECTION & SERVER INFO - Get Server Version")
    print("=" * 60)

    response = ds.query("SELECT version() AS version")

    if response.success and response.data:
        rows = response.data.get("result_rows", [])
        version = rows[0][0] if rows else None
        if version:
            print(f"  ClickHouse Server Version: {version}")
            return version

    print(f"  Version query failed: {response.error}")
    return None


def check_min_version(ds: ClickHouseDataSource, min_ver: str) -> bool:
    """Check if server meets minimum version requirement."""
    print("\n" + "=" * 60)
    print(f"1. CONNECTION & SERVER INFO - Check Min Version ({min_ver})")
    print("=" * 60)

    response = ds.min_version(min_ver)

    if response.success and response.data:
        meets = response.data.get("result", False)
        if meets:
            print(f"  Server meets minimum version requirement: {min_ver}")
        else:
            print(f"  Server does NOT meet minimum version requirement: {min_ver}")
        return meets

    print(f"  Version check failed: {response.error}")
    return False


# ---------------------------------------------------------------------------
# 2. Database Management
# ---------------------------------------------------------------------------

def create_database(ds: ClickHouseDataSource, db_name: str) -> bool:
    """Create a new database."""
    print("\n" + "=" * 60)
    print(f"2. DATABASE MANAGEMENT - Create Database '{db_name}'")
    print("=" * 60)

    response = ds.command(f"CREATE DATABASE IF NOT EXISTS {db_name}")

    if response.success:
        print(f"  Database '{db_name}' created successfully")
        return True

    print(f"  Failed to create database: {response.error}")
    return False


def list_databases(ds: ClickHouseDataSource) -> List[str]:
    """List all databases via system.databases."""
    print("\n" + "=" * 60)
    print("2. DATABASE MANAGEMENT - List Databases")
    print("=" * 60)

    response = ds.query("SELECT name FROM system.databases ORDER BY name")

    databases: List[str] = []
    if response.success and response.data:
        rows = response.data.get("result_rows", [])
        databases = [row[0] for row in rows]
        print(f"  Found {len(databases)} database(s):")
        for db in databases:
            print(f"    - {db}")
    else:
        print(f"  Failed to list databases: {response.error}")

    return databases


# ---------------------------------------------------------------------------
# 3. Table Management
# ---------------------------------------------------------------------------

def create_table(ds: ClickHouseDataSource, db_name: str, table_name: str) -> bool:
    """Create test_users table with MergeTree engine."""
    print("\n" + "=" * 60)
    print(f"3. TABLE MANAGEMENT - Create Table '{db_name}.{table_name}'")
    print("=" * 60)

    create_sql = f"""
    CREATE TABLE IF NOT EXISTS {db_name}.{table_name} (
        id UInt32,
        name String,
        email String,
        age UInt8,
        department String,
        salary Float64,
        created_at DateTime
    ) ENGINE = MergeTree()
    ORDER BY id
    """

    response = ds.command(create_sql)

    if response.success:
        print(f"  Table '{db_name}.{table_name}' created successfully")
        print("  Schema: id, name, email, age, department, salary, created_at")
        return True

    print(f"  Failed to create table: {response.error}")
    return False


def list_tables(ds: ClickHouseDataSource, db_name: str) -> List[str]:
    """List all tables in a database via system.tables."""
    print("\n" + "=" * 60)
    print(f"3. TABLE MANAGEMENT - List Tables in '{db_name}'")
    print("=" * 60)

    response = ds.query(
        "SELECT name, engine FROM system.tables "
        f"WHERE database = '{db_name}' ORDER BY name"
    )

    tables: List[str] = []
    if response.success and response.data:
        rows = response.data.get("result_rows", [])
        tables = [row[0] for row in rows]
        print(f"  Found {len(tables)} table(s):")
        for row in rows:
            print(f"    - {row[0]} (Engine: {row[1]})")
    else:
        print(f"  Failed to list tables: {response.error}")

    return tables


# ---------------------------------------------------------------------------
# 4. INSERT (Create)
# ---------------------------------------------------------------------------

def insert_sample_data(ds: ClickHouseDataSource, db_name: str, table_name: str) -> int:
    """Insert 10 sample user records into test_users table."""
    print("\n" + "=" * 60)
    print(f"4. INSERT (CREATE) - Insert Sample Data into '{db_name}.{table_name}'")
    print("=" * 60)

    now = datetime.now().replace(microsecond=0)
    sample_data = [
        [1, "Alice Johnson", "alice@example.com", 28, "Engineering", 95000.0, now],
        [2, "Bob Smith", "bob@example.com", 35, "Sales", 75000.0, now],
        [3, "Charlie Brown", "charlie@example.com", 42, "Management", 120000.0, now],
        [4, "Diana Prince", "diana@example.com", 30, "Engineering", 98000.0, now],
        [5, "Eve Wilson", "eve@example.com", 26, "Marketing", 68000.0, now],
        [6, "Frank Miller", "frank@example.com", 38, "Engineering", 105000.0, now],
        [7, "Grace Lee", "grace@example.com", 29, "Sales", 72000.0, now],
        [8, "Henry Davis", "henry@example.com", 45, "Management", 135000.0, now],
        [9, "Iris Chen", "iris@example.com", 32, "Marketing", 78000.0, now],
        [10, "Jack Taylor", "jack@example.com", 27, "Engineering", 92000.0, now],
    ]

    column_names = ["id", "name", "email", "age", "department", "salary", "created_at"]

    response = ds.insert(
        table=f"{db_name}.{table_name}",
        data=sample_data,
        column_names=column_names,
    )

    if response.success:
        written = (
            response.data.get("written_rows", len(sample_data))
            if response.data
            else len(sample_data)
        )
        print(f"  Inserted {written} row(s) successfully")
        for row in sample_data:
            print(f"    [{row[0]}] {row[1]} | {row[4]} | ${row[5]:,.0f}")
        return written

    print(f"  Failed to insert data: {response.error}")
    return 0


# ---------------------------------------------------------------------------
# 5. SELECT (Read)
# ---------------------------------------------------------------------------

def select_all_records(
    ds: ClickHouseDataSource, db_name: str, table_name: str
) -> List[Any]:
    """Select all records from table."""
    print("\n" + "=" * 60)
    print(f"5. SELECT (READ) - All Records from '{db_name}.{table_name}'")
    print("=" * 60)

    response = ds.query(f"SELECT * FROM {db_name}.{table_name} ORDER BY id")

    if response.success and response.data:
        rows = response.data.get("result_rows", [])
        cols = response.data.get("column_names", [])
        print(f"  Retrieved {len(rows)} row(s)")
        print(f"  Columns: {', '.join(cols)}")
        for row in rows[:5]:
            print(f"    {row}")
        if len(rows) > 5:
            print(f"    ... and {len(rows) - 5} more rows")
        return rows

    print(f"  Failed to select records: {response.error}")
    return []


def select_filtered_records(
    ds: ClickHouseDataSource, db_name: str, table_name: str
) -> List[Any]:
    """Select filtered records — Engineering dept, age < 35, ordered by salary."""
    print("\n" + "=" * 60)
    print("5. SELECT (READ) - Filtered (Engineering, age < 35, salary desc)")
    print("=" * 60)

    sql = (
        f"SELECT name, age, department, salary "
        f"FROM {db_name}.{table_name} "
        f"WHERE department = 'Engineering' AND age < 35 "
        f"ORDER BY salary DESC "
        f"LIMIT 5"
    )

    response = ds.query(sql)

    if response.success and response.data:
        rows = response.data.get("result_rows", [])
        cols = response.data.get("column_names", [])
        print(f"  Retrieved {len(rows)} row(s)")
        print(f"  Columns: {', '.join(cols)}")
        for row in rows:
            print(f"    {row}")
        return rows

    print(f"  Failed to select filtered records: {response.error}")
    return []


def select_aggregated_data(
    ds: ClickHouseDataSource, db_name: str, table_name: str
) -> List[Any]:
    """Select aggregated data — GROUP BY department with COUNT, AVG, MAX."""
    print("\n" + "=" * 60)
    print("5. SELECT (READ) - Aggregated Data (by department)")
    print("=" * 60)

    sql = (
        f"SELECT department, "
        f"count(*) AS employee_count, "
        f"avg(age) AS avg_age, "
        f"avg(salary) AS avg_salary, "
        f"max(salary) AS max_salary "
        f"FROM {db_name}.{table_name} "
        f"GROUP BY department "
        f"ORDER BY avg_salary DESC"
    )

    response = ds.query(sql)

    if response.success and response.data:
        rows = response.data.get("result_rows", [])
        cols = response.data.get("column_names", [])
        print(f"  Retrieved {len(rows)} department(s)")
        print(f"  Columns: {', '.join(cols)}")
        for row in rows:
            print(f"    {row}")
        return rows

    print(f"  Failed to select aggregated data: {response.error}")
    return []


# ---------------------------------------------------------------------------
# 6. UPDATE
# ---------------------------------------------------------------------------

def update_records(ds: ClickHouseDataSource, db_name: str, table_name: str) -> bool:
    """Update salary for Engineering department (+10%) via ALTER TABLE mutation."""
    print("\n" + "=" * 60)
    print("6. UPDATE - Increase Engineering salaries by 10%")
    print("=" * 60)

    sql = (
        f"ALTER TABLE {db_name}.{table_name} "
        f"UPDATE salary = salary * 1.10 "
        f"WHERE department = 'Engineering'"
    )

    response = ds.command(sql)

    if response.success:
        print("  Update command executed successfully")
        print("  Note: ClickHouse mutations are asynchronous and may take a moment")
        return True

    print(f"  Failed to update records: {response.error}")
    return False


# ---------------------------------------------------------------------------
# 7. DELETE
# ---------------------------------------------------------------------------

def delete_records(ds: ClickHouseDataSource, db_name: str, table_name: str) -> bool:
    """Delete records where age > 40 via ALTER TABLE mutation."""
    print("\n" + "=" * 60)
    print("7. DELETE - Remove records where age > 40")
    print("=" * 60)

    sql = (
        f"ALTER TABLE {db_name}.{table_name} "
        f"DELETE WHERE age > 40"
    )

    response = ds.command(sql)

    if response.success:
        print("  Delete command executed successfully")
        print("  Note: ClickHouse mutations are asynchronous and may take a moment")
        return True

    print(f"  Failed to delete records: {response.error}")
    return False


# ---------------------------------------------------------------------------
# 8. Raw Query
# ---------------------------------------------------------------------------

def raw_query_json(
    ds: ClickHouseDataSource, db_name: str, table_name: str
) -> Optional[bytes]:
    """Execute raw query and get results in JSON format."""
    print("\n" + "=" * 60)
    print("8. RAW QUERY - Get data in JSON format")
    print("=" * 60)

    response = ds.raw_query(
        f"SELECT * FROM {db_name}.{table_name} LIMIT 3", fmt="JSON"
    )

    if response.success and response.data:
        raw_data = response.data.get("raw")
        size = response.data.get("size", 0)
        print(f"  Retrieved {size} bytes in JSON format")
        if raw_data:
            preview = raw_data[:300] if isinstance(raw_data, bytes) else str(raw_data)[:300]
            print(f"  Preview:\n    {preview}")
        return raw_data

    print(f"  Failed to execute raw query (JSON): {response.error}")
    return None


def raw_query_csv(
    ds: ClickHouseDataSource, db_name: str, table_name: str
) -> Optional[bytes]:
    """Execute raw query and get results in CSV format."""
    print("\n" + "=" * 60)
    print("8. RAW QUERY - Get data in CSV format")
    print("=" * 60)

    response = ds.raw_query(
        f"SELECT * FROM {db_name}.{table_name} LIMIT 3", fmt="CSV"
    )

    if response.success and response.data:
        raw_data = response.data.get("raw")
        size = response.data.get("size", 0)
        print(f"  Retrieved {size} bytes in CSV format")
        if raw_data:
            preview = raw_data[:300] if isinstance(raw_data, bytes) else str(raw_data)[:300]
            print(f"  Preview:\n    {preview}")
        return raw_data

    print(f"  Failed to execute raw query (CSV): {response.error}")
    return None


# ---------------------------------------------------------------------------
# 9. Streaming Query
# ---------------------------------------------------------------------------

def stream_query_results(
    ds: ClickHouseDataSource, db_name: str, table_name: str
) -> int:
    """Execute streaming query using query_row_block_stream."""
    print("\n" + "=" * 60)
    print("9. STREAMING QUERY - Stream results block by block")
    print("=" * 60)

    try:
        with ds.query_row_block_stream(
            f"SELECT * FROM {db_name}.{table_name}"
        ) as stream:
            total_rows = 0
            block_num = 0
            for block in stream:
                block_num += 1
                block_rows = len(block)
                total_rows += block_rows
                print(f"  Block {block_num}: {block_rows} row(s)")
                if block_num == 1 and block_rows > 0:
                    print(f"    First row: {block[0]}")

        print(f"  Streamed {total_rows} row(s) total across {block_num} block(s)")
        return total_rows
    except Exception as e:
        print(f"  Failed to stream query results: {e}")
        return 0


# ---------------------------------------------------------------------------
# 10. Role Management
# ---------------------------------------------------------------------------

def create_roles(ds: ClickHouseDataSource) -> bool:
    """Create test roles: test_analyst_role, test_admin_role."""
    print("\n" + "=" * 60)
    print("10. ROLE MANAGEMENT - Create Roles")
    print("=" * 60)

    roles = ["test_analyst_role", "test_admin_role"]

    for role in roles:
        response = ds.command(f"CREATE ROLE IF NOT EXISTS {role}")
        if response.success:
            print(f"  Role '{role}' created successfully")
        else:
            print(f"  Failed to create role '{role}': {response.error}")
            return False

    return True


def list_roles(ds: ClickHouseDataSource) -> List[str]:
    """List all roles from system.roles."""
    print("\n" + "=" * 60)
    print("10. ROLE MANAGEMENT - List Roles")
    print("=" * 60)

    response = ds.query("SELECT name FROM system.roles ORDER BY name")

    roles: List[str] = []
    if response.success and response.data:
        rows = response.data.get("result_rows", [])
        roles = [row[0] for row in rows]
        print(f"  Found {len(roles)} role(s):")
        for role in roles:
            print(f"    - {role}")
    else:
        print(f"  Failed to list roles: {response.error}")

    return roles


# ---------------------------------------------------------------------------
# 11. User Management
# ---------------------------------------------------------------------------

def create_users(ds: ClickHouseDataSource) -> bool:
    """Create test users: test_user_alice, test_user_bob, test_user_charlie."""
    print("\n" + "=" * 60)
    print("11. USER MANAGEMENT - Create Users")
    print("=" * 60)

    users = [
        ("test_user_alice", "password_alice_123"),
        ("test_user_bob", "password_bob_456"),
        ("test_user_charlie", "password_charlie_789"),
    ]

    for username, password in users:
        response = ds.command(
            f"CREATE USER IF NOT EXISTS {username} IDENTIFIED BY '{password}'"
        )
        if response.success:
            print(f"  User '{username}' created successfully")
        else:
            print(f"  Failed to create user '{username}': {response.error}")
            return False

    return True


def list_users(ds: ClickHouseDataSource) -> List[str]:
    """List all users from system.users."""
    print("\n" + "=" * 60)
    print("11. USER MANAGEMENT - List Users")
    print("=" * 60)

    response = ds.query("SELECT name FROM system.users ORDER BY name")

    users: List[str] = []
    if response.success and response.data:
        rows = response.data.get("result_rows", [])
        users = [row[0] for row in rows]
        print(f"  Found {len(users)} user(s):")
        for user in users:
            print(f"    - {user}")
    else:
        print(f"  Failed to list users: {response.error}")

    return users


# ---------------------------------------------------------------------------
# 12. Grant Roles to Users
# ---------------------------------------------------------------------------

def grant_roles_to_users(ds: ClickHouseDataSource) -> bool:
    """Grant test_analyst_role to alice & bob, test_admin_role to charlie."""
    print("\n" + "=" * 60)
    print("12. GRANT ROLES - Assign roles to users")
    print("=" * 60)

    grants = [
        ("test_analyst_role", "test_user_alice"),
        ("test_analyst_role", "test_user_bob"),
        ("test_admin_role", "test_user_charlie"),
    ]

    for role, user in grants:
        response = ds.command(f"GRANT {role} TO {user}")
        if response.success:
            print(f"  Granted '{role}' to '{user}'")
        else:
            print(f"  Failed to grant '{role}' to '{user}': {response.error}")
            return False

    return True


def list_role_grants(ds: ClickHouseDataSource) -> List[Any]:
    """List role grants from system.role_grants."""
    print("\n" + "=" * 60)
    print("12. GRANT ROLES - List Role Grants")
    print("=" * 60)

    response = ds.query(
        "SELECT role_name, user_name "
        "FROM system.role_grants "
        "ORDER BY role_name, user_name"
    )

    grants: List[Any] = []
    if response.success and response.data:
        rows = response.data.get("result_rows", [])
        grants = rows
        print(f"  Found {len(grants)} role grant(s):")
        for row in grants:
            print(f"    - Role '{row[0]}' granted to '{row[1]}'")
    else:
        print(f"  Failed to list role grants: {response.error}")

    return grants


# ---------------------------------------------------------------------------
# 13. Cleanup
# ---------------------------------------------------------------------------

def cleanup_users(ds: ClickHouseDataSource) -> None:
    """Drop test users."""
    print("\n" + "=" * 60)
    print("13. CLEANUP - Drop Test Users")
    print("=" * 60)

    for user in ["test_user_alice", "test_user_bob", "test_user_charlie"]:
        response = ds.command(f"DROP USER IF EXISTS {user}")
        if response.success:
            print(f"  Dropped user '{user}'")
        else:
            print(f"  Failed to drop user '{user}': {response.error}")


def cleanup_roles(ds: ClickHouseDataSource) -> None:
    """Drop test roles."""
    print("\n" + "=" * 60)
    print("13. CLEANUP - Drop Test Roles")
    print("=" * 60)

    for role in ["test_analyst_role", "test_admin_role"]:
        response = ds.command(f"DROP ROLE IF EXISTS {role}")
        if response.success:
            print(f"  Dropped role '{role}'")
        else:
            print(f"  Failed to drop role '{role}': {response.error}")


def cleanup_table(ds: ClickHouseDataSource, db_name: str, table_name: str) -> None:
    """Drop test table."""
    print("\n" + "=" * 60)
    print(f"13. CLEANUP - Drop Table '{db_name}.{table_name}'")
    print("=" * 60)

    response = ds.command(f"DROP TABLE IF EXISTS {db_name}.{table_name}")
    if response.success:
        print(f"  Dropped table '{db_name}.{table_name}'")
    else:
        print(f"  Failed to drop table: {response.error}")


def cleanup_database(ds: ClickHouseDataSource, db_name: str) -> None:
    """Drop test database."""
    print("\n" + "=" * 60)
    print(f"13. CLEANUP - Drop Database '{db_name}'")
    print("=" * 60)

    response = ds.command(f"DROP DATABASE IF EXISTS {db_name}")
    if response.success:
        print(f"  Dropped database '{db_name}'")
    else:
        print(f"  Failed to drop database: {response.error}")


# ---------------------------------------------------------------------------
# 14. Close Connection
# ---------------------------------------------------------------------------

def close_connection(ds: ClickHouseDataSource) -> None:
    """Close ClickHouse connection."""
    print("\n" + "=" * 60)
    print("14. CLOSE CONNECTION")
    print("=" * 60)

    response = ds.close()
    if response.success:
        print("  Connection closed successfully")
    else:
        print(f"  Failed to close connection: {response.error}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    """Main function demonstrating all ClickHouse operations."""
    HOST = os.getenv("CLICKHOUSE_HOST", "localhost")
    PORT = int(os.getenv("CLICKHOUSE_PORT", "8123"))
    USERNAME = os.getenv("CLICKHOUSE_USERNAME", "default")
    PASSWORD = os.getenv("CLICKHOUSE_PASSWORD", "")
    DATABASE = os.getenv("CLICKHOUSE_DATABASE", "default")
    SECURE = os.getenv("CLICKHOUSE_SECURE", "false").lower() == "true"

    print("\n" + "=" * 60)
    print("ClickHouse API EXAMPLE - Comprehensive CRUD & Admin Operations")
    print("=" * 60)
    print(f"  Host: {HOST}:{PORT}")
    print(f"  Username: {USERNAME}")
    print(f"  Database: {DATABASE}")
    print(f"  Secure: {SECURE}")

    # Build client
    try:
        creds_client = ClickHouseClientViaCredentials(
            host=HOST,
            port=PORT,
            username=USERNAME,
            password=PASSWORD,
            database=DATABASE,
            secure=SECURE,
        )
        creds_client.create_client()
        client = ClickHouseClient(client=creds_client)
        ds = ClickHouseDataSource(client)
    except Exception as e:
        print(f"\n  Failed to create ClickHouse client: {e}")
        return

    TEST_DB = "test_example_db"
    TEST_TABLE = "test_users"

    try:
        # 1. Connection & Server Info
        ping_server(ds)
        get_server_version(ds)
        check_min_version(ds, "22.3")

        # 2. Database Management
        create_database(ds, TEST_DB)
        list_databases(ds)

        # 3. Table Management
        create_table(ds, TEST_DB, TEST_TABLE)
        list_tables(ds, TEST_DB)

        # 4. INSERT (Create)
        insert_sample_data(ds, TEST_DB, TEST_TABLE)

        # 5. SELECT (Read)
        select_all_records(ds, TEST_DB, TEST_TABLE)
        select_filtered_records(ds, TEST_DB, TEST_TABLE)
        select_aggregated_data(ds, TEST_DB, TEST_TABLE)

        # 6. UPDATE
        update_records(ds, TEST_DB, TEST_TABLE)

        # 7. DELETE
        delete_records(ds, TEST_DB, TEST_TABLE)

        # 8. Raw Query
        raw_query_json(ds, TEST_DB, TEST_TABLE)
        raw_query_csv(ds, TEST_DB, TEST_TABLE)

        # 9. Streaming Query
        stream_query_results(ds, TEST_DB, TEST_TABLE)

        # 10. Role Management
        create_roles(ds)
        list_roles(ds)

        # 11. User Management
        create_users(ds)
        list_users(ds)

        # 12. Grant Roles
        grant_roles_to_users(ds)
        list_role_grants(ds)

    finally:
        # 13. Cleanup (always runs)
        cleanup_users(ds)
        cleanup_roles(ds)
        cleanup_table(ds, TEST_DB, TEST_TABLE)
        cleanup_database(ds, TEST_DB)

        # 14. Close connection
        close_connection(ds)

    print("\n" + "=" * 60)
    print("All operations completed!")
    print("=" * 60)


if __name__ == "__main__":
    main()
