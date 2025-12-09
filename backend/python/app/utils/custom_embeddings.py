from __future__ import annotations

import json
import logging
from typing import (
    Any,
    Callable,
    Dict,
    List,
    Optional,
    Tuple,
    Union,
    cast,
)

import requests
from langchain_core.embeddings import Embeddings
from langchain_core.utils import convert_to_secret_str, get_from_dict_or_env
from pydantic import BaseModel, ConfigDict, SecretStr, model_validator
from tenacity import (
    before_sleep_log,
    retry,
    stop_after_attempt,
    wait_exponential,
)

from app.utils.logger import create_logger

logger = create_logger("custom_embeddings")


def _create_retry_decorator(embeddings: VoyageEmbeddings) -> Callable[[Any], Any]:
    min_seconds = 4
    max_seconds = 10
    # Wait 2^x * 1 second between each retry starting with
    # 4 seconds, then up to 10 seconds, then 10 seconds afterwards
    return retry(
        reraise=True,
        stop=stop_after_attempt(embeddings.max_retries),
        wait=wait_exponential(multiplier=1, min=min_seconds, max=max_seconds),
        before_sleep=before_sleep_log(logger, logging.WARNING),
    )


def _check_response(response: dict) -> dict:
    if "data" not in response:
        raise RuntimeError(f"Voyage API Error. Message: {json.dumps(response)}")
    return response


def embed_with_retry(embeddings: VoyageEmbeddings, **kwargs: dict[str, Any]) -> dict[str, Any]:
    """Use tenacity to retry the embedding call."""
    retry_decorator = _create_retry_decorator(embeddings)

    @retry_decorator
    def _embed_with_retry(**kwargs: dict[str, Any]) -> dict[str, Any]:
        response = requests.post(**kwargs)
        return _check_response(response.json())

    return _embed_with_retry(**kwargs)



class VoyageEmbeddings(BaseModel, Embeddings):
    """Voyage embedding models.

    To use, you should have the environment variable ``VOYAGE_API_KEY`` set with
    your API key or pass it as a named parameter to the constructor.

    Example:
        .. code-block:: python

            from langchain_community.embeddings import VoyageEmbeddings

            voyage = VoyageEmbeddings(voyage_api_key="your-api-key", model="voyage-2")
            text = "This is a test query."
            query_result = voyage.embed_query(text)
    """

    model: str
    voyage_api_base: str = "https://api.voyageai.com/v1/embeddings"
    voyage_api_key: Optional[SecretStr] = None
    batch_size: int
    """Maximum number of texts to embed in each API request."""
    max_retries: int = 6
    """Maximum number of retries to make when generating."""
    request_timeout: Optional[Union[float, Tuple[float, float]]] = None
    """Timeout in seconds for the API request."""
    show_progress_bar: bool = False
    """Whether to show a progress bar when embedding. Must have tqdm installed if set
        to True."""
    truncation: bool = True
    """Whether to truncate the input texts to fit within the context length.

        If True, over-length input texts will be truncated to fit within the context
        length, before vectorized by the embedding model. If False, an error will be
        raised if any given text exceeds the context length."""

    model_config = ConfigDict(
        extra="forbid",
    )

    @model_validator(mode="before")
    @classmethod
    def validate_environment(cls, values: Dict) -> Dict:
        """Validate that api key and python package exists in environment."""
        values["voyage_api_key"] = convert_to_secret_str(
            get_from_dict_or_env(values, "voyage_api_key", "VOYAGE_API_KEY")
        )

        if "model" not in values:
            values["model"] = "voyage-01"
            logger.warning(
                "model will become a required arg for VoyageAIEmbeddings, "
                "we recommend to specify it when using this class. "
                "Currently the default is set to voyage-01."
            )

        if "batch_size" not in values:
            values["batch_size"] = (
                72
                if "model" in values and (values["model"] in ["voyage-2", "voyage-02"])
                else 7
            )

        return values

    def _invocation_params(
        self, input: List[str], input_type: Optional[str] = None
    ) -> Dict:
        api_key = cast(SecretStr, self.voyage_api_key).get_secret_value()
        model = self.model
        if model == "voyage-multimodal-3":
            logger.debug("Using voyage-multimodal-3 multimodal embeddings endpoint")
            url = "https://api.voyageai.com/v1/multimodalembeddings"
            inputs =[]
            for text in input:
                if text.startswith("data:image"):
                    inputs.append({
                        "content" : [{
                            "type": "image_base64",
                            "image_base64": text
                        }]
                    })
                else:
                    inputs.append({
                        "content" : [{
                            "type": "text",
                            "text": text
                        }]
                    })
            json_data = {
                "model": model,
                "inputs": inputs,
            }
        else:
            logger.debug("Using voyage standard embeddings endpoint")
            url = self.voyage_api_base
            json_data = {
                "model": model,
                "input": input,
                "input_type": input_type,
                "truncation": self.truncation,
            }
        params: Dict = {
            "url": url,
            "headers": {"Authorization": f"Bearer {api_key}"},
            "json": json_data,
            "timeout": self.request_timeout,
        }
        return params

    def _get_embeddings(
        self,
        texts: List[str],
        batch_size: Optional[int] = None,
        input_type: Optional[str] = None,
    ) -> List[List[float]]:
        embeddings: List[List[float]] = []

        if batch_size is None:
            batch_size = self.batch_size

        if self.show_progress_bar:
            try:
                from tqdm.auto import tqdm
            except ImportError as e:
                raise ImportError(
                    "Must have tqdm installed if `show_progress_bar` is set to True. "
                    "Please install with `pip install tqdm`."
                ) from e

            _iter = tqdm(range(0, len(texts), batch_size))
        else:
            _iter = range(0, len(texts), batch_size)

        if input_type and input_type not in ["query", "document"]:
            raise ValueError(
                f"input_type {input_type} is invalid. Options: None, 'query', "
                "'document'."
            )

        for i in _iter:
            response = embed_with_retry(
                self,
                **self._invocation_params(
                    input=texts[i : i + batch_size], input_type=input_type
                ),
            )
            embeddings.extend(r["embedding"] for r in response["data"])

        return embeddings

    def embed_documents(self, texts: List[str]) -> List[List[float]]:
        """Call out to Voyage Embedding endpoint for embedding search docs.

        Args:
            texts: The list of texts to embed.

        Returns:
            List of embeddings, one for each text.
        """
        return self._get_embeddings(
            texts, batch_size=self.batch_size, input_type="document"
        )

    def embed_query(self, text: str) -> List[float]:
        """Call out to Voyage Embedding endpoint for embedding query text.

        Args:
            text: The text to embed.

        Returns:
            Embedding for the text.
        """
        return self._get_embeddings(
            [text], batch_size=self.batch_size, input_type="query"
        )[0]

    def embed_general_texts(
        self, texts: List[str], *, input_type: Optional[str] = None
    ) -> List[List[float]]:
        """Call out to Voyage Embedding endpoint for embedding general text.

        Args:
            texts: The list of texts to embed.
            input_type: Type of the input text. Default to None, meaning the type is
                unspecified. Other options: query, document.

        Returns:
            Embedding for the text.
        """
        return self._get_embeddings(
            texts, batch_size=self.batch_size, input_type=input_type
        )

import warnings
from typing import (
    Any,
    Dict,
    List,
    Literal,
    Mapping,
    Optional,
    Sequence,
    Set,
    Tuple,
    Union,
)

import openai
from langchain_core.utils.utils import from_env, get_pydantic_field_names, secret_from_env

from pydantic import (
    ConfigDict,
    Field,
    SecretStr,
    model_validator,
)
from typing_extensions import Self

class TogetherEmbeddings(BaseModel, Embeddings):
   
    client: Any = Field(default=None, exclude=True)  #: :meta private:
    async_client: Any = Field(default=None, exclude=True)  #: :meta private:
    model: str = "BAAI/bge-base-en-v1.5"
    """Embeddings model name to use.
    """
    dimensions: Optional[int] = None
    """The number of dimensions the resulting output embeddings should have.

    Not yet supported.
    """
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
    """Endpoint URL to use."""
    embedding_ctx_length: int = 4096
    """The maximum number of tokens to embed at once.

    Not yet supported.
    """
    allowed_special: Union[Literal["all"], Set[str]] = set()
    """Not yet supported."""
    disallowed_special: Union[Literal["all"], Set[str], Sequence[str]] = "all"
    """Not yet supported."""
    chunk_size: int = 1000
    """Maximum number of texts to embed in each batch.

    Not yet supported.
    """
    max_retries: int = 2
    """Maximum number of retries to make when generating."""
    request_timeout: Optional[Union[float, Tuple[float, float], Any]] = Field(
        default=None, alias="timeout"
    )
    """Timeout for requests to Together embedding API. Can be float, httpx.Timeout or
        None."""
    show_progress_bar: bool = False
    """Whether to show a progress bar when embedding.

    Not yet supported.
    """
    model_kwargs: Dict[str, Any] = Field(default_factory=dict)
    """Holds any model parameters valid for `create` call not explicitly specified."""
    skip_empty: bool = False
    """Whether to skip empty strings when embedding or raise an error.
    Defaults to not skipping.

    Not yet supported."""
    default_headers: Union[Mapping[str, str], None] = None
    default_query: Union[Mapping[str, object], None] = None
    # Configure a custom httpx client. See the
    # [httpx documentation](https://www.python-httpx.org/api/#client) for more details.
    http_client: Union[Any, None] = None
    """Optional httpx.Client. Only used for sync invocations. Must specify
        http_async_client as well if you'd like a custom client for async invocations.
    """
    http_async_client: Union[Any, None] = None
    """Optional httpx.AsyncClient. Only used for async invocations. Must specify
        http_client as well if you'd like a custom client for sync invocations."""

    model_config = ConfigDict(
        extra="forbid",
        populate_by_name=True,
        protected_namespaces=(),
    )

    @model_validator(mode="before")
    @classmethod
    def build_extra(cls, values: Dict[str, Any]) -> Any:
        """Build extra kwargs from additional params that were passed in."""
        all_required_field_names = get_pydantic_field_names(cls)
        extra = values.get("model_kwargs", {})
        for field_name in list(values):
            if field_name in extra:
                raise ValueError(f"Found {field_name} supplied twice.")
            if field_name not in all_required_field_names:
                warnings.warn(
                    f"""WARNING! {field_name} is not default parameter.
                    {field_name} was transferred to model_kwargs.
                    Please confirm that {field_name} is what you intended."""
                )
                extra[field_name] = values.pop(field_name)

        invalid_model_kwargs = all_required_field_names.intersection(extra.keys())
        if invalid_model_kwargs:
            raise ValueError(
                f"Parameters {invalid_model_kwargs} should be specified explicitly. "
                f"Instead they were passed in as part of `model_kwargs` parameter."
            )

        values["model_kwargs"] = extra
        return values

    @model_validator(mode="after")
    def post_init(self) -> Self:
        """Logic that will post Pydantic initialization."""
        client_params: dict = {
            "api_key": (
                self.together_api_key.get_secret_value()
                if self.together_api_key
                else None
            ),
            "base_url": self.together_api_base,
            "timeout": self.request_timeout,
            "max_retries": self.max_retries,
            "default_headers": self.default_headers,
            "default_query": self.default_query,
        }
        if not (self.client or None):
            sync_specific: dict = (
                {"http_client": self.http_client} if self.http_client else {}
            )
            self.client = openai.OpenAI(**client_params, **sync_specific).embeddings
        if not (self.async_client or None):
            async_specific: dict = (
                {"http_client": self.http_async_client}
                if self.http_async_client
                else {}
            )
            self.async_client = openai.AsyncOpenAI(
                **client_params, **async_specific
            ).embeddings
        return self

    @property
    def _invocation_params(self) -> Dict[str, Any]:
        params: Dict = {"model": self.model, **self.model_kwargs}
        if self.dimensions is not None:
            params["dimensions"] = self.dimensions
        return params

    def embed_documents(self, texts: List[str]) -> List[List[float]]:
        """Embed a list of document texts using passage model.

        Args:
            texts: The list of texts to embed.

        Returns:
            List of embeddings, one for each text.
        """
        embeddings = []
        params = self._invocation_params
        params["model"] = params["model"]

        for text in texts:
            response = self.client.create(input=text, **params)

            if not isinstance(response, dict):
                response = response.model_dump()
                embeddings.extend([i["embedding"] for i in response["data"]])
        return embeddings

    def embed_query(self, text: str) -> List[float]:
        """Embed query text using query model.

        Args:
            text: The text to embed.

        Returns:
            Embedding for the text.
        """
        params = self._invocation_params
        params["model"] = params["model"]

        response = self.client.create(input=text, **params)

        if not isinstance(response, dict):
            response = response.model_dump()
        return response["data"][0]["embedding"]

    async def aembed_documents(self, texts: List[str]) -> List[List[float]]:
        """Embed a list of document texts using passage model asynchronously.

        Args:
            texts: The list of texts to embed.

        Returns:
            List of embeddings, one for each text.
        """
        embeddings = []
        params = self._invocation_params
        params["model"] = params["model"]

        for text in texts:
            response = await self.async_client.create(input=text, **params)

            if not isinstance(response, dict):
                response = response.model_dump()
                embeddings.extend([i["embedding"] for i in response["data"]])
        return embeddings

    async def aembed_query(self, text: str) -> List[float]:
        """Asynchronous Embed query text using query model.

        Args:
            text: The text to embed.

        Returns:
            Embedding for the text.
        """
        params = self._invocation_params
        params["model"] = params["model"]

        response = await self.async_client.create(input=text, **params)

        if not isinstance(response, dict):
            response = response.model_dump()
        return response["data"][0]["embedding"]