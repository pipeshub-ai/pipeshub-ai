"""Provider configurations that build offline through the real model factories (no request is sent)."""

from __future__ import annotations


def service_account_json(email: str = "sa@p.iam.gserviceaccount.com") -> str:
    """A throw-away service account key (never used against Google: nothing here sends a request to it)."""
    import json

    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    ).decode()
    return json.dumps({
        "type": "service_account", "project_id": "p", "private_key_id": "k", "private_key": pem,
        "client_email": email, "client_id": "1", "token_uri": "https://oauth2.googleapis.com/token",
    })


BASE = {
    "model": "m", "apiKey": "k", "endpoint": "https://gateway.example.com/v1", "deploymentName": "d",
    "apiVersion": "2024-02-01", "region": "us-east-1", "awsAccessKeyId": "x", "awsAccessSecretKey": "y",
    "project": "p", "location": "us-central1",
}
