import base64

from app.sources.client.http.http_client import HTTPClient


class BambooHRClient(HTTPClient):
    """
    BambooHR REST client via API Key authentication.

    Args:
        subdomain: BambooHR company subdomain
        api_key: BambooHR API Key
    """

    def __init__(self, subdomain: str, api_key: str) -> None:
        self.base_url = f"https://api.bamboohr.com/api/gateway.php/{subdomain}/v1"

        # API Key Basic Auth Encoding
        token = base64.b64encode(f"{api_key}:x".encode("utf-8")).decode("utf-8")

        # Call parent HTTPClient: same pattern as JiraRESTClientViaToken
        super().__init__(token, "Basic")

    def get_base_url(self) -> str:  # noqa: D401
        """Return BambooHR base url."""
        return self.base_url
