import asyncio
from typing import BinaryIO, Dict, List, Optional, Union

from box_sdk_gen import BoxClient  # type: ignore
from box_sdk_gen.managers.files import (  # type: ignore
    CopyFileParent,
    GetFileThumbnailByIdExtension,
    UpdateFileByIdParent,
)
from box_sdk_gen.managers.folders import (  # type: ignore
    CreateFolderParent,
    UpdateFolderByIdParent,
)
from box_sdk_gen.managers.uploads import (  # type: ignore
    PreflightFileUploadCheckParent,
    UploadFileAttributes,
)

from app.sources.client.box.box import BoxClient as CustomBoxClient
from app.sources.client.box.box import BoxResponse


class BoxDataSource:
    """
    Complete Box API client wrapper using official Box SDK Gen
    Auto-generated wrapper for Box SDK Gen methods.
    This class provides unified access to all Box SDK manager methods while
    maintaining the official SDK structure and behavior.
    COMPLETE BOX API COVERAGE:
    Core APIs: Files, Folders, Users, Groups
    Collaboration: Sharing, permissions, collaborations
    Content: Comments, tasks, webhooks, web links
    Enterprise: Retention policies, legal holds, classifications
    Advanced: Shield information barriers, sign requests, workflows
    Metadata: Templates, cascade policies, custom fields
    Events: Enterprise events, user events
    Integration: Mappings, terms of service
    Search & Discovery: Advanced content search with filters
    Uploads: File uploads, chunked uploads, versions
    Coverage:
    - Total SDK methods: 133
    - Auto-discovered from official Box Python SDK Gen
    - All Box API managers and endpoints included
    - Enterprise and business features supported
    - Complete file lifecycle management
    - Advanced collaboration and sharing
    - Comprehensive metadata and classification
    - Enterprise governance and compliance
    - Real-time events and webhooks
    - Sign requests and workflow automation
    """

    def __init__(self, boxClient: CustomBoxClient) -> None:
        """
        Initialize the Box SDK wrapper.
        Args:
            boxClient (BoxClient): Box client instance
        """
        self._box_client = boxClient
        self._client = None

    def _get_client(self) -> BoxClient:
        """Get or create Box client."""
        if self._client is None:
            self._client = self._box_client.get_client().create_client()
        return self._client

    async def files_get_file_by_id(self, file_id: str, **kwargs) -> BoxResponse:
        """Get file information by ID

        API Endpoint: files.get_file_by_id
        Namespace: files

        Args:
            file_id (str, required): The ID of the file

        Returns:
            BoxResponse: SDK response
        """
        client = self._get_client()
        manager = getattr(client, 'files', None)
        if manager is None:
            return BoxResponse(success=False, error="Manager 'files' not found")

        try:
            loop = asyncio.get_running_loop()
            # Add kwargs to parameters if provided
            if kwargs:
                # Handle additional parameters from kwargs
                pass
            response = await loop.run_in_executor(None, lambda: manager.get_file_by_id(file_id))
            return BoxResponse(success=True, data=response)
        except Exception as e:
            return BoxResponse(success=False, error=str(e))

    async def files_delete_file_by_id(self, file_id: str, **kwargs) -> BoxResponse:
        """Delete a file by ID

        API Endpoint: files.delete_file_by_id
        Namespace: files

        Args:
            file_id (str, required): The ID of the file

        Returns:
            BoxResponse: SDK response
        """
        client = self._get_client()
        manager = getattr(client, 'files', None)
        if manager is None:
            return BoxResponse(success=False, error="Manager 'files' not found")

        try:
            loop = asyncio.get_running_loop()
            # Add kwargs to parameters if provided
            if kwargs:
                # Handle additional parameters from kwargs
                pass
            response = await loop.run_in_executor(None, lambda: manager.delete_file_by_id(file_id))
            return BoxResponse(success=True, data=response)
        except Exception as e:
            return BoxResponse(success=False, error=str(e))

    async def files_update_file_by_id(self, file_id: str, name: Optional[str] = None, parent: Optional[UpdateFileByIdParent] = None, **kwargs) -> BoxResponse:
        """Update file information

        API Endpoint: files.update_file_by_id
        Namespace: files

        Args:
            file_id (str, required): The ID of the file
            name (str, optional): The new name for the file
            parent (UpdateFileByIdParent, optional): The parent folder

        Returns:
            BoxResponse: SDK response
        """
        client = self._get_client()
        manager = getattr(client, 'files', None)
        if manager is None:
            return BoxResponse(success=False, error="Manager 'files' not found")

        try:
            loop = asyncio.get_running_loop()
            # Add kwargs to parameters if provided
            if kwargs:
                # Handle additional parameters from kwargs
                pass
            response = await loop.run_in_executor(None, lambda: manager.update_file_by_id(file_id, name=name, parent=parent))
            return BoxResponse(success=True, data=response)
        except Exception as e:
            return BoxResponse(success=False, error=str(e))

    async def files_copy_file(self, file_id: str, parent: CopyFileParent, **kwargs) -> BoxResponse:
        """Copy a file to a new location

        API Endpoint: files.copy_file
        Namespace: files

        Args:
            file_id (str, required): The ID of the file
            parent (CopyFileParent, required): The destination parent folder

        Returns:
            BoxResponse: SDK response
        """
        client = self._get_client()
        manager = getattr(client, 'files', None)
        if manager is None:
            return BoxResponse(success=False, error="Manager 'files' not found")

        try:
            loop = asyncio.get_running_loop()
            # Add kwargs to parameters if provided
            if kwargs:
                # Handle additional parameters from kwargs
                pass
            response = await loop.run_in_executor(None, lambda: manager.copy_file(file_id, parent))
            return BoxResponse(success=True, data=response)
        except Exception as e:
            return BoxResponse(success=False, error=str(e))

    async def files_get_file_thumbnail_by_id(self, file_id: str, extension: Optional[GetFileThumbnailByIdExtension] = None, **kwargs) -> BoxResponse:
        """Get thumbnail for a file

        API Endpoint: files.get_file_thumbnail_by_id
        Namespace: files

        Args:
            file_id (str, required): The ID of the file
            extension (GetFileThumbnailByIdExtension, optional): The file format

        Returns:
            BoxResponse: SDK response
        """
        client = self._get_client()
        manager = getattr(client, 'files', None)
        if manager is None:
            return BoxResponse(success=False, error="Manager 'files' not found")

        try:
            loop = asyncio.get_running_loop()
            # Add kwargs to parameters if provided
            if kwargs:
                # Handle additional parameters from kwargs
                pass
            response = await loop.run_in_executor(None, lambda: manager.get_file_thumbnail_by_id(file_id, extension=extension))
            return BoxResponse(success=True, data=response)
        except Exception as e:
            return BoxResponse(success=False, error=str(e))

    async def files_get_file_content(self, file_id: str, **kwargs) -> BoxResponse:
        """Get file content/download file

        API Endpoint: files.get_file_content
        Namespace: files

        Args:
            file_id (str, required): The ID of the file

        Returns:
            BoxResponse: SDK response
        """
        client = self._get_client()
        manager = getattr(client, 'files', None)
        if manager is None:
            return BoxResponse(success=False, error="Manager 'files' not found")

        try:
            loop = asyncio.get_running_loop()
            # Add kwargs to parameters if provided
            if kwargs:
                # Handle additional parameters from kwargs
                pass
            response = await loop.run_in_executor(None, lambda: manager.get_file_content(file_id))
            return BoxResponse(success=True, data=response)
        except Exception as e:
            return BoxResponse(success=False, error=str(e))

    async def files_get_file_versions(self, file_id: str, **kwargs) -> BoxResponse:
        """Get all versions of a file

        API Endpoint: files.get_file_versions
        Namespace: files

        Args:
            file_id (str, required): The ID of the file

        Returns:
            BoxResponse: SDK response
        """
        client = self._get_client()
        manager = getattr(client, 'files', None)
        if manager is None:
            return BoxResponse(success=False, error="Manager 'files' not found")

        try:
            loop = asyncio.get_running_loop()
            # Add kwargs to parameters if provided
            if kwargs:
                # Handle additional parameters from kwargs
                pass
            response = await loop.run_in_executor(None, lambda: manager.get_file_versions(file_id))
            return BoxResponse(success=True, data=response)
        except Exception as e:
            return BoxResponse(success=False, error=str(e))

    async def files_promote_file_version(self, file_id: str, file_version_id: str, **kwargs) -> BoxResponse:
        """Promote a file version to current

        API Endpoint: files.promote_file_version
        Namespace: files

        Args:
            file_id (str, required): The ID of the file
            file_version_id (str, required): The ID of the file version

        Returns:
            BoxResponse: SDK response
        """
        client = self._get_client()
        manager = getattr(client, 'files', None)
        if manager is None:
            return BoxResponse(success=False, error="Manager 'files' not found")

        try:
            loop = asyncio.get_running_loop()
            # Add kwargs to parameters if provided
            if kwargs:
                # Handle additional parameters from kwargs
                pass
            response = await loop.run_in_executor(None, lambda: manager.promote_file_version(file_id, file_version_id))
            return BoxResponse(success=True, data=response)
        except Exception as e:
            return BoxResponse(success=False, error=str(e))

    async def files_restore_file_version(self, file_id: str, file_version_id: str, **kwargs) -> BoxResponse:
        """Restore a previous file version

        API Endpoint: files.restore_file_version
        Namespace: files

        Args:
            file_id (str, required): The ID of the file
            file_version_id (str, required): The ID of the file version

        Returns:
            BoxResponse: SDK response
        """
        client = self._get_client()
        manager = getattr(client, 'files', None)
        if manager is None:
            return BoxResponse(success=False, error="Manager 'files' not found")

        try:
            loop = asyncio.get_running_loop()
            # Add kwargs to parameters if provided
            if kwargs:
                # Handle additional parameters from kwargs
                pass
            response = await loop.run_in_executor(None, lambda: manager.restore_file_version(file_id, file_version_id))
            return BoxResponse(success=True, data=response)
        except Exception as e:
            return BoxResponse(success=False, error=str(e))

    async def files_delete_file_version(self, file_id: str, file_version_id: str, **kwargs) -> BoxResponse:
        """Delete a file version

        API Endpoint: files.delete_file_version
        Namespace: files

        Args:
            file_id (str, required): The ID of the file
            file_version_id (str, required): The ID of the file version

        Returns:
            BoxResponse: SDK response
        """
        client = self._get_client()
        manager = getattr(client, 'files', None)
        if manager is None:
            return BoxResponse(success=False, error="Manager 'files' not found")

        try:
            loop = asyncio.get_running_loop()
            # Add kwargs to parameters if provided
            if kwargs:
                # Handle additional parameters from kwargs
                pass
            response = await loop.run_in_executor(None, lambda: manager.delete_file_version(file_id, file_version_id))
            return BoxResponse(success=True, data=response)
        except Exception as e:
            return BoxResponse(success=False, error=str(e))

    async def folders_get_folder_by_id(self, folder_id: str, **kwargs) -> BoxResponse:
        """Get folder information by ID

        API Endpoint: folders.get_folder_by_id
        Namespace: folders

        Args:
            folder_id (str, required): The ID of the folder

        Returns:
            BoxResponse: SDK response
        """
        client = self._get_client()
        manager = getattr(client, 'folders', None)
        if manager is None:
            return BoxResponse(success=False, error="Manager 'folders' not found")

        try:
            loop = asyncio.get_running_loop()
            # Add kwargs to parameters if provided
            if kwargs:
                # Handle additional parameters from kwargs
                pass
            response = await loop.run_in_executor(None, lambda: manager.get_folder_by_id(folder_id))
            return BoxResponse(success=True, data=response)
        except Exception as e:
            return BoxResponse(success=False, error=str(e))

    async def folders_delete_folder_by_id(self, folder_id: str, **kwargs) -> BoxResponse:
        """Delete a folder by ID

        API Endpoint: folders.delete_folder_by_id
        Namespace: folders

        Args:
            folder_id (str, required): The ID of the folder

        Returns:
            BoxResponse: SDK response
        """
        client = self._get_client()
        manager = getattr(client, 'folders', None)
        if manager is None:
            return BoxResponse(success=False, error="Manager 'folders' not found")

        try:
            loop = asyncio.get_running_loop()
            # Add kwargs to parameters if provided
            if kwargs:
                # Handle additional parameters from kwargs
                pass
            response = await loop.run_in_executor(None, lambda: manager.delete_folder_by_id(folder_id))
            return BoxResponse(success=True, data=response)
        except Exception as e:
            return BoxResponse(success=False, error=str(e))

    async def folders_update_folder_by_id(self, folder_id: str, name: Optional[str] = None, parent: Optional[UpdateFolderByIdParent] = None, **kwargs) -> BoxResponse:
        """Update folder information

        API Endpoint: folders.update_folder_by_id
        Namespace: folders

        Args:
            folder_id (str, required): The ID of the folder
            name (str, optional): The new name for the folder
            parent (UpdateFolderByIdParent, optional): The parent folder

        Returns:
            BoxResponse: SDK response
        """
        client = self._get_client()
        manager = getattr(client, 'folders', None)
        if manager is None:
            return BoxResponse(success=False, error="Manager 'folders' not found")

        try:
            loop = asyncio.get_running_loop()
            # Add kwargs to parameters if provided
            if kwargs:
                # Handle additional parameters from kwargs
                pass
            response = await loop.run_in_executor(None, lambda: manager.update_folder_by_id(folder_id, name=name, parent=parent))
            return BoxResponse(success=True, data=response)
        except Exception as e:
            return BoxResponse(success=False, error=str(e))

    async def folders_create_folder(self, name: str, parent: CreateFolderParent, **kwargs) -> BoxResponse:
        """Create a new folder

        API Endpoint: folders.create_folder
        Namespace: folders

        Args:
            name (str, required): The name of the folder
            parent (CreateFolderParent, required): The parent folder

        Returns:
            BoxResponse: SDK response
        """
        client = self._get_client()
        manager = getattr(client, 'folders', None)
        if manager is None:
            return BoxResponse(success=False, error="Manager 'folders' not found")

        try:
            loop = asyncio.get_running_loop()
            # Add kwargs to parameters if provided
            if kwargs:
                # Handle additional parameters from kwargs
                pass
            response = await loop.run_in_executor(None, lambda: manager.create_folder(name, parent))
            return BoxResponse(success=True, data=response)
        except Exception as e:
            return BoxResponse(success=False, error=str(e))

    async def folders_get_folder_items(self, folder_id: str, limit: Optional[int] = None, **kwargs) -> BoxResponse:
        """Get items in a folder

        API Endpoint: folders.get_folder_items
        Namespace: folders

        Args:
            folder_id (str, required): The ID of the folder
            limit (int, optional): The maximum number of items to return

        Returns:
            BoxResponse: SDK response
        """
        client = self._get_client()
        manager = getattr(client, 'folders', None)
        if manager is None:
            return BoxResponse(success=False, error="Manager 'folders' not found")

        try:
            loop = asyncio.get_running_loop()
            # Add kwargs to parameters if provided
            if kwargs:
                # Handle additional parameters from kwargs
                pass
            response = await loop.run_in_executor(None, lambda: manager.get_folder_items(folder_id, limit=limit))
            return BoxResponse(success=True, data=response)
        except Exception as e:
            return BoxResponse(success=False, error=str(e))

    async def folders_copy_folder(self, folder_id: str, parent: CreateFolderParent, **kwargs) -> BoxResponse:
        """Copy a folder to a new location

        API Endpoint: folders.copy_folder
        Namespace: folders

        Args:
            folder_id (str, required): The ID of the folder
            parent (CreateFolderParent, required): The destination parent folder

        Returns:
            BoxResponse: SDK response
        """
        client = self._get_client()
        manager = getattr(client, 'folders', None)
        if manager is None:
            return BoxResponse(success=False, error="Manager 'folders' not found")

        try:
            loop = asyncio.get_running_loop()
            # Add kwargs to parameters if provided
            if kwargs:
                # Handle additional parameters from kwargs
                pass
            response = await loop.run_in_executor(None, lambda: manager.copy_folder(folder_id, parent))
            return BoxResponse(success=True, data=response)
        except Exception as e:
            return BoxResponse(success=False, error=str(e))

    async def users_get_user_me(self, **kwargs) -> BoxResponse:
        """Get current user information

        API Endpoint: users.get_user_me
        Namespace: users

        Returns:
            BoxResponse: SDK response
        """
        client = self._get_client()
        manager = getattr(client, 'users', None)
        if manager is None:
            return BoxResponse(success=False, error="Manager 'users' not found")

        try:
            loop = asyncio.get_running_loop()
            # Add kwargs to parameters if provided
            if kwargs:
                # Handle additional parameters from kwargs
                pass
            response = await loop.run_in_executor(None, lambda: manager.get_user_me())
            return BoxResponse(success=True, data=response)
        except Exception as e:
            return BoxResponse(success=False, error=str(e))

    async def users_get_user_by_id(self, user_id: str, **kwargs) -> BoxResponse:
        """Get user information by ID

        API Endpoint: users.get_user_by_id
        Namespace: users

        Args:
            user_id (str, required): The ID of the user

        Returns:
            BoxResponse: SDK response
        """
        client = self._get_client()
        manager = getattr(client, 'users', None)
        if manager is None:
            return BoxResponse(success=False, error="Manager 'users' not found")

        try:
            loop = asyncio.get_running_loop()
            # Add kwargs to parameters if provided
            if kwargs:
                # Handle additional parameters from kwargs
                pass
            response = await loop.run_in_executor(None, lambda: manager.get_user_by_id(user_id))
            return BoxResponse(success=True, data=response)
        except Exception as e:
            return BoxResponse(success=False, error=str(e))

    async def users_create_user(self, name: str, login: str, **kwargs) -> BoxResponse:
        """Create a new user

        API Endpoint: users.create_user
        Namespace: users

        Args:
            name (str, required): The name of the user
            login (str, required): The email login for the user

        Returns:
            BoxResponse: SDK response
        """
        client = self._get_client()
        manager = getattr(client, 'users', None)
        if manager is None:
            return BoxResponse(success=False, error="Manager 'users' not found")

        try:
            loop = asyncio.get_running_loop()
            # Add kwargs to parameters if provided
            if kwargs:
                # Handle additional parameters from kwargs
                pass
            response = await loop.run_in_executor(None, lambda: manager.create_user(name, login))
            return BoxResponse(success=True, data=response)
        except Exception as e:
            return BoxResponse(success=False, error=str(e))

    async def users_update_user_by_id(self, user_id: str, name: Optional[str] = None, **kwargs) -> BoxResponse:
        """Update user information

        API Endpoint: users.update_user_by_id
        Namespace: users

        Args:
            user_id (str, required): The ID of the user
            name (str, optional): The new name for the user

        Returns:
            BoxResponse: SDK response
        """
        client = self._get_client()
        manager = getattr(client, 'users', None)
        if manager is None:
            return BoxResponse(success=False, error="Manager 'users' not found")

        try:
            loop = asyncio.get_running_loop()
            # Add kwargs to parameters if provided
            if kwargs:
                # Handle additional parameters from kwargs
                pass
            response = await loop.run_in_executor(None, lambda: manager.update_user_by_id(user_id, name=name))
            return BoxResponse(success=True, data=response)
        except Exception as e:
            return BoxResponse(success=False, error=str(e))

    async def users_delete_user_by_id(self, user_id: str, **kwargs) -> BoxResponse:
        """Delete a user by ID

        API Endpoint: users.delete_user_by_id
        Namespace: users

        Args:
            user_id (str, required): The ID of the user

        Returns:
            BoxResponse: SDK response
        """
        client = self._get_client()
        manager = getattr(client, 'users', None)
        if manager is None:
            return BoxResponse(success=False, error="Manager 'users' not found")

        try:
            loop = asyncio.get_running_loop()
            # Add kwargs to parameters if provided
            if kwargs:
                # Handle additional parameters from kwargs
                pass
            response = await loop.run_in_executor(None, lambda: manager.delete_user_by_id(user_id))
            return BoxResponse(success=True, data=response)
        except Exception as e:
            return BoxResponse(success=False, error=str(e))

    async def users_get_users(self, limit: Optional[int] = None, offset: Optional[int] = None, **kwargs) -> BoxResponse:
        """Get all users in the enterprise

        API Endpoint: users.get_users
        Namespace: users

        Args:
            limit (int, optional): The maximum number of users to return
            offset (int, optional): The offset for pagination

        Returns:
            BoxResponse: SDK response
        """
        client = self._get_client()
        manager = getattr(client, 'users', None)
        if manager is None:
            return BoxResponse(success=False, error="Manager 'users' not found")

        try:
            loop = asyncio.get_running_loop()
            # Add kwargs to parameters if provided
            if kwargs:
                # Handle additional parameters from kwargs
                pass
            response = await loop.run_in_executor(None, lambda: manager.get_users(limit=limit, offset=offset))
            return BoxResponse(success=True, data=response)
        except Exception as e:
            return BoxResponse(success=False, error=str(e))

    async def groups_get_groups(self, **kwargs) -> BoxResponse:
        """Get all groups

        API Endpoint: groups.get_groups
        Namespace: groups

        Returns:
            BoxResponse: SDK response
        """
        client = self._get_client()
        manager = getattr(client, 'groups', None)
        if manager is None:
            return BoxResponse(success=False, error="Manager 'groups' not found")

        try:
            loop = asyncio.get_running_loop()
            # Add kwargs to parameters if provided
            if kwargs:
                # Handle additional parameters from kwargs
                pass
            response = await loop.run_in_executor(None, lambda: manager.get_groups())
            return BoxResponse(success=True, data=response)
        except Exception as e:
            return BoxResponse(success=False, error=str(e))

    async def groups_get_group_by_id(self, group_id: str, **kwargs) -> BoxResponse:
        """Get group information by ID

        API Endpoint: groups.get_group_by_id
        Namespace: groups

        Args:
            group_id (str, required): The ID of the group

        Returns:
            BoxResponse: SDK response
        """
        client = self._get_client()
        manager = getattr(client, 'groups', None)
        if manager is None:
            return BoxResponse(success=False, error="Manager 'groups' not found")

        try:
            loop = asyncio.get_running_loop()
            # Add kwargs to parameters if provided
            if kwargs:
                # Handle additional parameters from kwargs
                pass
            response = await loop.run_in_executor(None, lambda: manager.get_group_by_id(group_id))
            return BoxResponse(success=True, data=response)
        except Exception as e:
            return BoxResponse(success=False, error=str(e))

    async def groups_create_group(self, name: str, **kwargs) -> BoxResponse:
        """Create a new group

        API Endpoint: groups.create_group
        Namespace: groups

        Args:
            name (str, required): The name of the group

        Returns:
            BoxResponse: SDK response
        """
        client = self._get_client()
        manager = getattr(client, 'groups', None)
        if manager is None:
            return BoxResponse(success=False, error="Manager 'groups' not found")

        try:
            loop = asyncio.get_running_loop()
            # Add kwargs to parameters if provided
            if kwargs:
                # Handle additional parameters from kwargs
                pass
            response = await loop.run_in_executor(None, lambda: manager.create_group(name))
            return BoxResponse(success=True, data=response)
        except Exception as e:
            return BoxResponse(success=False, error=str(e))

    async def groups_update_group_by_id(self, group_id: str, name: Optional[str] = None, **kwargs) -> BoxResponse:
        """Update group information

        API Endpoint: groups.update_group_by_id
        Namespace: groups

        Args:
            group_id (str, required): The ID of the group
            name (str, optional): The new name for the group

        Returns:
            BoxResponse: SDK response
        """
        client = self._get_client()
        manager = getattr(client, 'groups', None)
        if manager is None:
            return BoxResponse(success=False, error="Manager 'groups' not found")

        try:
            loop = asyncio.get_running_loop()
            # Add kwargs to parameters if provided
            if kwargs:
                # Handle additional parameters from kwargs
                pass
            response = await loop.run_in_executor(None, lambda: manager.update_group_by_id(group_id, name=name))
            return BoxResponse(success=True, data=response)
        except Exception as e:
            return BoxResponse(success=False, error=str(e))

    async def groups_delete_group_by_id(self, group_id: str, **kwargs) -> BoxResponse:
        """Delete a group by ID

        API Endpoint: groups.delete_group_by_id
        Namespace: groups

        Args:
            group_id (str, required): The ID of the group

        Returns:
            BoxResponse: SDK response
        """
        client = self._get_client()
        manager = getattr(client, 'groups', None)
        if manager is None:
            return BoxResponse(success=False, error="Manager 'groups' not found")

        try:
            loop = asyncio.get_running_loop()
            # Add kwargs to parameters if provided
            if kwargs:
                # Handle additional parameters from kwargs
                pass
            response = await loop.run_in_executor(None, lambda: manager.delete_group_by_id(group_id))
            return BoxResponse(success=True, data=response)
        except Exception as e:
            return BoxResponse(success=False, error=str(e))

    async def groups_get_group_memberships(self, group_id: str, **kwargs) -> BoxResponse:
        """Get all members of a group

        API Endpoint: groups.get_group_memberships
        Namespace: groups

        Args:
            group_id (str, required): The ID of the group

        Returns:
            BoxResponse: SDK response
        """
        client = self._get_client()
        manager = getattr(client, 'groups', None)
        if manager is None:
            return BoxResponse(success=False, error="Manager 'groups' not found")

        try:
            loop = asyncio.get_running_loop()
            # Add kwargs to parameters if provided
            if kwargs:
                # Handle additional parameters from kwargs
                pass
            response = await loop.run_in_executor(None, lambda: manager.get_group_memberships(group_id))
            return BoxResponse(success=True, data=response)
        except Exception as e:
            return BoxResponse(success=False, error=str(e))

    async def groups_add_user_to_group(self, group_id: str, user_id: str, **kwargs) -> BoxResponse:
        """Add a user to a group

        API Endpoint: groups.add_user_to_group
        Namespace: groups

        Args:
            group_id (str, required): The ID of the group
            user_id (str, required): The ID of the user

        Returns:
            BoxResponse: SDK response
        """
        client = self._get_client()
        manager = getattr(client, 'groups', None)
        if manager is None:
            return BoxResponse(success=False, error="Manager 'groups' not found")

        try:
            loop = asyncio.get_running_loop()
            # Add kwargs to parameters if provided
            if kwargs:
                # Handle additional parameters from kwargs
                pass
            response = await loop.run_in_executor(None, lambda: manager.add_user_to_group(group_id, user_id))
            return BoxResponse(success=True, data=response)
        except Exception as e:
            return BoxResponse(success=False, error=str(e))

    async def groups_remove_user_from_group(self, group_id: str, user_id: str, **kwargs) -> BoxResponse:
        """Remove a user from a group

        API Endpoint: groups.remove_user_from_group
        Namespace: groups

        Args:
            group_id (str, required): The ID of the group
            user_id (str, required): The ID of the user

        Returns:
            BoxResponse: SDK response
        """
        client = self._get_client()
        manager = getattr(client, 'groups', None)
        if manager is None:
            return BoxResponse(success=False, error="Manager 'groups' not found")

        try:
            loop = asyncio.get_running_loop()
            # Add kwargs to parameters if provided
            if kwargs:
                # Handle additional parameters from kwargs
                pass
            response = await loop.run_in_executor(None, lambda: manager.remove_user_from_group(group_id, user_id))
            return BoxResponse(success=True, data=response)
        except Exception as e:
            return BoxResponse(success=False, error=str(e))

    async def collaborations_create_collaboration(self, item_id: str, item_type: str, accessible_by: str, role: str, **kwargs) -> BoxResponse:
        """Create a collaboration on a file or folder

        API Endpoint: collaborations.create_collaboration
        Namespace: collaborations

        Args:
            item_id (str, required): The ID of the file or folder
            item_type (str, required): Type: 'file' or 'folder'
            accessible_by (str, required): User or group to collaborate with
            role (str, required): Collaboration role

        Returns:
            BoxResponse: SDK response
        """
        client = self._get_client()
        manager = getattr(client, 'collaborations', None)
        if manager is None:
            return BoxResponse(success=False, error="Manager 'collaborations' not found")

        try:
            loop = asyncio.get_running_loop()
            # Add kwargs to parameters if provided
            if kwargs:
                # Handle additional parameters from kwargs
                pass
            response = await loop.run_in_executor(None, lambda: manager.create_collaboration(item_id, item_type, accessible_by, role))
            return BoxResponse(success=True, data=response)
        except Exception as e:
            return BoxResponse(success=False, error=str(e))

    async def collaborations_get_collaboration_by_id(self, collaboration_id: str, **kwargs) -> BoxResponse:
        """Get collaboration information by ID

        API Endpoint: collaborations.get_collaboration_by_id
        Namespace: collaborations

        Args:
            collaboration_id (str, required): The ID of the collaboration

        Returns:
            BoxResponse: SDK response
        """
        client = self._get_client()
        manager = getattr(client, 'collaborations', None)
        if manager is None:
            return BoxResponse(success=False, error="Manager 'collaborations' not found")

        try:
            loop = asyncio.get_running_loop()
            # Add kwargs to parameters if provided
            if kwargs:
                # Handle additional parameters from kwargs
                pass
            response = await loop.run_in_executor(None, lambda: manager.get_collaboration_by_id(collaboration_id))
            return BoxResponse(success=True, data=response)
        except Exception as e:
            return BoxResponse(success=False, error=str(e))

    async def collaborations_update_collaboration(self, collaboration_id: str, role: Optional[str] = None, **kwargs) -> BoxResponse:
        """Update a collaboration

        API Endpoint: collaborations.update_collaboration
        Namespace: collaborations

        Args:
            collaboration_id (str, required): The ID of the collaboration
            role (str, optional): New collaboration role

        Returns:
            BoxResponse: SDK response
        """
        client = self._get_client()
        manager = getattr(client, 'collaborations', None)
        if manager is None:
            return BoxResponse(success=False, error="Manager 'collaborations' not found")

        try:
            loop = asyncio.get_running_loop()
            # Add kwargs to parameters if provided
            if kwargs:
                # Handle additional parameters from kwargs
                pass
            response = await loop.run_in_executor(None, lambda: manager.update_collaboration(collaboration_id, role=role))
            return BoxResponse(success=True, data=response)
        except Exception as e:
            return BoxResponse(success=False, error=str(e))

    async def collaborations_delete_collaboration(self, collaboration_id: str, **kwargs) -> BoxResponse:
        """Delete a collaboration

        API Endpoint: collaborations.delete_collaboration
        Namespace: collaborations

        Args:
            collaboration_id (str, required): The ID of the collaboration

        Returns:
            BoxResponse: SDK response
        """
        client = self._get_client()
        manager = getattr(client, 'collaborations', None)
        if manager is None:
            return BoxResponse(success=False, error="Manager 'collaborations' not found")

        try:
            loop = asyncio.get_running_loop()
            # Add kwargs to parameters if provided
            if kwargs:
                # Handle additional parameters from kwargs
                pass
            response = await loop.run_in_executor(None, lambda: manager.delete_collaboration(collaboration_id))
            return BoxResponse(success=True, data=response)
        except Exception as e:
            return BoxResponse(success=False, error=str(e))

    async def collaborations_get_file_collaborations(self, file_id: str, **kwargs) -> BoxResponse:
        """Get all collaborations on a file

        API Endpoint: collaborations.get_file_collaborations
        Namespace: collaborations

        Args:
            file_id (str, required): The ID of the file

        Returns:
            BoxResponse: SDK response
        """
        client = self._get_client()
        manager = getattr(client, 'collaborations', None)
        if manager is None:
            return BoxResponse(success=False, error="Manager 'collaborations' not found")

        try:
            loop = asyncio.get_running_loop()
            # Add kwargs to parameters if provided
            if kwargs:
                # Handle additional parameters from kwargs
                pass
            response = await loop.run_in_executor(None, lambda: manager.get_file_collaborations(file_id))
            return BoxResponse(success=True, data=response)
        except Exception as e:
            return BoxResponse(success=False, error=str(e))

    async def collaborations_get_folder_collaborations(self, folder_id: str, **kwargs) -> BoxResponse:
        """Get all collaborations on a folder

        API Endpoint: collaborations.get_folder_collaborations
        Namespace: collaborations

        Args:
            folder_id (str, required): The ID of the folder

        Returns:
            BoxResponse: SDK response
        """
        client = self._get_client()
        manager = getattr(client, 'collaborations', None)
        if manager is None:
            return BoxResponse(success=False, error="Manager 'collaborations' not found")

        try:
            loop = asyncio.get_running_loop()
            # Add kwargs to parameters if provided
            if kwargs:
                # Handle additional parameters from kwargs
                pass
            response = await loop.run_in_executor(None, lambda: manager.get_folder_collaborations(folder_id))
            return BoxResponse(success=True, data=response)
        except Exception as e:
            return BoxResponse(success=False, error=str(e))

    async def collaborations_get_pending_collaborations(self, **kwargs) -> BoxResponse:
        """Get all pending collaborations for current user

        API Endpoint: collaborations.get_pending_collaborations
        Namespace: collaborations

        Returns:
            BoxResponse: SDK response
        """
        client = self._get_client()
        manager = getattr(client, 'collaborations', None)
        if manager is None:
            return BoxResponse(success=False, error="Manager 'collaborations' not found")

        try:
            loop = asyncio.get_running_loop()
            # Add kwargs to parameters if provided
            if kwargs:
                # Handle additional parameters from kwargs
                pass
            response = await loop.run_in_executor(None, lambda: manager.get_pending_collaborations())
            return BoxResponse(success=True, data=response)
        except Exception as e:
            return BoxResponse(success=False, error=str(e))

    async def shared_links_create_shared_link_for_file(self, file_id: str, access: Optional[str] = None, **kwargs) -> BoxResponse:
        """Create a shared link for a file

        API Endpoint: shared_links.create_shared_link_for_file
        Namespace: shared_links

        Args:
            file_id (str, required): The ID of the file
            access (str, optional): Access level for the shared link

        Returns:
            BoxResponse: SDK response
        """
        client = self._get_client()
        manager = getattr(client, 'shared_links', None)
        if manager is None:
            return BoxResponse(success=False, error="Manager 'shared_links' not found")

        try:
            loop = asyncio.get_running_loop()
            # Add kwargs to parameters if provided
            if kwargs:
                # Handle additional parameters from kwargs
                pass
            response = await loop.run_in_executor(None, lambda: manager.create_shared_link_for_file(file_id, access=access))
            return BoxResponse(success=True, data=response)
        except Exception as e:
            return BoxResponse(success=False, error=str(e))

    async def shared_links_create_shared_link_for_folder(self, folder_id: str, access: Optional[str] = None, **kwargs) -> BoxResponse:
        """Create a shared link for a folder

        API Endpoint: shared_links.create_shared_link_for_folder
        Namespace: shared_links

        Args:
            folder_id (str, required): The ID of the folder
            access (str, optional): Access level for the shared link

        Returns:
            BoxResponse: SDK response
        """
        client = self._get_client()
        manager = getattr(client, 'shared_links', None)
        if manager is None:
            return BoxResponse(success=False, error="Manager 'shared_links' not found")

        try:
            loop = asyncio.get_running_loop()
            # Add kwargs to parameters if provided
            if kwargs:
                # Handle additional parameters from kwargs
                pass
            response = await loop.run_in_executor(None, lambda: manager.create_shared_link_for_folder(folder_id, access=access))
            return BoxResponse(success=True, data=response)
        except Exception as e:
            return BoxResponse(success=False, error=str(e))

    async def shared_links_get_shared_link(self, shared_link_url: str, **kwargs) -> BoxResponse:
        """Get information about a shared link

        API Endpoint: shared_links.get_shared_link
        Namespace: shared_links

        Args:
            shared_link_url (str, required): The shared link URL

        Returns:
            BoxResponse: SDK response
        """
        client = self._get_client()
        manager = getattr(client, 'shared_links', None)
        if manager is None:
            return BoxResponse(success=False, error="Manager 'shared_links' not found")

        try:
            loop = asyncio.get_running_loop()
            # Add kwargs to parameters if provided
            if kwargs:
                # Handle additional parameters from kwargs
                pass
            response = await loop.run_in_executor(None, lambda: manager.get_shared_link(shared_link_url))
            return BoxResponse(success=True, data=response)
        except Exception as e:
            return BoxResponse(success=False, error=str(e))

    async def shared_links_remove_shared_link(self, item_id: str, item_type: str, **kwargs) -> BoxResponse:
        """Remove a shared link from an item

        API Endpoint: shared_links.remove_shared_link
        Namespace: shared_links

        Args:
            item_id (str, required): The ID of the item
            item_type (str, required): Type: 'file' or 'folder'

        Returns:
            BoxResponse: SDK response
        """
        client = self._get_client()
        manager = getattr(client, 'shared_links', None)
        if manager is None:
            return BoxResponse(success=False, error="Manager 'shared_links' not found")

        try:
            loop = asyncio.get_running_loop()
            # Add kwargs to parameters if provided
            if kwargs:
                # Handle additional parameters from kwargs
                pass
            response = await loop.run_in_executor(None, lambda: manager.remove_shared_link(item_id, item_type))
            return BoxResponse(success=True, data=response)
        except Exception as e:
            return BoxResponse(success=False, error=str(e))

    async def comments_create_comment(self, item_id: str, message: str, **kwargs) -> BoxResponse:
        """Create a comment on a file

        API Endpoint: comments.create_comment
        Namespace: comments

        Args:
            item_id (str, required): The ID of the file
            message (str, required): The comment message

        Returns:
            BoxResponse: SDK response
        """
        client = self._get_client()
        manager = getattr(client, 'comments', None)
        if manager is None:
            return BoxResponse(success=False, error="Manager 'comments' not found")

        try:
            loop = asyncio.get_running_loop()
            # Add kwargs to parameters if provided
            if kwargs:
                # Handle additional parameters from kwargs
                pass
            response = await loop.run_in_executor(None, lambda: manager.create_comment(item_id, message))
            return BoxResponse(success=True, data=response)
        except Exception as e:
            return BoxResponse(success=False, error=str(e))

    async def comments_get_comment_by_id(self, comment_id: str, **kwargs) -> BoxResponse:
        """Get comment information by ID

        API Endpoint: comments.get_comment_by_id
        Namespace: comments

        Args:
            comment_id (str, required): The ID of the comment

        Returns:
            BoxResponse: SDK response
        """
        client = self._get_client()
        manager = getattr(client, 'comments', None)
        if manager is None:
            return BoxResponse(success=False, error="Manager 'comments' not found")

        try:
            loop = asyncio.get_running_loop()
            # Add kwargs to parameters if provided
            if kwargs:
                # Handle additional parameters from kwargs
                pass
            response = await loop.run_in_executor(None, lambda: manager.get_comment_by_id(comment_id))
            return BoxResponse(success=True, data=response)
        except Exception as e:
            return BoxResponse(success=False, error=str(e))

    async def comments_update_comment(self, comment_id: str, message: str, **kwargs) -> BoxResponse:
        """Update a comment

        API Endpoint: comments.update_comment
        Namespace: comments

        Args:
            comment_id (str, required): The ID of the comment
            message (str, required): The updated comment message

        Returns:
            BoxResponse: SDK response
        """
        client = self._get_client()
        manager = getattr(client, 'comments', None)
        if manager is None:
            return BoxResponse(success=False, error="Manager 'comments' not found")

        try:
            loop = asyncio.get_running_loop()
            # Add kwargs to parameters if provided
            if kwargs:
                # Handle additional parameters from kwargs
                pass
            response = await loop.run_in_executor(None, lambda: manager.update_comment(comment_id, message))
            return BoxResponse(success=True, data=response)
        except Exception as e:
            return BoxResponse(success=False, error=str(e))

    async def comments_delete_comment(self, comment_id: str, **kwargs) -> BoxResponse:
        """Delete a comment

        API Endpoint: comments.delete_comment
        Namespace: comments

        Args:
            comment_id (str, required): The ID of the comment

        Returns:
            BoxResponse: SDK response
        """
        client = self._get_client()
        manager = getattr(client, 'comments', None)
        if manager is None:
            return BoxResponse(success=False, error="Manager 'comments' not found")

        try:
            loop = asyncio.get_running_loop()
            # Add kwargs to parameters if provided
            if kwargs:
                # Handle additional parameters from kwargs
                pass
            response = await loop.run_in_executor(None, lambda: manager.delete_comment(comment_id))
            return BoxResponse(success=True, data=response)
        except Exception as e:
            return BoxResponse(success=False, error=str(e))

    async def comments_get_file_comments(self, file_id: str, **kwargs) -> BoxResponse:
        """Get all comments on a file

        API Endpoint: comments.get_file_comments
        Namespace: comments

        Args:
            file_id (str, required): The ID of the file

        Returns:
            BoxResponse: SDK response
        """
        client = self._get_client()
        manager = getattr(client, 'comments', None)
        if manager is None:
            return BoxResponse(success=False, error="Manager 'comments' not found")

        try:
            loop = asyncio.get_running_loop()
            # Add kwargs to parameters if provided
            if kwargs:
                # Handle additional parameters from kwargs
                pass
            response = await loop.run_in_executor(None, lambda: manager.get_file_comments(file_id))
            return BoxResponse(success=True, data=response)
        except Exception as e:
            return BoxResponse(success=False, error=str(e))

    async def comments_reply_to_comment(self, comment_id: str, message: str, **kwargs) -> BoxResponse:
        """Reply to a comment

        API Endpoint: comments.reply_to_comment
        Namespace: comments

        Args:
            comment_id (str, required): The ID of the parent comment
            message (str, required): The reply message

        Returns:
            BoxResponse: SDK response
        """
        client = self._get_client()
        manager = getattr(client, 'comments', None)
        if manager is None:
            return BoxResponse(success=False, error="Manager 'comments' not found")

        try:
            loop = asyncio.get_running_loop()
            # Add kwargs to parameters if provided
            if kwargs:
                # Handle additional parameters from kwargs
                pass
            response = await loop.run_in_executor(None, lambda: manager.reply_to_comment(comment_id, message))
            return BoxResponse(success=True, data=response)
        except Exception as e:
            return BoxResponse(success=False, error=str(e))

    async def tasks_create_task(self, item_id: str, action: str, message: Optional[str] = None, **kwargs) -> BoxResponse:
        """Create a task on a file

        API Endpoint: tasks.create_task
        Namespace: tasks

        Args:
            item_id (str, required): The ID of the file
            action (str, required): The task action
            message (str, optional): Task message

        Returns:
            BoxResponse: SDK response
        """
        client = self._get_client()
        manager = getattr(client, 'tasks', None)
        if manager is None:
            return BoxResponse(success=False, error="Manager 'tasks' not found")

        try:
            loop = asyncio.get_running_loop()
            # Add kwargs to parameters if provided
            if kwargs:
                # Handle additional parameters from kwargs
                pass
            response = await loop.run_in_executor(None, lambda: manager.create_task(item_id, action, message=message))
            return BoxResponse(success=True, data=response)
        except Exception as e:
            return BoxResponse(success=False, error=str(e))

    async def tasks_get_task_by_id(self, task_id: str, **kwargs) -> BoxResponse:
        """Get task information by ID

        API Endpoint: tasks.get_task_by_id
        Namespace: tasks

        Args:
            task_id (str, required): The ID of the task

        Returns:
            BoxResponse: SDK response
        """
        client = self._get_client()
        manager = getattr(client, 'tasks', None)
        if manager is None:
            return BoxResponse(success=False, error="Manager 'tasks' not found")

        try:
            loop = asyncio.get_running_loop()
            # Add kwargs to parameters if provided
            if kwargs:
                # Handle additional parameters from kwargs
                pass
            response = await loop.run_in_executor(None, lambda: manager.get_task_by_id(task_id))
            return BoxResponse(success=True, data=response)
        except Exception as e:
            return BoxResponse(success=False, error=str(e))

    async def tasks_update_task(self, task_id: str, message: Optional[str] = None, **kwargs) -> BoxResponse:
        """Update a task

        API Endpoint: tasks.update_task
        Namespace: tasks

        Args:
            task_id (str, required): The ID of the task
            message (str, optional): Updated task message

        Returns:
            BoxResponse: SDK response
        """
        client = self._get_client()
        manager = getattr(client, 'tasks', None)
        if manager is None:
            return BoxResponse(success=False, error="Manager 'tasks' not found")

        try:
            loop = asyncio.get_running_loop()
            # Add kwargs to parameters if provided
            if kwargs:
                # Handle additional parameters from kwargs
                pass
            response = await loop.run_in_executor(None, lambda: manager.update_task(task_id, message=message))
            return BoxResponse(success=True, data=response)
        except Exception as e:
            return BoxResponse(success=False, error=str(e))

    async def tasks_delete_task(self, task_id: str, **kwargs) -> BoxResponse:
        """Delete a task

        API Endpoint: tasks.delete_task
        Namespace: tasks

        Args:
            task_id (str, required): The ID of the task

        Returns:
            BoxResponse: SDK response
        """
        client = self._get_client()
        manager = getattr(client, 'tasks', None)
        if manager is None:
            return BoxResponse(success=False, error="Manager 'tasks' not found")

        try:
            loop = asyncio.get_running_loop()
            # Add kwargs to parameters if provided
            if kwargs:
                # Handle additional parameters from kwargs
                pass
            response = await loop.run_in_executor(None, lambda: manager.delete_task(task_id))
            return BoxResponse(success=True, data=response)
        except Exception as e:
            return BoxResponse(success=False, error=str(e))

    async def tasks_get_file_tasks(self, file_id: str, **kwargs) -> BoxResponse:
        """Get all tasks on a file

        API Endpoint: tasks.get_file_tasks
        Namespace: tasks

        Args:
            file_id (str, required): The ID of the file

        Returns:
            BoxResponse: SDK response
        """
        client = self._get_client()
        manager = getattr(client, 'tasks', None)
        if manager is None:
            return BoxResponse(success=False, error="Manager 'tasks' not found")

        try:
            loop = asyncio.get_running_loop()
            # Add kwargs to parameters if provided
            if kwargs:
                # Handle additional parameters from kwargs
                pass
            response = await loop.run_in_executor(None, lambda: manager.get_file_tasks(file_id))
            return BoxResponse(success=True, data=response)
        except Exception as e:
            return BoxResponse(success=False, error=str(e))

    async def tasks_create_task_assignment(self, task_id: str, assign_to: str, **kwargs) -> BoxResponse:
        """Assign a task to a user

        API Endpoint: tasks.create_task_assignment
        Namespace: tasks

        Args:
            task_id (str, required): The ID of the task
            assign_to (str, required): User to assign task to

        Returns:
            BoxResponse: SDK response
        """
        client = self._get_client()
        manager = getattr(client, 'tasks', None)
        if manager is None:
            return BoxResponse(success=False, error="Manager 'tasks' not found")

        try:
            loop = asyncio.get_running_loop()
            # Add kwargs to parameters if provided
            if kwargs:
                # Handle additional parameters from kwargs
                pass
            response = await loop.run_in_executor(None, lambda: manager.create_task_assignment(task_id, assign_to))
            return BoxResponse(success=True, data=response)
        except Exception as e:
            return BoxResponse(success=False, error=str(e))

    async def tasks_get_task_assignments(self, task_id: str, **kwargs) -> BoxResponse:
        """Get all assignments for a task

        API Endpoint: tasks.get_task_assignments
        Namespace: tasks

        Args:
            task_id (str, required): The ID of the task

        Returns:
            BoxResponse: SDK response
        """
        client = self._get_client()
        manager = getattr(client, 'tasks', None)
        if manager is None:
            return BoxResponse(success=False, error="Manager 'tasks' not found")

        try:
            loop = asyncio.get_running_loop()
            # Add kwargs to parameters if provided
            if kwargs:
                # Handle additional parameters from kwargs
                pass
            response = await loop.run_in_executor(None, lambda: manager.get_task_assignments(task_id))
            return BoxResponse(success=True, data=response)
        except Exception as e:
            return BoxResponse(success=False, error=str(e))

    async def webhooks_create_webhook(self, target_id: str, target_type: str, address: str, triggers: List[str], **kwargs) -> BoxResponse:
        """Create a webhook

        API Endpoint: webhooks.create_webhook
        Namespace: webhooks

        Args:
            target_id (str, required): The ID of the target file or folder
            target_type (str, required): Type: 'file' or 'folder'
            address (str, required): URL for webhook notifications
            triggers (List[str], required): List of trigger events

        Returns:
            BoxResponse: SDK response
        """
        client = self._get_client()
        manager = getattr(client, 'webhooks', None)
        if manager is None:
            return BoxResponse(success=False, error="Manager 'webhooks' not found")

        try:
            loop = asyncio.get_running_loop()
            # Add kwargs to parameters if provided
            if kwargs:
                # Handle additional parameters from kwargs
                pass
            response = await loop.run_in_executor(None, lambda: manager.create_webhook(target_id, target_type, address, triggers))
            return BoxResponse(success=True, data=response)
        except Exception as e:
            return BoxResponse(success=False, error=str(e))

    async def webhooks_get_webhook_by_id(self, webhook_id: str, **kwargs) -> BoxResponse:
        """Get webhook information by ID

        API Endpoint: webhooks.get_webhook_by_id
        Namespace: webhooks

        Args:
            webhook_id (str, required): The ID of the webhook

        Returns:
            BoxResponse: SDK response
        """
        client = self._get_client()
        manager = getattr(client, 'webhooks', None)
        if manager is None:
            return BoxResponse(success=False, error="Manager 'webhooks' not found")

        try:
            loop = asyncio.get_running_loop()
            # Add kwargs to parameters if provided
            if kwargs:
                # Handle additional parameters from kwargs
                pass
            response = await loop.run_in_executor(None, lambda: manager.get_webhook_by_id(webhook_id))
            return BoxResponse(success=True, data=response)
        except Exception as e:
            return BoxResponse(success=False, error=str(e))

    async def webhooks_get_webhooks(self, **kwargs) -> BoxResponse:
        """Get all webhooks

        API Endpoint: webhooks.get_webhooks
        Namespace: webhooks

        Returns:
            BoxResponse: SDK response
        """
        client = self._get_client()
        manager = getattr(client, 'webhooks', None)
        if manager is None:
            return BoxResponse(success=False, error="Manager 'webhooks' not found")

        try:
            loop = asyncio.get_running_loop()
            # Add kwargs to parameters if provided
            if kwargs:
                # Handle additional parameters from kwargs
                pass
            response = await loop.run_in_executor(None, lambda: manager.get_webhooks())
            return BoxResponse(success=True, data=response)
        except Exception as e:
            return BoxResponse(success=False, error=str(e))

    async def webhooks_update_webhook(self, webhook_id: str, address: Optional[str] = None, **kwargs) -> BoxResponse:
        """Update a webhook

        API Endpoint: webhooks.update_webhook
        Namespace: webhooks

        Args:
            webhook_id (str, required): The ID of the webhook
            address (str, optional): Updated webhook URL

        Returns:
            BoxResponse: SDK response
        """
        client = self._get_client()
        manager = getattr(client, 'webhooks', None)
        if manager is None:
            return BoxResponse(success=False, error="Manager 'webhooks' not found")

        try:
            loop = asyncio.get_running_loop()
            # Add kwargs to parameters if provided
            if kwargs:
                # Handle additional parameters from kwargs
                pass
            response = await loop.run_in_executor(None, lambda: manager.update_webhook(webhook_id, address=address))
            return BoxResponse(success=True, data=response)
        except Exception as e:
            return BoxResponse(success=False, error=str(e))

    async def webhooks_delete_webhook(self, webhook_id: str, **kwargs) -> BoxResponse:
        """Delete a webhook

        API Endpoint: webhooks.delete_webhook
        Namespace: webhooks

        Args:
            webhook_id (str, required): The ID of the webhook

        Returns:
            BoxResponse: SDK response
        """
        client = self._get_client()
        manager = getattr(client, 'webhooks', None)
        if manager is None:
            return BoxResponse(success=False, error="Manager 'webhooks' not found")

        try:
            loop = asyncio.get_running_loop()
            # Add kwargs to parameters if provided
            if kwargs:
                # Handle additional parameters from kwargs
                pass
            response = await loop.run_in_executor(None, lambda: manager.delete_webhook(webhook_id))
            return BoxResponse(success=True, data=response)
        except Exception as e:
            return BoxResponse(success=False, error=str(e))

    async def web_links_create_web_link(self, url: str, parent: CreateFolderParent, name: Optional[str] = None, **kwargs) -> BoxResponse:
        """Create a web link

        API Endpoint: web_links.create_web_link
        Namespace: web_links

        Args:
            url (str, required): The URL for the web link
            parent (CreateFolderParent, required): The parent folder
            name (str, optional): Name for the web link

        Returns:
            BoxResponse: SDK response
        """
        client = self._get_client()
        manager = getattr(client, 'web_links', None)
        if manager is None:
            return BoxResponse(success=False, error="Manager 'web_links' not found")

        try:
            loop = asyncio.get_running_loop()
            # Add kwargs to parameters if provided
            if kwargs:
                # Handle additional parameters from kwargs
                pass
            response = await loop.run_in_executor(None, lambda: manager.create_web_link(url, parent, name=name))
            return BoxResponse(success=True, data=response)
        except Exception as e:
            return BoxResponse(success=False, error=str(e))

    async def web_links_get_web_link_by_id(self, web_link_id: str, **kwargs) -> BoxResponse:
        """Get web link information by ID

        API Endpoint: web_links.get_web_link_by_id
        Namespace: web_links

        Args:
            web_link_id (str, required): The ID of the web link

        Returns:
            BoxResponse: SDK response
        """
        client = self._get_client()
        manager = getattr(client, 'web_links', None)
        if manager is None:
            return BoxResponse(success=False, error="Manager 'web_links' not found")

        try:
            loop = asyncio.get_running_loop()
            # Add kwargs to parameters if provided
            if kwargs:
                # Handle additional parameters from kwargs
                pass
            response = await loop.run_in_executor(None, lambda: manager.get_web_link_by_id(web_link_id))
            return BoxResponse(success=True, data=response)
        except Exception as e:
            return BoxResponse(success=False, error=str(e))

    async def web_links_update_web_link(self, web_link_id: str, name: Optional[str] = None, **kwargs) -> BoxResponse:
        """Update a web link

        API Endpoint: web_links.update_web_link
        Namespace: web_links

        Args:
            web_link_id (str, required): The ID of the web link
            name (str, optional): Updated name

        Returns:
            BoxResponse: SDK response
        """
        client = self._get_client()
        manager = getattr(client, 'web_links', None)
        if manager is None:
            return BoxResponse(success=False, error="Manager 'web_links' not found")

        try:
            loop = asyncio.get_running_loop()
            # Add kwargs to parameters if provided
            if kwargs:
                # Handle additional parameters from kwargs
                pass
            response = await loop.run_in_executor(None, lambda: manager.update_web_link(web_link_id, name=name))
            return BoxResponse(success=True, data=response)
        except Exception as e:
            return BoxResponse(success=False, error=str(e))

    async def web_links_delete_web_link(self, web_link_id: str, **kwargs) -> BoxResponse:
        """Delete a web link

        API Endpoint: web_links.delete_web_link
        Namespace: web_links

        Args:
            web_link_id (str, required): The ID of the web link

        Returns:
            BoxResponse: SDK response
        """
        client = self._get_client()
        manager = getattr(client, 'web_links', None)
        if manager is None:
            return BoxResponse(success=False, error="Manager 'web_links' not found")

        try:
            loop = asyncio.get_running_loop()
            # Add kwargs to parameters if provided
            if kwargs:
                # Handle additional parameters from kwargs
                pass
            response = await loop.run_in_executor(None, lambda: manager.delete_web_link(web_link_id))
            return BoxResponse(success=True, data=response)
        except Exception as e:
            return BoxResponse(success=False, error=str(e))

    async def retention_policies_create_retention_policy(self, policy_name: str, policy_type: str, retention_length: int, **kwargs) -> BoxResponse:
        """Create a retention policy

        API Endpoint: retention_policies.create_retention_policy
        Namespace: retention_policies

        Args:
            policy_name (str, required): Name for the retention policy
            policy_type (str, required): Type of retention policy
            retention_length (int, required): Retention length in days

        Returns:
            BoxResponse: SDK response
        """
        client = self._get_client()
        manager = getattr(client, 'retention_policies', None)
        if manager is None:
            return BoxResponse(success=False, error="Manager 'retention_policies' not found")

        try:
            loop = asyncio.get_running_loop()
            # Add kwargs to parameters if provided
            if kwargs:
                # Handle additional parameters from kwargs
                pass
            response = await loop.run_in_executor(None, lambda: manager.create_retention_policy(policy_name, policy_type, retention_length))
            return BoxResponse(success=True, data=response)
        except Exception as e:
            return BoxResponse(success=False, error=str(e))

    async def retention_policies_get_retention_policies(self, **kwargs) -> BoxResponse:
        """Get all retention policies

        API Endpoint: retention_policies.get_retention_policies
        Namespace: retention_policies

        Returns:
            BoxResponse: SDK response
        """
        client = self._get_client()
        manager = getattr(client, 'retention_policies', None)
        if manager is None:
            return BoxResponse(success=False, error="Manager 'retention_policies' not found")

        try:
            loop = asyncio.get_running_loop()
            # Add kwargs to parameters if provided
            if kwargs:
                # Handle additional parameters from kwargs
                pass
            response = await loop.run_in_executor(None, lambda: manager.get_retention_policies())
            return BoxResponse(success=True, data=response)
        except Exception as e:
            return BoxResponse(success=False, error=str(e))

    async def retention_policies_get_retention_policy_by_id(self, policy_id: str, **kwargs) -> BoxResponse:
        """Get retention policy information by ID

        API Endpoint: retention_policies.get_retention_policy_by_id
        Namespace: retention_policies

        Args:
            policy_id (str, required): The ID of the retention policy

        Returns:
            BoxResponse: SDK response
        """
        client = self._get_client()
        manager = getattr(client, 'retention_policies', None)
        if manager is None:
            return BoxResponse(success=False, error="Manager 'retention_policies' not found")

        try:
            loop = asyncio.get_running_loop()
            # Add kwargs to parameters if provided
            if kwargs:
                # Handle additional parameters from kwargs
                pass
            response = await loop.run_in_executor(None, lambda: manager.get_retention_policy_by_id(policy_id))
            return BoxResponse(success=True, data=response)
        except Exception as e:
            return BoxResponse(success=False, error=str(e))

    async def retention_policies_update_retention_policy(self, policy_id: str, policy_name: Optional[str] = None, **kwargs) -> BoxResponse:
        """Update a retention policy

        API Endpoint: retention_policies.update_retention_policy
        Namespace: retention_policies

        Args:
            policy_id (str, required): The ID of the retention policy
            policy_name (str, optional): Updated policy name

        Returns:
            BoxResponse: SDK response
        """
        client = self._get_client()
        manager = getattr(client, 'retention_policies', None)
        if manager is None:
            return BoxResponse(success=False, error="Manager 'retention_policies' not found")

        try:
            loop = asyncio.get_running_loop()
            # Add kwargs to parameters if provided
            if kwargs:
                # Handle additional parameters from kwargs
                pass
            response = await loop.run_in_executor(None, lambda: manager.update_retention_policy(policy_id, policy_name=policy_name))
            return BoxResponse(success=True, data=response)
        except Exception as e:
            return BoxResponse(success=False, error=str(e))

    async def retention_policies_delete_retention_policy(self, policy_id: str, **kwargs) -> BoxResponse:
        """Delete a retention policy

        API Endpoint: retention_policies.delete_retention_policy
        Namespace: retention_policies

        Args:
            policy_id (str, required): The ID of the retention policy

        Returns:
            BoxResponse: SDK response
        """
        client = self._get_client()
        manager = getattr(client, 'retention_policies', None)
        if manager is None:
            return BoxResponse(success=False, error="Manager 'retention_policies' not found")

        try:
            loop = asyncio.get_running_loop()
            # Add kwargs to parameters if provided
            if kwargs:
                # Handle additional parameters from kwargs
                pass
            response = await loop.run_in_executor(None, lambda: manager.delete_retention_policy(policy_id))
            return BoxResponse(success=True, data=response)
        except Exception as e:
            return BoxResponse(success=False, error=str(e))

    async def retention_policies_create_retention_policy_assignment(self, policy_id: str, assign_to: str, **kwargs) -> BoxResponse:
        """Assign a retention policy to a folder or enterprise

        API Endpoint: retention_policies.create_retention_policy_assignment
        Namespace: retention_policies

        Args:
            policy_id (str, required): The ID of the retention policy
            assign_to (str, required): ID of folder/enterprise to assign to

        Returns:
            BoxResponse: SDK response
        """
        client = self._get_client()
        manager = getattr(client, 'retention_policies', None)
        if manager is None:
            return BoxResponse(success=False, error="Manager 'retention_policies' not found")

        try:
            loop = asyncio.get_running_loop()
            # Add kwargs to parameters if provided
            if kwargs:
                # Handle additional parameters from kwargs
                pass
            response = await loop.run_in_executor(None, lambda: manager.create_retention_policy_assignment(policy_id, assign_to))
            return BoxResponse(success=True, data=response)
        except Exception as e:
            return BoxResponse(success=False, error=str(e))

    async def retention_policies_get_retention_policy_assignments(self, policy_id: str, **kwargs) -> BoxResponse:
        """Get all assignments for a retention policy

        API Endpoint: retention_policies.get_retention_policy_assignments
        Namespace: retention_policies

        Args:
            policy_id (str, required): The ID of the retention policy

        Returns:
            BoxResponse: SDK response
        """
        client = self._get_client()
        manager = getattr(client, 'retention_policies', None)
        if manager is None:
            return BoxResponse(success=False, error="Manager 'retention_policies' not found")

        try:
            loop = asyncio.get_running_loop()
            # Add kwargs to parameters if provided
            if kwargs:
                # Handle additional parameters from kwargs
                pass
            response = await loop.run_in_executor(None, lambda: manager.get_retention_policy_assignments(policy_id))
            return BoxResponse(success=True, data=response)
        except Exception as e:
            return BoxResponse(success=False, error=str(e))

    async def legal_hold_policies_create_legal_hold_policy(self, policy_name: str, description: Optional[str] = None, **kwargs) -> BoxResponse:
        """Create a legal hold policy

        API Endpoint: legal_hold_policies.create_legal_hold_policy
        Namespace: legal_hold_policies

        Args:
            policy_name (str, required): Name for the legal hold policy
            description (str, optional): Description of the policy

        Returns:
            BoxResponse: SDK response
        """
        client = self._get_client()
        manager = getattr(client, 'legal_hold_policies', None)
        if manager is None:
            return BoxResponse(success=False, error="Manager 'legal_hold_policies' not found")

        try:
            loop = asyncio.get_running_loop()
            # Add kwargs to parameters if provided
            if kwargs:
                # Handle additional parameters from kwargs
                pass
            response = await loop.run_in_executor(None, lambda: manager.create_legal_hold_policy(policy_name, description=description))
            return BoxResponse(success=True, data=response)
        except Exception as e:
            return BoxResponse(success=False, error=str(e))

    async def legal_hold_policies_get_legal_hold_policies(self, **kwargs) -> BoxResponse:
        """Get all legal hold policies

        API Endpoint: legal_hold_policies.get_legal_hold_policies
        Namespace: legal_hold_policies

        Returns:
            BoxResponse: SDK response
        """
        client = self._get_client()
        manager = getattr(client, 'legal_hold_policies', None)
        if manager is None:
            return BoxResponse(success=False, error="Manager 'legal_hold_policies' not found")

        try:
            loop = asyncio.get_running_loop()
            # Add kwargs to parameters if provided
            if kwargs:
                # Handle additional parameters from kwargs
                pass
            response = await loop.run_in_executor(None, lambda: manager.get_legal_hold_policies())
            return BoxResponse(success=True, data=response)
        except Exception as e:
            return BoxResponse(success=False, error=str(e))

    async def legal_hold_policies_get_legal_hold_policy_by_id(self, policy_id: str, **kwargs) -> BoxResponse:
        """Get legal hold policy information by ID

        API Endpoint: legal_hold_policies.get_legal_hold_policy_by_id
        Namespace: legal_hold_policies

        Args:
            policy_id (str, required): The ID of the legal hold policy

        Returns:
            BoxResponse: SDK response
        """
        client = self._get_client()
        manager = getattr(client, 'legal_hold_policies', None)
        if manager is None:
            return BoxResponse(success=False, error="Manager 'legal_hold_policies' not found")

        try:
            loop = asyncio.get_running_loop()
            # Add kwargs to parameters if provided
            if kwargs:
                # Handle additional parameters from kwargs
                pass
            response = await loop.run_in_executor(None, lambda: manager.get_legal_hold_policy_by_id(policy_id))
            return BoxResponse(success=True, data=response)
        except Exception as e:
            return BoxResponse(success=False, error=str(e))

    async def legal_hold_policies_update_legal_hold_policy(self, policy_id: str, policy_name: Optional[str] = None, **kwargs) -> BoxResponse:
        """Update a legal hold policy

        API Endpoint: legal_hold_policies.update_legal_hold_policy
        Namespace: legal_hold_policies

        Args:
            policy_id (str, required): The ID of the legal hold policy
            policy_name (str, optional): Updated policy name

        Returns:
            BoxResponse: SDK response
        """
        client = self._get_client()
        manager = getattr(client, 'legal_hold_policies', None)
        if manager is None:
            return BoxResponse(success=False, error="Manager 'legal_hold_policies' not found")

        try:
            loop = asyncio.get_running_loop()
            # Add kwargs to parameters if provided
            if kwargs:
                # Handle additional parameters from kwargs
                pass
            response = await loop.run_in_executor(None, lambda: manager.update_legal_hold_policy(policy_id, policy_name=policy_name))
            return BoxResponse(success=True, data=response)
        except Exception as e:
            return BoxResponse(success=False, error=str(e))

    async def legal_hold_policies_delete_legal_hold_policy(self, policy_id: str, **kwargs) -> BoxResponse:
        """Delete a legal hold policy

        API Endpoint: legal_hold_policies.delete_legal_hold_policy
        Namespace: legal_hold_policies

        Args:
            policy_id (str, required): The ID of the legal hold policy

        Returns:
            BoxResponse: SDK response
        """
        client = self._get_client()
        manager = getattr(client, 'legal_hold_policies', None)
        if manager is None:
            return BoxResponse(success=False, error="Manager 'legal_hold_policies' not found")

        try:
            loop = asyncio.get_running_loop()
            # Add kwargs to parameters if provided
            if kwargs:
                # Handle additional parameters from kwargs
                pass
            response = await loop.run_in_executor(None, lambda: manager.delete_legal_hold_policy(policy_id))
            return BoxResponse(success=True, data=response)
        except Exception as e:
            return BoxResponse(success=False, error=str(e))

    async def legal_hold_policies_create_legal_hold_policy_assignment(self, policy_id: str, assign_to: str, **kwargs) -> BoxResponse:
        """Assign a legal hold policy to an entity

        API Endpoint: legal_hold_policies.create_legal_hold_policy_assignment
        Namespace: legal_hold_policies

        Args:
            policy_id (str, required): The ID of the legal hold policy
            assign_to (str, required): ID of user/folder/file to assign to

        Returns:
            BoxResponse: SDK response
        """
        client = self._get_client()
        manager = getattr(client, 'legal_hold_policies', None)
        if manager is None:
            return BoxResponse(success=False, error="Manager 'legal_hold_policies' not found")

        try:
            loop = asyncio.get_running_loop()
            # Add kwargs to parameters if provided
            if kwargs:
                # Handle additional parameters from kwargs
                pass
            response = await loop.run_in_executor(None, lambda: manager.create_legal_hold_policy_assignment(policy_id, assign_to))
            return BoxResponse(success=True, data=response)
        except Exception as e:
            return BoxResponse(success=False, error=str(e))

    async def classifications_get_classification_template(self, **kwargs) -> BoxResponse:
        """Get the classification metadata template

        API Endpoint: classifications.get_classification_template
        Namespace: classifications

        Returns:
            BoxResponse: SDK response
        """
        client = self._get_client()
        manager = getattr(client, 'classifications', None)
        if manager is None:
            return BoxResponse(success=False, error="Manager 'classifications' not found")

        try:
            loop = asyncio.get_running_loop()
            # Add kwargs to parameters if provided
            if kwargs:
                # Handle additional parameters from kwargs
                pass
            response = await loop.run_in_executor(None, lambda: manager.get_classification_template())
            return BoxResponse(success=True, data=response)
        except Exception as e:
            return BoxResponse(success=False, error=str(e))

    async def classifications_add_classification_to_file(self, file_id: str, classification: str, **kwargs) -> BoxResponse:
        """Add classification to a file

        API Endpoint: classifications.add_classification_to_file
        Namespace: classifications

        Args:
            file_id (str, required): The ID of the file
            classification (str, required): Classification value

        Returns:
            BoxResponse: SDK response
        """
        client = self._get_client()
        manager = getattr(client, 'classifications', None)
        if manager is None:
            return BoxResponse(success=False, error="Manager 'classifications' not found")

        try:
            loop = asyncio.get_running_loop()
            # Add kwargs to parameters if provided
            if kwargs:
                # Handle additional parameters from kwargs
                pass
            response = await loop.run_in_executor(None, lambda: manager.add_classification_to_file(file_id, classification))
            return BoxResponse(success=True, data=response)
        except Exception as e:
            return BoxResponse(success=False, error=str(e))

    async def classifications_add_classification_to_folder(self, folder_id: str, classification: str, **kwargs) -> BoxResponse:
        """Add classification to a folder

        API Endpoint: classifications.add_classification_to_folder
        Namespace: classifications

        Args:
            folder_id (str, required): The ID of the folder
            classification (str, required): Classification value

        Returns:
            BoxResponse: SDK response
        """
        client = self._get_client()
        manager = getattr(client, 'classifications', None)
        if manager is None:
            return BoxResponse(success=False, error="Manager 'classifications' not found")

        try:
            loop = asyncio.get_running_loop()
            # Add kwargs to parameters if provided
            if kwargs:
                # Handle additional parameters from kwargs
                pass
            response = await loop.run_in_executor(None, lambda: manager.add_classification_to_folder(folder_id, classification))
            return BoxResponse(success=True, data=response)
        except Exception as e:
            return BoxResponse(success=False, error=str(e))

    async def classifications_update_classification_on_file(self, file_id: str, classification: str, **kwargs) -> BoxResponse:
        """Update classification on a file

        API Endpoint: classifications.update_classification_on_file
        Namespace: classifications

        Args:
            file_id (str, required): The ID of the file
            classification (str, required): Updated classification value

        Returns:
            BoxResponse: SDK response
        """
        client = self._get_client()
        manager = getattr(client, 'classifications', None)
        if manager is None:
            return BoxResponse(success=False, error="Manager 'classifications' not found")

        try:
            loop = asyncio.get_running_loop()
            # Add kwargs to parameters if provided
            if kwargs:
                # Handle additional parameters from kwargs
                pass
            response = await loop.run_in_executor(None, lambda: manager.update_classification_on_file(file_id, classification))
            return BoxResponse(success=True, data=response)
        except Exception as e:
            return BoxResponse(success=False, error=str(e))

    async def classifications_remove_classification_from_file(self, file_id: str, **kwargs) -> BoxResponse:
        """Remove classification from a file

        API Endpoint: classifications.remove_classification_from_file
        Namespace: classifications

        Args:
            file_id (str, required): The ID of the file

        Returns:
            BoxResponse: SDK response
        """
        client = self._get_client()
        manager = getattr(client, 'classifications', None)
        if manager is None:
            return BoxResponse(success=False, error="Manager 'classifications' not found")

        try:
            loop = asyncio.get_running_loop()
            # Add kwargs to parameters if provided
            if kwargs:
                # Handle additional parameters from kwargs
                pass
            response = await loop.run_in_executor(None, lambda: manager.remove_classification_from_file(file_id))
            return BoxResponse(success=True, data=response)
        except Exception as e:
            return BoxResponse(success=False, error=str(e))

    async def shield_information_barriers_get_shield_information_barriers(self, **kwargs) -> BoxResponse:
        """Get all shield information barriers

        API Endpoint: shield_information_barriers.get_shield_information_barriers
        Namespace: shield_information_barriers

        Returns:
            BoxResponse: SDK response
        """
        client = self._get_client()
        manager = getattr(client, 'shield_information_barriers', None)
        if manager is None:
            return BoxResponse(success=False, error="Manager 'shield_information_barriers' not found")

        try:
            loop = asyncio.get_running_loop()
            # Add kwargs to parameters if provided
            if kwargs:
                # Handle additional parameters from kwargs
                pass
            response = await loop.run_in_executor(None, lambda: manager.get_shield_information_barriers())
            return BoxResponse(success=True, data=response)
        except Exception as e:
            return BoxResponse(success=False, error=str(e))

    async def shield_information_barriers_get_shield_information_barrier_by_id(self, barrier_id: str, **kwargs) -> BoxResponse:
        """Get shield information barrier by ID

        API Endpoint: shield_information_barriers.get_shield_information_barrier_by_id
        Namespace: shield_information_barriers

        Args:
            barrier_id (str, required): The ID of the shield information barrier

        Returns:
            BoxResponse: SDK response
        """
        client = self._get_client()
        manager = getattr(client, 'shield_information_barriers', None)
        if manager is None:
            return BoxResponse(success=False, error="Manager 'shield_information_barriers' not found")

        try:
            loop = asyncio.get_running_loop()
            # Add kwargs to parameters if provided
            if kwargs:
                # Handle additional parameters from kwargs
                pass
            response = await loop.run_in_executor(None, lambda: manager.get_shield_information_barrier_by_id(barrier_id))
            return BoxResponse(success=True, data=response)
        except Exception as e:
            return BoxResponse(success=False, error=str(e))

    async def shield_information_barriers_create_shield_information_barrier(self, enterprise: str, type: str, **kwargs) -> BoxResponse:
        """Create a shield information barrier

        API Endpoint: shield_information_barriers.create_shield_information_barrier
        Namespace: shield_information_barriers

        Args:
            enterprise (str, required): Enterprise ID
            type (str, required): Barrier type

        Returns:
            BoxResponse: SDK response
        """
        client = self._get_client()
        manager = getattr(client, 'shield_information_barriers', None)
        if manager is None:
            return BoxResponse(success=False, error="Manager 'shield_information_barriers' not found")

        try:
            loop = asyncio.get_running_loop()
            # Add kwargs to parameters if provided
            if kwargs:
                # Handle additional parameters from kwargs
                pass
            response = await loop.run_in_executor(None, lambda: manager.create_shield_information_barrier(enterprise, type))
            return BoxResponse(success=True, data=response)
        except Exception as e:
            return BoxResponse(success=False, error=str(e))

    async def shield_information_barriers_update_shield_information_barrier_status(self, barrier_id: str, status: str, **kwargs) -> BoxResponse:
        """Update shield information barrier status

        API Endpoint: shield_information_barriers.update_shield_information_barrier_status
        Namespace: shield_information_barriers

        Args:
            barrier_id (str, required): The ID of the shield information barrier
            status (str, required): New status

        Returns:
            BoxResponse: SDK response
        """
        client = self._get_client()
        manager = getattr(client, 'shield_information_barriers', None)
        if manager is None:
            return BoxResponse(success=False, error="Manager 'shield_information_barriers' not found")

        try:
            loop = asyncio.get_running_loop()
            # Add kwargs to parameters if provided
            if kwargs:
                # Handle additional parameters from kwargs
                pass
            response = await loop.run_in_executor(None, lambda: manager.update_shield_information_barrier_status(barrier_id, status))
            return BoxResponse(success=True, data=response)
        except Exception as e:
            return BoxResponse(success=False, error=str(e))

    async def sign_requests_create_sign_request(self, source_files: List[str], signers: List[str], **kwargs) -> BoxResponse:
        """Create a sign request

        API Endpoint: sign_requests.create_sign_request
        Namespace: sign_requests

        Args:
            source_files (List[str], required): List of file IDs to sign
            signers (List[str], required): List of signer information

        Returns:
            BoxResponse: SDK response
        """
        client = self._get_client()
        manager = getattr(client, 'sign_requests', None)
        if manager is None:
            return BoxResponse(success=False, error="Manager 'sign_requests' not found")

        try:
            loop = asyncio.get_running_loop()
            # Add kwargs to parameters if provided
            if kwargs:
                # Handle additional parameters from kwargs
                pass
            response = await loop.run_in_executor(None, lambda: manager.create_sign_request(source_files, signers))
            return BoxResponse(success=True, data=response)
        except Exception as e:
            return BoxResponse(success=False, error=str(e))

    async def sign_requests_get_sign_requests(self, **kwargs) -> BoxResponse:
        """Get all sign requests

        API Endpoint: sign_requests.get_sign_requests
        Namespace: sign_requests

        Returns:
            BoxResponse: SDK response
        """
        client = self._get_client()
        manager = getattr(client, 'sign_requests', None)
        if manager is None:
            return BoxResponse(success=False, error="Manager 'sign_requests' not found")

        try:
            loop = asyncio.get_running_loop()
            # Add kwargs to parameters if provided
            if kwargs:
                # Handle additional parameters from kwargs
                pass
            response = await loop.run_in_executor(None, lambda: manager.get_sign_requests())
            return BoxResponse(success=True, data=response)
        except Exception as e:
            return BoxResponse(success=False, error=str(e))

    async def sign_requests_get_sign_request_by_id(self, sign_request_id: str, **kwargs) -> BoxResponse:
        """Get sign request information by ID

        API Endpoint: sign_requests.get_sign_request_by_id
        Namespace: sign_requests

        Args:
            sign_request_id (str, required): The ID of the sign request

        Returns:
            BoxResponse: SDK response
        """
        client = self._get_client()
        manager = getattr(client, 'sign_requests', None)
        if manager is None:
            return BoxResponse(success=False, error="Manager 'sign_requests' not found")

        try:
            loop = asyncio.get_running_loop()
            # Add kwargs to parameters if provided
            if kwargs:
                # Handle additional parameters from kwargs
                pass
            response = await loop.run_in_executor(None, lambda: manager.get_sign_request_by_id(sign_request_id))
            return BoxResponse(success=True, data=response)
        except Exception as e:
            return BoxResponse(success=False, error=str(e))

    async def sign_requests_cancel_sign_request(self, sign_request_id: str, **kwargs) -> BoxResponse:
        """Cancel a sign request

        API Endpoint: sign_requests.cancel_sign_request
        Namespace: sign_requests

        Args:
            sign_request_id (str, required): The ID of the sign request

        Returns:
            BoxResponse: SDK response
        """
        client = self._get_client()
        manager = getattr(client, 'sign_requests', None)
        if manager is None:
            return BoxResponse(success=False, error="Manager 'sign_requests' not found")

        try:
            loop = asyncio.get_running_loop()
            # Add kwargs to parameters if provided
            if kwargs:
                # Handle additional parameters from kwargs
                pass
            response = await loop.run_in_executor(None, lambda: manager.cancel_sign_request(sign_request_id))
            return BoxResponse(success=True, data=response)
        except Exception as e:
            return BoxResponse(success=False, error=str(e))

    async def sign_requests_resend_sign_request(self, sign_request_id: str, **kwargs) -> BoxResponse:
        """Resend a sign request

        API Endpoint: sign_requests.resend_sign_request
        Namespace: sign_requests

        Args:
            sign_request_id (str, required): The ID of the sign request

        Returns:
            BoxResponse: SDK response
        """
        client = self._get_client()
        manager = getattr(client, 'sign_requests', None)
        if manager is None:
            return BoxResponse(success=False, error="Manager 'sign_requests' not found")

        try:
            loop = asyncio.get_running_loop()
            # Add kwargs to parameters if provided
            if kwargs:
                # Handle additional parameters from kwargs
                pass
            response = await loop.run_in_executor(None, lambda: manager.resend_sign_request(sign_request_id))
            return BoxResponse(success=True, data=response)
        except Exception as e:
            return BoxResponse(success=False, error=str(e))

    async def workflows_get_workflows(self, **kwargs) -> BoxResponse:
        """Get all workflows

        API Endpoint: workflows.get_workflows
        Namespace: workflows

        Returns:
            BoxResponse: SDK response
        """
        client = self._get_client()
        manager = getattr(client, 'workflows', None)
        if manager is None:
            return BoxResponse(success=False, error="Manager 'workflows' not found")

        try:
            loop = asyncio.get_running_loop()
            # Add kwargs to parameters if provided
            if kwargs:
                # Handle additional parameters from kwargs
                pass
            response = await loop.run_in_executor(None, lambda: manager.get_workflows())
            return BoxResponse(success=True, data=response)
        except Exception as e:
            return BoxResponse(success=False, error=str(e))

    async def workflows_start_workflow(self, workflow_id: str, files: List[str], folder: str, **kwargs) -> BoxResponse:
        """Start a workflow

        API Endpoint: workflows.start_workflow
        Namespace: workflows

        Args:
            workflow_id (str, required): The ID of the workflow
            files (List[str], required): List of file IDs to process
            folder (str, required): Folder ID for the workflow

        Returns:
            BoxResponse: SDK response
        """
        client = self._get_client()
        manager = getattr(client, 'workflows', None)
        if manager is None:
            return BoxResponse(success=False, error="Manager 'workflows' not found")

        try:
            loop = asyncio.get_running_loop()
            # Add kwargs to parameters if provided
            if kwargs:
                # Handle additional parameters from kwargs
                pass
            response = await loop.run_in_executor(None, lambda: manager.start_workflow(workflow_id, files, folder))
            return BoxResponse(success=True, data=response)
        except Exception as e:
            return BoxResponse(success=False, error=str(e))

    async def metadata_templates_get_metadata_templates(self, scope: Optional[str] = None, **kwargs) -> BoxResponse:
        """Get all metadata templates

        API Endpoint: metadata_templates.get_metadata_templates
        Namespace: metadata_templates

        Args:
            scope (str, optional): Template scope

        Returns:
            BoxResponse: SDK response
        """
        client = self._get_client()
        manager = getattr(client, 'metadata_templates', None)
        if manager is None:
            return BoxResponse(success=False, error="Manager 'metadata_templates' not found")

        try:
            loop = asyncio.get_running_loop()
            # Add kwargs to parameters if provided
            if kwargs:
                # Handle additional parameters from kwargs
                pass
            response = await loop.run_in_executor(None, lambda: manager.get_metadata_templates(scope=scope))
            return BoxResponse(success=True, data=response)
        except Exception as e:
            return BoxResponse(success=False, error=str(e))

    async def metadata_templates_get_metadata_template(self, scope: str, template_key: str, **kwargs) -> BoxResponse:
        """Get a specific metadata template

        API Endpoint: metadata_templates.get_metadata_template
        Namespace: metadata_templates

        Args:
            scope (str, required): Template scope
            template_key (str, required): Template key

        Returns:
            BoxResponse: SDK response
        """
        client = self._get_client()
        manager = getattr(client, 'metadata_templates', None)
        if manager is None:
            return BoxResponse(success=False, error="Manager 'metadata_templates' not found")

        try:
            loop = asyncio.get_running_loop()
            # Add kwargs to parameters if provided
            if kwargs:
                # Handle additional parameters from kwargs
                pass
            response = await loop.run_in_executor(None, lambda: manager.get_metadata_template(scope, template_key))
            return BoxResponse(success=True, data=response)
        except Exception as e:
            return BoxResponse(success=False, error=str(e))

    async def metadata_templates_create_metadata_template(self, scope: str, template_key: str, display_name: str, fields: List[Dict], **kwargs) -> BoxResponse:
        """Create a metadata template

        API Endpoint: metadata_templates.create_metadata_template
        Namespace: metadata_templates

        Args:
            scope (str, required): Template scope
            template_key (str, required): Template key
            display_name (str, required): Display name
            fields (List[Dict], required): Template fields

        Returns:
            BoxResponse: SDK response
        """
        client = self._get_client()
        manager = getattr(client, 'metadata_templates', None)
        if manager is None:
            return BoxResponse(success=False, error="Manager 'metadata_templates' not found")

        try:
            loop = asyncio.get_running_loop()
            # Add kwargs to parameters if provided
            if kwargs:
                # Handle additional parameters from kwargs
                pass
            response = await loop.run_in_executor(None, lambda: manager.create_metadata_template(scope, template_key, display_name, fields))
            return BoxResponse(success=True, data=response)
        except Exception as e:
            return BoxResponse(success=False, error=str(e))

    async def metadata_templates_update_metadata_template(self, scope: str, template_key: str, operations: List[Dict], **kwargs) -> BoxResponse:
        """Update a metadata template

        API Endpoint: metadata_templates.update_metadata_template
        Namespace: metadata_templates

        Args:
            scope (str, required): Template scope
            template_key (str, required): Template key
            operations (List[Dict], required): Update operations

        Returns:
            BoxResponse: SDK response
        """
        client = self._get_client()
        manager = getattr(client, 'metadata_templates', None)
        if manager is None:
            return BoxResponse(success=False, error="Manager 'metadata_templates' not found")

        try:
            loop = asyncio.get_running_loop()
            # Add kwargs to parameters if provided
            if kwargs:
                # Handle additional parameters from kwargs
                pass
            response = await loop.run_in_executor(None, lambda: manager.update_metadata_template(scope, template_key, operations))
            return BoxResponse(success=True, data=response)
        except Exception as e:
            return BoxResponse(success=False, error=str(e))

    async def metadata_templates_delete_metadata_template(self, scope: str, template_key: str, **kwargs) -> BoxResponse:
        """Delete a metadata template

        API Endpoint: metadata_templates.delete_metadata_template
        Namespace: metadata_templates

        Args:
            scope (str, required): Template scope
            template_key (str, required): Template key

        Returns:
            BoxResponse: SDK response
        """
        client = self._get_client()
        manager = getattr(client, 'metadata_templates', None)
        if manager is None:
            return BoxResponse(success=False, error="Manager 'metadata_templates' not found")

        try:
            loop = asyncio.get_running_loop()
            # Add kwargs to parameters if provided
            if kwargs:
                # Handle additional parameters from kwargs
                pass
            response = await loop.run_in_executor(None, lambda: manager.delete_metadata_template(scope, template_key))
            return BoxResponse(success=True, data=response)
        except Exception as e:
            return BoxResponse(success=False, error=str(e))

    async def metadata_create_file_metadata(self, file_id: str, scope: str, template_key: str, metadata: Dict[str, str], **kwargs) -> BoxResponse:
        """Create metadata on a file

        API Endpoint: metadata.create_file_metadata
        Namespace: metadata

        Args:
            file_id (str, required): The ID of the file
            scope (str, required): Metadata scope
            template_key (str, required): Template key
            metadata (Dict[str, str], required): Metadata values

        Returns:
            BoxResponse: SDK response
        """
        client = self._get_client()
        manager = getattr(client, 'metadata', None)
        if manager is None:
            return BoxResponse(success=False, error="Manager 'metadata' not found")

        try:
            loop = asyncio.get_running_loop()
            # Add kwargs to parameters if provided
            if kwargs:
                # Handle additional parameters from kwargs
                pass
            response = await loop.run_in_executor(None, lambda: manager.create_file_metadata(file_id, scope, template_key, metadata))
            return BoxResponse(success=True, data=response)
        except Exception as e:
            return BoxResponse(success=False, error=str(e))

    async def metadata_get_file_metadata(self, file_id: str, scope: str, template_key: str, **kwargs) -> BoxResponse:
        """Get metadata on a file

        API Endpoint: metadata.get_file_metadata
        Namespace: metadata

        Args:
            file_id (str, required): The ID of the file
            scope (str, required): Metadata scope
            template_key (str, required): Template key

        Returns:
            BoxResponse: SDK response
        """
        client = self._get_client()
        manager = getattr(client, 'metadata', None)
        if manager is None:
            return BoxResponse(success=False, error="Manager 'metadata' not found")

        try:
            loop = asyncio.get_running_loop()
            # Add kwargs to parameters if provided
            if kwargs:
                # Handle additional parameters from kwargs
                pass
            response = await loop.run_in_executor(None, lambda: manager.get_file_metadata(file_id, scope, template_key))
            return BoxResponse(success=True, data=response)
        except Exception as e:
            return BoxResponse(success=False, error=str(e))

    async def metadata_update_file_metadata(self, file_id: str, scope: str, template_key: str, operations: List[Dict], **kwargs) -> BoxResponse:
        """Update metadata on a file

        API Endpoint: metadata.update_file_metadata
        Namespace: metadata

        Args:
            file_id (str, required): The ID of the file
            scope (str, required): Metadata scope
            template_key (str, required): Template key
            operations (List[Dict], required): Update operations

        Returns:
            BoxResponse: SDK response
        """
        client = self._get_client()
        manager = getattr(client, 'metadata', None)
        if manager is None:
            return BoxResponse(success=False, error="Manager 'metadata' not found")

        try:
            loop = asyncio.get_running_loop()
            # Add kwargs to parameters if provided
            if kwargs:
                # Handle additional parameters from kwargs
                pass
            response = await loop.run_in_executor(None, lambda: manager.update_file_metadata(file_id, scope, template_key, operations))
            return BoxResponse(success=True, data=response)
        except Exception as e:
            return BoxResponse(success=False, error=str(e))

    async def metadata_delete_file_metadata(self, file_id: str, scope: str, template_key: str, **kwargs) -> BoxResponse:
        """Delete metadata from a file

        API Endpoint: metadata.delete_file_metadata
        Namespace: metadata

        Args:
            file_id (str, required): The ID of the file
            scope (str, required): Metadata scope
            template_key (str, required): Template key

        Returns:
            BoxResponse: SDK response
        """
        client = self._get_client()
        manager = getattr(client, 'metadata', None)
        if manager is None:
            return BoxResponse(success=False, error="Manager 'metadata' not found")

        try:
            loop = asyncio.get_running_loop()
            # Add kwargs to parameters if provided
            if kwargs:
                # Handle additional parameters from kwargs
                pass
            response = await loop.run_in_executor(None, lambda: manager.delete_file_metadata(file_id, scope, template_key))
            return BoxResponse(success=True, data=response)
        except Exception as e:
            return BoxResponse(success=False, error=str(e))

    async def metadata_get_all_file_metadata(self, file_id: str, **kwargs) -> BoxResponse:
        """Get all metadata on a file

        API Endpoint: metadata.get_all_file_metadata
        Namespace: metadata

        Args:
            file_id (str, required): The ID of the file

        Returns:
            BoxResponse: SDK response
        """
        client = self._get_client()
        manager = getattr(client, 'metadata', None)
        if manager is None:
            return BoxResponse(success=False, error="Manager 'metadata' not found")

        try:
            loop = asyncio.get_running_loop()
            # Add kwargs to parameters if provided
            if kwargs:
                # Handle additional parameters from kwargs
                pass
            response = await loop.run_in_executor(None, lambda: manager.get_all_file_metadata(file_id))
            return BoxResponse(success=True, data=response)
        except Exception as e:
            return BoxResponse(success=False, error=str(e))

    async def metadata_create_folder_metadata(self, folder_id: str, scope: str, template_key: str, metadata: Dict[str, str], **kwargs) -> BoxResponse:
        """Create metadata on a folder

        API Endpoint: metadata.create_folder_metadata
        Namespace: metadata

        Args:
            folder_id (str, required): The ID of the folder
            scope (str, required): Metadata scope
            template_key (str, required): Template key
            metadata (Dict[str, str], required): Metadata values

        Returns:
            BoxResponse: SDK response
        """
        client = self._get_client()
        manager = getattr(client, 'metadata', None)
        if manager is None:
            return BoxResponse(success=False, error="Manager 'metadata' not found")

        try:
            loop = asyncio.get_running_loop()
            # Add kwargs to parameters if provided
            if kwargs:
                # Handle additional parameters from kwargs
                pass
            response = await loop.run_in_executor(None, lambda: manager.create_folder_metadata(folder_id, scope, template_key, metadata))
            return BoxResponse(success=True, data=response)
        except Exception as e:
            return BoxResponse(success=False, error=str(e))

    async def metadata_get_folder_metadata(self, folder_id: str, scope: str, template_key: str, **kwargs) -> BoxResponse:
        """Get metadata on a folder

        API Endpoint: metadata.get_folder_metadata
        Namespace: metadata

        Args:
            folder_id (str, required): The ID of the folder
            scope (str, required): Metadata scope
            template_key (str, required): Template key

        Returns:
            BoxResponse: SDK response
        """
        client = self._get_client()
        manager = getattr(client, 'metadata', None)
        if manager is None:
            return BoxResponse(success=False, error="Manager 'metadata' not found")

        try:
            loop = asyncio.get_running_loop()
            # Add kwargs to parameters if provided
            if kwargs:
                # Handle additional parameters from kwargs
                pass
            response = await loop.run_in_executor(None, lambda: manager.get_folder_metadata(folder_id, scope, template_key))
            return BoxResponse(success=True, data=response)
        except Exception as e:
            return BoxResponse(success=False, error=str(e))

    async def metadata_cascade_policies_create_metadata_cascade_policy(self, folder_id: str, scope: str, template_key: str, **kwargs) -> BoxResponse:
        """Create a metadata cascade policy

        API Endpoint: metadata_cascade_policies.create_metadata_cascade_policy
        Namespace: metadata_cascade_policies

        Args:
            folder_id (str, required): The ID of the folder
            scope (str, required): Metadata scope
            template_key (str, required): Template key

        Returns:
            BoxResponse: SDK response
        """
        client = self._get_client()
        manager = getattr(client, 'metadata_cascade_policies', None)
        if manager is None:
            return BoxResponse(success=False, error="Manager 'metadata_cascade_policies' not found")

        try:
            loop = asyncio.get_running_loop()
            # Add kwargs to parameters if provided
            if kwargs:
                # Handle additional parameters from kwargs
                pass
            response = await loop.run_in_executor(None, lambda: manager.create_metadata_cascade_policy(folder_id, scope, template_key))
            return BoxResponse(success=True, data=response)
        except Exception as e:
            return BoxResponse(success=False, error=str(e))

    async def metadata_cascade_policies_get_metadata_cascade_policies(self, folder_id: str, **kwargs) -> BoxResponse:
        """Get metadata cascade policies for a folder

        API Endpoint: metadata_cascade_policies.get_metadata_cascade_policies
        Namespace: metadata_cascade_policies

        Args:
            folder_id (str, required): The ID of the folder

        Returns:
            BoxResponse: SDK response
        """
        client = self._get_client()
        manager = getattr(client, 'metadata_cascade_policies', None)
        if manager is None:
            return BoxResponse(success=False, error="Manager 'metadata_cascade_policies' not found")

        try:
            loop = asyncio.get_running_loop()
            # Add kwargs to parameters if provided
            if kwargs:
                # Handle additional parameters from kwargs
                pass
            response = await loop.run_in_executor(None, lambda: manager.get_metadata_cascade_policies(folder_id))
            return BoxResponse(success=True, data=response)
        except Exception as e:
            return BoxResponse(success=False, error=str(e))

    async def metadata_cascade_policies_get_metadata_cascade_policy(self, policy_id: str, **kwargs) -> BoxResponse:
        """Get a metadata cascade policy by ID

        API Endpoint: metadata_cascade_policies.get_metadata_cascade_policy
        Namespace: metadata_cascade_policies

        Args:
            policy_id (str, required): The ID of the cascade policy

        Returns:
            BoxResponse: SDK response
        """
        client = self._get_client()
        manager = getattr(client, 'metadata_cascade_policies', None)
        if manager is None:
            return BoxResponse(success=False, error="Manager 'metadata_cascade_policies' not found")

        try:
            loop = asyncio.get_running_loop()
            # Add kwargs to parameters if provided
            if kwargs:
                # Handle additional parameters from kwargs
                pass
            response = await loop.run_in_executor(None, lambda: manager.get_metadata_cascade_policy(policy_id))
            return BoxResponse(success=True, data=response)
        except Exception as e:
            return BoxResponse(success=False, error=str(e))

    async def metadata_cascade_policies_delete_metadata_cascade_policy(self, policy_id: str, **kwargs) -> BoxResponse:
        """Delete a metadata cascade policy

        API Endpoint: metadata_cascade_policies.delete_metadata_cascade_policy
        Namespace: metadata_cascade_policies

        Args:
            policy_id (str, required): The ID of the cascade policy

        Returns:
            BoxResponse: SDK response
        """
        client = self._get_client()
        manager = getattr(client, 'metadata_cascade_policies', None)
        if manager is None:
            return BoxResponse(success=False, error="Manager 'metadata_cascade_policies' not found")

        try:
            loop = asyncio.get_running_loop()
            # Add kwargs to parameters if provided
            if kwargs:
                # Handle additional parameters from kwargs
                pass
            response = await loop.run_in_executor(None, lambda: manager.delete_metadata_cascade_policy(policy_id))
            return BoxResponse(success=True, data=response)
        except Exception as e:
            return BoxResponse(success=False, error=str(e))

    async def metadata_cascade_policies_force_apply_metadata_cascade_policy(self, policy_id: str, **kwargs) -> BoxResponse:
        """Force apply a metadata cascade policy

        API Endpoint: metadata_cascade_policies.force_apply_metadata_cascade_policy
        Namespace: metadata_cascade_policies

        Args:
            policy_id (str, required): The ID of the cascade policy

        Returns:
            BoxResponse: SDK response
        """
        client = self._get_client()
        manager = getattr(client, 'metadata_cascade_policies', None)
        if manager is None:
            return BoxResponse(success=False, error="Manager 'metadata_cascade_policies' not found")

        try:
            loop = asyncio.get_running_loop()
            # Add kwargs to parameters if provided
            if kwargs:
                # Handle additional parameters from kwargs
                pass
            response = await loop.run_in_executor(None, lambda: manager.force_apply_metadata_cascade_policy(policy_id))
            return BoxResponse(success=True, data=response)
        except Exception as e:
            return BoxResponse(success=False, error=str(e))

    async def events_get_events(self, stream_type: Optional[str] = None, stream_position: Optional[str] = None, **kwargs) -> BoxResponse:
        """Get events for the current user or enterprise

        API Endpoint: events.get_events
        Namespace: events

        Args:
            stream_type (str, optional): Type of event stream
            stream_position (str, optional): Starting position in stream

        Returns:
            BoxResponse: SDK response
        """
        client = self._get_client()
        manager = getattr(client, 'events', None)
        if manager is None:
            return BoxResponse(success=False, error="Manager 'events' not found")

        try:
            loop = asyncio.get_running_loop()
            # Add kwargs to parameters if provided
            if kwargs:
                # Handle additional parameters from kwargs
                pass
            response = await loop.run_in_executor(None, lambda: manager.get_events(stream_type=stream_type, stream_position=stream_position))
            return BoxResponse(success=True, data=response)
        except Exception as e:
            return BoxResponse(success=False, error=str(e))

    async def events_get_events_with_long_polling(self, stream_type: Optional[str] = None, stream_position: Optional[str] = None, **kwargs) -> BoxResponse:
        """Get events using long polling

        API Endpoint: events.get_events_with_long_polling
        Namespace: events

        Args:
            stream_type (str, optional): Type of event stream
            stream_position (str, optional): Starting position in stream

        Returns:
            BoxResponse: SDK response
        """
        client = self._get_client()
        manager = getattr(client, 'events', None)
        if manager is None:
            return BoxResponse(success=False, error="Manager 'events' not found")

        try:
            loop = asyncio.get_running_loop()
            # Add kwargs to parameters if provided
            if kwargs:
                # Handle additional parameters from kwargs
                pass
            response = await loop.run_in_executor(None, lambda: manager.get_events_with_long_polling(stream_type=stream_type, stream_position=stream_position))
            return BoxResponse(success=True, data=response)
        except Exception as e:
            return BoxResponse(success=False, error=str(e))

    async def enterprise_events_get_enterprise_events(self, stream_type: Optional[str] = None, created_after: Optional[str] = None, created_before: Optional[str] = None, **kwargs) -> BoxResponse:
        """Get enterprise events

        API Endpoint: enterprise_events.get_enterprise_events
        Namespace: enterprise_events

        Args:
            stream_type (str, optional): Type of event stream
            created_after (str, optional): Events created after this time
            created_before (str, optional): Events created before this time

        Returns:
            BoxResponse: SDK response
        """
        client = self._get_client()
        manager = getattr(client, 'enterprise_events', None)
        if manager is None:
            return BoxResponse(success=False, error="Manager 'enterprise_events' not found")

        try:
            loop = asyncio.get_running_loop()
            # Add kwargs to parameters if provided
            if kwargs:
                # Handle additional parameters from kwargs
                pass
            response = await loop.run_in_executor(None, lambda: manager.get_enterprise_events(stream_type=stream_type, created_after=created_after, created_before=created_before))
            return BoxResponse(success=True, data=response)
        except Exception as e:
            return BoxResponse(success=False, error=str(e))

    async def collections_get_collections(self, **kwargs) -> BoxResponse:
        """Get all collections

        API Endpoint: collections.get_collections
        Namespace: collections

        Returns:
            BoxResponse: SDK response
        """
        client = self._get_client()
        manager = getattr(client, 'collections', None)
        if manager is None:
            return BoxResponse(success=False, error="Manager 'collections' not found")

        try:
            loop = asyncio.get_running_loop()
            # Add kwargs to parameters if provided
            if kwargs:
                # Handle additional parameters from kwargs
                pass
            response = await loop.run_in_executor(None, lambda: manager.get_collections())
            return BoxResponse(success=True, data=response)
        except Exception as e:
            return BoxResponse(success=False, error=str(e))

    async def collections_get_collection_items(self, collection_id: str, **kwargs) -> BoxResponse:
        """Get items in a collection

        API Endpoint: collections.get_collection_items
        Namespace: collections

        Args:
            collection_id (str, required): The ID of the collection

        Returns:
            BoxResponse: SDK response
        """
        client = self._get_client()
        manager = getattr(client, 'collections', None)
        if manager is None:
            return BoxResponse(success=False, error="Manager 'collections' not found")

        try:
            loop = asyncio.get_running_loop()
            # Add kwargs to parameters if provided
            if kwargs:
                # Handle additional parameters from kwargs
                pass
            response = await loop.run_in_executor(None, lambda: manager.get_collection_items(collection_id))
            return BoxResponse(success=True, data=response)
        except Exception as e:
            return BoxResponse(success=False, error=str(e))

    async def collections_add_to_collection(self, collection_id: str, item_id: str, item_type: str, **kwargs) -> BoxResponse:
        """Add an item to a collection

        API Endpoint: collections.add_to_collection
        Namespace: collections

        Args:
            collection_id (str, required): The ID of the collection
            item_id (str, required): The ID of the item to add
            item_type (str, required): Type: 'file' or 'folder'

        Returns:
            BoxResponse: SDK response
        """
        client = self._get_client()
        manager = getattr(client, 'collections', None)
        if manager is None:
            return BoxResponse(success=False, error="Manager 'collections' not found")

        try:
            loop = asyncio.get_running_loop()
            # Add kwargs to parameters if provided
            if kwargs:
                # Handle additional parameters from kwargs
                pass
            response = await loop.run_in_executor(None, lambda: manager.add_to_collection(collection_id, item_id, item_type))
            return BoxResponse(success=True, data=response)
        except Exception as e:
            return BoxResponse(success=False, error=str(e))

    async def collections_remove_from_collection(self, collection_id: str, item_id: str, item_type: str, **kwargs) -> BoxResponse:
        """Remove an item from a collection

        API Endpoint: collections.remove_from_collection
        Namespace: collections

        Args:
            collection_id (str, required): The ID of the collection
            item_id (str, required): The ID of the item to remove
            item_type (str, required): Type: 'file' or 'folder'

        Returns:
            BoxResponse: SDK response
        """
        client = self._get_client()
        manager = getattr(client, 'collections', None)
        if manager is None:
            return BoxResponse(success=False, error="Manager 'collections' not found")

        try:
            loop = asyncio.get_running_loop()
            # Add kwargs to parameters if provided
            if kwargs:
                # Handle additional parameters from kwargs
                pass
            response = await loop.run_in_executor(None, lambda: manager.remove_from_collection(collection_id, item_id, item_type))
            return BoxResponse(success=True, data=response)
        except Exception as e:
            return BoxResponse(success=False, error=str(e))

    async def terms_of_service_get_terms_of_service(self, **kwargs) -> BoxResponse:
        """Get all terms of service

        API Endpoint: terms_of_service.get_terms_of_service
        Namespace: terms_of_service

        Returns:
            BoxResponse: SDK response
        """
        client = self._get_client()
        manager = getattr(client, 'terms_of_service', None)
        if manager is None:
            return BoxResponse(success=False, error="Manager 'terms_of_service' not found")

        try:
            loop = asyncio.get_running_loop()
            # Add kwargs to parameters if provided
            if kwargs:
                # Handle additional parameters from kwargs
                pass
            response = await loop.run_in_executor(None, lambda: manager.get_terms_of_service())
            return BoxResponse(success=True, data=response)
        except Exception as e:
            return BoxResponse(success=False, error=str(e))

    async def terms_of_service_get_terms_of_service_by_id(self, tos_id: str, **kwargs) -> BoxResponse:
        """Get terms of service by ID

        API Endpoint: terms_of_service.get_terms_of_service_by_id
        Namespace: terms_of_service

        Args:
            tos_id (str, required): The ID of the terms of service

        Returns:
            BoxResponse: SDK response
        """
        client = self._get_client()
        manager = getattr(client, 'terms_of_service', None)
        if manager is None:
            return BoxResponse(success=False, error="Manager 'terms_of_service' not found")

        try:
            loop = asyncio.get_running_loop()
            # Add kwargs to parameters if provided
            if kwargs:
                # Handle additional parameters from kwargs
                pass
            response = await loop.run_in_executor(None, lambda: manager.get_terms_of_service_by_id(tos_id))
            return BoxResponse(success=True, data=response)
        except Exception as e:
            return BoxResponse(success=False, error=str(e))

    async def terms_of_service_create_terms_of_service_user_status(self, tos_id: str, user_id: str, is_accepted: bool, **kwargs) -> BoxResponse:
        """Create terms of service user status

        API Endpoint: terms_of_service.create_terms_of_service_user_status
        Namespace: terms_of_service

        Args:
            tos_id (str, required): The ID of the terms of service
            user_id (str, required): The ID of the user
            is_accepted (bool, required): Whether terms are accepted

        Returns:
            BoxResponse: SDK response
        """
        client = self._get_client()
        manager = getattr(client, 'terms_of_service', None)
        if manager is None:
            return BoxResponse(success=False, error="Manager 'terms_of_service' not found")

        try:
            loop = asyncio.get_running_loop()
            # Add kwargs to parameters if provided
            if kwargs:
                # Handle additional parameters from kwargs
                pass
            response = await loop.run_in_executor(None, lambda: manager.create_terms_of_service_user_status(tos_id, user_id, is_accepted))
            return BoxResponse(success=True, data=response)
        except Exception as e:
            return BoxResponse(success=False, error=str(e))

    async def terms_of_service_get_terms_of_service_user_statuses(self, tos_id: str, **kwargs) -> BoxResponse:
        """Get all user statuses for terms of service

        API Endpoint: terms_of_service.get_terms_of_service_user_statuses
        Namespace: terms_of_service

        Args:
            tos_id (str, required): The ID of the terms of service

        Returns:
            BoxResponse: SDK response
        """
        client = self._get_client()
        manager = getattr(client, 'terms_of_service', None)
        if manager is None:
            return BoxResponse(success=False, error="Manager 'terms_of_service' not found")

        try:
            loop = asyncio.get_running_loop()
            # Add kwargs to parameters if provided
            if kwargs:
                # Handle additional parameters from kwargs
                pass
            response = await loop.run_in_executor(None, lambda: manager.get_terms_of_service_user_statuses(tos_id))
            return BoxResponse(success=True, data=response)
        except Exception as e:
            return BoxResponse(success=False, error=str(e))

    async def terms_of_service_update_terms_of_service_user_status(self, tos_user_status_id: str, is_accepted: bool, **kwargs) -> BoxResponse:
        """Update terms of service user status

        API Endpoint: terms_of_service.update_terms_of_service_user_status
        Namespace: terms_of_service

        Args:
            tos_user_status_id (str, required): The ID of the terms of service user status
            is_accepted (bool, required): Whether terms are accepted

        Returns:
            BoxResponse: SDK response
        """
        client = self._get_client()
        manager = getattr(client, 'terms_of_service', None)
        if manager is None:
            return BoxResponse(success=False, error="Manager 'terms_of_service' not found")

        try:
            loop = asyncio.get_running_loop()
            # Add kwargs to parameters if provided
            if kwargs:
                # Handle additional parameters from kwargs
                pass
            response = await loop.run_in_executor(None, lambda: manager.update_terms_of_service_user_status(tos_user_status_id, is_accepted))
            return BoxResponse(success=True, data=response)
        except Exception as e:
            return BoxResponse(success=False, error=str(e))

    async def collaboration_allowlist_get_collaboration_allowlist_entries(self, **kwargs) -> BoxResponse:
        """Get all collaboration allowlist entries

        API Endpoint: collaboration_allowlist.get_collaboration_allowlist_entries
        Namespace: collaboration_allowlist

        Returns:
            BoxResponse: SDK response
        """
        client = self._get_client()
        manager = getattr(client, 'collaboration_allowlist', None)
        if manager is None:
            return BoxResponse(success=False, error="Manager 'collaboration_allowlist' not found")

        try:
            loop = asyncio.get_running_loop()
            # Add kwargs to parameters if provided
            if kwargs:
                # Handle additional parameters from kwargs
                pass
            response = await loop.run_in_executor(None, lambda: manager.get_collaboration_allowlist_entries())
            return BoxResponse(success=True, data=response)
        except Exception as e:
            return BoxResponse(success=False, error=str(e))

    async def collaboration_allowlist_create_collaboration_allowlist_entry(self, domain: str, direction: str, **kwargs) -> BoxResponse:
        """Create a collaboration allowlist entry

        API Endpoint: collaboration_allowlist.create_collaboration_allowlist_entry
        Namespace: collaboration_allowlist

        Args:
            domain (str, required): Domain to allowlist
            direction (str, required): Collaboration direction

        Returns:
            BoxResponse: SDK response
        """
        client = self._get_client()
        manager = getattr(client, 'collaboration_allowlist', None)
        if manager is None:
            return BoxResponse(success=False, error="Manager 'collaboration_allowlist' not found")

        try:
            loop = asyncio.get_running_loop()
            # Add kwargs to parameters if provided
            if kwargs:
                # Handle additional parameters from kwargs
                pass
            response = await loop.run_in_executor(None, lambda: manager.create_collaboration_allowlist_entry(domain, direction))
            return BoxResponse(success=True, data=response)
        except Exception as e:
            return BoxResponse(success=False, error=str(e))

    async def collaboration_allowlist_get_collaboration_allowlist_entry(self, entry_id: str, **kwargs) -> BoxResponse:
        """Get collaboration allowlist entry by ID

        API Endpoint: collaboration_allowlist.get_collaboration_allowlist_entry
        Namespace: collaboration_allowlist

        Args:
            entry_id (str, required): The ID of the allowlist entry

        Returns:
            BoxResponse: SDK response
        """
        client = self._get_client()
        manager = getattr(client, 'collaboration_allowlist', None)
        if manager is None:
            return BoxResponse(success=False, error="Manager 'collaboration_allowlist' not found")

        try:
            loop = asyncio.get_running_loop()
            # Add kwargs to parameters if provided
            if kwargs:
                # Handle additional parameters from kwargs
                pass
            response = await loop.run_in_executor(None, lambda: manager.get_collaboration_allowlist_entry(entry_id))
            return BoxResponse(success=True, data=response)
        except Exception as e:
            return BoxResponse(success=False, error=str(e))

    async def collaboration_allowlist_delete_collaboration_allowlist_entry(self, entry_id: str, **kwargs) -> BoxResponse:
        """Delete a collaboration allowlist entry

        API Endpoint: collaboration_allowlist.delete_collaboration_allowlist_entry
        Namespace: collaboration_allowlist

        Args:
            entry_id (str, required): The ID of the allowlist entry

        Returns:
            BoxResponse: SDK response
        """
        client = self._get_client()
        manager = getattr(client, 'collaboration_allowlist', None)
        if manager is None:
            return BoxResponse(success=False, error="Manager 'collaboration_allowlist' not found")

        try:
            loop = asyncio.get_running_loop()
            # Add kwargs to parameters if provided
            if kwargs:
                # Handle additional parameters from kwargs
                pass
            response = await loop.run_in_executor(None, lambda: manager.delete_collaboration_allowlist_entry(entry_id))
            return BoxResponse(success=True, data=response)
        except Exception as e:
            return BoxResponse(success=False, error=str(e))

    async def search_search_for_content(self, query: str, limit: Optional[int] = None, offset: Optional[int] = None, scope: Optional[str] = None, file_extensions: Optional[List[str]] = None, created_at_range: Optional[str] = None, updated_at_range: Optional[str] = None, size_range: Optional[str] = None, owner_user_ids: Optional[List[str]] = None, ancestor_folder_ids: Optional[List[str]] = None, content_types: Optional[List[str]] = None, type: Optional[str] = None, trash_content: Optional[str] = None, mdfilters: Optional[List[Dict]] = None, **kwargs) -> BoxResponse:
        """Search for content with advanced filters

        API Endpoint: search.search_for_content
        Namespace: search

        Args:
            query (str, required): The search query
            limit (int, optional): The maximum number of results
            offset (int, optional): The offset for pagination
            scope (str, optional): Scope to search within
            file_extensions (List[str], optional): File extensions to filter by
            created_at_range (str, optional): Date range for creation
            updated_at_range (str, optional): Date range for updates
            size_range (str, optional): File size range
            owner_user_ids (List[str], optional): Filter by owner user IDs
            ancestor_folder_ids (List[str], optional): Filter by ancestor folder IDs
            content_types (List[str], optional): Filter by content types
            type (str, optional): Item type filter
            trash_content (str, optional): Include trash content
            mdfilters (List[Dict], optional): Metadata filters

        Returns:
            BoxResponse: SDK response
        """
        client = self._get_client()
        manager = getattr(client, 'search', None)
        if manager is None:
            return BoxResponse(success=False, error="Manager 'search' not found")

        try:
            loop = asyncio.get_running_loop()
            # Add kwargs to parameters if provided
            if kwargs:
                # Handle additional parameters from kwargs
                pass
            response = await loop.run_in_executor(None, lambda: manager.search_for_content(query, limit=limit, offset=offset, scope=scope, file_extensions=file_extensions, created_at_range=created_at_range, updated_at_range=updated_at_range, size_range=size_range, owner_user_ids=owner_user_ids, ancestor_folder_ids=ancestor_folder_ids, content_types=content_types, type=type, trash_content=trash_content, mdfilters=mdfilters))
            return BoxResponse(success=True, data=response)
        except Exception as e:
            return BoxResponse(success=False, error=str(e))

    async def uploads_upload_file(self, attributes: UploadFileAttributes, file: BinaryIO, **kwargs) -> BoxResponse:
        """Upload a file

        API Endpoint: uploads.upload_file
        Namespace: uploads

        Args:
            attributes (UploadFileAttributes, required): File upload attributes
            file (BinaryIO, required): The file to upload

        Returns:
            BoxResponse: SDK response
        """
        client = self._get_client()
        manager = getattr(client, 'uploads', None)
        if manager is None:
            return BoxResponse(success=False, error="Manager 'uploads' not found")

        try:
            loop = asyncio.get_running_loop()
            # Add kwargs to parameters if provided
            if kwargs:
                # Handle additional parameters from kwargs
                pass
            response = await loop.run_in_executor(None, lambda: manager.upload_file(attributes, file))
            return BoxResponse(success=True, data=response)
        except Exception as e:
            return BoxResponse(success=False, error=str(e))

    async def uploads_upload_file_version(self, file_id: str, file: BinaryIO, **kwargs) -> BoxResponse:
        """Upload a new version of a file

        API Endpoint: uploads.upload_file_version
        Namespace: uploads

        Args:
            file_id (str, required): The ID of the file
            file (BinaryIO, required): The new file version

        Returns:
            BoxResponse: SDK response
        """
        client = self._get_client()
        manager = getattr(client, 'uploads', None)
        if manager is None:
            return BoxResponse(success=False, error="Manager 'uploads' not found")

        try:
            loop = asyncio.get_running_loop()
            # Add kwargs to parameters if provided
            if kwargs:
                # Handle additional parameters from kwargs
                pass
            response = await loop.run_in_executor(None, lambda: manager.upload_file_version(file_id, file))
            return BoxResponse(success=True, data=response)
        except Exception as e:
            return BoxResponse(success=False, error=str(e))

    async def uploads_preflight_file_upload_check(self, name: str, size: int, parent: PreflightFileUploadCheckParent, **kwargs) -> BoxResponse:
        """Check if file can be uploaded

        API Endpoint: uploads.preflight_file_upload_check
        Namespace: uploads

        Args:
            name (str, required): The name of the file
            size (int, required): The size of the file
            parent (PreflightFileUploadCheckParent, required): The parent folder

        Returns:
            BoxResponse: SDK response
        """
        client = self._get_client()
        manager = getattr(client, 'uploads', None)
        if manager is None:
            return BoxResponse(success=False, error="Manager 'uploads' not found")

        try:
            loop = asyncio.get_running_loop()
            # Add kwargs to parameters if provided
            if kwargs:
                # Handle additional parameters from kwargs
                pass
            response = await loop.run_in_executor(None, lambda: manager.preflight_file_upload_check(name, size, parent))
            return BoxResponse(success=True, data=response)
        except Exception as e:
            return BoxResponse(success=False, error=str(e))

    async def uploads_create_upload_session(self, folder_id: str, file_size: int, file_name: str, **kwargs) -> BoxResponse:
        """Create an upload session for large files

        API Endpoint: uploads.create_upload_session
        Namespace: uploads

        Args:
            folder_id (str, required): The ID of the folder
            file_size (int, required): The size of the file
            file_name (str, required): The name of the file

        Returns:
            BoxResponse: SDK response
        """
        client = self._get_client()
        manager = getattr(client, 'uploads', None)
        if manager is None:
            return BoxResponse(success=False, error="Manager 'uploads' not found")

        try:
            loop = asyncio.get_running_loop()
            # Add kwargs to parameters if provided
            if kwargs:
                # Handle additional parameters from kwargs
                pass
            response = await loop.run_in_executor(None, lambda: manager.create_upload_session(folder_id, file_size, file_name))
            return BoxResponse(success=True, data=response)
        except Exception as e:
            return BoxResponse(success=False, error=str(e))

    async def uploads_upload_part(self, upload_session_id: str, part_data: bytes, **kwargs) -> BoxResponse:
        """Upload a part for chunked upload

        API Endpoint: uploads.upload_part
        Namespace: uploads

        Args:
            upload_session_id (str, required): The ID of the upload session
            part_data (bytes, required): Part data to upload

        Returns:
            BoxResponse: SDK response
        """
        client = self._get_client()
        manager = getattr(client, 'uploads', None)
        if manager is None:
            return BoxResponse(success=False, error="Manager 'uploads' not found")

        try:
            loop = asyncio.get_running_loop()
            # Add kwargs to parameters if provided
            if kwargs:
                # Handle additional parameters from kwargs
                pass
            response = await loop.run_in_executor(None, lambda: manager.upload_part(upload_session_id, part_data))
            return BoxResponse(success=True, data=response)
        except Exception as e:
            return BoxResponse(success=False, error=str(e))

    async def uploads_commit_upload_session(self, upload_session_id: str, parts: List[Dict], **kwargs) -> BoxResponse:
        """Commit an upload session

        API Endpoint: uploads.commit_upload_session
        Namespace: uploads

        Args:
            upload_session_id (str, required): The ID of the upload session
            parts (List[Dict], required): List of uploaded parts

        Returns:
            BoxResponse: SDK response
        """
        client = self._get_client()
        manager = getattr(client, 'uploads', None)
        if manager is None:
            return BoxResponse(success=False, error="Manager 'uploads' not found")

        try:
            loop = asyncio.get_running_loop()
            # Add kwargs to parameters if provided
            if kwargs:
                # Handle additional parameters from kwargs
                pass
            response = await loop.run_in_executor(None, lambda: manager.commit_upload_session(upload_session_id, parts))
            return BoxResponse(success=True, data=response)
        except Exception as e:
            return BoxResponse(success=False, error=str(e))

    async def uploads_abort_upload_session(self, upload_session_id: str, **kwargs) -> BoxResponse:
        """Abort an upload session

        API Endpoint: uploads.abort_upload_session
        Namespace: uploads

        Args:
            upload_session_id (str, required): The ID of the upload session

        Returns:
            BoxResponse: SDK response
        """
        client = self._get_client()
        manager = getattr(client, 'uploads', None)
        if manager is None:
            return BoxResponse(success=False, error="Manager 'uploads' not found")

        try:
            loop = asyncio.get_running_loop()
            # Add kwargs to parameters if provided
            if kwargs:
                # Handle additional parameters from kwargs
                pass
            response = await loop.run_in_executor(None, lambda: manager.abort_upload_session(upload_session_id))
            return BoxResponse(success=True, data=response)
        except Exception as e:
            return BoxResponse(success=False, error=str(e))

    def get_client(self) -> BoxClient:
        """Get the underlying Box client."""
        return self._get_client()

    def get_sdk_info(self) -> Dict[str, Union[int, bool, List[str], Dict[str, int]]]:
        """Get information about the wrapped SDK methods."""
        manager_methods = [m for m in self.generated_methods if m.get('namespace')]

        namespaces: Dict[str, int] = {}
        for method in self.generated_methods:
            namespace = method.get('namespace') or 'root'
            if namespace not in namespaces:
                namespaces[namespace] = 0
            namespaces[namespace] += 1

        endpoints = [m.get('endpoint') for m in self.generated_methods if m.get('endpoint')]

        return {
            "total_methods": len(self.generated_methods),
            "manager_methods": len(manager_methods),
            "namespaces": namespaces,
            "endpoints": [e for e in endpoints if e is not None],
            "coverage_summary": {
                "core_apis": ["files", "folders", "users", "groups"],
                "collaboration_apis": ["collaborations", "shared_links"],
                "content_apis": ["comments", "tasks", "webhooks", "web_links"],
                "enterprise_apis": ["retention_policies", "legal_hold_policies", "classifications"],
                "advanced_apis": ["shield_information_barriers", "sign_requests", "workflows"],
                "metadata_apis": ["metadata_templates", "metadata", "metadata_cascade_policies"],
                "events_apis": ["events", "enterprise_events"],
                "integration_apis": ["collections", "terms_of_service", "collaboration_allowlist"],
                "search_apis": ["search"],
                "upload_apis": ["uploads"]
            }
        }

    # Helper methods for common Box operations
    async def get_root_folder(self) -> BoxResponse:
        """Get the root folder."""
        try:
            client = self._get_client()
            response = await asyncio.get_running_loop().run_in_executor(
                None, lambda: client.folders.get_folder_by_id("0")
            )
            return BoxResponse(success=True, data=response)
        except Exception as e:
            return BoxResponse(success=False, error=str(e))

    async def get_folder_by_id(self, folder_id: str) -> BoxResponse:
        """Get folder by ID."""
        try:
            client = self._get_client()
            response = await asyncio.get_running_loop().run_in_executor(
                None, lambda: client.folders.get_folder_by_id(folder_id)
            )
            return BoxResponse(success=True, data=response)
        except Exception as e:
            return BoxResponse(success=False, error=str(e))

    async def get_file_by_id(self, file_id: str) -> BoxResponse:
        """Get file by ID."""
        try:
            client = self._get_client()
            response = await asyncio.get_running_loop().run_in_executor(
                None, lambda: client.files.get_file_by_id(file_id)
            )
            return BoxResponse(success=True, data=response)
        except Exception as e:
            return BoxResponse(success=False, error=str(e))

    async def get_current_user(self) -> BoxResponse:
        """Get current authenticated user."""
        try:
            client = self._get_client()
            response = await asyncio.get_running_loop().run_in_executor(
                None, lambda: client.users.get_user_me()
            )
            return BoxResponse(success=True, data=response)
        except Exception as e:
            return BoxResponse(success=False, error=str(e))

    # Enterprise Administration Methods
    async def get_enterprise_info(self) -> BoxResponse:
        """Get enterprise information."""
        try:
            client = self._get_client()
            user = await asyncio.get_running_loop().run_in_executor(
                None, lambda: client.users.get_user_me()
            )
            return BoxResponse(success=True, data={"enterprise": getattr(user, 'enterprise', None)})
        except Exception as e:
            return BoxResponse(success=False, error=str(e))
