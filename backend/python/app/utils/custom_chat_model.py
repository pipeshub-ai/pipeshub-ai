
from typing import (
    Any,
    Dict,
    List,
    Optional,
)

import openai
from langchain_core.language_models.base import LangSmithParams
from langchain_core.utils.utils import from_env, secret_from_env
from langchain_openai.chat_models.base import BaseChatOpenAI
from pydantic import ConfigDict, Field, SecretStr, model_validator
from typing_extensions import Self


class ChatTogether(BaseChatOpenAI):
    
    @property
    def lc_secrets(self) -> Dict[str, str]:
        """A map of constructor argument names to secret ids.

        For example,
            {"together_api_key": "TOGETHER_API_KEY"}
        """
        return {"together_api_key": "TOGETHER_API_KEY"}

    @classmethod
    def get_lc_namespace(cls) -> List[str]:
        """Get the namespace of the langchain object."""
        return ["langchain", "chat_models", "together"]

    @property
    def lc_attributes(self) -> Dict[str, Any]:
        """List of attribute names that should be included in the serialized kwargs.

        These attributes must be accepted by the constructor.
        """
        attributes: Dict[str, Any] = {}

        if self.together_api_base:
            attributes["together_api_base"] = self.together_api_base

        return attributes

    @property
    def _llm_type(self) -> str:
        """Return type of chat model."""
        return "together-chat"

    def _get_ls_params(
        self, stop: Optional[List[str]] = None, **kwargs: Any
    ) -> LangSmithParams:
        """Get the parameters used to invoke the model."""
        params = super()._get_ls_params(stop=stop, **kwargs)
        params["ls_provider"] = "together"
        return params

    model_name: str = Field(
        default="meta-llama/Meta-Llama-3.1-8B-Instruct-Turbo", alias="model"
    )
    """Model name to use."""
    together_api_key: Optional[SecretStr] = Field(
        alias="api_key",
        default_factory=secret_from_env("TOGETHER_API_KEY", default=None),
    )
    """Together AI API key.

    Automatically read from env variable `TOGETHER_API_KEY` if not provided.
    """
    together_api_base: str = Field(
        default_factory=from_env(
            "TOGETHER_API_BASE", default="https://api.together.xyz/v1/"
        ),
        alias="base_url",
    )

    model_config = ConfigDict(
        populate_by_name=True,
    )

    @model_validator(mode="after")
    def validate_environment(self) -> Self:
        """Validate that api key and python package exists in environment."""
        if self.n is not None and self.n < 1:
            raise ValueError("n must be at least 1.")
        if self.n is not None and self.n > 1 and self.streaming:
            raise ValueError("n must be 1 when streaming.")

        client_params: dict = {
            "api_key": (
                self.together_api_key.get_secret_value()
                if self.together_api_key
                else None
            ),
            "base_url": self.together_api_base,
            "timeout": self.request_timeout,
            "default_headers": self.default_headers,
            "default_query": self.default_query,
        }
        if self.max_retries is not None:
            client_params["max_retries"] = self.max_retries

        if not (self.client or None):
            sync_specific: dict = {"http_client": self.http_client}
            self.client = openai.OpenAI(
                **client_params, **sync_specific
            ).chat.completions
            self.root_client = openai.OpenAI(**client_params, **sync_specific)
        if not (self.async_client or None):
            async_specific: dict = {"http_client": self.http_async_client}
            self.async_client = openai.AsyncOpenAI(
                **client_params, **async_specific
            ).chat.completions
            self.root_async_client = openai.AsyncOpenAI(
                **client_params, **async_specific
            )
        return self