"""Get the consent URL of a Jira connector that signs in with OAuth.

Completing the flow needs a person to consent in a browser, so this stops at the URL.
"""

import uuid
from urllib.parse import parse_qs, urlparse

from pipeshub_sdk import Pipeshub


def test_handwritten_connector_o_auth_authorization(pipeshub: Pipeshub):
    # OAuth app and connector names are unique in an org.
    name = f"sdk-test-jira-{uuid.uuid4()}"

    config_id = pipeshub.o_auth_configuration.create_o_auth_config(
        connector_type="Jira",
        oauth_instance_name=name,
        config={
            "client_id": "sdk-test-client-id",
            "client_secret": "sdk-test-client-secret",
        },
    ).oauth_config.id
    try:
        connector_id = pipeshub.connector_instances.create_connector_instance(
            connector_type="Jira",
            instance_name=name,
            scope="team",
            auth_type="OAUTH",
            config={"auth": {"oauth_config_id": config_id}},
        ).connector.connector_id
        try:
            authorization = pipeshub.connector_o_auth.get_o_auth_authorization_url(
                connector_id=connector_id
            )
            url = urlparse(authorization.authorization_url)
            assert f"{url.scheme}://{url.netloc}" == "https://auth.atlassian.com"
            assert parse_qs(url.query)["client_id"] == ["sdk-test-client-id"]
            assert authorization.state
        finally:
            pipeshub.connector_instances.delete_connector_instance(
                connector_id=connector_id
            )
    finally:
        pipeshub.o_auth_configuration.delete_o_auth_config(
            connector_type="Jira", config_id=config_id
        )
