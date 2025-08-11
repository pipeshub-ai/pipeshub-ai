import json
from typing import BinaryIO, Optional

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import Resource

from app.agents.actions.google.auth.auth import drive_auth
from app.agents.actions.google.google_drive.config import GoogleDriveConfig


class GoogleDrive:
    """Google Drive tool exposed to the agents"""
    def __init__(self, config: GoogleDriveConfig) -> None:
        """Initialize the Google Drive tool"""
        """
        Args:
            config: Google Drive configuration
        Returns:
            None
        """
        self.config = config
        self.service: Optional[Resource] = None
        self.credentials: Optional[Credentials] = None

    @drive_auth()
    def get_files_list(self, folder_id: Optional[str] = None, page_token: Optional[str] = None) -> tuple[bool, str]:
        """Get the list of files in the Google Drive"""
        """
        Args:
            folder_id: The id of the folder to get the files from
            page_token: The token to get the next page of files
        Returns:
            tuple[bool, str]: True if the file list is retrieved, False otherwise
        """
        try:
            files = self.service.files().list( # type: ignore
                q=f"'{folder_id}' in parents and trashed=false",
                spaces="drive",
                fields="nextPageToken, files(id, name, mimeType, size, webViewLink, md5Checksum, sha1Checksum, sha256Checksum, headRevisionId, parents, createdTime, modifiedTime, trashed, trashedTime, fileExtension)",
                pageToken=page_token,
                pageSize=1000,
            ).execute()
            return True, json.dumps({
                "files": files.get("files", []),
                "nextPageToken": files.get("nextPageToken", None),
                "totalResults": files.get("resultSizeEstimate", 0),
                "pageToken": files.get("nextPageToken", None),
            })
        except Exception as e:
            return False, json.dumps(str(e))

    @drive_auth()
    def create_folder(self, folder_name: str) -> tuple[bool, str]:
        """Create a folder in the Google Drive"""
        """
        Args:
            folder_name: The name of the folder
        Returns:
            tuple[bool, str]: True if the folder is created, False otherwise
        """
        try:
            folder = self.service.files().create( # type: ignore
                body={
                    "name": folder_name,
                    "mimeType": "application/vnd.google-apps.folder",
                },
            ).execute() # type: ignore
            return True, json.dumps({
                "folder_id": folder.get("id", ""),
                "folder_name": folder.get("name", ""),
                "folder_parents": folder.get("parents", []),
                "folder_mimeType": folder.get("mimeType", ""),
            })
        except Exception as e:
            return False, json.dumps(str(e))

    @drive_auth()
    def upload_file(self, file_name: str, file_content: BinaryIO, folder_id: Optional[str] = None) -> tuple[bool, str]:
        """Upload a file to the Google Drive
        """
        """
        Args:
            file_name: The name of the file
            file_content: The content of the file
            folder_id: The id of the folder to upload the file to
        Returns:
            tuple[bool, str]: True if the file is uploaded, False otherwise
        """
        try:
            file = self.service.files().create( # type: ignore
                media_body=file_content,
                body={
                    "name": file_name,
                    "parents": [folder_id],
                    "mimeType": "application/vnd.google-apps.file",
                },
            ).execute() # type: ignore
            return True, json.dumps({
                "file_id": file.get("id", ""),
                "file_name": file.get("name", ""),
                "file_parents": file.get("parents", []),
                "file_mimeType": file.get("mimeType", ""),
            })
        except Exception as e:
            return False, json.dumps(str(e))

    @drive_auth()
    def download_file(self, file_id: str) -> tuple[bool, Optional[BinaryIO]]:
        """Download a file from the Google Drive"""
        """
        Args:
            file_id: The id of the file
        Returns:
            tuple[bool, Optional[BinaryIO]]: True if the file is downloaded, False otherwise
        """
        try:
            file = self.service.files().get_media(fileId=file_id).execute() # type: ignore
            return True, file.content
        except Exception:
            return False, None

    @drive_auth()
    def delete_file(self, file_id: str) -> tuple[bool, str]:
        """Delete a file from the Google Drive"""
        """
        Args:
            file_id: The id of the file
        Returns:
            tuple[bool, str]: True if the file is deleted, False otherwise
        """
        try:
            self.service.files().delete(fileId=file_id).execute() # type: ignore
            return True, json.dumps({"message": "File deleted successfully"})
        except Exception as e:
            return False, json.dumps(str(e))

    @drive_auth()
    def get_file_details(self, file_id: str) -> tuple[bool, str]:
        """Get the details of a file in the Google Drive"""
        """
        Args:
            file_id: The id of the file
        Returns:
            tuple[bool, str]: True if the file is retrieved, False otherwise
        """
        try:
            file = self.service.files().get(fileId=file_id).execute() # type: ignore
            return True, json.dumps(file)
        except Exception as e:
            return False, json.dumps(str(e))
