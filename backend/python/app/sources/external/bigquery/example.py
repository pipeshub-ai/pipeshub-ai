# ruff: noqa
from __future__ import annotations

import json
import os

from dotenv import load_dotenv

from app.sources.client.bigquery.bigquery import (
    BigQueryClient,
    BigQueryOAuthConfig,
    BigQueryResponse,
    BigQueryServiceAccountConfig,
)
from app.sources.external.bigquery.bigquery_ import BigQueryDataSource


def _print_status(title: str, res: BigQueryResponse) -> None:
    print(f"\n== {title} ==")
    if not res.success:
        print("error:", res.error or res.message)
    else:
        print("ok")


def main() -> None:
    # Load .env if present
    load_dotenv()

    # Minimal envs
    auth_type = os.getenv("BIGQUERY_AUTH_TYPE", "SERVICE_ACCOUNT")  # SERVICE_ACCOUNT or OAUTH
    project_id = os.getenv("BIGQUERY_PROJECT_ID", "")

    if not project_id:
        raise RuntimeError("BIGQUERY_PROJECT_ID is required")

    if auth_type == "SERVICE_ACCOUNT":
        sa_path = os.getenv("BIGQUERY_SERVICE_ACCOUNT_JSON", "")
        if not sa_path:
            raise RuntimeError("BIGQUERY_SERVICE_ACCOUNT_JSON path is required for SERVICE_ACCOUNT auth")
        with open(sa_path, "r") as f:
            sa_json = json.load(f)
        client = BigQueryClient.build_with_config(
            BigQueryServiceAccountConfig(
                service_account_json=sa_json,
                project_id=project_id,
            )
        )
    else:
        access_token = os.getenv("BIGQUERY_ACCESS_TOKEN", "")
        if not access_token:
            raise RuntimeError("BIGQUERY_ACCESS_TOKEN is required for OAUTH auth")
        client = BigQueryClient.build_with_config(
            BigQueryOAuthConfig(
                access_token=access_token,
                project_id=project_id,
            )
        )

    ds = BigQueryDataSource(client)

    # 1) List datasets
    datasets_res: BigQueryResponse = ds.list_datasets()
    _print_status("List Datasets", datasets_res)
    if datasets_res.success and datasets_res.data:
        names = [getattr(d, "dataset_id", str(d)) for d in datasets_res.data[:10]]
        print("datasets:", names)

    # 2) If datasets exist, list tables in the first one
    if datasets_res.success and datasets_res.data and len(datasets_res.data) > 0:
        first_dataset = getattr(datasets_res.data[0], "dataset_id", None)
        if first_dataset:
            tables_res: BigQueryResponse = ds.list_tables(first_dataset)
            _print_status(f"List Tables ({first_dataset})", tables_res)
            if tables_res.success and tables_res.data:
                names = [getattr(t, "table_id", str(t)) for t in tables_res.data[:10]]
                print("tables:", names)

                # Get schema for first table
                if len(tables_res.data) > 0:
                    first_table = tables_res.data[0]
                    table_ref = f"{project_id}.{first_dataset}.{getattr(first_table, 'table_id', '')}"
                    try:
                        schema_res: BigQueryResponse = ds.get_table_schema(table_ref)
                        _print_status(f"Get Table Schema ({table_ref})", schema_res)
                        if schema_res.success and schema_res.data:
                            fields = [getattr(f, "name", str(f)) for f in schema_res.data[:10]]
                            print("schema fields:", fields)
                    except Exception as e:
                        print(f"Get table schema failed: {e}")

    # 3) Run a simple query
    try:
        query_res: BigQueryResponse = ds.query(
            f"SELECT 1 AS test_col, 'hello' AS greeting"
        )
        _print_status("Simple Query", query_res)
        if query_res.success and query_res.data:
            print("rows:", query_res.data)
    except Exception as e:
        print(f"Query failed: {e}")

    # 4) List jobs
    try:
        jobs_res: BigQueryResponse = ds.list_jobs(max_results=5)
        _print_status("List Jobs", jobs_res)
        if jobs_res.success and jobs_res.data:
            job_ids = [getattr(j, "job_id", str(j)) for j in jobs_res.data[:5]]
            print("jobs:", job_ids)
    except Exception as e:
        print(f"List jobs failed: {e}")


if __name__ == "__main__":
    main()
