# ruff: noqa
from __future__ import annotations

import os

from dotenv import load_dotenv

from app.sources.client.splunk.splunk import (
    SplunkClient,
    SplunkCredentialsConfig,
    SplunkResponse,
    SplunkTokenConfig,
)
from app.sources.external.splunk.splunk_ import SplunkDataSource


def _print_status(title: str, res: SplunkResponse) -> None:
    print(f"\n== {title} ==")
    if not res.success:
        print("error:", res.error or res.message)
    else:
        print("ok")


def main() -> None:
    # Load .env if present
    load_dotenv()

    # Minimal envs
    host = os.getenv("SPLUNK_HOST", "localhost")
    port = int(os.getenv("SPLUNK_PORT", "8089"))
    auth_type = os.getenv("SPLUNK_AUTH_TYPE", "CREDENTIALS")  # CREDENTIALS or BEARER_TOKEN

    if auth_type == "BEARER_TOKEN":
        token = os.getenv("SPLUNK_TOKEN", "")
        if not token:
            raise RuntimeError("SPLUNK_TOKEN is required for BEARER_TOKEN auth")
        client = SplunkClient.build_with_config(
            SplunkTokenConfig(
                host=host,
                port=port,
                token=token,
            )
        )
    else:
        username = os.getenv("SPLUNK_USERNAME", "admin")
        password = os.getenv("SPLUNK_PASSWORD", "")
        if not password:
            raise RuntimeError("SPLUNK_PASSWORD is required for CREDENTIALS auth")
        client = SplunkClient.build_with_config(
            SplunkCredentialsConfig(
                host=host,
                port=port,
                username=username,
                password=password,
            )
        )

    ds = SplunkDataSource(client)

    # 1) Server info
    info_res: SplunkResponse = ds.get_server_info()
    _print_status("Server Info", info_res)
    if info_res.success and info_res.data:
        print("server_name:", getattr(info_res.data, "server_name", "unknown"))

    # 2) List apps
    apps_res: SplunkResponse = ds.list_apps()
    _print_status("List Apps", apps_res)
    if apps_res.success and apps_res.data:
        names = [getattr(a, "name", str(a)) for a in apps_res.data[:10]]
        print("apps:", names)

    # 3) List indexes
    indexes_res: SplunkResponse = ds.list_indexes()
    _print_status("List Indexes", indexes_res)
    if indexes_res.success and indexes_res.data:
        names = [getattr(i, "name", str(i)) for i in indexes_res.data[:10]]
        print("indexes:", names)

    # 4) List saved searches
    try:
        ss_res: SplunkResponse = ds.list_saved_searches()
        _print_status("List Saved Searches", ss_res)
        if ss_res.success and ss_res.data:
            names = [getattr(s, "name", str(s)) for s in ss_res.data[:10]]
            print("saved_searches:", names)
    except Exception as e:
        print(f"List saved searches failed: {e}")

    # 5) List users
    try:
        users_res: SplunkResponse = ds.list_users()
        _print_status("List Users", users_res)
        if users_res.success and users_res.data:
            names = [getattr(u, "name", str(u)) for u in users_res.data[:10]]
            print("users:", names)
    except Exception as e:
        print(f"List users failed: {e}")

    # 6) List jobs
    try:
        jobs_res: SplunkResponse = ds.list_jobs()
        _print_status("List Jobs", jobs_res)
        if jobs_res.success and jobs_res.data:
            sids = [getattr(j, "sid", str(j)) for j in jobs_res.data[:10]]
            print("jobs:", sids)
    except Exception as e:
        print(f"List jobs failed: {e}")

    # 7) Run a simple search (if an index exists)
    try:
        search_res: SplunkResponse = ds.search(
            "search index=_internal | head 5",
            earliest_time="-1h",
            latest_time="now",
        )
        _print_status("Search", search_res)
        if search_res.success and search_res.data:
            print(f"results: {len(search_res.data)} events")
    except Exception as e:
        print(f"Search failed: {e}")

    # 8) List inputs
    try:
        inputs_res: SplunkResponse = ds.list_inputs()
        _print_status("List Inputs", inputs_res)
        if inputs_res.success and inputs_res.data:
            names = [getattr(i, "name", str(i)) for i in inputs_res.data[:10]]
            print("inputs:", names)
    except Exception as e:
        print(f"List inputs failed: {e}")


if __name__ == "__main__":
    main()
