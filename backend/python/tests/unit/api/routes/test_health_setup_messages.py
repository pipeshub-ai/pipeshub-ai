"""What the AI model setup dialog shows when a check fails."""

import pytest
from botocore.exceptions import ClientError

from app.api.routes.health import (
    EMBEDDING_UNAVAILABLE_MESSAGE,
    _model_setup_failed_message,
    _rejected_settings_message,
)
from app.utils.user_errors import provider_error_code

_BEDROCK = {"provider": "bedrock", "configuration": {"provider": "openai"}}


def test_names_the_provider_and_the_next_step() -> None:
    assert _model_setup_failed_message("chat model", "openAI") == (
        "Couldn't connect to OpenAI with these chat model settings. "
        "Check the API key, model name and endpoint, then try again."
    )


def test_unknown_provider_ids_are_shown_as_given() -> None:
    assert _model_setup_failed_message("image model", "someNewProvider").startswith(
        "Couldn't connect to someNewProvider with these image model settings."
    )


def test_bare_bedrock_openai_id_names_the_inference_profiles() -> None:
    message = _rejected_settings_message("invalid_request", "chat model", _BEDROCK, "openai.gpt-6-luna")
    assert "us.openai.gpt-6-luna or global.openai.gpt-6-luna" in message


def test_bedrock_profile_id_rejection_keeps_the_generic_message() -> None:
    message = _rejected_settings_message("invalid_request", "chat model", _BEDROCK, "us.openai.gpt-6-luna")
    assert "inference profile" not in message
    assert "refused these chat model settings" in message


def test_bedrock_openai_access_denied_names_the_default_project() -> None:
    message = _rejected_settings_message("auth_error", "chat model", _BEDROCK, "us.openai.gpt-6-luna")
    assert "bedrock:InvokeModel" in message
    assert "project/default" in message
    assert "API key" not in message


def test_bedrock_claude_access_denied_skips_the_project_note() -> None:
    message = _rejected_settings_message("auth_error", "chat model", _BEDROCK, "us.anthropic.claude-sonnet-5")
    assert "bedrock:InvokeModel" in message
    assert "project/default" not in message


def _access_denied(message: str) -> ClientError:
    return ClientError(
        {
            "Error": {"Code": "AccessDeniedException", "Message": message},
            "ResponseMetadata": {"HTTPStatusCode": 403},
        },
        "Converse",
    )


_IAM_DENIAL = _access_denied(
    "User: arn:aws:iam::111122223333:user/bedrock is not authorized to perform: "
    "bedrock:InvokeModel on resource: arn:aws:bedrock:us-east-1::foundation-model/openai.gpt-6-luna "
    "because no identity-based policy allows the bedrock:InvokeModel action"
)
_ACCOUNT_DENIAL = _access_denied(
    "openai.gpt-6-luna is not available for this account. You can explore other available "
    "models on Amazon Bedrock. For additional access options, contact AWS Sales at "
    "https://aws.amazon.com/contact-us/sales-support/"
)
_SCP_DENIAL = _access_denied(
    "User: arn:aws:iam::111122223333:user/bedrock is not authorized to perform: "
    "bedrock:InvokeModel on resource: arn:aws:bedrock:us-east-1:111122223333:inference-profile/"
    "us.openai.gpt-6-luna with an explicit deny in a service control policy: "
    "arn:aws:organizations::111122223333:policy/o-abc/service_control_policy/p-abc"
)


_MARKETPLACE_DENIAL = _access_denied(
    "Model access is denied due to INVALID_PAYMENT_INSTRUMENT:A valid payment instrument must "
    "be provided.. Your AWS Marketplace subscription for this model cannot be completed at this "
    "time. If you recently fixed this issue, try again after 2 minutes."
)


def test_bedrock_marketplace_denial_points_to_billing() -> None:
    message = _rejected_settings_message(
        "auth_error", "chat model", _BEDROCK, "global.anthropic.claude-sonnet-4-6", _MARKETPLACE_DENIAL,
    )
    assert "payment method" in message
    assert "bedrock:InvokeModel" not in message


def test_bedrock_unrecognised_denial_is_not_called_an_iam_problem() -> None:
    message = _rejected_settings_message(
        "auth_error", "chat model", _BEDROCK, "us.amazon.nova-pro-v1:0",
        _access_denied("Something new went wrong"),
    )
    assert "Bedrock denied access" in message
    assert "bedrock:InvokeModel" not in message


def test_bedrock_iam_denial_names_the_foundation_model_resource() -> None:
    message = _rejected_settings_message(
        "auth_error", "chat model", _BEDROCK, "us.openai.gpt-6-luna", _IAM_DENIAL,
    )
    assert "foundation-model" in message
    assert "project/default" in message


def test_bedrock_model_not_enabled_for_account_points_to_aws_support() -> None:
    message = _rejected_settings_message(
        "auth_error", "chat model", _BEDROCK, "us.openai.gpt-6-luna", _ACCOUNT_DENIAL,
    )
    assert "AWS Support" in message
    assert "IAM permissions can't fix" in message
    assert "bedrock:InvokeModel" not in message


def test_bedrock_scp_denial_points_to_the_organizations_admin() -> None:
    message = _rejected_settings_message(
        "auth_error", "chat model", _BEDROCK, "us.openai.gpt-6-luna", _SCP_DENIAL,
    )
    assert "service control policy" in message
    assert "bedrock:InvokeModel" not in message


def test_bedrock_denial_cause_is_read_through_a_wrapping_exception() -> None:
    try:
        try:
            raise _ACCOUNT_DENIAL
        except ClientError as inner:
            raise RuntimeError("Converse call failed") from inner
    except RuntimeError as outer:
        message = _rejected_settings_message(
            "auth_error", "chat model", _BEDROCK, "us.openai.gpt-6-luna", outer,
        )
    assert "AWS Support" in message


def _credential_failure(code: str, message: str) -> ClientError:
    return ClientError(
        {"Error": {"Code": code, "Message": message}, "ResponseMetadata": {"HTTPStatusCode": 403}},
        "Converse",
    )


@pytest.mark.parametrize(
    ("code", "message"),
    (
        ("UnrecognizedClientException", "The security token included in the request is invalid."),
        ("InvalidSignatureException", "The request signature we calculated does not match the signature you provided."),
        ("ExpiredTokenException", "The security token included in the request is expired"),
    ),
)
def test_bedrock_bad_credentials_tell_the_admin_to_check_the_key(code: str, message: str) -> None:
    error = _credential_failure(code, message)
    assert provider_error_code(error) == "auth_error"
    text = _rejected_settings_message("auth_error", "chat model", _BEDROCK, "us.anthropic.claude-sonnet-5", error)
    assert "credentials" in text
    assert "access key" in text
    assert "billing" not in text
    assert "Marketplace" not in text
    assert "bedrock:InvokeModel" not in text


def test_bedrock_iam_denial_names_the_streaming_action_too() -> None:
    message = _rejected_settings_message(
        "auth_error", "chat model", _BEDROCK, "us.anthropic.claude-sonnet-5", _IAM_DENIAL,
    )
    assert "bedrock:InvokeModel " in message
    assert "bedrock:InvokeModelWithResponseStream" in message


def test_bedrock_not_authorized_400_names_the_iam_actions() -> None:
    error = ClientError(
        {
            "Error": {"Code": "NotAuthorized", "Message": "You do not have permission to perform this action."},
            "ResponseMetadata": {"HTTPStatusCode": 400},
        },
        "Converse",
    )
    code = provider_error_code(error)
    assert code == "auth_error"
    message = _rejected_settings_message(code, "chat model", _BEDROCK, "us.anthropic.claude-sonnet-5", error)
    assert "bedrock:InvokeModelWithResponseStream" in message
    assert "refused these chat model settings" not in message


def test_bedrock_access_denied_is_classified_as_auth_error() -> None:
    assert provider_error_code(_ACCOUNT_DENIAL) == "auth_error"


def test_embedding_start_failure_points_to_ai_models() -> None:
    assert EMBEDDING_UNAVAILABLE_MESSAGE == (
        "The embedding model couldn't start. Check it in Workspace > AI Models, then try again."
    )
