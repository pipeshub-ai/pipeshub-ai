# ruff: noqa
"""
Benchling SDK DataSource - Auto-generated SDK wrapper

Generated from Benchling SDK method specifications.
Wraps the official benchling-sdk Python package.
All methods have explicit parameter signatures.
"""

from __future__ import annotations

from typing import Any, Union, cast

from benchling_sdk.benchling import Benchling  # type: ignore[reportMissingImports]

from app.sources.client.benchling.benchling import BenchlingClient, BenchlingResponse


class BenchlingDataSource:
    """Benchling SDK DataSource

    Provides typed wrapper methods for Benchling SDK operations:
    - Notebook entries
    - Folders
    - Entity schemas
    - Custom entities
    - DNA sequences
    - AA sequences
    - Oligos
    - Users
    - Projects

    All methods return BenchlingResponse objects.
    """

    def __init__(self, client_or_sdk: Union[BenchlingClient, Benchling, object]) -> None:  # type: ignore[reportUnknownParameterType]
        """Initialize with BenchlingClient, raw SDK, or any wrapper with ``get_sdk()``.

        Args:
            client_or_sdk: BenchlingClient, Benchling SDK instance, or wrapper
        """
        super().__init__()
        if isinstance(client_or_sdk, Benchling):  # type: ignore[reportUnknownMemberType]
            self._sdk: Benchling = client_or_sdk  # type: ignore[reportUnknownMemberType]
        elif hasattr(client_or_sdk, "get_sdk"):  # type: ignore[reportUnknownArgumentType]
            sdk_obj = getattr(client_or_sdk, "get_sdk")()  # type: ignore[reportUnknownArgumentType]
            self._sdk = cast(Benchling, sdk_obj)
        else:
            self._sdk = cast(Benchling, client_or_sdk)

    # -----------------------------------------------------------------------
    # Entries
    # -----------------------------------------------------------------------

    def list_entries(
        self,
    ) -> BenchlingResponse:
        """List notebook entries.
        Returns:
            BenchlingResponse with operation result
        """
        try:
            result: Any = list(self._sdk.entries.list())  # type: ignore[reportUnknownMemberType, reportUnknownArgumentType]
            return BenchlingResponse(success=True, data=result)  # type: ignore[reportUnknownArgumentType]
        except Exception as e:
            return BenchlingResponse(
                success=False, error=str(e), message="Failed to execute list_entries"
            )


    def get_entry(
        self,
        entry_id: str,
    ) -> BenchlingResponse:
        """Get a single notebook entry by ID.

        Args:
            entry_id: The entry ID (e.g. ``etr_xxx``)

        Returns:
            BenchlingResponse with operation result
        """
        try:
            result: Any = self._sdk.entries.get_by_id(entry_id)  # type: ignore[reportUnknownMemberType, reportUnknownVariableType]
            return BenchlingResponse(success=True, data=result)  # type: ignore[reportUnknownArgumentType]
        except Exception as e:
            return BenchlingResponse(
                success=False, error=str(e), message="Failed to execute get_entry"
            )


    # -----------------------------------------------------------------------
    # Folders
    # -----------------------------------------------------------------------

    def list_folders(
        self,
    ) -> BenchlingResponse:
        """List folders.
        Returns:
            BenchlingResponse with operation result
        """
        try:
            result: Any = list(self._sdk.folders.list())  # type: ignore[reportUnknownMemberType, reportUnknownArgumentType]
            return BenchlingResponse(success=True, data=result)  # type: ignore[reportUnknownArgumentType]
        except Exception as e:
            return BenchlingResponse(
                success=False, error=str(e), message="Failed to execute list_folders"
            )


    def get_folder(
        self,
        folder_id: str,
    ) -> BenchlingResponse:
        """Get a single folder by ID.

        Args:
            folder_id: The folder ID

        Returns:
            BenchlingResponse with operation result
        """
        try:
            result: Any = self._sdk.folders.get_by_id(folder_id)  # type: ignore[reportUnknownMemberType, reportUnknownVariableType]
            return BenchlingResponse(success=True, data=result)  # type: ignore[reportUnknownArgumentType]
        except Exception as e:
            return BenchlingResponse(
                success=False, error=str(e), message="Failed to execute get_folder"
            )


    # -----------------------------------------------------------------------
    # Schemas
    # -----------------------------------------------------------------------

    def list_entity_schemas(
        self,
    ) -> BenchlingResponse:
        """List entity schemas.
        Returns:
            BenchlingResponse with operation result
        """
        try:
            result: Any = list(self._sdk.schemas.list_entity_schemas())  # type: ignore[reportUnknownMemberType, reportUnknownArgumentType]
            return BenchlingResponse(success=True, data=result)  # type: ignore[reportUnknownArgumentType]
        except Exception as e:
            return BenchlingResponse(
                success=False, error=str(e), message="Failed to execute list_entity_schemas"
            )


    def get_entity_schema(
        self,
        schema_id: str,
    ) -> BenchlingResponse:
        """Get a single entity schema by ID.

        Args:
            schema_id: The schema ID

        Returns:
            BenchlingResponse with operation result
        """
        try:
            result: Any = self._sdk.schemas.get_entity_schema_by_id(schema_id)  # type: ignore[reportUnknownMemberType, reportUnknownVariableType]
            return BenchlingResponse(success=True, data=result)  # type: ignore[reportUnknownArgumentType]
        except Exception as e:
            return BenchlingResponse(
                success=False, error=str(e), message="Failed to execute get_entity_schema"
            )


    # -----------------------------------------------------------------------
    # Custom Entities
    # -----------------------------------------------------------------------

    def list_custom_entities(
        self,
    ) -> BenchlingResponse:
        """List custom entities.
        Returns:
            BenchlingResponse with operation result
        """
        try:
            result: Any = list(self._sdk.custom_entities.list())  # type: ignore[reportUnknownMemberType, reportUnknownArgumentType]
            return BenchlingResponse(success=True, data=result)  # type: ignore[reportUnknownArgumentType]
        except Exception as e:
            return BenchlingResponse(
                success=False, error=str(e), message="Failed to execute list_custom_entities"
            )


    def get_custom_entity(
        self,
        entity_id: str,
    ) -> BenchlingResponse:
        """Get a single custom entity by ID.

        Args:
            entity_id: The custom entity ID

        Returns:
            BenchlingResponse with operation result
        """
        try:
            result: Any = self._sdk.custom_entities.get_by_id(entity_id)  # type: ignore[reportUnknownMemberType, reportUnknownVariableType]
            return BenchlingResponse(success=True, data=result)  # type: ignore[reportUnknownArgumentType]
        except Exception as e:
            return BenchlingResponse(
                success=False, error=str(e), message="Failed to execute get_custom_entity"
            )


    # -----------------------------------------------------------------------
    # DNA Sequences
    # -----------------------------------------------------------------------

    def list_dna_sequences(
        self,
    ) -> BenchlingResponse:
        """List DNA sequences.
        Returns:
            BenchlingResponse with operation result
        """
        try:
            result: Any = list(self._sdk.dna_sequences.list())  # type: ignore[reportUnknownMemberType, reportUnknownArgumentType]
            return BenchlingResponse(success=True, data=result)  # type: ignore[reportUnknownArgumentType]
        except Exception as e:
            return BenchlingResponse(
                success=False, error=str(e), message="Failed to execute list_dna_sequences"
            )


    def get_dna_sequence(
        self,
        sequence_id: str,
    ) -> BenchlingResponse:
        """Get a single DNA sequence by ID.

        Args:
            sequence_id: The DNA sequence ID

        Returns:
            BenchlingResponse with operation result
        """
        try:
            result: Any = self._sdk.dna_sequences.get_by_id(sequence_id)  # type: ignore[reportUnknownMemberType, reportUnknownVariableType]
            return BenchlingResponse(success=True, data=result)  # type: ignore[reportUnknownArgumentType]
        except Exception as e:
            return BenchlingResponse(
                success=False, error=str(e), message="Failed to execute get_dna_sequence"
            )


    # -----------------------------------------------------------------------
    # AA Sequences
    # -----------------------------------------------------------------------

    def list_aa_sequences(
        self,
    ) -> BenchlingResponse:
        """List AA (amino acid) sequences.
        Returns:
            BenchlingResponse with operation result
        """
        try:
            result: Any = list(self._sdk.aa_sequences.list())  # type: ignore[reportUnknownMemberType, reportUnknownArgumentType]
            return BenchlingResponse(success=True, data=result)  # type: ignore[reportUnknownArgumentType]
        except Exception as e:
            return BenchlingResponse(
                success=False, error=str(e), message="Failed to execute list_aa_sequences"
            )


    def get_aa_sequence(
        self,
        sequence_id: str,
    ) -> BenchlingResponse:
        """Get a single AA sequence by ID.

        Args:
            sequence_id: The AA sequence ID

        Returns:
            BenchlingResponse with operation result
        """
        try:
            result: Any = self._sdk.aa_sequences.get_by_id(sequence_id)  # type: ignore[reportUnknownMemberType, reportUnknownVariableType]
            return BenchlingResponse(success=True, data=result)  # type: ignore[reportUnknownArgumentType]
        except Exception as e:
            return BenchlingResponse(
                success=False, error=str(e), message="Failed to execute get_aa_sequence"
            )


    # -----------------------------------------------------------------------
    # Oligos
    # -----------------------------------------------------------------------

    def list_oligos(
        self,
    ) -> BenchlingResponse:
        """List oligos.
        Returns:
            BenchlingResponse with operation result
        """
        try:
            result: Any = list(self._sdk.oligos.list())  # type: ignore[reportUnknownMemberType, reportUnknownArgumentType]
            return BenchlingResponse(success=True, data=result)  # type: ignore[reportUnknownArgumentType]
        except Exception as e:
            return BenchlingResponse(
                success=False, error=str(e), message="Failed to execute list_oligos"
            )


    def get_oligo(
        self,
        oligo_id: str,
    ) -> BenchlingResponse:
        """Get a single oligo by ID.

        Args:
            oligo_id: The oligo ID

        Returns:
            BenchlingResponse with operation result
        """
        try:
            result: Any = self._sdk.oligos.get_by_id(oligo_id)  # type: ignore[reportUnknownMemberType, reportUnknownVariableType]
            return BenchlingResponse(success=True, data=result)  # type: ignore[reportUnknownArgumentType]
        except Exception as e:
            return BenchlingResponse(
                success=False, error=str(e), message="Failed to execute get_oligo"
            )


    # -----------------------------------------------------------------------
    # Users
    # -----------------------------------------------------------------------

    def list_users(
        self,
    ) -> BenchlingResponse:
        """List users.
        Returns:
            BenchlingResponse with operation result
        """
        try:
            result: Any = list(self._sdk.users.list())  # type: ignore[reportUnknownMemberType, reportUnknownArgumentType]
            return BenchlingResponse(success=True, data=result)  # type: ignore[reportUnknownArgumentType]
        except Exception as e:
            return BenchlingResponse(
                success=False, error=str(e), message="Failed to execute list_users"
            )


    def get_user(
        self,
        user_id: str,
    ) -> BenchlingResponse:
        """Get a single user by ID.

        Args:
            user_id: The user ID

        Returns:
            BenchlingResponse with operation result
        """
        try:
            result: Any = self._sdk.users.get_by_id(user_id)  # type: ignore[reportUnknownMemberType, reportUnknownVariableType]
            return BenchlingResponse(success=True, data=result)  # type: ignore[reportUnknownArgumentType]
        except Exception as e:
            return BenchlingResponse(
                success=False, error=str(e), message="Failed to execute get_user"
            )


    # -----------------------------------------------------------------------
    # Projects
    # -----------------------------------------------------------------------

    def list_projects(
        self,
    ) -> BenchlingResponse:
        """List projects.
        Returns:
            BenchlingResponse with operation result
        """
        try:
            result: Any = list(self._sdk.projects.list())  # type: ignore[reportUnknownMemberType, reportUnknownArgumentType]
            return BenchlingResponse(success=True, data=result)  # type: ignore[reportUnknownArgumentType]
        except Exception as e:
            return BenchlingResponse(
                success=False, error=str(e), message="Failed to execute list_projects"
            )


    def get_project(
        self,
        project_id: str,
    ) -> BenchlingResponse:
        """Get a single project by ID.

        Args:
            project_id: The project ID

        Returns:
            BenchlingResponse with operation result
        """
        try:
            result: Any = self._sdk.projects.get_by_id(project_id)  # type: ignore[reportUnknownMemberType, reportUnknownVariableType]
            return BenchlingResponse(success=True, data=result)  # type: ignore[reportUnknownArgumentType]
        except Exception as e:
            return BenchlingResponse(
                success=False, error=str(e), message="Failed to execute get_project"
            )

