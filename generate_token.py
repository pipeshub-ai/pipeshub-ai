import os
import requests
from base64 import b64encode


def generate_zoom_token():
    """Generate a Zoom OAuth token securely using environment variables."""
    account_id = os.getenv("ZOOM_ACCOUNT_ID")
    client_id = os.getenv("ZOOM_CLIENT_ID")
    client_secret = os.getenv("ZOOM_CLIENT_SECRET")

    if not all([account_id, client_id, client_secret]):
        raise ValueError(
            "Missing one or more environment variables:\n"
            " - ZOOM_ACCOUNT_ID\n - ZOOM_CLIENT_ID\n - ZOOM_CLIENT_SECRET\n\n"
            "Please set them before running this script.\n"
            "Example:\n"
            "export ZOOM_ACCOUNT_ID='your_account_id'\n"
            "export ZOOM_CLIENT_ID='your_client_id'\n"
            "export ZOOM_CLIENT_SECRET='your_client_secret'"
        )

    # Prepare Basic Auth header
    auth_header = b64encode(f"{client_id}:{client_secret}".encode()).decode()
    url = f"https://zoom.us/oauth/token?grant_type=account_credentials&account_id={account_id}"
    headers = {"Authorization": f"Basic {auth_header}"}

    response = requests.post(url, headers=headers)

    if response.status_code == 200:
        print("✅ Token generated successfully:")
        print(response.json())
    else:
        print("❌ Failed to generate token:")
        print(response.status_code, response.text)


if __name__ == "__main__":
    generate_zoom_token()
