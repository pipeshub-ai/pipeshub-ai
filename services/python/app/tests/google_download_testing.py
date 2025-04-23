import io
import os
import pickle

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from google.auth.transport.requests import Request
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaIoBaseDownload

app = FastAPI()


def authenticate():
    """Authenticate using OAuth2 for individual user"""
    try:
        SCOPES = [
            "https://www.googleapis.com/auth/drive.readonly",
            "https://www.googleapis.com/auth/documents.readonly",
        ]

        creds = None
        if os.path.exists("token.pickle"):
            with open("token.pickle", "rb") as token:
                creds = pickle.load(token)

        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
            else:
                flow = InstalledAppFlow.from_client_secrets_file(
                    "credentials.json", SCOPES
                )
                creds = flow.run_local_server(port=8090)
            with open("token.pickle", "wb") as token:
                pickle.dump(creds, token)

        # Build both Drive and Docs services
        drive_service = build("drive", "v3", credentials=creds)
        return drive_service

    except Exception as e:
        print(f"❌ Authentication failed: {str(e)}")
        return None


def download_file(service, real_file_id):
    """Downloads a file from Google Drive with comprehensive logging"""

    try:
        print(f"📥 Starting download for file ID: {real_file_id}")

        # First get file metadata to log details
        file_metadata = (
            service.files()
            .get(fileId=real_file_id, fields="name, size, mimeType")
            .execute()
        )
        print("📋 File details:")
        print(f"   - Name: {file_metadata.get('name')}")
        print(f"   - Size: {file_metadata.get('size', 'Unknown')} bytes")
        print(f"   - Type: {file_metadata.get('mimeType')}")

        request = service.files().get_media(fileId=real_file_id)
        file = io.BytesIO()
        chunk_size = 1024 * 1024  # 1MB chunks
        downloader = MediaIoBaseDownload(file, request, chunksize=chunk_size)

        print("⏳ Download in progress...")
        done = False
        total_size = int(file_metadata.get("size", 0))
        bytes_downloaded = 0

        while done is False:
            status, done = downloader.next_chunk()
            current_pos = file.tell()
            if current_pos > bytes_downloaded:
                bytes_downloaded = current_pos
                percentage = (
                    (bytes_downloaded / total_size) * 100 if total_size > 0 else 0
                )
                print(
                    f"📊 Download progress: {percentage:.1f}% ({bytes_downloaded}/{total_size} bytes)"
                )

        print("✅ Download completed successfully!")

        # Verify downloaded content
        content = file.getvalue()
        content_size = len(content)
        print(f"📦 Downloaded content size: {content_size} bytes")

        if content_size == 0:
            raise ValueError("Downloaded file is empty!")

        return content

    except HttpError as error:
        print(f"❌ HTTP Error occurred: {error}")
        print(
            f"   Error details: {error.error_details if hasattr(error, 'error_details') else 'No details available'}"
        )
        return None
    except Exception as e:
        print(f"❌ Unexpected error: {str(e)}")
        print(f"   Error type: {type(e).__name__}")
        return None


async def stream_file(content: bytes):
    """Generator function to stream file content"""
    yield content


@app.get("/download/{file_id}")
async def stream_google_drive_file(file_id: str):
    """Stream a file from Google Drive"""
    try:
        service = authenticate()
        if service is None:
            raise HTTPException(
                status_code=500, detail="Failed to authenticate with Google Drive"
            )

        content = download_file(service, file_id)
        if content is None:
            raise HTTPException(
                status_code=404, detail="File not found or download failed"
            )

        # Get file metadata for content type
        file_metadata = (
            service.files().get(fileId=file_id, fields="name, mimeType").execute()
        )
        filename = file_metadata.get("name", "downloaded_file")
        mime_type = file_metadata.get("mimeType", "application/octet-stream")

        return StreamingResponse(
            stream_file(content),
            media_type=mime_type,
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# Replace the main block with FastAPI startup
if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
