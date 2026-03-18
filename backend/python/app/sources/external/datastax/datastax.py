# ruff: noqa
"""
DataStax Astra DB SDK DataSource - Auto-generated SDK wrapper

Generated from DataStax SDK method specifications.
Wraps the official astrapy Python package.
All methods have explicit parameter signatures.
"""

from __future__ import annotations

from typing import Any, Union, cast

from astrapy import DataAPIClient  # type: ignore[reportMissingImports]

from app.sources.client.datastax.datastax import DataStaxClient, DataStaxResponse


class DataStaxDataSource:
    """DataStax Astra DB SDK DataSource

    Provides typed wrapper methods for DataStax Astra DB operations:
    - Database listing
    - Collection management
    - Document CRUD operations

    All methods return DataStaxResponse objects.
    """

    def __init__(self, client_or_sdk: Union[DataStaxClient, DataAPIClient, object]) -> None:  # type: ignore[reportUnknownParameterType]
        """Initialize with DataStaxClient, raw SDK, or any wrapper with ``get_sdk()``.

        The ``api_endpoint`` must be provided either via the DataStaxClient wrapper
        or passed when constructing with a raw SDK.

        Args:
            client_or_sdk: DataStaxClient, DataAPIClient instance, or wrapper
        """
        super().__init__()
        if isinstance(client_or_sdk, DataStaxClient):
            self._sdk: DataAPIClient = client_or_sdk.get_sdk()  # type: ignore[reportUnknownMemberType]
            self._api_endpoint: str = client_or_sdk.get_api_endpoint()
        elif isinstance(client_or_sdk, DataAPIClient):  # type: ignore[reportUnknownMemberType]
            self._sdk = client_or_sdk  # type: ignore[reportUnknownMemberType]
            self._api_endpoint = ""
        elif hasattr(client_or_sdk, "get_sdk"):  # type: ignore[reportUnknownArgumentType]
            self._sdk = cast(DataAPIClient, getattr(client_or_sdk, "get_sdk")())  # type: ignore[reportUnknownArgumentType]
            if hasattr(client_or_sdk, "get_api_endpoint"):  # type: ignore[reportUnknownArgumentType]
                self._api_endpoint = str(getattr(client_or_sdk, "get_api_endpoint")())  # type: ignore[reportUnknownArgumentType]
            else:
                self._api_endpoint = ""
        else:
            self._sdk = cast(DataAPIClient, client_or_sdk)
            self._api_endpoint = ""

        # Lazily connect to the database
        self._database: Any = self._sdk.get_database(api_endpoint=self._api_endpoint) if self._api_endpoint else None  # type: ignore[reportUnknownMemberType]

    def set_api_endpoint(self, api_endpoint: str) -> None:
        """Set the API endpoint and connect to the database.

        Args:
            api_endpoint: The database API endpoint URL
        """
        self._api_endpoint = api_endpoint
        self._database = self._sdk.get_database(api_endpoint=api_endpoint)  # type: ignore[reportUnknownMemberType]

    def _ensure_database(self) -> None:
        """Ensure a database connection is established."""
        if self._database is None:
            raise ValueError(
                "No database connection. Provide api_endpoint via DataStaxClient "
                "or call set_api_endpoint()."
            )

    # -----------------------------------------------------------------------
    # Databases
    # -----------------------------------------------------------------------

    def list_databases(
        self,
    ) -> DataStaxResponse:
        """List all databases accessible via the admin API.
        Returns:
            DataStaxResponse with operation result
        """
        try:
            admin = self._sdk.get_admin()  # type: ignore[reportUnknownMemberType]
            dbs = list(admin.list_databases())  # type: ignore[reportUnknownMemberType, reportUnknownArgumentType]
            result = [str(db) for db in dbs]  # type: ignore[reportUnknownVariableType, reportUnknownArgumentType]
            return DataStaxResponse(success=True, data=result)  # type: ignore[reportUnknownArgumentType]
        except Exception as e:
            return DataStaxResponse(
                success=False, error=str(e), message="Failed to execute list_databases"
            )


    # -----------------------------------------------------------------------
    # Collections
    # -----------------------------------------------------------------------

    def list_collections(
        self,
    ) -> DataStaxResponse:
        """List all collection names in the database.
        Returns:
            DataStaxResponse with operation result
        """
        try:
            result: Any = self._database.list_collection_names()
            return DataStaxResponse(success=True, data=result)  # type: ignore[reportUnknownArgumentType]
        except Exception as e:
            return DataStaxResponse(
                success=False, error=str(e), message="Failed to execute list_collections"
            )


    def get_collection_info(
        self,
        collection_name: str,
    ) -> DataStaxResponse:
        """Get information about a collection by listing and filtering.

        Args:
            collection_name: The collection name

        Returns:
            DataStaxResponse with operation result
        """
        try:
            names = self._database.list_collection_names()
            found = collection_name in names
            result = {"name": collection_name, "exists": found}
            return DataStaxResponse(success=True, data=result)  # type: ignore[reportUnknownArgumentType]
        except Exception as e:
            return DataStaxResponse(
                success=False, error=str(e), message="Failed to execute get_collection_info"
            )


    # -----------------------------------------------------------------------
    # Documents
    # -----------------------------------------------------------------------

    def find_documents(
        self,
        collection_name: str,
        *,
        filter: dict[str, object] | None = None,
        limit: int | None = None,
    ) -> DataStaxResponse:
        """Find documents in a collection with optional filter.

        Args:
            collection_name: The collection name
            filter: Filter criteria
            limit: Maximum documents to return

        Returns:
            DataStaxResponse with operation result
        """
        try:
            collection = self._database.get_collection(collection_name)
            filter_dict = filter or {}
            cursor = collection.find(filter_dict, limit=limit or 20)
            result: Any = list(cursor)
            return DataStaxResponse(success=True, data=result)  # type: ignore[reportUnknownArgumentType]
        except Exception as e:
            return DataStaxResponse(
                success=False, error=str(e), message="Failed to execute find_documents"
            )


    def find_one(
        self,
        collection_name: str,
        *,
        filter: dict[str, object] | None = None,
    ) -> DataStaxResponse:
        """Find a single document by filter.

        Args:
            collection_name: The collection name
            filter: Filter criteria

        Returns:
            DataStaxResponse with operation result
        """
        try:
            collection = self._database.get_collection(collection_name)
            filter_dict = filter or {}
            result: Any = collection.find_one(filter_dict)
            return DataStaxResponse(success=True, data=result)  # type: ignore[reportUnknownArgumentType]
        except Exception as e:
            return DataStaxResponse(
                success=False, error=str(e), message="Failed to execute find_one"
            )


    def find_by_id(
        self,
        collection_name: str,
        document_id: str,
    ) -> DataStaxResponse:
        """Find a single document by its ``_id``.

        Args:
            collection_name: The collection name
            document_id: The document _id

        Returns:
            DataStaxResponse with operation result
        """
        try:
            collection = self._database.get_collection(collection_name)
            result: Any = collection.find_one({"_id": document_id})
            return DataStaxResponse(success=True, data=result)  # type: ignore[reportUnknownArgumentType]
        except Exception as e:
            return DataStaxResponse(
                success=False, error=str(e), message="Failed to execute find_by_id"
            )


    def insert_one(
        self,
        collection_name: str,
        document: dict[str, object],
    ) -> DataStaxResponse:
        """Insert a single document into a collection.

        Args:
            collection_name: The collection name
            document: The document to insert

        Returns:
            DataStaxResponse with operation result
        """
        try:
            collection = self._database.get_collection(collection_name)
            result: Any = collection.insert_one(document)
            return DataStaxResponse(success=True, data=result)  # type: ignore[reportUnknownArgumentType]
        except Exception as e:
            return DataStaxResponse(
                success=False, error=str(e), message="Failed to execute insert_one"
            )


    def insert_many(
        self,
        collection_name: str,
        documents: list[dict[str, object]],
    ) -> DataStaxResponse:
        """Insert multiple documents into a collection.

        Args:
            collection_name: The collection name
            documents: List of documents to insert

        Returns:
            DataStaxResponse with operation result
        """
        try:
            collection = self._database.get_collection(collection_name)
            result: Any = collection.insert_many(documents)
            return DataStaxResponse(success=True, data=result)  # type: ignore[reportUnknownArgumentType]
        except Exception as e:
            return DataStaxResponse(
                success=False, error=str(e), message="Failed to execute insert_many"
            )


    def update_one(
        self,
        collection_name: str,
        filter: dict[str, object],
        update: dict[str, object],
    ) -> DataStaxResponse:
        """Update a single document matching the filter.

        Args:
            collection_name: The collection name
            filter: Filter criteria
            update: Update operations

        Returns:
            DataStaxResponse with operation result
        """
        try:
            collection = self._database.get_collection(collection_name)
            result: Any = collection.update_one(filter, update)
            return DataStaxResponse(success=True, data=result)  # type: ignore[reportUnknownArgumentType]
        except Exception as e:
            return DataStaxResponse(
                success=False, error=str(e), message="Failed to execute update_one"
            )


    def delete_one(
        self,
        collection_name: str,
        filter: dict[str, object],
    ) -> DataStaxResponse:
        """Delete a single document matching the filter.

        Args:
            collection_name: The collection name
            filter: Filter criteria

        Returns:
            DataStaxResponse with operation result
        """
        try:
            collection = self._database.get_collection(collection_name)
            result: Any = collection.delete_one(filter)
            return DataStaxResponse(success=True, data=result)  # type: ignore[reportUnknownArgumentType]
        except Exception as e:
            return DataStaxResponse(
                success=False, error=str(e), message="Failed to execute delete_one"
            )


    def count_documents(
        self,
        collection_name: str,
        *,
        filter: dict[str, object] | None = None,
        upper_bound: int | None = None,
    ) -> DataStaxResponse:
        """Count documents in a collection matching an optional filter.

        Args:
            collection_name: The collection name
            filter: Filter criteria
            upper_bound: Upper bound for count estimation

        Returns:
            DataStaxResponse with operation result
        """
        try:
            collection = self._database.get_collection(collection_name)
            filter_dict = filter or {}
            result: Any = collection.count_documents(filter_dict, upper_bound=upper_bound or 1000)
            return DataStaxResponse(success=True, data=result)  # type: ignore[reportUnknownArgumentType]
        except Exception as e:
            return DataStaxResponse(
                success=False, error=str(e), message="Failed to execute count_documents"
            )


    # -----------------------------------------------------------------------
    # Collection Management
    # -----------------------------------------------------------------------

    def create_collection(
        self,
        collection_name: str,
    ) -> DataStaxResponse:
        """Create a new collection in the database.

        Args:
            collection_name: The collection name

        Returns:
            DataStaxResponse with operation result
        """
        try:
            self._database.create_collection(collection_name)
            result = {"name": collection_name, "created": True}
            return DataStaxResponse(success=True, data=result)  # type: ignore[reportUnknownArgumentType]
        except Exception as e:
            return DataStaxResponse(
                success=False, error=str(e), message="Failed to execute create_collection"
            )


    def drop_collection(
        self,
        collection_name: str,
    ) -> DataStaxResponse:
        """Drop (delete) a collection from the database.

        Args:
            collection_name: The collection name

        Returns:
            DataStaxResponse with operation result
        """
        try:
            self._database.drop_collection(collection_name)
            result = {"name": collection_name, "dropped": True}
            return DataStaxResponse(success=True, data=result)  # type: ignore[reportUnknownArgumentType]
        except Exception as e:
            return DataStaxResponse(
                success=False, error=str(e), message="Failed to execute drop_collection"
            )
