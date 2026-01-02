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

    elif app_name.lower() == Connectors.DROPBOX_PERSONAL.value.lower():
        oauth_config.token_access_type = "offline"

    elif app_name.lower() == Connectors.NOTION.value.lower():
        # Notion requires Basic Auth with JSON body
        oauth_config.additional_params["use_basic_auth"] = True
        oauth_config.additional_params["use_json_body"] = True
        oauth_config.additional_params["notion_version"] = "2025-09-03"

    return oauth_config
