import requests
from base64 import b64encode

# 🔐 Replace these with your actual credentials from Zoom
account_id = "AVrSC4mPQT2NpgJk6FKBCA"
client_id = "k7oqg4fWS4uDYWPNo72R4g"
client_secret = "8n8Bd8inzl49b5EGk8ulVXwD9EXxAhxt"

# Combine client_id and client_secret into a base64-encoded string
auth_header = b64encode(f"{client_id}:{client_secret}".encode()).decode()

# Zoom OAuth URL for Server-to-Server apps
url = f"https://zoom.us/oauth/token?grant_type=account_credentials&account_id={account_id}"

# Set authorization header
headers = {"Authorization": f"Basic {auth_header}"}

# Make POST request to get the token
response = requests.post(url, headers=headers)

# Print the token response
print(response.json())
