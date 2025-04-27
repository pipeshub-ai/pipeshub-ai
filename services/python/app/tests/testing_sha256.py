import base64
import hashlib
import os

import dotenv

# Load environment variables from .env file
dotenv.load_dotenv()

def test_secret_key_hashing():
    # Get the secret key from environment
    secret_key = os.getenv("SECRET_KEY")
    if not secret_key:
        print("❌ ERROR: SECRET_KEY environment variable is not set")
        return

    # Hash the secret key using SHA256
    hashed_key = hashlib.sha256(secret_key.encode()).digest()

    # Convert to different formats for visualization
    hex_key = hashed_key.hex()
    base64_key = base64.b64encode(hashed_key).decode()

    # Print the results
    print("\n🔑 Secret Key Hashing Results:")
    print(f"Original Secret Key Length: {len(secret_key)} characters")
    print(f"Hashed Key Length: {len(hashed_key)} bytes")
    print("\nHashed Key in different formats:")
    print(f"Hex format     : {hex_key}")
    print(f"Base64 format  : {base64_key}")
    print(f"Raw bytes      : {hashed_key}")

if __name__ == "__main__":
    test_secret_key_hashing()
