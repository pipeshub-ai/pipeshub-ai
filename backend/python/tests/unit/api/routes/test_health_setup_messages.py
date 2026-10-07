"""What the AI model setup dialog shows when a check fails."""

from app.api.routes.health import (
    EMBEDDING_UNAVAILABLE_MESSAGE,
    _model_setup_failed_message,
)


def test_names_the_provider_and_the_next_step() -> None:
    assert _model_setup_failed_message("chat model", "openAI") == (
        "Couldn't connect to OpenAI with these chat model settings. "
        "Check the API key, model name and endpoint, then try again."
    )


def test_unknown_provider_ids_are_shown_as_given() -> None:
    assert _model_setup_failed_message("image model", "someNewProvider").startswith(
        "Couldn't connect to someNewProvider with these image model settings."
    )


def test_embedding_start_failure_points_to_ai_models() -> None:
    assert EMBEDDING_UNAVAILABLE_MESSAGE == (
        "The embedding model couldn't start. Check it in Workspace > AI Models, then try again."
    )
