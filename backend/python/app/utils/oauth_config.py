from app.config.constants.arangodb import Connectors
from app.connectors.core.base.token_service.oauth_service import OAuthConfig


def get_oauth_config(app_name: str, auth_config: dict) -> OAuthConfig:

    oauth_config = OAuthConfig(
            client_id=auth_config['clientId'],
            client_secret=auth_config['clientSecret'],
            redirect_uri=auth_config.get('redirectUri', ''),
            authorize_url=auth_config.get('authorizeUrl', ''),
            token_url=auth_config.get('tokenUrl', ''),
            scope=' '.join(auth_config.get('scopes', [])) if auth_config.get('scopes') else ''
        )

    if app_name.lower() == Connectors.DROPBOX.value.lower():
        oauth_config.token_access_type = "offline"

    return oauth_config
