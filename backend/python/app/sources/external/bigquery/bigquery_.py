# ruff: noqa
from __future__ import annotations

from google.cloud import bigquery  # type: ignore[import-untyped]
from typing import Dict, List, Optional, Union, cast

from app.sources.client.bigquery.bigquery import BigQueryResponse

class BigQueryDataSource:
    """
    Strict, typed wrapper over google-cloud-bigquery for common BigQuery operations.

    Accepts either a google-cloud-bigquery `Client` instance *or* any object with `.get_sdk() -> bigquery.Client`.
    """

    def __init__(self, client_or_sdk: Union[bigquery.Client, object]) -> None:
        super().__init__()
        # Support a raw SDK or a wrapper that exposes `.get_sdk()`
        if hasattr(client_or_sdk, "get_sdk"):
            sdk_obj = getattr(client_or_sdk, "get_sdk")()
            self._sdk: bigquery.Client = cast(bigquery.Client, sdk_obj)
        else:
            self._sdk = cast(bigquery.Client, client_or_sdk)

    # ---- helpers ----
    @staticmethod
    def _params(**kwargs: object) -> Dict[str, object]:
        # Filter out Nones to avoid overriding SDK defaults
        out: Dict[str, object] = {}
        for k, v in kwargs.items():
            if v is None:
                continue
            if isinstance(v, (list, dict)) and len(v) == 0:
                continue
            out[k] = v
        return out
    def query(self, query_string: str, project: Optional[str] = None, location: Optional[str] = None) -> BigQueryResponse:
        """Execute a SQL query and return results as list of dicts."""
        job_config_kwargs = self._params()
        query_kwargs = self._params(project=project, location=location)
        query_job = self._sdk.query(query_string, **query_kwargs)
        results = query_job.result()
        rows = [dict(row) for row in results]
        return BigQueryResponse(success=True, data=rows)
    def list_datasets(self, project: Optional[str] = None, max_results: Optional[int] = None) -> BigQueryResponse:
        """List datasets in the project."""
        params = self._params(project=project, max_results=max_results)
        datasets = list(self._sdk.list_datasets(**params))
        return BigQueryResponse(success=True, data=datasets)
    def get_dataset(self, dataset_id: str) -> BigQueryResponse:
        """Get a dataset by ID."""
        dataset = self._sdk.get_dataset(dataset_id)
        return BigQueryResponse(success=True, data=dataset)
    def create_dataset(self, dataset_id: str, location: Optional[str] = None, description: Optional[str] = None) -> BigQueryResponse:
        """Create a new dataset."""
        dataset_ref = bigquery.Dataset(self._sdk.dataset(dataset_id))
        if location is not None:
            dataset_ref.location = location
        if description is not None:
            dataset_ref.description = description
        dataset = self._sdk.create_dataset(dataset_ref)
        return BigQueryResponse(success=True, data=dataset)
    def delete_dataset(self, dataset_id: str, delete_contents: bool = False) -> BigQueryResponse:
        """Delete a dataset."""
        self._sdk.delete_dataset(dataset_id, delete_contents=delete_contents)
        return BigQueryResponse(success=True, data=True)
    def list_tables(self, dataset_id: str, max_results: Optional[int] = None) -> BigQueryResponse:
        """List tables in a dataset."""
        params = self._params(max_results=max_results)
        tables = list(self._sdk.list_tables(dataset_id, **params))
        return BigQueryResponse(success=True, data=tables)
    def get_table(self, table_ref: str) -> BigQueryResponse:
        """Get a table by reference (dataset.table)."""
        table = self._sdk.get_table(table_ref)
        return BigQueryResponse(success=True, data=table)
    def create_table(self, table_ref: str, schema: Optional[List[Dict[str, str]]] = None) -> BigQueryResponse:
        """Create a table with optional schema."""
        table = bigquery.Table(table_ref)
        if schema is not None and len(schema) > 0:
            fields = [bigquery.SchemaField(f['name'], f.get('type', 'STRING'), mode=f.get('mode', 'NULLABLE')) for f in schema]
            table.schema = fields
        result = self._sdk.create_table(table)
        return BigQueryResponse(success=True, data=result)
    def delete_table(self, table_ref: str) -> BigQueryResponse:
        """Delete a table."""
        self._sdk.delete_table(table_ref)
        return BigQueryResponse(success=True, data=True)
    def get_table_schema(self, table_ref: str) -> BigQueryResponse:
        """Get the schema of a table."""
        table = self._sdk.get_table(table_ref)
        schema = table.schema
        return BigQueryResponse(success=True, data=schema)
    def list_jobs(self, project: Optional[str] = None, max_results: Optional[int] = None, state_filter: Optional[str] = None) -> BigQueryResponse:
        """List jobs in the project."""
        params = self._params(project=project, max_results=max_results, state_filter=state_filter)
        jobs = list(self._sdk.list_jobs(**params))
        return BigQueryResponse(success=True, data=jobs)
    def get_job(self, job_id: str, project: Optional[str] = None, location: Optional[str] = None) -> BigQueryResponse:
        """Get a job by ID."""
        params = self._params(project=project, location=location)
        job = self._sdk.get_job(job_id, **params)
        return BigQueryResponse(success=True, data=job)

