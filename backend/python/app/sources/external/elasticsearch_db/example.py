# ruff: noqa
from __future__ import annotations

import os

from dotenv import load_dotenv

from app.sources.client.elasticsearch_db.elasticsearch_db import (
    ElasticsearchClient,
    ElasticsearchApiKeyConfig,
    ElasticsearchBasicAuthConfig,
    ElasticsearchResponse,
)
from app.sources.external.elasticsearch_db.elasticsearch_db_ import ElasticsearchDataSource


def _print_status(title: str, res: ElasticsearchResponse) -> None:
    print(f"\n== {title} ==")
    if not res.success:
        print("error:", res.error or res.message)
    else:
        print("ok")


def main() -> None:
    # Load .env if present
    load_dotenv()

    # Minimal envs
    hosts_str = os.getenv("ELASTICSEARCH_HOSTS", "https://localhost:9200")
    hosts = [h.strip() for h in hosts_str.split(",")]
    auth_type = os.getenv("ELASTICSEARCH_AUTH_TYPE", "BASIC_AUTH")  # API_KEY or BASIC_AUTH

    if auth_type == "API_KEY":
        api_key_id = os.getenv("ELASTICSEARCH_API_KEY_ID", "")
        api_key_secret = os.getenv("ELASTICSEARCH_API_KEY_SECRET", "")
        if not api_key_id or not api_key_secret:
            raise RuntimeError("ELASTICSEARCH_API_KEY_ID and ELASTICSEARCH_API_KEY_SECRET are required for API_KEY auth")
        client = ElasticsearchClient.build_with_config(
            ElasticsearchApiKeyConfig(
                hosts=hosts,
                api_key_id=api_key_id,
                api_key_secret=api_key_secret,
                verify_certs=False,
            )
        )
    else:
        username = os.getenv("ELASTICSEARCH_USERNAME", "elastic")
        password = os.getenv("ELASTICSEARCH_PASSWORD", "")
        if not password:
            raise RuntimeError("ELASTICSEARCH_PASSWORD is required for BASIC_AUTH")
        client = ElasticsearchClient.build_with_config(
            ElasticsearchBasicAuthConfig(
                hosts=hosts,
                username=username,
                password=password,
                verify_certs=False,
            )
        )

    ds = ElasticsearchDataSource(client)

    # 1) Cluster info
    info_res: ElasticsearchResponse = ds.info()
    _print_status("Cluster Info", info_res)
    if info_res.success and info_res.data:
        print("cluster_name:", info_res.data.get("cluster_name"))

    # 2) Cluster health
    health_res: ElasticsearchResponse = ds.get_cluster_health()
    _print_status("Cluster Health", health_res)
    if health_res.success and health_res.data:
        print("status:", health_res.data.get("status"))

    # 3) List indices
    indices_res: ElasticsearchResponse = ds.list_indices()
    _print_status("List Indices", indices_res)
    if indices_res.success and indices_res.data:
        index_names = list(indices_res.data.keys())[:10]
        print("indices:", index_names)

    # 4) Create a test index
    test_index = "pipeshub-test-index"
    try:
        create_res: ElasticsearchResponse = ds.create_index(
            test_index,
            body={"settings": {"number_of_shards": 1, "number_of_replicas": 0}},
        )
        _print_status(f"Create Index ({test_index})", create_res)
    except Exception as e:
        print(f"Create index failed (may already exist): {e}")

    # 5) Index a document
    try:
        doc_res: ElasticsearchResponse = ds.index_document(
            test_index,
            body={"title": "Test document", "content": "Hello from PipesHub"},
            doc_id="doc-1",
        )
        _print_status("Index Document", doc_res)
    except Exception as e:
        print(f"Index document failed: {e}")

    # 6) Get the document
    try:
        get_res: ElasticsearchResponse = ds.get_document(test_index, "doc-1")
        _print_status("Get Document", get_res)
        if get_res.success and get_res.data:
            print("source:", get_res.data.get("_source"))
    except Exception as e:
        print(f"Get document failed: {e}")

    # 7) Search
    try:
        search_res: ElasticsearchResponse = ds.search(
            test_index, body={"query": {"match_all": {}}}
        )
        _print_status("Search", search_res)
        if search_res.success and search_res.data:
            hits = search_res.data.get("hits", {}).get("hits", [])
            print(f"hits: {len(hits)}")
    except Exception as e:
        print(f"Search failed: {e}")

    # 8) Count
    try:
        count_res: ElasticsearchResponse = ds.count(test_index)
        _print_status("Count", count_res)
        if count_res.success and count_res.data:
            print("count:", count_res.data.get("count"))
    except Exception as e:
        print(f"Count failed: {e}")

    # 9) Delete the document
    try:
        del_doc_res: ElasticsearchResponse = ds.delete_document(test_index, "doc-1")
        _print_status("Delete Document", del_doc_res)
    except Exception as e:
        print(f"Delete document failed: {e}")

    # 10) Delete the test index
    try:
        del_idx_res: ElasticsearchResponse = ds.delete_index(test_index)
        _print_status(f"Delete Index ({test_index})", del_idx_res)
    except Exception as e:
        print(f"Delete index failed: {e}")


if __name__ == "__main__":
    main()
