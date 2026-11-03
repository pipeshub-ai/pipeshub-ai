"""Requests the server refuses, and the status each one gets."""

import os

from pipeshub_sdk import Pipeshub

from .conftest import bearer_client
from .errors import raises_status

# IDs no record, connector, agent or conversation has.
NO_UUID = "00000000-0000-4000-8000-000000000000"
NO_OBJECT_ID = "000000000000000000000000"


def test_handwritten_error_responses(pipeshub: Pipeshub):
    with raises_status(404):
        pipeshub.conversations.get_conversation_by_id(conversation_id=NO_OBJECT_ID)
    with raises_status(404):
        pipeshub.conversations.pin_conversation(conversation_id=NO_OBJECT_ID)
    with raises_status(404):
        pipeshub.conversations.add_message(
            conversation_id=NO_OBJECT_ID, query="sdk-test"
        )
    with raises_status(404):
        pipeshub.conversations.fork_conversation(
            conversation_id=NO_OBJECT_ID, message_id=NO_OBJECT_ID
        )
    with raises_status(400):
        pipeshub.conversations.cancel_conversation_stream(
            conversation_id=NO_OBJECT_ID, run_id="sdk-test-not-a-uuid"
        )
    with raises_status(404):
        pipeshub.agents.get_agent(agent_key=NO_UUID)

    # DELETE /search ignores its search filter and deletes all of the user's history, which the
    # other suites share, so only a request it refuses before deleting anything is sent.
    with raises_status(400):
        pipeshub.semantic_search.delete_search_history(
            search="<script>sdk-test</script>"
        )

    with raises_status(404):
        pipeshub.knowledge_base.get_record_by_id(record_id=NO_UUID)
    with raises_status(403):
        pipeshub.connector.get_record_content(record_id=NO_UUID)
    # ENABLE_SOFT_DELETE is off on the CI app (the default), so the trash refuses.
    with raises_status(403):
        pipeshub.knowledge_base.list_trash(kb_id=NO_UUID)
    with raises_status(403):
        pipeshub.knowledge_base.restore_record(record_id=NO_UUID)
    with raises_status(404):
        pipeshub.knowledge_base.reindex_record_group(record_group_id=NO_UUID, depth=0)
    with raises_status(404):
        pipeshub.knowledge_hub.get_knowledge_hub_child_nodes(
            parent_type="recordGroup", parent_id=NO_UUID
        )
    with raises_status(400):
        pipeshub.connector.navigate_knowledge_graph(limit=10)

    with raises_status(404):
        pipeshub.connector_registry.get_connector_schema(
            connector_type="sdk-test-no-such-type"
        )
    with raises_status(400):
        pipeshub.connector_registry.test_connector_connection(
            connector_type="RSS", auth={}
        )
    with raises_status(404):
        pipeshub.connector_instances.get_connector_instance(connector_id=NO_UUID)
    with raises_status(404):
        pipeshub.connector_instances.delete_connector_instance(connector_id=NO_UUID)
    with raises_status(404):
        pipeshub.o_auth_configuration.get_o_auth_connector_type(connector_type="RSS")
    with raises_status(404):
        pipeshub.o_auth_configuration.get_o_auth_config(
            connector_type="Jira", config_id=NO_UUID
        )

    with raises_status(401):
        pipeshub.o_auth_provider.oauth_token(
            grant_type="client_credentials",
            client_id=os.environ["PIPESHUB_CLIENT_ID"],
            client_secret="sdk-test-wrong-secret",
        )


def test_handwritten_session_error_responses(session: Pipeshub):
    with raises_status(404):
        session.o_auth_apps.get_o_auth_app(app_id=NO_OBJECT_ID)
    with raises_status(404):
        session.personal_access_tokens.revoke_personal_access_token(
            token_id=NO_OBJECT_ID
        )
    with raises_status(404):
        session.personal_access_tokens.admin_revoke_personal_access_token(
            token_id=NO_OBJECT_ID
        )
    with raises_status(400):
        session.personal_access_tokens.create_personal_access_token(
            name="sdk-test-pat-bad-scope", scopes=["sdk-test:no-such-scope"]
        )
    # userinfo needs a token that carries `openid`; a session JWT carries no scopes.
    with raises_status(401):
        session.open_id_connect.oauth_user_info()
    # Only bodies the server refuses: a real change would alter how the other suites sign in.
    with raises_status(400):
        session.organization_auth_config.update_auth_method(auth_method=[])
    with raises_status(400):
        session.organization_auth_config.update_auth_method(
            auth_method=[
                {
                    "order": 1,
                    "allowed_methods": [{"type": "password"}, {"type": "password"}],
                }
            ]
        )
    with raises_status(401):
        session.user_account.reset_password(
            current_password="sdk-test-wrong-current-password",
            new_password="Sdk-test-unused-password-1",
        )

    with raises_status(400):
        session.user_account.init_auth(request={"email": "sdk-test-not-an-email"})
    with raises_status(401):
        session.user_account.authenticate(
            x_session_token="sdk-test-invalid-session",
            method="password",
            credentials={"password": "sdk-test-wrong-password"},
        )
    with raises_status(401):
        session.user_account.refresh_token(
            security={"scoped_token": "sdk-test-invalid-refresh-token"}
        )

    # A personal access token works on the API but not on the routes that manage tokens and apps.
    created = session.personal_access_tokens.create_personal_access_token(
        name="sdk-test-pat-refused", scopes=["openid", "kb:read"], expiry_days=30
    ).token
    try:
        with bearer_client(created.access_token) as pat:
            with raises_status(403):
                pat.o_auth_apps.list_o_auth_apps()
            with raises_status(403):
                pat.personal_access_tokens.create_personal_access_token(name="sdk-test")
    finally:
        session.personal_access_tokens.revoke_personal_access_token(token_id=created.id)
    with bearer_client(created.access_token) as pat, raises_status(401):
        pat.open_id_connect.oauth_user_info()
