"""
Evernote DataSource - Thrift Client Wrapper
============================================

Auto-generated wrapper for Evernote SDK Thrift clients.

This wraps the official Evernote SDK (https://github.com/evernote/evernote-sdk-python)
which uses Apache Thrift RPC protocol, NOT REST APIs.

Usage:
    from evernote.api.client import EvernoteClient
    from evernote_data_source import EvernoteDataSource

    # Initialize Evernote SDK client
    evernote_client = EvernoteClient(
        token='your_auth_token',
        sandbox=True  # or False for production
    )

    # Create datasource wrapper
    datasource = EvernoteDataSource(evernote_client)
    # Call methods (async)
    response = await datasource.list_notebooks(authentication_token=token)
    if response.success:
        print(response.data)

Generated Methods:
    - NoteStore: 77 methods
    - UserStore: 16 methods
    - Total: 93 methods
"""

from typing import Any, Dict, Optional

from app.sources.client.evernote.evernote import EvernoteClient, EvernoteResponse


class EvernoteDataSource:
    """
    Comprehensive Evernote API wrapper for Thrift clients.
    Wraps all NoteStore and UserStore methods from the Evernote SDK.
    Handles Thrift exceptions and provides standardized responses.
    Architecture:
        - Uses Evernote SDK Thrift clients (not HTTP requests)
        - Wraps note_store and user_store Thrift objects
        - Converts Thrift objects to Python dicts
        - Handles exceptions gracefully
    Methods:
        NoteStore (77 methods):
            - Sync: getSyncState, getSyncChunk, getFilteredSyncChunk
            - Notebooks: listNotebooks, createNotebook, updateNotebook, etc.
            - Notes: getNote, createNote, updateNote, deleteNote, etc.
            - Tags: listTags, createTag, updateTag, expungeTag, etc.
            - Resources: getResource, updateResource, getResourceData, etc.
            - Search: findNotes, findNotesMetadata, findNoteCounts, etc.
            - Sharing: shareNotebook, shareNote, manageNotebookShares, etc.
        UserStore (16 methods):
            - Auth: authenticateLongSession, authenticateToBusiness, etc.
            - User: getUser, getPremiumInfo, getUserUrls, etc.
            - Business: inviteToBusiness, listBusinessUsers, etc.
    """

    def __init__(self, evernote_client: EvernoteClient) -> None:
        """
        Initialize with Evernote SDK client.
        Args:
            evernote_client: Instance of EvernoteClient
        """
        self.client = evernote_client
        self.note_store = evernote_client.get_note_store()
        self.user_store = evernote_client.get_user_store()

    def _thrift_to_dict(self, obj: object) -> Dict[str, Any]:
        """Convert Thrift object to dictionary."""
        if obj is None:
            return None

        if hasattr(obj, '__dict__'):
            result = {}
            for key, value in obj.__dict__.items():
                if value is not None:
                    if isinstance(value, list):
                        result[key] = [self._thrift_to_dict(item) for item in value]
                    elif hasattr(value, '__dict__'):
                        result[key] = self._thrift_to_dict(value)
                    else:
                        result[key] = value
            return result

        return obj

    def get_client(self) -> EvernoteClient:
        """Get the underlying Evernote client."""
        return self.client

    def get_note_store(self) -> object:
        """Get the NoteStore Thrift client."""
        return self.note_store

    def get_user_store(self) -> object:
        """Get the UserStore Thrift client."""
        return self.user_store

    async def get_sync_state(
        self,
        authentication_token: str
    ) -> EvernoteResponse:
        """
        Wrapper for note_store.getSyncState()
        Args:
    authentication_token (required): authenticationToken
        Returns:
            EvernoteResponse: Standardized response with success/data/error
        """
        try:
            # Build arguments list
            args = []
            if authentication_token is not None:
                args.append(authentication_token)

            # Call Thrift client method
            result = self.note_store.getSyncState(*args)

            # Convert Thrift objects to dict for easier handling
            if hasattr(result, '__dict__'):
                data = self._thrift_to_dict(result)
            elif isinstance(result, list):
                data = [self._thrift_to_dict(item) if hasattr(item, '__dict__') else item for item in result]
            else:
                data = result

            return EvernoteResponse(
                success=True,
                data=data
            )
        except Exception as e:
            # Handle Thrift exceptions
            error_type = type(e).__name__
            return EvernoteResponse(
                success=False,
                error=f"{error_type}: {str(e)}"
            )

    async def get_sync_chunk(
        self,
        authentication_token: str,
        after_usn: str,
        max_entries: str,
        full_sync_only: str
    ) -> EvernoteResponse:
        """
        Wrapper for note_store.getSyncChunk()
        Args:
    authentication_token (required): authenticationToken
            after_usn (required): afterUSN
            max_entries (required): maxEntries
            full_sync_only (required): fullSyncOnly
        Returns:
            EvernoteResponse: Standardized response with success/data/error
        """
        try:
            # Build arguments list
            args = []
            if authentication_token is not None:
                args.append(authentication_token)
            if after_usn is not None:
                args.append(after_usn)
            if max_entries is not None:
                args.append(max_entries)
            if full_sync_only is not None:
                args.append(full_sync_only)

            # Call Thrift client method
            result = self.note_store.getSyncChunk(*args)

            # Convert Thrift objects to dict for easier handling
            if hasattr(result, '__dict__'):
                data = self._thrift_to_dict(result)
            elif isinstance(result, list):
                data = [self._thrift_to_dict(item) if hasattr(item, '__dict__') else item for item in result]
            else:
                data = result

            return EvernoteResponse(
                success=True,
                data=data
            )
        except Exception as e:
            # Handle Thrift exceptions
            error_type = type(e).__name__
            return EvernoteResponse(
                success=False,
                error=f"{error_type}: {str(e)}"
            )

    async def get_filtered_sync_chunk(
        self,
        authentication_token: str,
        after_usn: str,
        max_entries: str,
        filter: str
    ) -> EvernoteResponse:
        """
        Wrapper for note_store.getFilteredSyncChunk()
        Args:
    authentication_token (required): authenticationToken
            after_usn (required): afterUSN
            max_entries (required): maxEntries
            filter (required): filter
        Returns:
            EvernoteResponse: Standardized response with success/data/error
        """
        try:
            # Build arguments list
            args = []
            if authentication_token is not None:
                args.append(authentication_token)
            if after_usn is not None:
                args.append(after_usn)
            if max_entries is not None:
                args.append(max_entries)
            if filter is not None:
                args.append(filter)

            # Call Thrift client method
            result = self.note_store.getFilteredSyncChunk(*args)

            # Convert Thrift objects to dict for easier handling
            if hasattr(result, '__dict__'):
                data = self._thrift_to_dict(result)
            elif isinstance(result, list):
                data = [self._thrift_to_dict(item) if hasattr(item, '__dict__') else item for item in result]
            else:
                data = result

            return EvernoteResponse(
                success=True,
                data=data
            )
        except Exception as e:
            # Handle Thrift exceptions
            error_type = type(e).__name__
            return EvernoteResponse(
                success=False,
                error=f"{error_type}: {str(e)}"
            )

    async def get_linked_notebook_sync_state(
        self,
        authentication_token: str,
        linked_notebook: str
    ) -> EvernoteResponse:
        """
        Wrapper for note_store.getLinkedNotebookSyncState()
        Args:
    authentication_token (required): authenticationToken
            linked_notebook (required): linkedNotebook
        Returns:
            EvernoteResponse: Standardized response with success/data/error
        """
        try:
            # Build arguments list
            args = []
            if authentication_token is not None:
                args.append(authentication_token)
            if linked_notebook is not None:
                args.append(linked_notebook)

            # Call Thrift client method
            result = self.note_store.getLinkedNotebookSyncState(*args)

            # Convert Thrift objects to dict for easier handling
            if hasattr(result, '__dict__'):
                data = self._thrift_to_dict(result)
            elif isinstance(result, list):
                data = [self._thrift_to_dict(item) if hasattr(item, '__dict__') else item for item in result]
            else:
                data = result

            return EvernoteResponse(
                success=True,
                data=data
            )
        except Exception as e:
            # Handle Thrift exceptions
            error_type = type(e).__name__
            return EvernoteResponse(
                success=False,
                error=f"{error_type}: {str(e)}"
            )

    async def get_linked_notebook_sync_chunk(
        self,
        authentication_token: str,
        linked_notebook: str,
        after_usn: str,
        max_entries: str,
        full_sync_only: str
    ) -> EvernoteResponse:
        """
        Wrapper for note_store.getLinkedNotebookSyncChunk()
        Args:
    authentication_token (required): authenticationToken
            linked_notebook (required): linkedNotebook
            after_usn (required): afterUSN
            max_entries (required): maxEntries
            full_sync_only (required): fullSyncOnly
        Returns:
            EvernoteResponse: Standardized response with success/data/error
        """
        try:
            # Build arguments list
            args = []
            if authentication_token is not None:
                args.append(authentication_token)
            if linked_notebook is not None:
                args.append(linked_notebook)
            if after_usn is not None:
                args.append(after_usn)
            if max_entries is not None:
                args.append(max_entries)
            if full_sync_only is not None:
                args.append(full_sync_only)

            # Call Thrift client method
            result = self.note_store.getLinkedNotebookSyncChunk(*args)

            # Convert Thrift objects to dict for easier handling
            if hasattr(result, '__dict__'):
                data = self._thrift_to_dict(result)
            elif isinstance(result, list):
                data = [self._thrift_to_dict(item) if hasattr(item, '__dict__') else item for item in result]
            else:
                data = result

            return EvernoteResponse(
                success=True,
                data=data
            )
        except Exception as e:
            # Handle Thrift exceptions
            error_type = type(e).__name__
            return EvernoteResponse(
                success=False,
                error=f"{error_type}: {str(e)}"
            )

    async def list_notebooks(
        self,
        authentication_token: str
    ) -> EvernoteResponse:
        """
        Wrapper for note_store.listNotebooks()
        Args:
    authentication_token (required): authenticationToken
        Returns:
            EvernoteResponse: Standardized response with success/data/error
        """
        try:
            # Build arguments list
            args = []
            if authentication_token is not None:
                args.append(authentication_token)

            # Call Thrift client method
            result = self.note_store.listNotebooks(*args)

            # Convert Thrift objects to dict for easier handling
            if hasattr(result, '__dict__'):
                data = self._thrift_to_dict(result)
            elif isinstance(result, list):
                data = [self._thrift_to_dict(item) if hasattr(item, '__dict__') else item for item in result]
            else:
                data = result

            return EvernoteResponse(
                success=True,
                data=data
            )
        except Exception as e:
            # Handle Thrift exceptions
            error_type = type(e).__name__
            return EvernoteResponse(
                success=False,
                error=f"{error_type}: {str(e)}"
            )

    async def list_accessible_business_notebooks(
        self,
        authentication_token: str
    ) -> EvernoteResponse:
        """
        Wrapper for note_store.listAccessibleBusinessNotebooks()
        Args:
    authentication_token (required): authenticationToken
        Returns:
            EvernoteResponse: Standardized response with success/data/error
        """
        try:
            # Build arguments list
            args = []
            if authentication_token is not None:
                args.append(authentication_token)

            # Call Thrift client method
            result = self.note_store.listAccessibleBusinessNotebooks(*args)

            # Convert Thrift objects to dict for easier handling
            if hasattr(result, '__dict__'):
                data = self._thrift_to_dict(result)
            elif isinstance(result, list):
                data = [self._thrift_to_dict(item) if hasattr(item, '__dict__') else item for item in result]
            else:
                data = result

            return EvernoteResponse(
                success=True,
                data=data
            )
        except Exception as e:
            # Handle Thrift exceptions
            error_type = type(e).__name__
            return EvernoteResponse(
                success=False,
                error=f"{error_type}: {str(e)}"
            )

    async def get_notebook(
        self,
        authentication_token: str,
        guid: str
    ) -> EvernoteResponse:
        """
        Wrapper for note_store.getNotebook()
        Args:
    authentication_token (required): authenticationToken
            guid (required): guid
        Returns:
            EvernoteResponse: Standardized response with success/data/error
        """
        try:
            # Build arguments list
            args = []
            if authentication_token is not None:
                args.append(authentication_token)
            if guid is not None:
                args.append(guid)

            # Call Thrift client method
            result = self.note_store.getNotebook(*args)

            # Convert Thrift objects to dict for easier handling
            if hasattr(result, '__dict__'):
                data = self._thrift_to_dict(result)
            elif isinstance(result, list):
                data = [self._thrift_to_dict(item) if hasattr(item, '__dict__') else item for item in result]
            else:
                data = result

            return EvernoteResponse(
                success=True,
                data=data
            )
        except Exception as e:
            # Handle Thrift exceptions
            error_type = type(e).__name__
            return EvernoteResponse(
                success=False,
                error=f"{error_type}: {str(e)}"
            )

    async def get_default_notebook(
        self,
        authentication_token: str
    ) -> EvernoteResponse:
        """
        Wrapper for note_store.getDefaultNotebook()
        Args:
    authentication_token (required): authenticationToken
        Returns:
            EvernoteResponse: Standardized response with success/data/error
        """
        try:
            # Build arguments list
            args = []
            if authentication_token is not None:
                args.append(authentication_token)

            # Call Thrift client method
            result = self.note_store.getDefaultNotebook(*args)

            # Convert Thrift objects to dict for easier handling
            if hasattr(result, '__dict__'):
                data = self._thrift_to_dict(result)
            elif isinstance(result, list):
                data = [self._thrift_to_dict(item) if hasattr(item, '__dict__') else item for item in result]
            else:
                data = result

            return EvernoteResponse(
                success=True,
                data=data
            )
        except Exception as e:
            # Handle Thrift exceptions
            error_type = type(e).__name__
            return EvernoteResponse(
                success=False,
                error=f"{error_type}: {str(e)}"
            )

    async def create_notebook(
        self,
        authentication_token: str,
        notebook: str
    ) -> EvernoteResponse:
        """
        Wrapper for note_store.createNotebook()
        Args:
    authentication_token (required): authenticationToken
            notebook (required): notebook
        Returns:
            EvernoteResponse: Standardized response with success/data/error
        """
        try:
            # Build arguments list
            args = []
            if authentication_token is not None:
                args.append(authentication_token)
            if notebook is not None:
                args.append(notebook)

            # Call Thrift client method
            result = self.note_store.createNotebook(*args)

            # Convert Thrift objects to dict for easier handling
            if hasattr(result, '__dict__'):
                data = self._thrift_to_dict(result)
            elif isinstance(result, list):
                data = [self._thrift_to_dict(item) if hasattr(item, '__dict__') else item for item in result]
            else:
                data = result

            return EvernoteResponse(
                success=True,
                data=data
            )
        except Exception as e:
            # Handle Thrift exceptions
            error_type = type(e).__name__
            return EvernoteResponse(
                success=False,
                error=f"{error_type}: {str(e)}"
            )

    async def update_notebook(
        self,
        authentication_token: str,
        notebook: str
    ) -> EvernoteResponse:
        """
        Wrapper for note_store.updateNotebook()
        Args:
    authentication_token (required): authenticationToken
            notebook (required): notebook
        Returns:
            EvernoteResponse: Standardized response with success/data/error
        """
        try:
            # Build arguments list
            args = []
            if authentication_token is not None:
                args.append(authentication_token)
            if notebook is not None:
                args.append(notebook)

            # Call Thrift client method
            result = self.note_store.updateNotebook(*args)

            # Convert Thrift objects to dict for easier handling
            if hasattr(result, '__dict__'):
                data = self._thrift_to_dict(result)
            elif isinstance(result, list):
                data = [self._thrift_to_dict(item) if hasattr(item, '__dict__') else item for item in result]
            else:
                data = result

            return EvernoteResponse(
                success=True,
                data=data
            )
        except Exception as e:
            # Handle Thrift exceptions
            error_type = type(e).__name__
            return EvernoteResponse(
                success=False,
                error=f"{error_type}: {str(e)}"
            )

    async def expunge_notebook(
        self,
        authentication_token: str,
        guid: str
    ) -> EvernoteResponse:
        """
        Wrapper for note_store.expungeNotebook()
        Args:
    authentication_token (required): authenticationToken
            guid (required): guid
        Returns:
            EvernoteResponse: Standardized response with success/data/error
        """
        try:
            # Build arguments list
            args = []
            if authentication_token is not None:
                args.append(authentication_token)
            if guid is not None:
                args.append(guid)

            # Call Thrift client method
            result = self.note_store.expungeNotebook(*args)

            # Convert Thrift objects to dict for easier handling
            if hasattr(result, '__dict__'):
                data = self._thrift_to_dict(result)
            elif isinstance(result, list):
                data = [self._thrift_to_dict(item) if hasattr(item, '__dict__') else item for item in result]
            else:
                data = result

            return EvernoteResponse(
                success=True,
                data=data
            )
        except Exception as e:
            # Handle Thrift exceptions
            error_type = type(e).__name__
            return EvernoteResponse(
                success=False,
                error=f"{error_type}: {str(e)}"
            )

    async def get_note(
        self,
        authentication_token: str,
        guid: str,
        with_content: str,
        with_resources_data: str,
        with_resources_recognition: str,
        with_resources_alternate_data: str
    ) -> EvernoteResponse:
        """
        Wrapper for note_store.getNote()
        Args:
    authentication_token (required): authenticationToken
            guid (required): guid
            with_content (required): withContent
            with_resources_data (required): withResourcesData
            with_resources_recognition (required): withResourcesRecognition
            with_resources_alternate_data (required): withResourcesAlternateData
        Returns:
            EvernoteResponse: Standardized response with success/data/error
        """
        try:
            # Build arguments list
            args = []
            if authentication_token is not None:
                args.append(authentication_token)
            if guid is not None:
                args.append(guid)
            if with_content is not None:
                args.append(with_content)
            if with_resources_data is not None:
                args.append(with_resources_data)
            if with_resources_recognition is not None:
                args.append(with_resources_recognition)
            if with_resources_alternate_data is not None:
                args.append(with_resources_alternate_data)

            # Call Thrift client method
            result = self.note_store.getNote(*args)

            # Convert Thrift objects to dict for easier handling
            if hasattr(result, '__dict__'):
                data = self._thrift_to_dict(result)
            elif isinstance(result, list):
                data = [self._thrift_to_dict(item) if hasattr(item, '__dict__') else item for item in result]
            else:
                data = result

            return EvernoteResponse(
                success=True,
                data=data
            )
        except Exception as e:
            # Handle Thrift exceptions
            error_type = type(e).__name__
            return EvernoteResponse(
                success=False,
                error=f"{error_type}: {str(e)}"
            )

    async def get_note_with_result_spec(
        self,
        authentication_token: str,
        guid: str,
        result_spec: str
    ) -> EvernoteResponse:
        """
        Wrapper for note_store.getNoteWithResultSpec()
        Args:
    authentication_token (required): authenticationToken
            guid (required): guid
            result_spec (required): resultSpec
        Returns:
            EvernoteResponse: Standardized response with success/data/error
        """
        try:
            # Build arguments list
            args = []
            if authentication_token is not None:
                args.append(authentication_token)
            if guid is not None:
                args.append(guid)
            if result_spec is not None:
                args.append(result_spec)

            # Call Thrift client method
            result = self.note_store.getNoteWithResultSpec(*args)

            # Convert Thrift objects to dict for easier handling
            if hasattr(result, '__dict__'):
                data = self._thrift_to_dict(result)
            elif isinstance(result, list):
                data = [self._thrift_to_dict(item) if hasattr(item, '__dict__') else item for item in result]
            else:
                data = result

            return EvernoteResponse(
                success=True,
                data=data
            )
        except Exception as e:
            # Handle Thrift exceptions
            error_type = type(e).__name__
            return EvernoteResponse(
                success=False,
                error=f"{error_type}: {str(e)}"
            )

    async def get_note_application_data(
        self,
        authentication_token: str,
        guid: str
    ) -> EvernoteResponse:
        """
        Wrapper for note_store.getNoteApplicationData()
        Args:
    authentication_token (required): authenticationToken
            guid (required): guid
        Returns:
            EvernoteResponse: Standardized response with success/data/error
        """
        try:
            # Build arguments list
            args = []
            if authentication_token is not None:
                args.append(authentication_token)
            if guid is not None:
                args.append(guid)

            # Call Thrift client method
            result = self.note_store.getNoteApplicationData(*args)

            # Convert Thrift objects to dict for easier handling
            if hasattr(result, '__dict__'):
                data = self._thrift_to_dict(result)
            elif isinstance(result, list):
                data = [self._thrift_to_dict(item) if hasattr(item, '__dict__') else item for item in result]
            else:
                data = result

            return EvernoteResponse(
                success=True,
                data=data
            )
        except Exception as e:
            # Handle Thrift exceptions
            error_type = type(e).__name__
            return EvernoteResponse(
                success=False,
                error=f"{error_type}: {str(e)}"
            )

    async def get_note_application_data_entry(
        self,
        authentication_token: str,
        guid: str,
        key: str
    ) -> EvernoteResponse:
        """
        Wrapper for note_store.getNoteApplicationDataEntry()
        Args:
    authentication_token (required): authenticationToken
            guid (required): guid
            key (required): key
        Returns:
            EvernoteResponse: Standardized response with success/data/error
        """
        try:
            # Build arguments list
            args = []
            if authentication_token is not None:
                args.append(authentication_token)
            if guid is not None:
                args.append(guid)
            if key is not None:
                args.append(key)

            # Call Thrift client method
            result = self.note_store.getNoteApplicationDataEntry(*args)

            # Convert Thrift objects to dict for easier handling
            if hasattr(result, '__dict__'):
                data = self._thrift_to_dict(result)
            elif isinstance(result, list):
                data = [self._thrift_to_dict(item) if hasattr(item, '__dict__') else item for item in result]
            else:
                data = result

            return EvernoteResponse(
                success=True,
                data=data
            )
        except Exception as e:
            # Handle Thrift exceptions
            error_type = type(e).__name__
            return EvernoteResponse(
                success=False,
                error=f"{error_type}: {str(e)}"
            )

    async def set_note_application_data_entry(
        self,
        authentication_token: str,
        guid: str,
        key: str,
        value: str
    ) -> EvernoteResponse:
        """
        Wrapper for note_store.setNoteApplicationDataEntry()
        Args:
    authentication_token (required): authenticationToken
            guid (required): guid
            key (required): key
            value (required): value
        Returns:
            EvernoteResponse: Standardized response with success/data/error
        """
        try:
            # Build arguments list
            args = []
            if authentication_token is not None:
                args.append(authentication_token)
            if guid is not None:
                args.append(guid)
            if key is not None:
                args.append(key)
            if value is not None:
                args.append(value)

            # Call Thrift client method
            result = self.note_store.setNoteApplicationDataEntry(*args)

            # Convert Thrift objects to dict for easier handling
            if hasattr(result, '__dict__'):
                data = self._thrift_to_dict(result)
            elif isinstance(result, list):
                data = [self._thrift_to_dict(item) if hasattr(item, '__dict__') else item for item in result]
            else:
                data = result

            return EvernoteResponse(
                success=True,
                data=data
            )
        except Exception as e:
            # Handle Thrift exceptions
            error_type = type(e).__name__
            return EvernoteResponse(
                success=False,
                error=f"{error_type}: {str(e)}"
            )

    async def unset_note_application_data_entry(
        self,
        authentication_token: str,
        guid: str,
        key: str
    ) -> EvernoteResponse:
        """
        Wrapper for note_store.unsetNoteApplicationDataEntry()
        Args:
    authentication_token (required): authenticationToken
            guid (required): guid
            key (required): key
        Returns:
            EvernoteResponse: Standardized response with success/data/error
        """
        try:
            # Build arguments list
            args = []
            if authentication_token is not None:
                args.append(authentication_token)
            if guid is not None:
                args.append(guid)
            if key is not None:
                args.append(key)

            # Call Thrift client method
            result = self.note_store.unsetNoteApplicationDataEntry(*args)

            # Convert Thrift objects to dict for easier handling
            if hasattr(result, '__dict__'):
                data = self._thrift_to_dict(result)
            elif isinstance(result, list):
                data = [self._thrift_to_dict(item) if hasattr(item, '__dict__') else item for item in result]
            else:
                data = result

            return EvernoteResponse(
                success=True,
                data=data
            )
        except Exception as e:
            # Handle Thrift exceptions
            error_type = type(e).__name__
            return EvernoteResponse(
                success=False,
                error=f"{error_type}: {str(e)}"
            )

    async def get_note_content(
        self,
        authentication_token: str,
        guid: str
    ) -> EvernoteResponse:
        """
        Wrapper for note_store.getNoteContent()
        Args:
    authentication_token (required): authenticationToken
            guid (required): guid
        Returns:
            EvernoteResponse: Standardized response with success/data/error
        """
        try:
            # Build arguments list
            args = []
            if authentication_token is not None:
                args.append(authentication_token)
            if guid is not None:
                args.append(guid)

            # Call Thrift client method
            result = self.note_store.getNoteContent(*args)

            # Convert Thrift objects to dict for easier handling
            if hasattr(result, '__dict__'):
                data = self._thrift_to_dict(result)
            elif isinstance(result, list):
                data = [self._thrift_to_dict(item) if hasattr(item, '__dict__') else item for item in result]
            else:
                data = result

            return EvernoteResponse(
                success=True,
                data=data
            )
        except Exception as e:
            # Handle Thrift exceptions
            error_type = type(e).__name__
            return EvernoteResponse(
                success=False,
                error=f"{error_type}: {str(e)}"
            )

    async def get_note_search_text(
        self,
        authentication_token: str,
        guid: str,
        note_only: str,
        tokenize_for_indexing: str
    ) -> EvernoteResponse:
        """
        Wrapper for note_store.getNoteSearchText()
        Args:
    authentication_token (required): authenticationToken
            guid (required): guid
            note_only (required): noteOnly
            tokenize_for_indexing (required): tokenizeForIndexing
        Returns:
            EvernoteResponse: Standardized response with success/data/error
        """
        try:
            # Build arguments list
            args = []
            if authentication_token is not None:
                args.append(authentication_token)
            if guid is not None:
                args.append(guid)
            if note_only is not None:
                args.append(note_only)
            if tokenize_for_indexing is not None:
                args.append(tokenize_for_indexing)

            # Call Thrift client method
            result = self.note_store.getNoteSearchText(*args)

            # Convert Thrift objects to dict for easier handling
            if hasattr(result, '__dict__'):
                data = self._thrift_to_dict(result)
            elif isinstance(result, list):
                data = [self._thrift_to_dict(item) if hasattr(item, '__dict__') else item for item in result]
            else:
                data = result

            return EvernoteResponse(
                success=True,
                data=data
            )
        except Exception as e:
            # Handle Thrift exceptions
            error_type = type(e).__name__
            return EvernoteResponse(
                success=False,
                error=f"{error_type}: {str(e)}"
            )

    async def get_resource_search_text(
        self,
        authentication_token: str,
        guid: str
    ) -> EvernoteResponse:
        """
        Wrapper for note_store.getResourceSearchText()
        Args:
    authentication_token (required): authenticationToken
            guid (required): guid
        Returns:
            EvernoteResponse: Standardized response with success/data/error
        """
        try:
            # Build arguments list
            args = []
            if authentication_token is not None:
                args.append(authentication_token)
            if guid is not None:
                args.append(guid)

            # Call Thrift client method
            result = self.note_store.getResourceSearchText(*args)

            # Convert Thrift objects to dict for easier handling
            if hasattr(result, '__dict__'):
                data = self._thrift_to_dict(result)
            elif isinstance(result, list):
                data = [self._thrift_to_dict(item) if hasattr(item, '__dict__') else item for item in result]
            else:
                data = result

            return EvernoteResponse(
                success=True,
                data=data
            )
        except Exception as e:
            # Handle Thrift exceptions
            error_type = type(e).__name__
            return EvernoteResponse(
                success=False,
                error=f"{error_type}: {str(e)}"
            )

    async def get_note_tag_names(
        self,
        authentication_token: str,
        guid: str
    ) -> EvernoteResponse:
        """
        Wrapper for note_store.getNoteTagNames()
        Args:
    authentication_token (required): authenticationToken
            guid (required): guid
        Returns:
            EvernoteResponse: Standardized response with success/data/error
        """
        try:
            # Build arguments list
            args = []
            if authentication_token is not None:
                args.append(authentication_token)
            if guid is not None:
                args.append(guid)

            # Call Thrift client method
            result = self.note_store.getNoteTagNames(*args)

            # Convert Thrift objects to dict for easier handling
            if hasattr(result, '__dict__'):
                data = self._thrift_to_dict(result)
            elif isinstance(result, list):
                data = [self._thrift_to_dict(item) if hasattr(item, '__dict__') else item for item in result]
            else:
                data = result

            return EvernoteResponse(
                success=True,
                data=data
            )
        except Exception as e:
            # Handle Thrift exceptions
            error_type = type(e).__name__
            return EvernoteResponse(
                success=False,
                error=f"{error_type}: {str(e)}"
            )

    async def create_note(
        self,
        authentication_token: str,
        note: str
    ) -> EvernoteResponse:
        """
        Wrapper for note_store.createNote()
        Args:
    authentication_token (required): authenticationToken
            note (required): note
        Returns:
            EvernoteResponse: Standardized response with success/data/error
        """
        try:
            # Build arguments list
            args = []
            if authentication_token is not None:
                args.append(authentication_token)
            if note is not None:
                args.append(note)

            # Call Thrift client method
            result = self.note_store.createNote(*args)

            # Convert Thrift objects to dict for easier handling
            if hasattr(result, '__dict__'):
                data = self._thrift_to_dict(result)
            elif isinstance(result, list):
                data = [self._thrift_to_dict(item) if hasattr(item, '__dict__') else item for item in result]
            else:
                data = result

            return EvernoteResponse(
                success=True,
                data=data
            )
        except Exception as e:
            # Handle Thrift exceptions
            error_type = type(e).__name__
            return EvernoteResponse(
                success=False,
                error=f"{error_type}: {str(e)}"
            )

    async def update_note(
        self,
        authentication_token: str,
        note: str
    ) -> EvernoteResponse:
        """
        Wrapper for note_store.updateNote()
        Args:
    authentication_token (required): authenticationToken
            note (required): note
        Returns:
            EvernoteResponse: Standardized response with success/data/error
        """
        try:
            # Build arguments list
            args = []
            if authentication_token is not None:
                args.append(authentication_token)
            if note is not None:
                args.append(note)

            # Call Thrift client method
            result = self.note_store.updateNote(*args)

            # Convert Thrift objects to dict for easier handling
            if hasattr(result, '__dict__'):
                data = self._thrift_to_dict(result)
            elif isinstance(result, list):
                data = [self._thrift_to_dict(item) if hasattr(item, '__dict__') else item for item in result]
            else:
                data = result

            return EvernoteResponse(
                success=True,
                data=data
            )
        except Exception as e:
            # Handle Thrift exceptions
            error_type = type(e).__name__
            return EvernoteResponse(
                success=False,
                error=f"{error_type}: {str(e)}"
            )

    async def delete_note(
        self,
        authentication_token: str,
        guid: str
    ) -> EvernoteResponse:
        """
        Wrapper for note_store.deleteNote()
        Args:
    authentication_token (required): authenticationToken
            guid (required): guid
        Returns:
            EvernoteResponse: Standardized response with success/data/error
        """
        try:
            # Build arguments list
            args = []
            if authentication_token is not None:
                args.append(authentication_token)
            if guid is not None:
                args.append(guid)

            # Call Thrift client method
            result = self.note_store.deleteNote(*args)

            # Convert Thrift objects to dict for easier handling
            if hasattr(result, '__dict__'):
                data = self._thrift_to_dict(result)
            elif isinstance(result, list):
                data = [self._thrift_to_dict(item) if hasattr(item, '__dict__') else item for item in result]
            else:
                data = result

            return EvernoteResponse(
                success=True,
                data=data
            )
        except Exception as e:
            # Handle Thrift exceptions
            error_type = type(e).__name__
            return EvernoteResponse(
                success=False,
                error=f"{error_type}: {str(e)}"
            )

    async def expunge_note(
        self,
        authentication_token: str,
        guid: str
    ) -> EvernoteResponse:
        """
        Wrapper for note_store.expungeNote()
        Args:
    authentication_token (required): authenticationToken
            guid (required): guid
        Returns:
            EvernoteResponse: Standardized response with success/data/error
        """
        try:
            # Build arguments list
            args = []
            if authentication_token is not None:
                args.append(authentication_token)
            if guid is not None:
                args.append(guid)

            # Call Thrift client method
            result = self.note_store.expungeNote(*args)

            # Convert Thrift objects to dict for easier handling
            if hasattr(result, '__dict__'):
                data = self._thrift_to_dict(result)
            elif isinstance(result, list):
                data = [self._thrift_to_dict(item) if hasattr(item, '__dict__') else item for item in result]
            else:
                data = result

            return EvernoteResponse(
                success=True,
                data=data
            )
        except Exception as e:
            # Handle Thrift exceptions
            error_type = type(e).__name__
            return EvernoteResponse(
                success=False,
                error=f"{error_type}: {str(e)}"
            )

    async def copy_note(
        self,
        authentication_token: str,
        note_guid: str,
        to_notebook_guid: str
    ) -> EvernoteResponse:
        """
        Wrapper for note_store.copyNote()
        Args:
    authentication_token (required): authenticationToken
            note_guid (required): noteGuid
            to_notebook_guid (required): toNotebookGuid
        Returns:
            EvernoteResponse: Standardized response with success/data/error
        """
        try:
            # Build arguments list
            args = []
            if authentication_token is not None:
                args.append(authentication_token)
            if note_guid is not None:
                args.append(note_guid)
            if to_notebook_guid is not None:
                args.append(to_notebook_guid)

            # Call Thrift client method
            result = self.note_store.copyNote(*args)

            # Convert Thrift objects to dict for easier handling
            if hasattr(result, '__dict__'):
                data = self._thrift_to_dict(result)
            elif isinstance(result, list):
                data = [self._thrift_to_dict(item) if hasattr(item, '__dict__') else item for item in result]
            else:
                data = result

            return EvernoteResponse(
                success=True,
                data=data
            )
        except Exception as e:
            # Handle Thrift exceptions
            error_type = type(e).__name__
            return EvernoteResponse(
                success=False,
                error=f"{error_type}: {str(e)}"
            )

    async def list_note_versions(
        self,
        authentication_token: str,
        note_guid: str
    ) -> EvernoteResponse:
        """
        Wrapper for note_store.listNoteVersions()
        Args:
    authentication_token (required): authenticationToken
            note_guid (required): noteGuid
        Returns:
            EvernoteResponse: Standardized response with success/data/error
        """
        try:
            # Build arguments list
            args = []
            if authentication_token is not None:
                args.append(authentication_token)
            if note_guid is not None:
                args.append(note_guid)

            # Call Thrift client method
            result = self.note_store.listNoteVersions(*args)

            # Convert Thrift objects to dict for easier handling
            if hasattr(result, '__dict__'):
                data = self._thrift_to_dict(result)
            elif isinstance(result, list):
                data = [self._thrift_to_dict(item) if hasattr(item, '__dict__') else item for item in result]
            else:
                data = result

            return EvernoteResponse(
                success=True,
                data=data
            )
        except Exception as e:
            # Handle Thrift exceptions
            error_type = type(e).__name__
            return EvernoteResponse(
                success=False,
                error=f"{error_type}: {str(e)}"
            )

    async def get_note_version(
        self,
        authentication_token: str,
        note_guid: str,
        update_sequence_num: str,
        with_resources_data: str,
        with_resources_recognition: str,
        with_resources_alternate_data: str
    ) -> EvernoteResponse:
        """
        Wrapper for note_store.getNoteVersion()
        Args:
    authentication_token (required): authenticationToken
            note_guid (required): noteGuid
            update_sequence_num (required): updateSequenceNum
            with_resources_data (required): withResourcesData
            with_resources_recognition (required): withResourcesRecognition
            with_resources_alternate_data (required): withResourcesAlternateData
        Returns:
            EvernoteResponse: Standardized response with success/data/error
        """
        try:
            # Build arguments list
            args = []
            if authentication_token is not None:
                args.append(authentication_token)
            if note_guid is not None:
                args.append(note_guid)
            if update_sequence_num is not None:
                args.append(update_sequence_num)
            if with_resources_data is not None:
                args.append(with_resources_data)
            if with_resources_recognition is not None:
                args.append(with_resources_recognition)
            if with_resources_alternate_data is not None:
                args.append(with_resources_alternate_data)

            # Call Thrift client method
            result = self.note_store.getNoteVersion(*args)

            # Convert Thrift objects to dict for easier handling
            if hasattr(result, '__dict__'):
                data = self._thrift_to_dict(result)
            elif isinstance(result, list):
                data = [self._thrift_to_dict(item) if hasattr(item, '__dict__') else item for item in result]
            else:
                data = result

            return EvernoteResponse(
                success=True,
                data=data
            )
        except Exception as e:
            # Handle Thrift exceptions
            error_type = type(e).__name__
            return EvernoteResponse(
                success=False,
                error=f"{error_type}: {str(e)}"
            )

    async def find_notes(
        self,
        authentication_token: str,
        filter: str,
        offset: str,
        max_notes: str
    ) -> EvernoteResponse:
        """
        Wrapper for note_store.findNotes()
        Args:
    authentication_token (required): authenticationToken
            filter (required): filter
            offset (required): offset
            max_notes (required): maxNotes
        Returns:
            EvernoteResponse: Standardized response with success/data/error
        """
        try:
            # Build arguments list
            args = []
            if authentication_token is not None:
                args.append(authentication_token)
            if filter is not None:
                args.append(filter)
            if offset is not None:
                args.append(offset)
            if max_notes is not None:
                args.append(max_notes)

            # Call Thrift client method
            result = self.note_store.findNotes(*args)

            # Convert Thrift objects to dict for easier handling
            if hasattr(result, '__dict__'):
                data = self._thrift_to_dict(result)
            elif isinstance(result, list):
                data = [self._thrift_to_dict(item) if hasattr(item, '__dict__') else item for item in result]
            else:
                data = result

            return EvernoteResponse(
                success=True,
                data=data
            )
        except Exception as e:
            # Handle Thrift exceptions
            error_type = type(e).__name__
            return EvernoteResponse(
                success=False,
                error=f"{error_type}: {str(e)}"
            )

    async def find_note_offset(
        self,
        authentication_token: str,
        filter: str,
        guid: str
    ) -> EvernoteResponse:
        """
        Wrapper for note_store.findNoteOffset()
        Args:
    authentication_token (required): authenticationToken
            filter (required): filter
            guid (required): guid
        Returns:
            EvernoteResponse: Standardized response with success/data/error
        """
        try:
            # Build arguments list
            args = []
            if authentication_token is not None:
                args.append(authentication_token)
            if filter is not None:
                args.append(filter)
            if guid is not None:
                args.append(guid)

            # Call Thrift client method
            result = self.note_store.findNoteOffset(*args)

            # Convert Thrift objects to dict for easier handling
            if hasattr(result, '__dict__'):
                data = self._thrift_to_dict(result)
            elif isinstance(result, list):
                data = [self._thrift_to_dict(item) if hasattr(item, '__dict__') else item for item in result]
            else:
                data = result

            return EvernoteResponse(
                success=True,
                data=data
            )
        except Exception as e:
            # Handle Thrift exceptions
            error_type = type(e).__name__
            return EvernoteResponse(
                success=False,
                error=f"{error_type}: {str(e)}"
            )

    async def find_notes_metadata(
        self,
        authentication_token: str,
        filter: str,
        offset: str,
        max_notes: str,
        result_spec: str
    ) -> EvernoteResponse:
        """
        Wrapper for note_store.findNotesMetadata()
        Args:
    authentication_token (required): authenticationToken
            filter (required): filter
            offset (required): offset
            max_notes (required): maxNotes
            result_spec (required): resultSpec
        Returns:
            EvernoteResponse: Standardized response with success/data/error
        """
        try:
            # Build arguments list
            args = []
            if authentication_token is not None:
                args.append(authentication_token)
            if filter is not None:
                args.append(filter)
            if offset is not None:
                args.append(offset)
            if max_notes is not None:
                args.append(max_notes)
            if result_spec is not None:
                args.append(result_spec)

            # Call Thrift client method
            result = self.note_store.findNotesMetadata(*args)

            # Convert Thrift objects to dict for easier handling
            if hasattr(result, '__dict__'):
                data = self._thrift_to_dict(result)
            elif isinstance(result, list):
                data = [self._thrift_to_dict(item) if hasattr(item, '__dict__') else item for item in result]
            else:
                data = result

            return EvernoteResponse(
                success=True,
                data=data
            )
        except Exception as e:
            # Handle Thrift exceptions
            error_type = type(e).__name__
            return EvernoteResponse(
                success=False,
                error=f"{error_type}: {str(e)}"
            )

    async def find_note_counts(
        self,
        authentication_token: str,
        filter: str,
        with_trash: str
    ) -> EvernoteResponse:
        """
        Wrapper for note_store.findNoteCounts()
        Args:
    authentication_token (required): authenticationToken
            filter (required): filter
            with_trash (required): withTrash
        Returns:
            EvernoteResponse: Standardized response with success/data/error
        """
        try:
            # Build arguments list
            args = []
            if authentication_token is not None:
                args.append(authentication_token)
            if filter is not None:
                args.append(filter)
            if with_trash is not None:
                args.append(with_trash)

            # Call Thrift client method
            result = self.note_store.findNoteCounts(*args)

            # Convert Thrift objects to dict for easier handling
            if hasattr(result, '__dict__'):
                data = self._thrift_to_dict(result)
            elif isinstance(result, list):
                data = [self._thrift_to_dict(item) if hasattr(item, '__dict__') else item for item in result]
            else:
                data = result

            return EvernoteResponse(
                success=True,
                data=data
            )
        except Exception as e:
            # Handle Thrift exceptions
            error_type = type(e).__name__
            return EvernoteResponse(
                success=False,
                error=f"{error_type}: {str(e)}"
            )

    async def find_related(
        self,
        authentication_token: str,
        query: str,
        result_spec: str
    ) -> EvernoteResponse:
        """
        Wrapper for note_store.findRelated()
        Args:
    authentication_token (required): authenticationToken
            query (required): query
            result_spec (required): resultSpec
        Returns:
            EvernoteResponse: Standardized response with success/data/error
        """
        try:
            # Build arguments list
            args = []
            if authentication_token is not None:
                args.append(authentication_token)
            if query is not None:
                args.append(query)
            if result_spec is not None:
                args.append(result_spec)

            # Call Thrift client method
            result = self.note_store.findRelated(*args)

            # Convert Thrift objects to dict for easier handling
            if hasattr(result, '__dict__'):
                data = self._thrift_to_dict(result)
            elif isinstance(result, list):
                data = [self._thrift_to_dict(item) if hasattr(item, '__dict__') else item for item in result]
            else:
                data = result

            return EvernoteResponse(
                success=True,
                data=data
            )
        except Exception as e:
            # Handle Thrift exceptions
            error_type = type(e).__name__
            return EvernoteResponse(
                success=False,
                error=f"{error_type}: {str(e)}"
            )

    async def list_tags(
        self,
        authentication_token: str
    ) -> EvernoteResponse:
        """
        Wrapper for note_store.listTags()
        Args:
    authentication_token (required): authenticationToken
        Returns:
            EvernoteResponse: Standardized response with success/data/error
        """
        try:
            # Build arguments list
            args = []
            if authentication_token is not None:
                args.append(authentication_token)

            # Call Thrift client method
            result = self.note_store.listTags(*args)

            # Convert Thrift objects to dict for easier handling
            if hasattr(result, '__dict__'):
                data = self._thrift_to_dict(result)
            elif isinstance(result, list):
                data = [self._thrift_to_dict(item) if hasattr(item, '__dict__') else item for item in result]
            else:
                data = result

            return EvernoteResponse(
                success=True,
                data=data
            )
        except Exception as e:
            # Handle Thrift exceptions
            error_type = type(e).__name__
            return EvernoteResponse(
                success=False,
                error=f"{error_type}: {str(e)}"
            )

    async def list_tags_by_notebook(
        self,
        authentication_token: str,
        notebook_guid: str
    ) -> EvernoteResponse:
        """
        Wrapper for note_store.listTagsByNotebook()
        Args:
    authentication_token (required): authenticationToken
            notebook_guid (required): notebookGuid
        Returns:
            EvernoteResponse: Standardized response with success/data/error
        """
        try:
            # Build arguments list
            args = []
            if authentication_token is not None:
                args.append(authentication_token)
            if notebook_guid is not None:
                args.append(notebook_guid)

            # Call Thrift client method
            result = self.note_store.listTagsByNotebook(*args)

            # Convert Thrift objects to dict for easier handling
            if hasattr(result, '__dict__'):
                data = self._thrift_to_dict(result)
            elif isinstance(result, list):
                data = [self._thrift_to_dict(item) if hasattr(item, '__dict__') else item for item in result]
            else:
                data = result

            return EvernoteResponse(
                success=True,
                data=data
            )
        except Exception as e:
            # Handle Thrift exceptions
            error_type = type(e).__name__
            return EvernoteResponse(
                success=False,
                error=f"{error_type}: {str(e)}"
            )

    async def get_tag(
        self,
        authentication_token: str,
        guid: str
    ) -> EvernoteResponse:
        """
        Wrapper for note_store.getTag()
        Args:
    authentication_token (required): authenticationToken
            guid (required): guid
        Returns:
            EvernoteResponse: Standardized response with success/data/error
        """
        try:
            # Build arguments list
            args = []
            if authentication_token is not None:
                args.append(authentication_token)
            if guid is not None:
                args.append(guid)

            # Call Thrift client method
            result = self.note_store.getTag(*args)

            # Convert Thrift objects to dict for easier handling
            if hasattr(result, '__dict__'):
                data = self._thrift_to_dict(result)
            elif isinstance(result, list):
                data = [self._thrift_to_dict(item) if hasattr(item, '__dict__') else item for item in result]
            else:
                data = result

            return EvernoteResponse(
                success=True,
                data=data
            )
        except Exception as e:
            # Handle Thrift exceptions
            error_type = type(e).__name__
            return EvernoteResponse(
                success=False,
                error=f"{error_type}: {str(e)}"
            )

    async def create_tag(
        self,
        authentication_token: str,
        tag: str
    ) -> EvernoteResponse:
        """
        Wrapper for note_store.createTag()
        Args:
    authentication_token (required): authenticationToken
            tag (required): tag
        Returns:
            EvernoteResponse: Standardized response with success/data/error
        """
        try:
            # Build arguments list
            args = []
            if authentication_token is not None:
                args.append(authentication_token)
            if tag is not None:
                args.append(tag)

            # Call Thrift client method
            result = self.note_store.createTag(*args)

            # Convert Thrift objects to dict for easier handling
            if hasattr(result, '__dict__'):
                data = self._thrift_to_dict(result)
            elif isinstance(result, list):
                data = [self._thrift_to_dict(item) if hasattr(item, '__dict__') else item for item in result]
            else:
                data = result

            return EvernoteResponse(
                success=True,
                data=data
            )
        except Exception as e:
            # Handle Thrift exceptions
            error_type = type(e).__name__
            return EvernoteResponse(
                success=False,
                error=f"{error_type}: {str(e)}"
            )

    async def update_tag(
        self,
        authentication_token: str,
        tag: str
    ) -> EvernoteResponse:
        """
        Wrapper for note_store.updateTag()
        Args:
    authentication_token (required): authenticationToken
            tag (required): tag
        Returns:
            EvernoteResponse: Standardized response with success/data/error
        """
        try:
            # Build arguments list
            args = []
            if authentication_token is not None:
                args.append(authentication_token)
            if tag is not None:
                args.append(tag)

            # Call Thrift client method
            result = self.note_store.updateTag(*args)

            # Convert Thrift objects to dict for easier handling
            if hasattr(result, '__dict__'):
                data = self._thrift_to_dict(result)
            elif isinstance(result, list):
                data = [self._thrift_to_dict(item) if hasattr(item, '__dict__') else item for item in result]
            else:
                data = result

            return EvernoteResponse(
                success=True,
                data=data
            )
        except Exception as e:
            # Handle Thrift exceptions
            error_type = type(e).__name__
            return EvernoteResponse(
                success=False,
                error=f"{error_type}: {str(e)}"
            )

    async def untag_all(
        self,
        authentication_token: str,
        guid: str
    ) -> EvernoteResponse:
        """
        Wrapper for note_store.untagAll()
        Args:
    authentication_token (required): authenticationToken
            guid (required): guid
        Returns:
            EvernoteResponse: Standardized response with success/data/error
        """
        try:
            # Build arguments list
            args = []
            if authentication_token is not None:
                args.append(authentication_token)
            if guid is not None:
                args.append(guid)

            # Call Thrift client method
            result = self.note_store.untagAll(*args)

            # Convert Thrift objects to dict for easier handling
            if hasattr(result, '__dict__'):
                data = self._thrift_to_dict(result)
            elif isinstance(result, list):
                data = [self._thrift_to_dict(item) if hasattr(item, '__dict__') else item for item in result]
            else:
                data = result

            return EvernoteResponse(
                success=True,
                data=data
            )
        except Exception as e:
            # Handle Thrift exceptions
            error_type = type(e).__name__
            return EvernoteResponse(
                success=False,
                error=f"{error_type}: {str(e)}"
            )

    async def expunge_tag(
        self,
        authentication_token: str,
        guid: str
    ) -> EvernoteResponse:
        """
        Wrapper for note_store.expungeTag()
        Args:
    authentication_token (required): authenticationToken
            guid (required): guid
        Returns:
            EvernoteResponse: Standardized response with success/data/error
        """
        try:
            # Build arguments list
            args = []
            if authentication_token is not None:
                args.append(authentication_token)
            if guid is not None:
                args.append(guid)

            # Call Thrift client method
            result = self.note_store.expungeTag(*args)

            # Convert Thrift objects to dict for easier handling
            if hasattr(result, '__dict__'):
                data = self._thrift_to_dict(result)
            elif isinstance(result, list):
                data = [self._thrift_to_dict(item) if hasattr(item, '__dict__') else item for item in result]
            else:
                data = result

            return EvernoteResponse(
                success=True,
                data=data
            )
        except Exception as e:
            # Handle Thrift exceptions
            error_type = type(e).__name__
            return EvernoteResponse(
                success=False,
                error=f"{error_type}: {str(e)}"
            )

    async def get_resource(
        self,
        authentication_token: str,
        guid: str,
        with_data: str,
        with_recognition: str,
        with_attributes: str,
        with_alternate_data: str
    ) -> EvernoteResponse:
        """
        Wrapper for note_store.getResource()
        Args:
    authentication_token (required): authenticationToken
            guid (required): guid
            with_data (required): withData
            with_recognition (required): withRecognition
            with_attributes (required): withAttributes
            with_alternate_data (required): withAlternateData
        Returns:
            EvernoteResponse: Standardized response with success/data/error
        """
        try:
            # Build arguments list
            args = []
            if authentication_token is not None:
                args.append(authentication_token)
            if guid is not None:
                args.append(guid)
            if with_data is not None:
                args.append(with_data)
            if with_recognition is not None:
                args.append(with_recognition)
            if with_attributes is not None:
                args.append(with_attributes)
            if with_alternate_data is not None:
                args.append(with_alternate_data)

            # Call Thrift client method
            result = self.note_store.getResource(*args)

            # Convert Thrift objects to dict for easier handling
            if hasattr(result, '__dict__'):
                data = self._thrift_to_dict(result)
            elif isinstance(result, list):
                data = [self._thrift_to_dict(item) if hasattr(item, '__dict__') else item for item in result]
            else:
                data = result

            return EvernoteResponse(
                success=True,
                data=data
            )
        except Exception as e:
            # Handle Thrift exceptions
            error_type = type(e).__name__
            return EvernoteResponse(
                success=False,
                error=f"{error_type}: {str(e)}"
            )

    async def get_resource_application_data(
        self,
        authentication_token: str,
        guid: str
    ) -> EvernoteResponse:
        """
        Wrapper for note_store.getResourceApplicationData()
        Args:
    authentication_token (required): authenticationToken
            guid (required): guid
        Returns:
            EvernoteResponse: Standardized response with success/data/error
        """
        try:
            # Build arguments list
            args = []
            if authentication_token is not None:
                args.append(authentication_token)
            if guid is not None:
                args.append(guid)

            # Call Thrift client method
            result = self.note_store.getResourceApplicationData(*args)

            # Convert Thrift objects to dict for easier handling
            if hasattr(result, '__dict__'):
                data = self._thrift_to_dict(result)
            elif isinstance(result, list):
                data = [self._thrift_to_dict(item) if hasattr(item, '__dict__') else item for item in result]
            else:
                data = result

            return EvernoteResponse(
                success=True,
                data=data
            )
        except Exception as e:
            # Handle Thrift exceptions
            error_type = type(e).__name__
            return EvernoteResponse(
                success=False,
                error=f"{error_type}: {str(e)}"
            )

    async def get_resource_application_data_entry(
        self,
        authentication_token: str,
        guid: str,
        key: str
    ) -> EvernoteResponse:
        """
        Wrapper for note_store.getResourceApplicationDataEntry()
        Args:
    authentication_token (required): authenticationToken
            guid (required): guid
            key (required): key
        Returns:
            EvernoteResponse: Standardized response with success/data/error
        """
        try:
            # Build arguments list
            args = []
            if authentication_token is not None:
                args.append(authentication_token)
            if guid is not None:
                args.append(guid)
            if key is not None:
                args.append(key)

            # Call Thrift client method
            result = self.note_store.getResourceApplicationDataEntry(*args)

            # Convert Thrift objects to dict for easier handling
            if hasattr(result, '__dict__'):
                data = self._thrift_to_dict(result)
            elif isinstance(result, list):
                data = [self._thrift_to_dict(item) if hasattr(item, '__dict__') else item for item in result]
            else:
                data = result

            return EvernoteResponse(
                success=True,
                data=data
            )
        except Exception as e:
            # Handle Thrift exceptions
            error_type = type(e).__name__
            return EvernoteResponse(
                success=False,
                error=f"{error_type}: {str(e)}"
            )

    async def set_resource_application_data_entry(
        self,
        authentication_token: str,
        guid: str,
        key: str,
        value: str
    ) -> EvernoteResponse:
        """
        Wrapper for note_store.setResourceApplicationDataEntry()
        Args:
    authentication_token (required): authenticationToken
            guid (required): guid
            key (required): key
            value (required): value
        Returns:
            EvernoteResponse: Standardized response with success/data/error
        """
        try:
            # Build arguments list
            args = []
            if authentication_token is not None:
                args.append(authentication_token)
            if guid is not None:
                args.append(guid)
            if key is not None:
                args.append(key)
            if value is not None:
                args.append(value)

            # Call Thrift client method
            result = self.note_store.setResourceApplicationDataEntry(*args)

            # Convert Thrift objects to dict for easier handling
            if hasattr(result, '__dict__'):
                data = self._thrift_to_dict(result)
            elif isinstance(result, list):
                data = [self._thrift_to_dict(item) if hasattr(item, '__dict__') else item for item in result]
            else:
                data = result

            return EvernoteResponse(
                success=True,
                data=data
            )
        except Exception as e:
            # Handle Thrift exceptions
            error_type = type(e).__name__
            return EvernoteResponse(
                success=False,
                error=f"{error_type}: {str(e)}"
            )

    async def unset_resource_application_data_entry(
        self,
        authentication_token: str,
        guid: str,
        key: str
    ) -> EvernoteResponse:
        """
        Wrapper for note_store.unsetResourceApplicationDataEntry()
        Args:
    authentication_token (required): authenticationToken
            guid (required): guid
            key (required): key
        Returns:
            EvernoteResponse: Standardized response with success/data/error
        """
        try:
            # Build arguments list
            args = []
            if authentication_token is not None:
                args.append(authentication_token)
            if guid is not None:
                args.append(guid)
            if key is not None:
                args.append(key)

            # Call Thrift client method
            result = self.note_store.unsetResourceApplicationDataEntry(*args)

            # Convert Thrift objects to dict for easier handling
            if hasattr(result, '__dict__'):
                data = self._thrift_to_dict(result)
            elif isinstance(result, list):
                data = [self._thrift_to_dict(item) if hasattr(item, '__dict__') else item for item in result]
            else:
                data = result

            return EvernoteResponse(
                success=True,
                data=data
            )
        except Exception as e:
            # Handle Thrift exceptions
            error_type = type(e).__name__
            return EvernoteResponse(
                success=False,
                error=f"{error_type}: {str(e)}"
            )

    async def update_resource(
        self,
        authentication_token: str,
        resource: str
    ) -> EvernoteResponse:
        """
        Wrapper for note_store.updateResource()
        Args:
    authentication_token (required): authenticationToken
            resource (required): resource
        Returns:
            EvernoteResponse: Standardized response with success/data/error
        """
        try:
            # Build arguments list
            args = []
            if authentication_token is not None:
                args.append(authentication_token)
            if resource is not None:
                args.append(resource)

            # Call Thrift client method
            result = self.note_store.updateResource(*args)

            # Convert Thrift objects to dict for easier handling
            if hasattr(result, '__dict__'):
                data = self._thrift_to_dict(result)
            elif isinstance(result, list):
                data = [self._thrift_to_dict(item) if hasattr(item, '__dict__') else item for item in result]
            else:
                data = result

            return EvernoteResponse(
                success=True,
                data=data
            )
        except Exception as e:
            # Handle Thrift exceptions
            error_type = type(e).__name__
            return EvernoteResponse(
                success=False,
                error=f"{error_type}: {str(e)}"
            )

    async def get_resource_data(
        self,
        authentication_token: str,
        guid: str
    ) -> EvernoteResponse:
        """
        Wrapper for note_store.getResourceData()
        Args:
    authentication_token (required): authenticationToken
            guid (required): guid
        Returns:
            EvernoteResponse: Standardized response with success/data/error
        """
        try:
            # Build arguments list
            args = []
            if authentication_token is not None:
                args.append(authentication_token)
            if guid is not None:
                args.append(guid)

            # Call Thrift client method
            result = self.note_store.getResourceData(*args)

            # Convert Thrift objects to dict for easier handling
            if hasattr(result, '__dict__'):
                data = self._thrift_to_dict(result)
            elif isinstance(result, list):
                data = [self._thrift_to_dict(item) if hasattr(item, '__dict__') else item for item in result]
            else:
                data = result

            return EvernoteResponse(
                success=True,
                data=data
            )
        except Exception as e:
            # Handle Thrift exceptions
            error_type = type(e).__name__
            return EvernoteResponse(
                success=False,
                error=f"{error_type}: {str(e)}"
            )

    async def get_resource_by_hash(
        self,
        authentication_token: str,
        note_guid: str,
        content_hash: str,
        with_data: str,
        with_recognition: str,
        with_alternate_data: str
    ) -> EvernoteResponse:
        """
        Wrapper for note_store.getResourceByHash()
        Args:
    authentication_token (required): authenticationToken
            note_guid (required): noteGuid
            content_hash (required): contentHash
            with_data (required): withData
            with_recognition (required): withRecognition
            with_alternate_data (required): withAlternateData
        Returns:
            EvernoteResponse: Standardized response with success/data/error
        """
        try:
            # Build arguments list
            args = []
            if authentication_token is not None:
                args.append(authentication_token)
            if note_guid is not None:
                args.append(note_guid)
            if content_hash is not None:
                args.append(content_hash)
            if with_data is not None:
                args.append(with_data)
            if with_recognition is not None:
                args.append(with_recognition)
            if with_alternate_data is not None:
                args.append(with_alternate_data)

            # Call Thrift client method
            result = self.note_store.getResourceByHash(*args)

            # Convert Thrift objects to dict for easier handling
            if hasattr(result, '__dict__'):
                data = self._thrift_to_dict(result)
            elif isinstance(result, list):
                data = [self._thrift_to_dict(item) if hasattr(item, '__dict__') else item for item in result]
            else:
                data = result

            return EvernoteResponse(
                success=True,
                data=data
            )
        except Exception as e:
            # Handle Thrift exceptions
            error_type = type(e).__name__
            return EvernoteResponse(
                success=False,
                error=f"{error_type}: {str(e)}"
            )

    async def get_resource_recognition(
        self,
        authentication_token: str,
        guid: str
    ) -> EvernoteResponse:
        """
        Wrapper for note_store.getResourceRecognition()
        Args:
    authentication_token (required): authenticationToken
            guid (required): guid
        Returns:
            EvernoteResponse: Standardized response with success/data/error
        """
        try:
            # Build arguments list
            args = []
            if authentication_token is not None:
                args.append(authentication_token)
            if guid is not None:
                args.append(guid)

            # Call Thrift client method
            result = self.note_store.getResourceRecognition(*args)

            # Convert Thrift objects to dict for easier handling
            if hasattr(result, '__dict__'):
                data = self._thrift_to_dict(result)
            elif isinstance(result, list):
                data = [self._thrift_to_dict(item) if hasattr(item, '__dict__') else item for item in result]
            else:
                data = result

            return EvernoteResponse(
                success=True,
                data=data
            )
        except Exception as e:
            # Handle Thrift exceptions
            error_type = type(e).__name__
            return EvernoteResponse(
                success=False,
                error=f"{error_type}: {str(e)}"
            )

    async def get_resource_alternate_data(
        self,
        authentication_token: str,
        guid: str
    ) -> EvernoteResponse:
        """
        Wrapper for note_store.getResourceAlternateData()
        Args:
    authentication_token (required): authenticationToken
            guid (required): guid
        Returns:
            EvernoteResponse: Standardized response with success/data/error
        """
        try:
            # Build arguments list
            args = []
            if authentication_token is not None:
                args.append(authentication_token)
            if guid is not None:
                args.append(guid)

            # Call Thrift client method
            result = self.note_store.getResourceAlternateData(*args)

            # Convert Thrift objects to dict for easier handling
            if hasattr(result, '__dict__'):
                data = self._thrift_to_dict(result)
            elif isinstance(result, list):
                data = [self._thrift_to_dict(item) if hasattr(item, '__dict__') else item for item in result]
            else:
                data = result

            return EvernoteResponse(
                success=True,
                data=data
            )
        except Exception as e:
            # Handle Thrift exceptions
            error_type = type(e).__name__
            return EvernoteResponse(
                success=False,
                error=f"{error_type}: {str(e)}"
            )

    async def get_resource_attributes(
        self,
        authentication_token: str,
        guid: str
    ) -> EvernoteResponse:
        """
        Wrapper for note_store.getResourceAttributes()
        Args:
    authentication_token (required): authenticationToken
            guid (required): guid
        Returns:
            EvernoteResponse: Standardized response with success/data/error
        """
        try:
            # Build arguments list
            args = []
            if authentication_token is not None:
                args.append(authentication_token)
            if guid is not None:
                args.append(guid)

            # Call Thrift client method
            result = self.note_store.getResourceAttributes(*args)

            # Convert Thrift objects to dict for easier handling
            if hasattr(result, '__dict__'):
                data = self._thrift_to_dict(result)
            elif isinstance(result, list):
                data = [self._thrift_to_dict(item) if hasattr(item, '__dict__') else item for item in result]
            else:
                data = result

            return EvernoteResponse(
                success=True,
                data=data
            )
        except Exception as e:
            # Handle Thrift exceptions
            error_type = type(e).__name__
            return EvernoteResponse(
                success=False,
                error=f"{error_type}: {str(e)}"
            )

    async def list_searches(
        self,
        authentication_token: str
    ) -> EvernoteResponse:
        """
        Wrapper for note_store.listSearches()
        Args:
    authentication_token (required): authenticationToken
        Returns:
            EvernoteResponse: Standardized response with success/data/error
        """
        try:
            # Build arguments list
            args = []
            if authentication_token is not None:
                args.append(authentication_token)

            # Call Thrift client method
            result = self.note_store.listSearches(*args)

            # Convert Thrift objects to dict for easier handling
            if hasattr(result, '__dict__'):
                data = self._thrift_to_dict(result)
            elif isinstance(result, list):
                data = [self._thrift_to_dict(item) if hasattr(item, '__dict__') else item for item in result]
            else:
                data = result

            return EvernoteResponse(
                success=True,
                data=data
            )
        except Exception as e:
            # Handle Thrift exceptions
            error_type = type(e).__name__
            return EvernoteResponse(
                success=False,
                error=f"{error_type}: {str(e)}"
            )

    async def get_search(
        self,
        authentication_token: str,
        guid: str
    ) -> EvernoteResponse:
        """
        Wrapper for note_store.getSearch()
        Args:
    authentication_token (required): authenticationToken
            guid (required): guid
        Returns:
            EvernoteResponse: Standardized response with success/data/error
        """
        try:
            # Build arguments list
            args = []
            if authentication_token is not None:
                args.append(authentication_token)
            if guid is not None:
                args.append(guid)

            # Call Thrift client method
            result = self.note_store.getSearch(*args)

            # Convert Thrift objects to dict for easier handling
            if hasattr(result, '__dict__'):
                data = self._thrift_to_dict(result)
            elif isinstance(result, list):
                data = [self._thrift_to_dict(item) if hasattr(item, '__dict__') else item for item in result]
            else:
                data = result

            return EvernoteResponse(
                success=True,
                data=data
            )
        except Exception as e:
            # Handle Thrift exceptions
            error_type = type(e).__name__
            return EvernoteResponse(
                success=False,
                error=f"{error_type}: {str(e)}"
            )

    async def create_search(
        self,
        authentication_token: str,
        search: str
    ) -> EvernoteResponse:
        """
        Wrapper for note_store.createSearch()
        Args:
    authentication_token (required): authenticationToken
            search (required): search
        Returns:
            EvernoteResponse: Standardized response with success/data/error
        """
        try:
            # Build arguments list
            args = []
            if authentication_token is not None:
                args.append(authentication_token)
            if search is not None:
                args.append(search)

            # Call Thrift client method
            result = self.note_store.createSearch(*args)

            # Convert Thrift objects to dict for easier handling
            if hasattr(result, '__dict__'):
                data = self._thrift_to_dict(result)
            elif isinstance(result, list):
                data = [self._thrift_to_dict(item) if hasattr(item, '__dict__') else item for item in result]
            else:
                data = result

            return EvernoteResponse(
                success=True,
                data=data
            )
        except Exception as e:
            # Handle Thrift exceptions
            error_type = type(e).__name__
            return EvernoteResponse(
                success=False,
                error=f"{error_type}: {str(e)}"
            )

    async def update_search(
        self,
        authentication_token: str,
        search: str
    ) -> EvernoteResponse:
        """
        Wrapper for note_store.updateSearch()
        Args:
    authentication_token (required): authenticationToken
            search (required): search
        Returns:
            EvernoteResponse: Standardized response with success/data/error
        """
        try:
            # Build arguments list
            args = []
            if authentication_token is not None:
                args.append(authentication_token)
            if search is not None:
                args.append(search)

            # Call Thrift client method
            result = self.note_store.updateSearch(*args)

            # Convert Thrift objects to dict for easier handling
            if hasattr(result, '__dict__'):
                data = self._thrift_to_dict(result)
            elif isinstance(result, list):
                data = [self._thrift_to_dict(item) if hasattr(item, '__dict__') else item for item in result]
            else:
                data = result

            return EvernoteResponse(
                success=True,
                data=data
            )
        except Exception as e:
            # Handle Thrift exceptions
            error_type = type(e).__name__
            return EvernoteResponse(
                success=False,
                error=f"{error_type}: {str(e)}"
            )

    async def expunge_search(
        self,
        authentication_token: str,
        guid: str
    ) -> EvernoteResponse:
        """
        Wrapper for note_store.expungeSearch()
        Args:
    authentication_token (required): authenticationToken
            guid (required): guid
        Returns:
            EvernoteResponse: Standardized response with success/data/error
        """
        try:
            # Build arguments list
            args = []
            if authentication_token is not None:
                args.append(authentication_token)
            if guid is not None:
                args.append(guid)

            # Call Thrift client method
            result = self.note_store.expungeSearch(*args)

            # Convert Thrift objects to dict for easier handling
            if hasattr(result, '__dict__'):
                data = self._thrift_to_dict(result)
            elif isinstance(result, list):
                data = [self._thrift_to_dict(item) if hasattr(item, '__dict__') else item for item in result]
            else:
                data = result

            return EvernoteResponse(
                success=True,
                data=data
            )
        except Exception as e:
            # Handle Thrift exceptions
            error_type = type(e).__name__
            return EvernoteResponse(
                success=False,
                error=f"{error_type}: {str(e)}"
            )

    async def list_linked_notebooks(
        self,
        authentication_token: str
    ) -> EvernoteResponse:
        """
        Wrapper for note_store.listLinkedNotebooks()
        Args:
    authentication_token (required): authenticationToken
        Returns:
            EvernoteResponse: Standardized response with success/data/error
        """
        try:
            # Build arguments list
            args = []
            if authentication_token is not None:
                args.append(authentication_token)

            # Call Thrift client method
            result = self.note_store.listLinkedNotebooks(*args)

            # Convert Thrift objects to dict for easier handling
            if hasattr(result, '__dict__'):
                data = self._thrift_to_dict(result)
            elif isinstance(result, list):
                data = [self._thrift_to_dict(item) if hasattr(item, '__dict__') else item for item in result]
            else:
                data = result

            return EvernoteResponse(
                success=True,
                data=data
            )
        except Exception as e:
            # Handle Thrift exceptions
            error_type = type(e).__name__
            return EvernoteResponse(
                success=False,
                error=f"{error_type}: {str(e)}"
            )

    async def create_linked_notebook(
        self,
        authentication_token: str,
        linked_notebook: str
    ) -> EvernoteResponse:
        """
        Wrapper for note_store.createLinkedNotebook()
        Args:
    authentication_token (required): authenticationToken
            linked_notebook (required): linkedNotebook
        Returns:
            EvernoteResponse: Standardized response with success/data/error
        """
        try:
            # Build arguments list
            args = []
            if authentication_token is not None:
                args.append(authentication_token)
            if linked_notebook is not None:
                args.append(linked_notebook)

            # Call Thrift client method
            result = self.note_store.createLinkedNotebook(*args)

            # Convert Thrift objects to dict for easier handling
            if hasattr(result, '__dict__'):
                data = self._thrift_to_dict(result)
            elif isinstance(result, list):
                data = [self._thrift_to_dict(item) if hasattr(item, '__dict__') else item for item in result]
            else:
                data = result

            return EvernoteResponse(
                success=True,
                data=data
            )
        except Exception as e:
            # Handle Thrift exceptions
            error_type = type(e).__name__
            return EvernoteResponse(
                success=False,
                error=f"{error_type}: {str(e)}"
            )

    async def update_linked_notebook(
        self,
        authentication_token: str,
        linked_notebook: str
    ) -> EvernoteResponse:
        """
        Wrapper for note_store.updateLinkedNotebook()
        Args:
    authentication_token (required): authenticationToken
            linked_notebook (required): linkedNotebook
        Returns:
            EvernoteResponse: Standardized response with success/data/error
        """
        try:
            # Build arguments list
            args = []
            if authentication_token is not None:
                args.append(authentication_token)
            if linked_notebook is not None:
                args.append(linked_notebook)

            # Call Thrift client method
            result = self.note_store.updateLinkedNotebook(*args)

            # Convert Thrift objects to dict for easier handling
            if hasattr(result, '__dict__'):
                data = self._thrift_to_dict(result)
            elif isinstance(result, list):
                data = [self._thrift_to_dict(item) if hasattr(item, '__dict__') else item for item in result]
            else:
                data = result

            return EvernoteResponse(
                success=True,
                data=data
            )
        except Exception as e:
            # Handle Thrift exceptions
            error_type = type(e).__name__
            return EvernoteResponse(
                success=False,
                error=f"{error_type}: {str(e)}"
            )

    async def expunge_linked_notebook(
        self,
        authentication_token: str,
        guid: str
    ) -> EvernoteResponse:
        """
        Wrapper for note_store.expungeLinkedNotebook()
        Args:
    authentication_token (required): authenticationToken
            guid (required): guid
        Returns:
            EvernoteResponse: Standardized response with success/data/error
        """
        try:
            # Build arguments list
            args = []
            if authentication_token is not None:
                args.append(authentication_token)
            if guid is not None:
                args.append(guid)

            # Call Thrift client method
            result = self.note_store.expungeLinkedNotebook(*args)

            # Convert Thrift objects to dict for easier handling
            if hasattr(result, '__dict__'):
                data = self._thrift_to_dict(result)
            elif isinstance(result, list):
                data = [self._thrift_to_dict(item) if hasattr(item, '__dict__') else item for item in result]
            else:
                data = result

            return EvernoteResponse(
                success=True,
                data=data
            )
        except Exception as e:
            # Handle Thrift exceptions
            error_type = type(e).__name__
            return EvernoteResponse(
                success=False,
                error=f"{error_type}: {str(e)}"
            )

    async def authenticate_to_shared_notebook(
        self,
        share_key: str,
        authentication_token: Optional[str] = None
    ) -> EvernoteResponse:
        """
        Wrapper for note_store.authenticateToSharedNotebook()
        Args:
    share_key (required): shareKey
            authentication_token (optional): authenticationToken
        Returns:
            EvernoteResponse: Standardized response with success/data/error
        """
        try:
            # Build arguments list
            args = []
            if share_key is not None:
                args.append(share_key)
            if authentication_token is not None:
                args.append(authentication_token)

            # Call Thrift client method
            result = self.note_store.authenticateToSharedNotebook(*args)

            # Convert Thrift objects to dict for easier handling
            if hasattr(result, '__dict__'):
                data = self._thrift_to_dict(result)
            elif isinstance(result, list):
                data = [self._thrift_to_dict(item) if hasattr(item, '__dict__') else item for item in result]
            else:
                data = result

            return EvernoteResponse(
                success=True,
                data=data
            )
        except Exception as e:
            # Handle Thrift exceptions
            error_type = type(e).__name__
            return EvernoteResponse(
                success=False,
                error=f"{error_type}: {str(e)}"
            )

    async def get_shared_notebook_by_auth(
        self,
        authentication_token: str
    ) -> EvernoteResponse:
        """
        Wrapper for note_store.getSharedNotebookByAuth()
        Args:
    authentication_token (required): authenticationToken
        Returns:
            EvernoteResponse: Standardized response with success/data/error
        """
        try:
            # Build arguments list
            args = []
            if authentication_token is not None:
                args.append(authentication_token)

            # Call Thrift client method
            result = self.note_store.getSharedNotebookByAuth(*args)

            # Convert Thrift objects to dict for easier handling
            if hasattr(result, '__dict__'):
                data = self._thrift_to_dict(result)
            elif isinstance(result, list):
                data = [self._thrift_to_dict(item) if hasattr(item, '__dict__') else item for item in result]
            else:
                data = result

            return EvernoteResponse(
                success=True,
                data=data
            )
        except Exception as e:
            # Handle Thrift exceptions
            error_type = type(e).__name__
            return EvernoteResponse(
                success=False,
                error=f"{error_type}: {str(e)}"
            )

    async def authenticate_to_shared_note(
        self,
        guid: str,
        note_key: str,
        authentication_token: Optional[str] = None
    ) -> EvernoteResponse:
        """
        Wrapper for note_store.authenticateToSharedNote()
        Args:
    guid (required): guid
            note_key (required): noteKey
            authentication_token (optional): authenticationToken
        Returns:
            EvernoteResponse: Standardized response with success/data/error
        """
        try:
            # Build arguments list
            args = []
            if guid is not None:
                args.append(guid)
            if note_key is not None:
                args.append(note_key)
            if authentication_token is not None:
                args.append(authentication_token)

            # Call Thrift client method
            result = self.note_store.authenticateToSharedNote(*args)

            # Convert Thrift objects to dict for easier handling
            if hasattr(result, '__dict__'):
                data = self._thrift_to_dict(result)
            elif isinstance(result, list):
                data = [self._thrift_to_dict(item) if hasattr(item, '__dict__') else item for item in result]
            else:
                data = result

            return EvernoteResponse(
                success=True,
                data=data
            )
        except Exception as e:
            # Handle Thrift exceptions
            error_type = type(e).__name__
            return EvernoteResponse(
                success=False,
                error=f"{error_type}: {str(e)}"
            )

    async def share_note(
        self,
        authentication_token: str,
        guid: str
    ) -> EvernoteResponse:
        """
        Wrapper for note_store.shareNote()
        Args:
    authentication_token (required): authenticationToken
            guid (required): guid
        Returns:
            EvernoteResponse: Standardized response with success/data/error
        """
        try:
            # Build arguments list
            args = []
            if authentication_token is not None:
                args.append(authentication_token)
            if guid is not None:
                args.append(guid)

            # Call Thrift client method
            result = self.note_store.shareNote(*args)

            # Convert Thrift objects to dict for easier handling
            if hasattr(result, '__dict__'):
                data = self._thrift_to_dict(result)
            elif isinstance(result, list):
                data = [self._thrift_to_dict(item) if hasattr(item, '__dict__') else item for item in result]
            else:
                data = result

            return EvernoteResponse(
                success=True,
                data=data
            )
        except Exception as e:
            # Handle Thrift exceptions
            error_type = type(e).__name__
            return EvernoteResponse(
                success=False,
                error=f"{error_type}: {str(e)}"
            )

    async def stop_sharing_note(
        self,
        authentication_token: str,
        guid: str
    ) -> EvernoteResponse:
        """
        Wrapper for note_store.stopSharingNote()
        Args:
    authentication_token (required): authenticationToken
            guid (required): guid
        Returns:
            EvernoteResponse: Standardized response with success/data/error
        """
        try:
            # Build arguments list
            args = []
            if authentication_token is not None:
                args.append(authentication_token)
            if guid is not None:
                args.append(guid)

            # Call Thrift client method
            result = self.note_store.stopSharingNote(*args)

            # Convert Thrift objects to dict for easier handling
            if hasattr(result, '__dict__'):
                data = self._thrift_to_dict(result)
            elif isinstance(result, list):
                data = [self._thrift_to_dict(item) if hasattr(item, '__dict__') else item for item in result]
            else:
                data = result

            return EvernoteResponse(
                success=True,
                data=data
            )
        except Exception as e:
            # Handle Thrift exceptions
            error_type = type(e).__name__
            return EvernoteResponse(
                success=False,
                error=f"{error_type}: {str(e)}"
            )

    async def list_shared_notebooks(
        self,
        authentication_token: str
    ) -> EvernoteResponse:
        """
        Wrapper for note_store.listSharedNotebooks()
        Args:
    authentication_token (required): authenticationToken
        Returns:
            EvernoteResponse: Standardized response with success/data/error
        """
        try:
            # Build arguments list
            args = []
            if authentication_token is not None:
                args.append(authentication_token)

            # Call Thrift client method
            result = self.note_store.listSharedNotebooks(*args)

            # Convert Thrift objects to dict for easier handling
            if hasattr(result, '__dict__'):
                data = self._thrift_to_dict(result)
            elif isinstance(result, list):
                data = [self._thrift_to_dict(item) if hasattr(item, '__dict__') else item for item in result]
            else:
                data = result

            return EvernoteResponse(
                success=True,
                data=data
            )
        except Exception as e:
            # Handle Thrift exceptions
            error_type = type(e).__name__
            return EvernoteResponse(
                success=False,
                error=f"{error_type}: {str(e)}"
            )

    async def share_notebook(
        self,
        authentication_token: str,
        shared_notebook: str,
        message: Optional[str] = None
    ) -> EvernoteResponse:
        """
        Wrapper for note_store.shareNotebook()
        Args:
    authentication_token (required): authenticationToken
            shared_notebook (required): sharedNotebook
            message (optional): message
        Returns:
            EvernoteResponse: Standardized response with success/data/error
        """
        try:
            # Build arguments list
            args = []
            if authentication_token is not None:
                args.append(authentication_token)
            if shared_notebook is not None:
                args.append(shared_notebook)
            if message is not None:
                args.append(message)

            # Call Thrift client method
            result = self.note_store.shareNotebook(*args)

            # Convert Thrift objects to dict for easier handling
            if hasattr(result, '__dict__'):
                data = self._thrift_to_dict(result)
            elif isinstance(result, list):
                data = [self._thrift_to_dict(item) if hasattr(item, '__dict__') else item for item in result]
            else:
                data = result

            return EvernoteResponse(
                success=True,
                data=data
            )
        except Exception as e:
            # Handle Thrift exceptions
            error_type = type(e).__name__
            return EvernoteResponse(
                success=False,
                error=f"{error_type}: {str(e)}"
            )

    async def create_or_update_notebook_shares(
        self,
        authentication_token: str,
        share_template: str
    ) -> EvernoteResponse:
        """
        Wrapper for note_store.createOrUpdateNotebookShares()
        Args:
    authentication_token (required): authenticationToken
            share_template (required): shareTemplate
        Returns:
            EvernoteResponse: Standardized response with success/data/error
        """
        try:
            # Build arguments list
            args = []
            if authentication_token is not None:
                args.append(authentication_token)
            if share_template is not None:
                args.append(share_template)

            # Call Thrift client method
            result = self.note_store.createOrUpdateNotebookShares(*args)

            # Convert Thrift objects to dict for easier handling
            if hasattr(result, '__dict__'):
                data = self._thrift_to_dict(result)
            elif isinstance(result, list):
                data = [self._thrift_to_dict(item) if hasattr(item, '__dict__') else item for item in result]
            else:
                data = result

            return EvernoteResponse(
                success=True,
                data=data
            )
        except Exception as e:
            # Handle Thrift exceptions
            error_type = type(e).__name__
            return EvernoteResponse(
                success=False,
                error=f"{error_type}: {str(e)}"
            )

    async def update_shared_notebook(
        self,
        authentication_token: str,
        shared_notebook: str
    ) -> EvernoteResponse:
        """
        Wrapper for note_store.updateSharedNotebook()
        Args:
    authentication_token (required): authenticationToken
            shared_notebook (required): sharedNotebook
        Returns:
            EvernoteResponse: Standardized response with success/data/error
        """
        try:
            # Build arguments list
            args = []
            if authentication_token is not None:
                args.append(authentication_token)
            if shared_notebook is not None:
                args.append(shared_notebook)

            # Call Thrift client method
            result = self.note_store.updateSharedNotebook(*args)

            # Convert Thrift objects to dict for easier handling
            if hasattr(result, '__dict__'):
                data = self._thrift_to_dict(result)
            elif isinstance(result, list):
                data = [self._thrift_to_dict(item) if hasattr(item, '__dict__') else item for item in result]
            else:
                data = result

            return EvernoteResponse(
                success=True,
                data=data
            )
        except Exception as e:
            # Handle Thrift exceptions
            error_type = type(e).__name__
            return EvernoteResponse(
                success=False,
                error=f"{error_type}: {str(e)}"
            )

    async def set_notebook_recipient_settings(
        self,
        authentication_token: str,
        notebook_guid: str,
        recipient_settings: str
    ) -> EvernoteResponse:
        """
        Wrapper for note_store.setNotebookRecipientSettings()
        Args:
    authentication_token (required): authenticationToken
            notebook_guid (required): notebookGuid
            recipient_settings (required): recipientSettings
        Returns:
            EvernoteResponse: Standardized response with success/data/error
        """
        try:
            # Build arguments list
            args = []
            if authentication_token is not None:
                args.append(authentication_token)
            if notebook_guid is not None:
                args.append(notebook_guid)
            if recipient_settings is not None:
                args.append(recipient_settings)

            # Call Thrift client method
            result = self.note_store.setNotebookRecipientSettings(*args)

            # Convert Thrift objects to dict for easier handling
            if hasattr(result, '__dict__'):
                data = self._thrift_to_dict(result)
            elif isinstance(result, list):
                data = [self._thrift_to_dict(item) if hasattr(item, '__dict__') else item for item in result]
            else:
                data = result

            return EvernoteResponse(
                success=True,
                data=data
            )
        except Exception as e:
            # Handle Thrift exceptions
            error_type = type(e).__name__
            return EvernoteResponse(
                success=False,
                error=f"{error_type}: {str(e)}"
            )

    async def get_notebook_shares(
        self,
        authentication_token: str,
        notebook_guid: str
    ) -> EvernoteResponse:
        """
        Wrapper for note_store.getNotebookShares()
        Args:
    authentication_token (required): authenticationToken
            notebook_guid (required): notebookGuid
        Returns:
            EvernoteResponse: Standardized response with success/data/error
        """
        try:
            # Build arguments list
            args = []
            if authentication_token is not None:
                args.append(authentication_token)
            if notebook_guid is not None:
                args.append(notebook_guid)

            # Call Thrift client method
            result = self.note_store.getNotebookShares(*args)

            # Convert Thrift objects to dict for easier handling
            if hasattr(result, '__dict__'):
                data = self._thrift_to_dict(result)
            elif isinstance(result, list):
                data = [self._thrift_to_dict(item) if hasattr(item, '__dict__') else item for item in result]
            else:
                data = result

            return EvernoteResponse(
                success=True,
                data=data
            )
        except Exception as e:
            # Handle Thrift exceptions
            error_type = type(e).__name__
            return EvernoteResponse(
                success=False,
                error=f"{error_type}: {str(e)}"
            )

    async def manage_notebook_shares(
        self,
        authentication_token: str,
        parameters: str
    ) -> EvernoteResponse:
        """
        Wrapper for note_store.manageNotebookShares()
        Args:
    authentication_token (required): authenticationToken
            parameters (required): parameters
        Returns:
            EvernoteResponse: Standardized response with success/data/error
        """
        try:
            # Build arguments list
            args = []
            if authentication_token is not None:
                args.append(authentication_token)
            if parameters is not None:
                args.append(parameters)

            # Call Thrift client method
            result = self.note_store.manageNotebookShares(*args)

            # Convert Thrift objects to dict for easier handling
            if hasattr(result, '__dict__'):
                data = self._thrift_to_dict(result)
            elif isinstance(result, list):
                data = [self._thrift_to_dict(item) if hasattr(item, '__dict__') else item for item in result]
            else:
                data = result

            return EvernoteResponse(
                success=True,
                data=data
            )
        except Exception as e:
            # Handle Thrift exceptions
            error_type = type(e).__name__
            return EvernoteResponse(
                success=False,
                error=f"{error_type}: {str(e)}"
            )

    async def manage_note_shares(
        self,
        authentication_token: str,
        parameters: str
    ) -> EvernoteResponse:
        """
        Wrapper for note_store.manageNoteShares()
        Args:
    authentication_token (required): authenticationToken
            parameters (required): parameters
        Returns:
            EvernoteResponse: Standardized response with success/data/error
        """
        try:
            # Build arguments list
            args = []
            if authentication_token is not None:
                args.append(authentication_token)
            if parameters is not None:
                args.append(parameters)

            # Call Thrift client method
            result = self.note_store.manageNoteShares(*args)

            # Convert Thrift objects to dict for easier handling
            if hasattr(result, '__dict__'):
                data = self._thrift_to_dict(result)
            elif isinstance(result, list):
                data = [self._thrift_to_dict(item) if hasattr(item, '__dict__') else item for item in result]
            else:
                data = result

            return EvernoteResponse(
                success=True,
                data=data
            )
        except Exception as e:
            # Handle Thrift exceptions
            error_type = type(e).__name__
            return EvernoteResponse(
                success=False,
                error=f"{error_type}: {str(e)}"
            )

    async def get_public_notebook(
        self,
        user_id: str,
        public_uri: str
    ) -> EvernoteResponse:
        """
        Wrapper for note_store.getPublicNotebook()
        Args:
    user_id (required): userId
            public_uri (required): publicUri
        Returns:
            EvernoteResponse: Standardized response with success/data/error
        """
        try:
            # Build arguments list
            args = []
            if user_id is not None:
                args.append(user_id)
            if public_uri is not None:
                args.append(public_uri)

            # Call Thrift client method
            result = self.note_store.getPublicNotebook(*args)

            # Convert Thrift objects to dict for easier handling
            if hasattr(result, '__dict__'):
                data = self._thrift_to_dict(result)
            elif isinstance(result, list):
                data = [self._thrift_to_dict(item) if hasattr(item, '__dict__') else item for item in result]
            else:
                data = result

            return EvernoteResponse(
                success=True,
                data=data
            )
        except Exception as e:
            # Handle Thrift exceptions
            error_type = type(e).__name__
            return EvernoteResponse(
                success=False,
                error=f"{error_type}: {str(e)}"
            )

    async def email_note(
        self,
        authentication_token: str,
        parameters: str
    ) -> EvernoteResponse:
        """
        Wrapper for note_store.emailNote()
        Args:
    authentication_token (required): authenticationToken
            parameters (required): parameters
        Returns:
            EvernoteResponse: Standardized response with success/data/error
        """
        try:
            # Build arguments list
            args = []
            if authentication_token is not None:
                args.append(authentication_token)
            if parameters is not None:
                args.append(parameters)

            # Call Thrift client method
            result = self.note_store.emailNote(*args)

            # Convert Thrift objects to dict for easier handling
            if hasattr(result, '__dict__'):
                data = self._thrift_to_dict(result)
            elif isinstance(result, list):
                data = [self._thrift_to_dict(item) if hasattr(item, '__dict__') else item for item in result]
            else:
                data = result

            return EvernoteResponse(
                success=True,
                data=data
            )
        except Exception as e:
            # Handle Thrift exceptions
            error_type = type(e).__name__
            return EvernoteResponse(
                success=False,
                error=f"{error_type}: {str(e)}"
            )

    async def update_note_if_usn_matches(
        self,
        authentication_token: str,
        note: str
    ) -> EvernoteResponse:
        """
        Wrapper for note_store.updateNoteIfUsnMatches()
        Args:
    authentication_token (required): authenticationToken
            note (required): note
        Returns:
            EvernoteResponse: Standardized response with success/data/error
        """
        try:
            # Build arguments list
            args = []
            if authentication_token is not None:
                args.append(authentication_token)
            if note is not None:
                args.append(note)

            # Call Thrift client method
            result = self.note_store.updateNoteIfUsnMatches(*args)

            # Convert Thrift objects to dict for easier handling
            if hasattr(result, '__dict__'):
                data = self._thrift_to_dict(result)
            elif isinstance(result, list):
                data = [self._thrift_to_dict(item) if hasattr(item, '__dict__') else item for item in result]
            else:
                data = result

            return EvernoteResponse(
                success=True,
                data=data
            )
        except Exception as e:
            # Handle Thrift exceptions
            error_type = type(e).__name__
            return EvernoteResponse(
                success=False,
                error=f"{error_type}: {str(e)}"
            )

    async def check_version(
        self,
        client_name: str,
        edam_version_major: Optional[str] = None,
        edam_version_minor: Optional[str] = None
    ) -> EvernoteResponse:
        """
        Wrapper for user_store.checkVersion()
        Args:
    client_name (required): clientName
            edam_version_major (optional): edamVersionMajor
            edam_version_minor (optional): edamVersionMinor
        Returns:
            EvernoteResponse: Standardized response with success/data/error
        """
        try:
            # Build arguments list
            args = []
            if client_name is not None:
                args.append(client_name)
            if edam_version_major is not None:
                args.append(edam_version_major)
            if edam_version_minor is not None:
                args.append(edam_version_minor)

            # Call Thrift client method
            result = self.user_store.checkVersion(*args)

            # Convert Thrift objects to dict for easier handling
            if hasattr(result, '__dict__'):
                data = self._thrift_to_dict(result)
            elif isinstance(result, list):
                data = [self._thrift_to_dict(item) if hasattr(item, '__dict__') else item for item in result]
            else:
                data = result

            return EvernoteResponse(
                success=True,
                data=data
            )
        except Exception as e:
            # Handle Thrift exceptions
            error_type = type(e).__name__
            return EvernoteResponse(
                success=False,
                error=f"{error_type}: {str(e)}"
            )

    async def get_bootstrap_info(
        self,
        locale: str
    ) -> EvernoteResponse:
        """
        Wrapper for user_store.getBootstrapInfo()
        Args:
    locale (required): locale
        Returns:
            EvernoteResponse: Standardized response with success/data/error
        """
        try:
            # Build arguments list
            args = []
            if locale is not None:
                args.append(locale)

            # Call Thrift client method
            result = self.user_store.getBootstrapInfo(*args)

            # Convert Thrift objects to dict for easier handling
            if hasattr(result, '__dict__'):
                data = self._thrift_to_dict(result)
            elif isinstance(result, list):
                data = [self._thrift_to_dict(item) if hasattr(item, '__dict__') else item for item in result]
            else:
                data = result

            return EvernoteResponse(
                success=True,
                data=data
            )
        except Exception as e:
            # Handle Thrift exceptions
            error_type = type(e).__name__
            return EvernoteResponse(
                success=False,
                error=f"{error_type}: {str(e)}"
            )

    async def authenticate_long_session(
        self,
        username: str,
        password: str,
        consumer_key: str,
        consumer_secret: str,
        device_description: str,
        supports_two_factor: str,
        device_identifier: Optional[str] = None
    ) -> EvernoteResponse:
        """
        Wrapper for user_store.authenticateLongSession()
        Args:
    username (required): username
            password (required): password
            consumer_key (required): consumerKey
            consumer_secret (required): consumerSecret
            device_identifier (optional): deviceIdentifier
            device_description (required): deviceDescription
            supports_two_factor (required): supportsTwoFactor
        Returns:
            EvernoteResponse: Standardized response with success/data/error
        """
        try:
            # Build arguments list
            args = []
            if username is not None:
                args.append(username)
            if password is not None:
                args.append(password)
            if consumer_key is not None:
                args.append(consumer_key)
            if consumer_secret is not None:
                args.append(consumer_secret)
            if device_identifier is not None:
                args.append(device_identifier)
            if device_description is not None:
                args.append(device_description)
            if supports_two_factor is not None:
                args.append(supports_two_factor)

            # Call Thrift client method
            result = self.user_store.authenticateLongSession(*args)

            # Convert Thrift objects to dict for easier handling
            if hasattr(result, '__dict__'):
                data = self._thrift_to_dict(result)
            elif isinstance(result, list):
                data = [self._thrift_to_dict(item) if hasattr(item, '__dict__') else item for item in result]
            else:
                data = result

            return EvernoteResponse(
                success=True,
                data=data
            )
        except Exception as e:
            # Handle Thrift exceptions
            error_type = type(e).__name__
            return EvernoteResponse(
                success=False,
                error=f"{error_type}: {str(e)}"
            )

    async def complete_two_factor_authentication(
        self,
        authentication_token: str,
        one_time_code: str,
        device_description: str,
        device_identifier: Optional[str] = None
    ) -> EvernoteResponse:
        """
        Wrapper for user_store.completeTwoFactorAuthentication()
        Args:
    authentication_token (required): authenticationToken
            one_time_code (required): oneTimeCode
            device_identifier (optional): deviceIdentifier
            device_description (required): deviceDescription
        Returns:
            EvernoteResponse: Standardized response with success/data/error
        """
        try:
            # Build arguments list
            args = []
            if authentication_token is not None:
                args.append(authentication_token)
            if one_time_code is not None:
                args.append(one_time_code)
            if device_identifier is not None:
                args.append(device_identifier)
            if device_description is not None:
                args.append(device_description)

            # Call Thrift client method
            result = self.user_store.completeTwoFactorAuthentication(*args)

            # Convert Thrift objects to dict for easier handling
            if hasattr(result, '__dict__'):
                data = self._thrift_to_dict(result)
            elif isinstance(result, list):
                data = [self._thrift_to_dict(item) if hasattr(item, '__dict__') else item for item in result]
            else:
                data = result

            return EvernoteResponse(
                success=True,
                data=data
            )
        except Exception as e:
            # Handle Thrift exceptions
            error_type = type(e).__name__
            return EvernoteResponse(
                success=False,
                error=f"{error_type}: {str(e)}"
            )

    async def revoke_long_session(
        self,
        authentication_token: str
    ) -> EvernoteResponse:
        """
        Wrapper for user_store.revokeLongSession()
        Args:
    authentication_token (required): authenticationToken
        Returns:
            EvernoteResponse: Standardized response with success/data/error
        """
        try:
            # Build arguments list
            args = []
            if authentication_token is not None:
                args.append(authentication_token)

            # Call Thrift client method
            result = self.user_store.revokeLongSession(*args)

            # Convert Thrift objects to dict for easier handling
            if hasattr(result, '__dict__'):
                data = self._thrift_to_dict(result)
            elif isinstance(result, list):
                data = [self._thrift_to_dict(item) if hasattr(item, '__dict__') else item for item in result]
            else:
                data = result

            return EvernoteResponse(
                success=True,
                data=data
            )
        except Exception as e:
            # Handle Thrift exceptions
            error_type = type(e).__name__
            return EvernoteResponse(
                success=False,
                error=f"{error_type}: {str(e)}"
            )

    async def authenticate_to_business(
        self,
        authentication_token: str
    ) -> EvernoteResponse:
        """
        Wrapper for user_store.authenticateToBusiness()
        Args:
    authentication_token (required): authenticationToken
        Returns:
            EvernoteResponse: Standardized response with success/data/error
        """
        try:
            # Build arguments list
            args = []
            if authentication_token is not None:
                args.append(authentication_token)

            # Call Thrift client method
            result = self.user_store.authenticateToBusiness(*args)

            # Convert Thrift objects to dict for easier handling
            if hasattr(result, '__dict__'):
                data = self._thrift_to_dict(result)
            elif isinstance(result, list):
                data = [self._thrift_to_dict(item) if hasattr(item, '__dict__') else item for item in result]
            else:
                data = result

            return EvernoteResponse(
                success=True,
                data=data
            )
        except Exception as e:
            # Handle Thrift exceptions
            error_type = type(e).__name__
            return EvernoteResponse(
                success=False,
                error=f"{error_type}: {str(e)}"
            )

    async def get_user(
        self,
        authentication_token: str
    ) -> EvernoteResponse:
        """
        Wrapper for user_store.getUser()
        Args:
    authentication_token (required): authenticationToken
        Returns:
            EvernoteResponse: Standardized response with success/data/error
        """
        try:
            # Build arguments list
            args = []
            if authentication_token is not None:
                args.append(authentication_token)

            # Call Thrift client method
            result = self.user_store.getUser(*args)

            # Convert Thrift objects to dict for easier handling
            if hasattr(result, '__dict__'):
                data = self._thrift_to_dict(result)
            elif isinstance(result, list):
                data = [self._thrift_to_dict(item) if hasattr(item, '__dict__') else item for item in result]
            else:
                data = result

            return EvernoteResponse(
                success=True,
                data=data
            )
        except Exception as e:
            # Handle Thrift exceptions
            error_type = type(e).__name__
            return EvernoteResponse(
                success=False,
                error=f"{error_type}: {str(e)}"
            )

    async def get_public_user_info(
        self,
        username: str
    ) -> EvernoteResponse:
        """
        Wrapper for user_store.getPublicUserInfo()
        Args:
    username (required): username
        Returns:
            EvernoteResponse: Standardized response with success/data/error
        """
        try:
            # Build arguments list
            args = []
            if username is not None:
                args.append(username)

            # Call Thrift client method
            result = self.user_store.getPublicUserInfo(*args)

            # Convert Thrift objects to dict for easier handling
            if hasattr(result, '__dict__'):
                data = self._thrift_to_dict(result)
            elif isinstance(result, list):
                data = [self._thrift_to_dict(item) if hasattr(item, '__dict__') else item for item in result]
            else:
                data = result

            return EvernoteResponse(
                success=True,
                data=data
            )
        except Exception as e:
            # Handle Thrift exceptions
            error_type = type(e).__name__
            return EvernoteResponse(
                success=False,
                error=f"{error_type}: {str(e)}"
            )

    async def get_premium_info(
        self,
        authentication_token: str
    ) -> EvernoteResponse:
        """
        Wrapper for user_store.getPremiumInfo()
        Args:
    authentication_token (required): authenticationToken
        Returns:
            EvernoteResponse: Standardized response with success/data/error
        """
        try:
            # Build arguments list
            args = []
            if authentication_token is not None:
                args.append(authentication_token)

            # Call Thrift client method
            result = self.user_store.getPremiumInfo(*args)

            # Convert Thrift objects to dict for easier handling
            if hasattr(result, '__dict__'):
                data = self._thrift_to_dict(result)
            elif isinstance(result, list):
                data = [self._thrift_to_dict(item) if hasattr(item, '__dict__') else item for item in result]
            else:
                data = result

            return EvernoteResponse(
                success=True,
                data=data
            )
        except Exception as e:
            # Handle Thrift exceptions
            error_type = type(e).__name__
            return EvernoteResponse(
                success=False,
                error=f"{error_type}: {str(e)}"
            )

    async def get_user_urls(
        self,
        authentication_token: str
    ) -> EvernoteResponse:
        """
        Wrapper for user_store.getUserUrls()
        Args:
    authentication_token (required): authenticationToken
        Returns:
            EvernoteResponse: Standardized response with success/data/error
        """
        try:
            # Build arguments list
            args = []
            if authentication_token is not None:
                args.append(authentication_token)

            # Call Thrift client method
            result = self.user_store.getUserUrls(*args)

            # Convert Thrift objects to dict for easier handling
            if hasattr(result, '__dict__'):
                data = self._thrift_to_dict(result)
            elif isinstance(result, list):
                data = [self._thrift_to_dict(item) if hasattr(item, '__dict__') else item for item in result]
            else:
                data = result

            return EvernoteResponse(
                success=True,
                data=data
            )
        except Exception as e:
            # Handle Thrift exceptions
            error_type = type(e).__name__
            return EvernoteResponse(
                success=False,
                error=f"{error_type}: {str(e)}"
            )

    async def invite_to_business(
        self,
        authentication_token: str,
        email_address: str
    ) -> EvernoteResponse:
        """
        Wrapper for user_store.inviteToBusiness()
        Args:
    authentication_token (required): authenticationToken
            email_address (required): emailAddress
        Returns:
            EvernoteResponse: Standardized response with success/data/error
        """
        try:
            # Build arguments list
            args = []
            if authentication_token is not None:
                args.append(authentication_token)
            if email_address is not None:
                args.append(email_address)

            # Call Thrift client method
            result = self.user_store.inviteToBusiness(*args)

            # Convert Thrift objects to dict for easier handling
            if hasattr(result, '__dict__'):
                data = self._thrift_to_dict(result)
            elif isinstance(result, list):
                data = [self._thrift_to_dict(item) if hasattr(item, '__dict__') else item for item in result]
            else:
                data = result

            return EvernoteResponse(
                success=True,
                data=data
            )
        except Exception as e:
            # Handle Thrift exceptions
            error_type = type(e).__name__
            return EvernoteResponse(
                success=False,
                error=f"{error_type}: {str(e)}"
            )

    async def remove_from_business(
        self,
        authentication_token: str,
        email_address: str
    ) -> EvernoteResponse:
        """
        Wrapper for user_store.removeFromBusiness()
        Args:
    authentication_token (required): authenticationToken
            email_address (required): emailAddress
        Returns:
            EvernoteResponse: Standardized response with success/data/error
        """
        try:
            # Build arguments list
            args = []
            if authentication_token is not None:
                args.append(authentication_token)
            if email_address is not None:
                args.append(email_address)

            # Call Thrift client method
            result = self.user_store.removeFromBusiness(*args)

            # Convert Thrift objects to dict for easier handling
            if hasattr(result, '__dict__'):
                data = self._thrift_to_dict(result)
            elif isinstance(result, list):
                data = [self._thrift_to_dict(item) if hasattr(item, '__dict__') else item for item in result]
            else:
                data = result

            return EvernoteResponse(
                success=True,
                data=data
            )
        except Exception as e:
            # Handle Thrift exceptions
            error_type = type(e).__name__
            return EvernoteResponse(
                success=False,
                error=f"{error_type}: {str(e)}"
            )

    async def update_business_user_identifier(
        self,
        authentication_token: str,
        old_email_address: str,
        new_email_address: str
    ) -> EvernoteResponse:
        """
        Wrapper for user_store.updateBusinessUserIdentifier()
        Args:
    authentication_token (required): authenticationToken
            old_email_address (required): oldEmailAddress
            new_email_address (required): newEmailAddress
        Returns:
            EvernoteResponse: Standardized response with success/data/error
        """
        try:
            # Build arguments list
            args = []
            if authentication_token is not None:
                args.append(authentication_token)
            if old_email_address is not None:
                args.append(old_email_address)
            if new_email_address is not None:
                args.append(new_email_address)

            # Call Thrift client method
            result = self.user_store.updateBusinessUserIdentifier(*args)

            # Convert Thrift objects to dict for easier handling
            if hasattr(result, '__dict__'):
                data = self._thrift_to_dict(result)
            elif isinstance(result, list):
                data = [self._thrift_to_dict(item) if hasattr(item, '__dict__') else item for item in result]
            else:
                data = result

            return EvernoteResponse(
                success=True,
                data=data
            )
        except Exception as e:
            # Handle Thrift exceptions
            error_type = type(e).__name__
            return EvernoteResponse(
                success=False,
                error=f"{error_type}: {str(e)}"
            )

    async def list_business_users(
        self,
        authentication_token: str
    ) -> EvernoteResponse:
        """
        Wrapper for user_store.listBusinessUsers()
        Args:
    authentication_token (required): authenticationToken
        Returns:
            EvernoteResponse: Standardized response with success/data/error
        """
        try:
            # Build arguments list
            args = []
            if authentication_token is not None:
                args.append(authentication_token)

            # Call Thrift client method
            result = self.user_store.listBusinessUsers(*args)

            # Convert Thrift objects to dict for easier handling
            if hasattr(result, '__dict__'):
                data = self._thrift_to_dict(result)
            elif isinstance(result, list):
                data = [self._thrift_to_dict(item) if hasattr(item, '__dict__') else item for item in result]
            else:
                data = result

            return EvernoteResponse(
                success=True,
                data=data
            )
        except Exception as e:
            # Handle Thrift exceptions
            error_type = type(e).__name__
            return EvernoteResponse(
                success=False,
                error=f"{error_type}: {str(e)}"
            )

    async def list_business_invitations(
        self,
        authentication_token: str,
        include_requested_invitations: str
    ) -> EvernoteResponse:
        """
        Wrapper for user_store.listBusinessInvitations()
        Args:
    authentication_token (required): authenticationToken
            include_requested_invitations (required): includeRequestedInvitations
        Returns:
            EvernoteResponse: Standardized response with success/data/error
        """
        try:
            # Build arguments list
            args = []
            if authentication_token is not None:
                args.append(authentication_token)
            if include_requested_invitations is not None:
                args.append(include_requested_invitations)

            # Call Thrift client method
            result = self.user_store.listBusinessInvitations(*args)

            # Convert Thrift objects to dict for easier handling
            if hasattr(result, '__dict__'):
                data = self._thrift_to_dict(result)
            elif isinstance(result, list):
                data = [self._thrift_to_dict(item) if hasattr(item, '__dict__') else item for item in result]
            else:
                data = result

            return EvernoteResponse(
                success=True,
                data=data
            )
        except Exception as e:
            # Handle Thrift exceptions
            error_type = type(e).__name__
            return EvernoteResponse(
                success=False,
                error=f"{error_type}: {str(e)}"
            )

    async def get_account_limits(
        self,
        service_level: str
    ) -> EvernoteResponse:
        """
        Wrapper for user_store.getAccountLimits()
        Args:
    service_level (required): serviceLevel
        Returns:
            EvernoteResponse: Standardized response with success/data/error
        """
        try:
            # Build arguments list
            args = []
            if service_level is not None:
                args.append(service_level)

            # Call Thrift client method
            result = self.user_store.getAccountLimits(*args)

            # Convert Thrift objects to dict for easier handling
            if hasattr(result, '__dict__'):
                data = self._thrift_to_dict(result)
            elif isinstance(result, list):
                data = [self._thrift_to_dict(item) if hasattr(item, '__dict__') else item for item in result]
            else:
                data = result

            return EvernoteResponse(
                success=True,
                data=data
            )
        except Exception as e:
            # Handle Thrift exceptions
            error_type = type(e).__name__
            return EvernoteResponse(
                success=False,
                error=f"{error_type}: {str(e)}"
            )
