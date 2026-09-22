"""What the AI model setup dialog shows when a check fails."""

from app.api.routes.health import (
    _embedding_unavailable_message,
    _model_setup_failed_message,
    _short_provider_reason,
)


class _ProviderError(Exception):
    def __init__(self, text: str) -> None:
        super().__init__(text)
        self.body = {"error": {"message": text}}


def test_names_the_provider_and_keeps_its_readable_reason() -> None:
    assert _model_setup_failed_message("chat model", "openAI", _ProviderError("Incorrect API key provided.")) == (
        "Couldn't connect to OpenAI with these chat model settings: Incorrect API key provided. "
        "Check the API key, model name and endpoint, then try again."
    )


def test_drops_reasons_that_are_our_own_exceptions_leaking() -> None:
    for leaked in (KeyError("llm"), AttributeError("'NoneType' object has no attribute 'invoke'")):
        assert _short_provider_reason(leaked) == ""
    assert _model_setup_failed_message("embedding model", "azureOpenAI", KeyError("llm")) == (
        "Couldn't connect to Azure OpenAI with these embedding model settings. "
        "Check the API key, model name and endpoint, then try again."
    )


def test_keeps_only_the_first_line_and_caps_length() -> None:
    assert _short_provider_reason(RuntimeError("model not found\nTraceback (most recent call last): ...")) == (
        "model not found"
    )
    assert len(_short_provider_reason(RuntimeError("x" * 500))) == 200


def test_unknown_provider_ids_are_shown_as_given() -> None:
    assert _model_setup_failed_message("image model", "someNewProvider", RuntimeError("quota exceeded")).startswith(
        "Couldn't connect to someNewProvider with these image model settings: quota exceeded."
    )


def test_embedding_start_failure_points_to_ai_models() -> None:
    assert _embedding_unavailable_message(None) == (
        "The embedding model couldn't start. Check it in Workspace > AI Models, then try again."
    )
    assert _embedding_unavailable_message(RuntimeError("Connection refused")) == (
        "The embedding model couldn't start: Connection refused. Check it in Workspace > AI Models, then try again."
    )
