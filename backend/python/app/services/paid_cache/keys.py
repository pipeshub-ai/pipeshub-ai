"""Cache keys of paid model results (a pure function of content, org first).

``digest = sha256("v1|" + "|".join(fields))`` over, in this order: org, record, kind, kind version, model
identity, prompt digest, params digest, input digest. The path is
``{org}/PipesHub/paid-results/{record}/{kind}/{digest}.json``. Retries, replays, replica races and
re-updates of one record all compute the same key; a model, prompt or params change computes another.
"""

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

KEY_VERSION = "v1"

KIND_OCR_PAGE = "ocr_page"
KIND_IMAGE_DESCRIPTION = "image_description"
KIND_TABLE_SUMMARY = "table_summary"
KIND_TABLE_ROWS = "table_rows"
KIND_EXCEL_HEADERS = "excel_headers"
KIND_CLASSIFY = "classify"
KIND_ENTITIES = "entities"

#: A new kind is a change to the storage routes' path rule as well.
KINDS = frozenset({
    KIND_OCR_PAGE, KIND_IMAGE_DESCRIPTION, KIND_TABLE_SUMMARY, KIND_TABLE_ROWS,
    KIND_EXCEL_HEADERS, KIND_CLASSIFY, KIND_ENTITIES,
})


#: What the storage routes accept as a record id (``PaidResultRecordId``). Record ids are uuid4 today.
_RECORD_ID = re.compile(r"^[A-Za-z0-9_-]{1,128}$")


#: Routed to the whole-org purge by the storage routes (``DELETE .../paid-results/org``), so never a record id.
RESERVED_RECORD_ID = "org"


def valid_record_id(record_id: str) -> bool:
    """False for an id the storage routes refuse: nothing can be stored under it, so a delete has nothing to do."""
    return bool(_RECORD_ID.fullmatch(record_id)) and record_id != RESERVED_RECORD_ID


def sha256_hex(data: bytes | str) -> str:
    raw = data.encode("utf-8") if isinstance(data, str) else data
    return hashlib.sha256(raw).hexdigest()


def prompt_digest(rendered_prompt: str) -> str:
    """Digest of the prompt text actually sent, so editing a template changes the key."""
    return sha256_hex(rendered_prompt)


def params_digest(params: Mapping[str, Any]) -> str:
    """Digest of the canonical invoke params (temperature, max tokens, reasoning effort, extra body, DPI ...).

    ``None`` values are dropped, so a param left unset and one passed as ``None`` are the same call.
    """
    canonical = json.dumps(
        {k: v for k, v in params.items() if v is not None},
        sort_keys=True, separators=(",", ":"), ensure_ascii=True, default=str,
    )
    return sha256_hex(canonical)


def model_identity(llm: object) -> str:
    """Provider class, model and endpoint host and port. Never a credential, a path or a query."""
    model = (
        getattr(llm, "model_name", None) or getattr(llm, "model", None) or getattr(llm, "model_id", None)
        or getattr(llm, "deployment_name", None) or "?"
    )
    endpoint = (
        getattr(llm, "openai_api_base", None) or getattr(llm, "base_url", None)
        or getattr(llm, "azure_endpoint", None) or getattr(llm, "endpoint_url", None) or ""
    )
    host = ""
    if endpoint:
        try:
            parts = urlsplit(str(endpoint))
            # The port is part of the identity: two local servers on one host serve different deployments.
            host = (parts.hostname or "") + (f":{parts.port}" if parts.hostname and parts.port else "")
        except ValueError:
            host = ""
    return f"{type(llm).__name__}:{model}@{host}" if host else f"{type(llm).__name__}:{model}"


#: The model attributes that change what a call returns. Read when set, so an unset one adds nothing.
_LLM_PARAM_ATTRS = (
    "temperature", "top_p", "max_tokens", "max_output_tokens", "max_completion_tokens",
    "reasoning_effort", "reasoning", "thinking", "extra_body", "model_kwargs", "seed",
)


def llm_params(llm: object, **invoke_params: Any) -> dict[str, Any]:  # noqa: ANN401 - a JSON value, model response or caller callable of any shape
    """The params a call goes out with: what the model carries, overridden by the call's own."""
    params: dict[str, Any] = {}
    for attr in _LLM_PARAM_ATTRS:
        value = getattr(llm, attr, None)
        if value is not None and not callable(value):
            params[attr] = value
    params.update({k: v for k, v in invoke_params.items() if v is not None})
    return params


def _field(value: object) -> str:
    # The separator is the only character that could make two field lists read alike.
    return str(value).replace("%", "%25").replace("|", "%7C")


@dataclass(frozen=True, slots=True)
class CacheKey:
    org_id: str
    record_id: str
    kind: str
    kind_version: int
    model_identity: str
    prompt_digest: str
    params_digest: str
    input_digest: str

    def __post_init__(self) -> None:
        if self.kind not in KINDS:
            raise ValueError(f"unknown paid-result kind {self.kind!r}")
        if not self.org_id or not self.record_id:
            raise ValueError("a paid-result key needs an org and a record")

    def digest(self) -> str:
        fields = (
            self.org_id, self.record_id, self.kind, self.kind_version, self.model_identity,
            self.prompt_digest, self.params_digest, self.input_digest,
        )
        return sha256_hex(f"{KEY_VERSION}|" + "|".join(_field(f) for f in fields))

    def relative_path(self) -> str:
        """The file inside the record's folder: the path storage's routes take."""
        return f"{self.kind}/{self.digest()}.json"

    def path(self) -> str:
        return f"{self.org_id}/PipesHub/paid-results/{self.record_id}/{self.relative_path()}"


def template_text(prompt: object) -> str:
    """The text of a prompt template, so editing it changes the key.

    A string is its own text. A LangChain chat prompt contributes each message template's text; anything
    else falls back to its repr, which still changes when the template does.
    """
    if isinstance(prompt, str):
        return prompt
    messages = getattr(prompt, "messages", None)
    if isinstance(messages, (list, tuple)):
        parts: list[str] = []
        for message in messages:
            inner = getattr(message, "prompt", None)
            text = getattr(inner, "template", None)
            parts.append(text if isinstance(text, str) else repr(message))
        return "\n".join(parts)
    text = getattr(prompt, "template", None)
    return text if isinstance(text, str) else repr(prompt)
