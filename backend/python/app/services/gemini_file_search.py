"""Gemini File Search multimodal retrieval service.

This feature lets Pipe augment its own vector search with Google's
multimodal File Search (text + images via ``gemini-embedding-2``). It is
controlled by a global Settings toggle and reuses the Gemini API key the
operator already configured on the "Gemini" provider card in
Settings -> AI Models. The key is never read from a hidden env var as the
primary source; env vars are only a last-resort fallback for dev/CI.
"""

from __future__ import annotations

import asyncio
import base64
import json
import mimetypes
import os
import re
import tempfile
import time
from dataclasses import dataclass
from typing import Any

from app.config.configuration_service import ConfigurationService
from app.config.constants.service import config_node_constants
from app.utils.image_utils import get_extension_from_mimetype
from app.utils.logger import create_logger

logger = create_logger(__name__)

_GEMINI_MARKDOWN_LINK_RE = re.compile(r"\[([^\]]+)\]\((?:https?://|/)[^)]+\)")


def sanitize_gemini_lead_answer(text: str | None) -> str:
    """Remove Gemini-generated markdown links before the final citation pass.

    The app already exposes first-class citation refs for Gemini File Search
    matches. Raw Gemini links can point at non-citation routes and bypass the
    normal citation chip renderer, so keep the visible label and drop the URL.
    """
    if not text:
        return ""
    return _GEMINI_MARKDOWN_LINK_RE.sub(lambda match: match.group(1), text).strip()


FALSE_VALUES = {"0", "false", "no", "off", "disabled"}
# Mime types Gemini File Search can index, per Gemini's published
# "Supported file types" (Application, Text, and Image sections). .docx
# (wordprocessingml.document) IS supported - STATE_FAILED for a given file
# means that specific document failed processing (e.g. oversized embedded
# images, corrupt content, or a transient Gemini error), not that the format
# is unsupported. Image support is intentionally limited to the documented
# File Search image formats. The indexing hook logs the state so failures are visible.
SUPPORTED_MIME_TYPES = {
    "application/pdf",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "image/png",
    "image/jpeg",
    "text/plain",
    "text/markdown",
    "text/html",
}

# KV config node holding the global File Search settings (enable flag + models).
# Mirrors how /services/aiModels and /services/webSearch are stored.
_FILE_SEARCH_CONFIG_NODE = "/services/geminiFileSearch"

# Default model values (used when neither the DB config nor an env override
# is present). Kept identical to the previous behaviour so nothing changes
# for existing deployments until an admin sets the DB toggle.
_DEFAULT_GENERATION_MODEL = "gemini-3.5-flash"
_DEFAULT_EMBEDDING_MODEL = "models/gemini-embedding-2"
_DEFAULT_MAX_STORES_PER_QUERY = 5
_DEFAULT_MAX_MEDIA_PER_QUERY = 5
_MAX_STORES_PER_QUERY = 5
_HTTP_TIMEOUT_MS = 120_000
MAX_FILE_SEARCH_DOCUMENT_BYTES = 100 * 1024 * 1024


@dataclass(frozen=True)
class GeminiFileSearchIndexResult:
    store_name: str
    document_name: str | None
    operation_name: str | None


class GeminiFileSearchDocumentError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        store_name: str,
        document_name: str | None,
    ) -> None:
        super().__init__(message)
        self.store_name = store_name
        self.document_name = document_name


@dataclass(frozen=True)
class GeminiFileSearchAnswer:
    text: str
    citations: list[dict[str, Any]]
    store_names: list[str]
    # Media (image bytes + renderable URL) downloaded for citations that
    # reference an image. Keyed by the media_id Gemini returned. Empty when
    # no media was referenced or downloads were skipped/capped.
    media: dict[str, dict[str, str]]


def _env_flag_enabled(name: str, *, default: bool) -> bool:
    raw_value = os.getenv(name)
    if raw_value is None:
        return default
    return raw_value.strip().lower() not in FALSE_VALUES


def _env_api_key() -> str | None:
    """Last-resort key source: environment variables only.

    Used purely for backward compatibility / local dev / CI where no Gemini
    provider card has been configured in the DB yet.
    """
    return (
        os.getenv("GEMINI_API_KEY")
        or os.getenv("GOOGLE_API_KEY")
        or os.getenv("GOOGLE_GENAI_API_KEY")
    )


def _select_gemini_config(bucket: list[dict] | None) -> dict | None:
    """Pick the active Gemini entry from a model-type bucket.

    Mirrors the selection rule used across the codebase: prefer the entry
    flagged ``isDefault``, falling back to the first entry. Returns ``None``
    when the selected entry is not a Gemini provider or has no key.
    """
    if not bucket:
        return None
    selected = next((c for c in bucket if c.get("isDefault")), bucket[0])
    if str(selected.get("provider", "")).lower() != "gemini":
        return None
    configuration = selected.get("configuration") or {}
    api_key = configuration.get("apiKey") or configuration.get("api_key")
    if not api_key:
        return None
    return {"config": selected, "apiKey": api_key}


class GeminiFileSearchService:
    """Thin async wrapper around the google-genai File Search API.

    Construction is async (use :meth:`create`) because resolving the API key
    requires reading the encrypted KV store.
    """

    def __init__(
        self,
        logger: Any,
        config_service: ConfigurationService | None,
    ) -> None:
        self.logger = logger
        self.config_service = config_service
        # Resolved lazily so a missing config_service (e.g. in unit tests)
        # does not blow up at construction time.
        self._api_key: str | None = None
        self._api_key_resolved = False
        self._key_source = "none"
        # Settings (filled in by :meth:`create` with DB-first / env-fallback).
        self.enabled = False
        self.global_toggle = False
        self.embedding_model = os.getenv(
            "GEMINI_FILE_SEARCH_EMBEDDING_MODEL", _DEFAULT_EMBEDDING_MODEL
        )
        self.generation_model = os.getenv(
            "GEMINI_FILE_SEARCH_MODEL", _DEFAULT_GENERATION_MODEL
        )
        self.poll_interval_seconds = float(
            os.getenv("GEMINI_FILE_SEARCH_POLL_INTERVAL_SECONDS", "5")
        )
        self.poll_timeout_seconds = float(
            os.getenv("GEMINI_FILE_SEARCH_POLL_TIMEOUT_SECONDS", "300")
        )
        self.max_stores_per_query = int(
            os.getenv(
                "GEMINI_FILE_SEARCH_MAX_STORES_PER_QUERY",
                str(_DEFAULT_MAX_STORES_PER_QUERY),
            )
        )
        self.max_media_per_query = int(
            os.getenv(
                "GEMINI_FILE_SEARCH_MAX_MEDIA_PER_QUERY",
                str(_DEFAULT_MAX_MEDIA_PER_QUERY),
            )
        )

    @classmethod
    async def create(
        cls,
        logger: Any,
        config_service: ConfigurationService | None,
    ) -> "GeminiFileSearchService":
        """Async factory: reads DB settings + resolves the API key."""
        service = cls(logger, config_service)
        await service._load_settings()
        return service

    # ------------------------------------------------------------------
    # Settings + key resolution
    # ------------------------------------------------------------------

    async def _load_settings(self) -> None:
        """Populate enable flag + model overrides from the DB, env fallback."""
        # --- Global enable toggle (DB primary, env override) ---
        self.global_toggle = await self._read_db_toggle()
        env_force = os.getenv("GEMINI_FILE_SEARCH_ENABLED")
        if env_force is not None:
            # Operator kill-switch / explicit env override wins.
            self.global_toggle = _env_flag_enabled(
                "GEMINI_FILE_SEARCH_ENABLED", default=self.global_toggle
            )

        # --- Model settings (DB primary, env fallback, hard-coded default) ---
        fs_config = await self._read_db_config() or {}
        self.embedding_model = (
            fs_config.get("embeddingModel")
            or os.getenv("GEMINI_FILE_SEARCH_EMBEDDING_MODEL")
            or _DEFAULT_EMBEDDING_MODEL
        )
        self.generation_model = (
            fs_config.get("generationModel")
            or os.getenv("GEMINI_FILE_SEARCH_MODEL")
            or _DEFAULT_GENERATION_MODEL
        )
        self.max_stores_per_query = min(
            max(
                int(
                    fs_config.get("maxStoresPerQuery")
                    or os.getenv("GEMINI_FILE_SEARCH_MAX_STORES_PER_QUERY")
                    or _DEFAULT_MAX_STORES_PER_QUERY
                ),
                1,
            ),
            _MAX_STORES_PER_QUERY,
        )
        self.max_media_per_query = int(
            fs_config.get("maxMediaPerQuery")
            or os.getenv("GEMINI_FILE_SEARCH_MAX_MEDIA_PER_QUERY")
            or _DEFAULT_MAX_MEDIA_PER_QUERY
        )

        # --- API key (DB primary, env last-resort fallback) ---
        await self._resolve_api_key()

        # Enabled = toggle on AND a key is available.
        self.enabled = bool(self.global_toggle and self._api_key)
        if self.enabled:
            self.logger.info(
                "Gemini File Search enabled (generation=%s, embedding=%s, "
                "maxStores=%d, maxMedia=%d, keySource=%s)",
                self.generation_model,
                self.embedding_model,
                self.max_stores_per_query,
                self.max_media_per_query,
                self._key_source,
            )

    async def _read_db_config(self) -> dict[str, Any] | None:
        if not self.config_service:
            return None
        try:
            return await self.config_service.get_config(
                _FILE_SEARCH_CONFIG_NODE, use_cache=False
            )
        except Exception:
            self.logger.debug(
                "Gemini File Search DB config not present yet", exc_info=True
            )
            return None

    async def _read_db_toggle(self) -> bool:
        config = await self._read_db_config()
        if not config:
            # Fresh install: default off. Auto-enable only if an env key is
            # set (preserves the previous auto-enable-on-env-key behaviour
            # for dev/CI without surprising production installs).
            return bool(_env_api_key())
        return bool(config.get("enabled"))

    async def _resolve_api_key(self) -> None:
        """Resolve the Gemini API key: DB (Gemini card) first, env fallback."""
        self._key_source = "none"
        if not self.config_service:
            self._api_key = _env_api_key()
            self._api_key_resolved = True
            if self._api_key:
                self._key_source = "env"
            return

        try:
            ai_models = await self.config_service.get_config(
                config_node_constants.AI_MODELS.value, use_cache=False
            )
        except Exception:
            ai_models = None

        # The same AIza key is used for every Gemini capability. Prefer the
        # embedding bucket (File Search is embedding-centric) then fall back
        # to the LLM bucket if only a Gemini chat model is configured.
        for bucket_name in ("embedding", "llm"):
            hit = _select_gemini_config(
                (ai_models or {}).get(bucket_name) if ai_models else None
            )
            if hit:
                self._api_key = hit["apiKey"]
                self._key_source = f"db:{bucket_name}"
                self._api_key_resolved = True
                return

        # Last resort: env (dev / CI).
        self._api_key = _env_api_key()
        if self._api_key:
            self._key_source = "env"
        self._api_key_resolved = True

    # ------------------------------------------------------------------
    # Public introspection
    # ------------------------------------------------------------------

    def is_enabled(self) -> bool:
        return bool(self.enabled and self._api_key)

    def supports_mime_type(self, mime_type: str | None) -> bool:
        if not mime_type:
            return False
        return mime_type.lower() in SUPPORTED_MIME_TYPES

    @staticmethod
    def escape_filter_value(value: str) -> str:
        """Escape a string literal for Gemini metadata filter syntax."""
        return str(value).replace("\\", "\\\\").replace('"', '\\"')

    def _client(self) -> Any:
        """Create a Gemini client with a bounded request timeout."""
        from google import genai
        from google.genai import types

        return genai.Client(
            api_key=self._api_key,
            http_options=types.HttpOptions(timeout=_HTTP_TIMEOUT_MS),
        )

    # ------------------------------------------------------------------
    # Indexing
    # ------------------------------------------------------------------

    async def ensure_store(self, *, org_id: str, kb_id: str) -> str | None:
        """Create the per-KB store if needed and return its name.

        Store names are globally scoped on Gemini's side, so we derive a
        stable, namespaced name from the org + KB id. ``create`` is
        idempotent-ish: we always create and let callers cache the returned
        name on the record-group node to avoid repeated calls.
        """
        if not self.is_enabled() or not kb_id:
            return None
        return await asyncio.to_thread(self._ensure_store_sync, org_id, kb_id)

    def _ensure_store_sync(self, org_id: str, kb_id: str) -> str:
        client = self._client()
        display_name = f"pipeshub-{org_id}-kb-{kb_id}"
        try:
            for store in client.file_search_stores.list():
                if getattr(store, "display_name", None) == display_name:
                    return store.name
        except Exception:
            self.logger.debug(
                "Could not list Gemini stores before creating %s",
                display_name,
                exc_info=True,
            )

        store = client.file_search_stores.create(
            config={
                "display_name": display_name,
                "embedding_model": self.embedding_model,
            }
        )
        return store.name

    async def index_file(
        self,
        *,
        org_id: str,
        record_id: str,
        record_name: str,
        mime_type: str | None,
        content: bytes | str | None,
        store_name: str | None = None,
        kb_id: str | None = None,
        custom_metadata: list[dict[str, Any]] | None = None,
    ) -> GeminiFileSearchIndexResult | None:
        """Index a file into a File Search store.

        ``store_name`` (an existing per-KB store) takes precedence; if
        absent, ``kb_id`` is used to create/resolve the KB store; if neither
        is provided we fall back to a per-record store (legacy behaviour).
        """
        if not self.is_enabled() or not self.supports_mime_type(mime_type):
            return None
        if not content:
            return None
        if isinstance(content, str):
            content = content.encode("utf-8")

        if not store_name and kb_id:
            store_name = await self.ensure_store(org_id=org_id, kb_id=kb_id)

        return await asyncio.to_thread(
            self._index_file_sync,
            org_id,
            record_id,
            record_name,
            mime_type,
            content,
            custom_metadata,
            store_name,
        )

    def _index_file_sync(
        self,
        org_id: str,
        record_id: str,
        record_name: str,
        mime_type: str | None,
        content: bytes,
        custom_metadata: list[dict[str, Any]] | None,
        store_name: str | None,
    ) -> GeminiFileSearchIndexResult:
        client = self._client()

        # Reuse the per-KB store when provided; otherwise create a per-record
        # store (legacy fallback so the service stays usable standalone).
        if store_name:
            store_obj_name = store_name
        else:
            display_name = f"pipeshub-{org_id}-{record_id}"
            store = client.file_search_stores.create(
                config={
                    "display_name": display_name,
                    "embedding_model": self.embedding_model,
                }
            )
            store_obj_name = store.name

        suffix = os.path.splitext(record_name)[1]
        if not suffix and mime_type:
            extension = get_extension_from_mimetype(
                mime_type.lower()
            ) or mimetypes.guess_extension(mime_type, strict=False)
            if extension:
                suffix = extension if extension.startswith(".") else f".{extension}"
        suffix = suffix or ".bin"
        # Let the SDK infer the upload MIME type from the corrected suffix.
        # Gemini rejects valid vendor MIME values such as DOCX/XLSX when they
        # are also supplied in UploadToFileSearchStoreRequest.mime_type.
        upload_config: dict[str, Any] = {"display_name": record_name}
        # Gemini accepts custom_metadata as a list of {key, stringValue|numericValue}.
        if custom_metadata:
            upload_config["custom_metadata"] = custom_metadata

        for existing in self._find_documents_by_record_id(client, store_obj_name, record_id):
            existing_name = getattr(existing, "name", None)
            if existing_name:
                self._delete_document_with_client(client, str(existing_name))

        with tempfile.NamedTemporaryFile(delete=True, suffix=suffix) as temp_file:
            temp_file.write(content)
            temp_file.flush()
            operation = client.file_search_stores.upload_to_file_search_store(
                file=temp_file.name,
                file_search_store_name=store_obj_name,
                config=upload_config,
            )
            operation_name = getattr(operation, "name", None)

        # Poll the document resource so the persisted status contains its
        # deletable resource name and remains compatible across SDK versions.
        document_name = self._poll_document_state(
            client,
            store_obj_name,
            record_name,
            record_id,
            operation_name,
        )
        return GeminiFileSearchIndexResult(
            store_name=store_obj_name,
            document_name=document_name,
            operation_name=operation_name,
        )

    def _poll_document_state(
        self,
        client: Any,
        store_name: str,
        record_name: str,
        record_id: str,
        operation_name: str | None,
    ) -> str | None:
        """Poll the store's documents until the uploaded file is ACTIVE.

        Lists documents in the store, finds the upload by its stable record ID,
        and waits for its state to leave processing. Returns the document's
        resource name, or ``None`` if it cannot be located after the timeout.
        """
        start = time.monotonic()
        # Documents appear in the store shortly after the upload operation is
        # accepted; allow some lead time before the first list call.
        first_poll = True
        while True:
            if time.monotonic() - start > self.poll_timeout_seconds:
                self.logger.warning(
                    "Timed out waiting for Gemini document '%s' to become active "
                    "(store=%s). The upload may still complete asynchronously.",
                    record_name,
                    store_name,
                )
                # Best-effort: return whatever document name we last saw.
                return self._find_document_name(client, store_name, record_name, record_id)

            if not first_poll:
                time.sleep(self.poll_interval_seconds)
            first_poll = False

            try:
                doc = self._find_document(client, store_name, record_name, record_id)
            except Exception:
                self.logger.debug(
                    "Error listing Gemini documents while polling %s",
                    record_name,
                    exc_info=True,
                )
                continue

            if doc is None:
                # Document not yet visible — keep waiting.
                continue

            name = getattr(doc, "name", None)
            state = self._document_state(doc)
            # Gemini's DocumentState enum stringifies as e.g. "STATE_ACTIVE",
            # "STATE_FAILED" — match on substrings so the prefix doesn't matter.
            if "ACTIVE" in state or "COMPLETED" in state:
                return name
            if "FAIL" in state or "ERROR" in state:
                # Surface the failure reason from the document when available.
                error_reason = self._document_error(doc)
                self.logger.warning(
                    "Gemini document '%s' failed to index (state=%s, store=%s, "
                    "reason=%s)",
                    record_name,
                    state,
                    store_name,
                    error_reason or "unknown",
                )
                raise GeminiFileSearchDocumentError(
                    "Gemini document failed to index "
                    f"(record={record_name}, state={state}, store={store_name}, "
                    f"reason={error_reason or 'unknown'})",
                    store_name=store_name,
                    document_name=str(name) if name else None,
                )

    def _find_document(
        self, client: Any, store_name: str, record_name: str, record_id: str | None = None
    ) -> Any | None:
        """Find a document by record ID, with a legacy display-name fallback."""
        documents = getattr(client.file_search_stores, "documents", None)
        if documents is None:
            return None
        target = (record_name or "").strip().lower()
        if not target:
            return None
        try:
            pager = documents.list(parent=store_name)
        except Exception:
            return None
        found = None
        all_docs = []
        for doc in pager or []:
            display = (getattr(doc, "display_name", None) or "").strip().lower()
            state = self._document_state(doc)
            all_docs.append(f"{display!r}(state={state})")
            doc_record_id = self._document_metadata_value(doc, "recordId")
            if record_id and doc_record_id == record_id:
                found = doc
            elif not record_id and display and display == target:
                found = doc
        if not all_docs:
            self.logger.info(
                "Gemini store %s: no documents listed yet (waiting for '%s')",
                store_name,
                record_name,
            )
        elif found is None:
            self.logger.info(
                "Gemini store %s: document '%s' not found among %d docs: %s",
                store_name,
                record_name,
                len(all_docs),
                ", ".join(all_docs[:5]),
            )
        else:
            self.logger.info(
                "Gemini store %s: matched doc '%s' state=%s",
                store_name,
                record_name,
                self._document_state(found),
            )
        return found

    def _find_document_name(
        self, client: Any, store_name: str, record_name: str, record_id: str | None = None
    ) -> str | None:
        doc = self._find_document(client, store_name, record_name, record_id)
        name = getattr(doc, "name", None) if doc is not None else None
        return str(name) if name else None

    @staticmethod
    def _document_metadata_value(doc: Any, key: str) -> str | None:
        for entry in getattr(doc, "custom_metadata", None) or []:
            entry_key = getattr(entry, "key", None)
            if entry_key is None and isinstance(entry, dict):
                entry_key = entry.get("key")
            if entry_key != key:
                continue
            value = getattr(entry, "string_value", None)
            if value is None and isinstance(entry, dict):
                value = entry.get("stringValue") or entry.get("string_value")
            return str(value) if value is not None else None
        return None

    def _find_documents_by_record_id(
        self, client: Any, store_name: str, record_id: str
    ) -> list[Any]:
        documents = getattr(client.file_search_stores, "documents", None)
        if documents is None:
            return []
        return [
            doc
            for doc in documents.list(parent=store_name) or []
            if self._document_metadata_value(doc, "recordId") == record_id
        ]

    @staticmethod
    def _document_state(doc: Any) -> str:
        """Extract a normalized state string from a Document."""
        state = getattr(doc, "state", None)
        if state is None:
            return ""
        # DocumentState enum: use .value if present, else stringify.
        value = getattr(state, "value", None)
        if value is not None:
            return str(value).upper()
        return str(state).upper()

    @staticmethod
    def _document_error(doc: Any) -> str | None:
        """Extract a failure reason from a failed Document, if present."""
        # Gemini's Document doesn't expose a direct error field, but the
        # underlying state may carry detail. Check common attribute names.
        for attr in ("error", "error_message", "failure_reason", "reason"):
            value = getattr(doc, attr, None)
            if value:
                return str(value)
        return None

    async def get_store_stats(self, store_name: str) -> dict[str, Any] | None:
        """Fetch store size + document counts via Gemini's get() API.

        Returns ``{sizeBytes, activeDocumentsCount, pendingDocumentsCount,
        failedDocumentsCount, displayName}`` or ``None`` on failure.
        """
        if not self.is_enabled() or not store_name:
            return None
        try:
            return await asyncio.to_thread(self._get_store_stats_sync, store_name)
        except Exception:
            self.logger.warning(
                "Failed to fetch Gemini store stats for %s",
                store_name,
                exc_info=True,
            )
            return None

    def _get_store_stats_sync(self, store_name: str) -> dict[str, Any] | None:
        client = self._client()
        get_store = getattr(client.file_search_stores, "get")
        try:
            store = get_store(name=store_name)
        except TypeError:
            store = get_store(file_search_store_name=store_name)
        if store is None:
            return None
        return {
            "sizeBytes": getattr(store, "size_bytes", None)
            or getattr(store, "sizeBytes", None),
            "activeDocumentsCount": getattr(store, "active_documents_count", None)
            or getattr(store, "activeDocumentsCount", None),
            "pendingDocumentsCount": getattr(store, "pending_documents_count", None)
            or getattr(store, "pendingDocumentsCount", None),
            "failedDocumentsCount": getattr(store, "failed_documents_count", None)
            or getattr(store, "failedDocumentsCount", None),
            "displayName": getattr(store, "display_name", None)
            or getattr(store, "displayName", None),
        }

    async def delete_store(self, store_name: str) -> None:
        """Delete an entire store. Use sparingly — removes all its docs."""
        if not self.is_enabled():
            raise RuntimeError("Gemini File Search is not configured")
        if not store_name:
            return
        await asyncio.to_thread(self._delete_store_sync, store_name)

    def _delete_store_sync(self, store_name: str) -> None:
        client = self._client()
        delete_store = getattr(client.file_search_stores, "delete")
        try:
            delete_store(name=store_name, config={"force": True})
        except TypeError:
            delete_store(file_search_store_name=store_name, config={"force": True})

    async def delete_document(self, document_name: str) -> None:
        """Delete a single document from its store (File Search Documents API).

        Use this when removing one file from a KB, so the shared KB store is
        preserved for the remaining files.
        """
        if not self.is_enabled():
            raise RuntimeError("Gemini File Search is not configured")
        if not document_name:
            return
        await asyncio.to_thread(self._delete_document_sync, document_name)

    def _delete_document_sync(self, document_name: str) -> None:
        client = self._client()
        self._delete_document_with_client(client, document_name)

    def _delete_document_with_client(self, client: Any, document_name: str) -> None:
        documents = getattr(client.file_search_stores, "documents", None)
        if documents is None:
            self.logger.warning(
                "google-genai client has no file_search_stores.documents; "
                "cannot delete document %s",
                document_name,
            )
            return
        delete_doc = getattr(documents, "delete")
        try:
            delete_doc(name=document_name, config={"force": True})
        except TypeError:
            # Older SDK spelling.
            delete_doc(document_name=document_name, config={"force": True})

    # ------------------------------------------------------------------
    # Querying
    # ------------------------------------------------------------------

    async def answer(
        self,
        *,
        query: str,
        store_names: list[str],
        metadata_filter: str | None = None,
    ) -> GeminiFileSearchAnswer | None:
        if not self.is_enabled() or not query.strip():
            return None

        deduped_store_names = list(dict.fromkeys(store_names))[
            : self.max_stores_per_query
        ]
        if not deduped_store_names:
            return None

        return await asyncio.to_thread(
            self._answer_sync,
            query,
            deduped_store_names,
            metadata_filter,
        )

    def _answer_sync(
        self,
        query: str,
        store_names: list[str],
        metadata_filter: str | None,
    ) -> GeminiFileSearchAnswer:
        from google.genai import types

        client = self._client()
        file_search_config: dict[str, Any] = {
            "file_search_store_names": store_names,
        }
        if metadata_filter:
            file_search_config["metadata_filter"] = metadata_filter
        try:
            file_search = types.FileSearch(**file_search_config)
        except TypeError:
            if metadata_filter:
                raise RuntimeError(
                    "Installed google-genai SDK does not support permission-scoped "
                    "File Search metadata filters"
                )
            file_search = types.FileSearch(**file_search_config)
        response = client.models.generate_content(
            model=self.generation_model,
            contents=query,
            config=types.GenerateContentConfig(
                tools=[types.Tool(file_search=file_search)]
            ),
        )

        text = getattr(response, "text", "") or ""
        citations = self._extract_citations(response)
        media = self._download_cited_media(client, citations)
        return GeminiFileSearchAnswer(
            text=text,
            citations=citations,
            store_names=store_names,
            media=media,
        )

    def _extract_citations(self, response: Any) -> list[dict[str, Any]]:
        citations: list[dict[str, Any]] = []
        for candidate in getattr(response, "candidates", None) or []:
            grounding_metadata = getattr(candidate, "grounding_metadata", None)
            if grounding_metadata is None:
                grounding_metadata = getattr(candidate, "groundingMetadata", None)
            chunks = getattr(grounding_metadata, "grounding_chunks", None) or []
            if not chunks:
                chunks = getattr(grounding_metadata, "groundingChunks", None) or []
            for chunk in chunks:
                file_data = getattr(chunk, "retrieved_context", None)
                if file_data is None:
                    file_data = getattr(chunk, "retrievedContext", None)
                if file_data is None:
                    file_data = getattr(chunk, "web", None)
                if file_data is None:
                    continue
                citations.append(
                    {
                        "title": getattr(file_data, "title", None),
                        "uri": getattr(file_data, "uri", None),
                        "pageNumber": getattr(file_data, "page_number", None),
                        "mediaId": getattr(file_data, "media_id", None),
                        "text": getattr(file_data, "text", None),
                        "storeName": getattr(file_data, "file_search_store", None)
                        or getattr(file_data, "fileSearchStore", None),
                        "customMetadata": self._read_custom_metadata(file_data),
                    }
                )
        return citations

    @staticmethod
    def _read_custom_metadata(file_data: Any) -> list[dict[str, Any]]:
        raw = (
            getattr(file_data, "custom_metadata", None)
            or getattr(file_data, "customMetadata", None)
            or []
        )
        out: list[dict[str, Any]] = []
        for entry in raw:
            out.append(
                {
                    "key": getattr(entry, "key", None),
                    "stringValue": getattr(entry, "string_value", None)
                    or getattr(entry, "stringValue", None),
                    "numericValue": getattr(entry, "numeric_value", None)
                    or getattr(entry, "numericValue", None),
                }
            )
        return out

    # ------------------------------------------------------------------
    # Media download (multimodal payoff)
    # ------------------------------------------------------------------

    def _download_cited_media(
        self,
        client: Any,
        citations: list[dict[str, Any]],
    ) -> dict[str, dict[str, str]]:
        """Download cited image chunks and return base64 data-URIs.

        Capped by ``max_media_per_query``. Any per-image failure is logged
        and skipped — never breaks the chat. The caller persists the bytes
        via BlobStorage and turns the data-URI into a renderable URL; this
        service stays free of storage dependencies.
        """
        media_ids = [c["mediaId"] for c in citations if c.get("mediaId")]
        # Preserve order, dedupe.
        seen: set[str] = set()
        unique_ids: list[str] = []
        for mid in media_ids:
            if mid not in seen:
                seen.add(mid)
                unique_ids.append(mid)
        unique_ids = unique_ids[: self.max_media_per_query]
        if not unique_ids:
            return {}

        download_media = getattr(client.file_search_stores, "download_media", None)
        if download_media is None:
            # Older SDK name spelling.
            download_media = getattr(client.file_search_stores, "downloadMedia", None)
        if download_media is None:
            self.logger.warning(
                "google-genai client has no download_media method; "
                "cited images will not be surfaced"
            )
            return {}

        result: dict[str, dict[str, str]] = {}
        for media_id in unique_ids:
            try:
                try:
                    blob = download_media(media_id=media_id)
                except TypeError:
                    blob = download_media(media_id)  # type: ignore[misc]
                mime = self._sniff_blob_mime(blob)
                b64 = base64.b64encode(self._blob_to_bytes(blob)).decode("ascii")
            except Exception:
                self.logger.warning(
                    "Failed to download Gemini media %s", media_id, exc_info=True
                )
                continue
            result[media_id] = {
                "dataUri": f"data:{mime};base64,{b64}",
                "mimeType": mime,
            }
        return result

    @staticmethod
    def _blob_to_bytes(blob: Any) -> bytes:
        if isinstance(blob, (bytes, bytearray)):
            return bytes(blob)
        response = getattr(blob, "response", None)
        if response is not None and hasattr(response, "content"):
            return response.content
        content = getattr(blob, "content", None)
        if isinstance(content, (bytes, bytearray)):
            return bytes(content)
        # Fall back to reading a file-like object.
        read = getattr(blob, "read", None)
        if callable(read):
            data = read()
            if isinstance(data, (bytes, bytearray)):
                return bytes(data)
        # Surface a clear error rather than silently dropping the image.
        raise TypeError(f"Unsupported Gemini media blob type: {type(blob)!r}")

    @staticmethod
    def _sniff_blob_mime(blob: Any) -> str:
        response = getattr(blob, "response", None)
        if response is not None:
            headers = getattr(response, "headers", {}) or {}
            ctype = headers.get("content-type") or headers.get("Content-Type")
            if ctype:
                return str(ctype).split(";")[0].strip()
        return "image/png"

    # ------------------------------------------------------------------
    # Helpers exposed to callers
    # ------------------------------------------------------------------

    @staticmethod
    def encode_file_search_status(payload: dict[str, Any] | None) -> str:
        """Serialize a File Search status payload for graph-DB storage.

        Neo4j only allows primitive property values (no nested maps), while
        Arango permits them. Storing the payload as a JSON string works on
        both backends and keeps the field shape identical across deployments.
        """
        if not payload:
            return ""
        # Drop None values — some graph backends reject explicit nulls inside
        # structures, and they add no information.
        cleaned = {k: v for k, v in payload.items() if v is not None}
        return json.dumps(cleaned, default=str)

    @staticmethod
    def decode_file_search_status(raw: Any) -> dict[str, Any]:
        """Inverse of :meth:`encode_file_search_status`.

        Accepts a JSON string (current writes), a dict (legacy Arango writes
        or in-memory values), or ``None``/empty — always returns a dict.
        """
        if not raw:
            return {}
        if isinstance(raw, dict):
            return raw
        if isinstance(raw, str):
            try:
                parsed = json.loads(raw)
                return parsed if isinstance(parsed, dict) else {}
            except (json.JSONDecodeError, TypeError):
                return {}
        return {}

    @staticmethod
    def build_custom_metadata(
        record: dict[str, Any], collection: str | None
    ) -> list[dict[str, Any]]:
        """Build Gemini ``custom_metadata`` from a PipesHub record node.

        Uses only string values (Gemini metadata_filter works cleanly with
        strings). Numeric values are intentionally avoided to keep filter
        expressions simple and predictable.
        """
        metadata: list[dict[str, str]] = []
        for key, value in (
            ("recordId", record.get("_key") or record.get("id")),
            ("recordName", record.get("recordName")),
            ("recordType", record.get("recordType")),
            ("mimeType", record.get("mimeType")),
            ("origin", record.get("origin")),
            ("connectorName", record.get("connectorName")),
            ("orgId", record.get("orgId")),
            ("webUrl", record.get("webUrl")),
            ("recordGroupId", record.get("recordGroupId")),
            ("collection", collection),
            ("sourceLastModifiedTimestamp", record.get("sourceLastModifiedTimestamp")),
        ):
            if value is None:
                continue
            text = str(value)
            if not text.strip():
                continue
            metadata.append({"key": key, "stringValue": text})
        return metadata


# ----------------------------------------------------------------------
# Reusable citation-builder (shared by retrieval.py + chatbot.py)
# ----------------------------------------------------------------------


async def build_gemini_citable_results(
    gemini_result: dict[str, Any],
    blob_store: Any,
    org_id: str,
    is_multimodal_llm: bool,
    config_service: Any,
) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]], str]:
    """Convert Gemini File Search citations into first-class citable results.

    Returns ``(flattened_results, companion_records, lead_answer_text)``.
    Each citation becomes a flattened result in the shape
    ``build_message_content_array`` expects, backed by a synthetic
    companion record so the citation chip deep-links to the real Pipe file.
    Cited images are persisted to BlobStorage and surfaced as both inline
    vision content (multimodal LLMs) and a downloadable URL.
    """
    from app.models.blocks import BlockType
    from app.utils.chat_helpers import build_block_web_url

    citations = gemini_result.get("citations") or []
    lead_answer = sanitize_gemini_lead_answer(gemini_result.get("answer"))

    flattened: list[dict[str, Any]] = []
    companions: dict[str, dict[str, Any]] = {}

    # Determine frontend base URL from config (for deep-link construction).
    frontend_url = ""
    if config_service is not None:
        try:
            endpoints = await config_service.get_config(
                config_node_constants.ENDPOINTS.value, use_cache=False
            )
            if isinstance(endpoints, dict):
                fe = endpoints.get("frontend")
                if isinstance(fe, dict):
                    frontend_url = fe.get("publicEndpoint") or ""
                elif isinstance(fe, str):
                    frontend_url = fe
        except Exception:
            pass

    for idx, citation in enumerate(citations):
        record = citation.get("record") or {}
        record_id = record.get("recordId") or record.get("_key")
        record_name = (
            record.get("recordName") or citation.get("title") or "Gemini source"
        )
        mime_type = record.get("mimeType")
        web_url = record.get("webUrl")
        record_type = record.get("recordType") or "FILE"
        connector_name = record.get("connectorName") or record.get("connector") or "KB"
        raw_origin = str(record.get("origin") or "UPLOAD")
        origin = (
            "UPLOAD" if raw_origin.upper() in {"KB", "KNOWLEDGE_BASE"} else raw_origin
        )
        hide_weburl = bool(record.get("hideWeburl", False))
        preview_renderable = record.get("previewRenderable")
        if preview_renderable is None:
            preview_renderable = True
        record_extension = record.get("extension")
        if not record_extension and mime_type:
            record_extension = get_extension_from_mimetype(mime_type) or ""
        page_number = citation.get("pageNumber")
        media = citation.get("media") or {}
        media_id = citation.get("mediaId")

        if not record_id:
            continue

        virtual_record_id = f"gemini-fs-{record_id}-{idx}"

        # Persist cited image to Pipe storage (best-effort).
        image_data_uri: str | None = None
        image_download_url: str | None = None
        if media and media.get("dataUri"):
            try:
                data_uri = media.get("dataUri")
                mime_type_media = media.get("mimeType") or "image/png"
                b64 = data_uri.split(",", 1)[-1] if "," in data_uri else ""
                raw_bytes = base64.b64decode(b64) if b64 else b""
                if raw_bytes:
                    extension = mime_type_media.split("/")[-1].split(";")[0] or "png"
                    file_name = f"gemini-fs-{record_id}-{idx}.{extension}"
                    document_id, _ = await blob_store.save_binary_to_storage(
                        org_id=org_id,
                        record_id=record_id,
                        file_name=file_name,
                        extension=extension,
                        content_type=mime_type_media,
                        binary_data=raw_bytes,
                    )
                    # Build the external download URL.
                    public_base = frontend_url.rstrip("/") if frontend_url else ""
                    if public_base and document_id:
                        from app.config.constants.service import Routes

                        image_download_url = (
                            f"{public_base}"
                            f"{Routes.STORAGE_DOWNLOAD_EXTERNAL.value.format(documentId=document_id)}"
                        )
                    image_data_uri = data_uri
            except Exception:
                logger.warning(
                    "Failed to persist Gemini media for citation %s", idx, exc_info=True
                )

        try:
            block_index = int(page_number) if page_number is not None else idx
        except (TypeError, ValueError):
            block_index = idx

        record_preview_url = build_block_web_url(frontend_url, record_id, block_index)
        chip_web_url = image_download_url or (
            record_preview_url
            if origin == "UPLOAD"
            else (web_url or record_preview_url)
        )

        companion = {
            "_id": record_id,
            "id": record_id,
            "_key": record_id,
            "record_name": record_name,
            "record_type": record_type,
            "mime_type": mime_type,
            "weburl": web_url,
            "origin": origin,
            "frontend_url": frontend_url,
            "context_metadata": (
                f"Record: {record_name}"
                + (f"\nType: {mime_type}" if mime_type else "")
                + (
                    f"\nSource: Gemini File Search (page {page_number})"
                    if page_number
                    else "\nSource: Gemini File Search"
                )
            ),
            "virtual_record_id": virtual_record_id,
        }
        companions[virtual_record_id] = companion

        has_image = bool(image_data_uri)
        if has_image and is_multimodal_llm:
            block_type = BlockType.IMAGE.value
            content_value: str = image_data_uri or ""
        else:
            block_type = BlockType.TEXT.value
            snippet = citation.get("text") or ""
            if not snippet and has_image:
                snippet = "Referenced image from Gemini File Search."
            elif not snippet:
                snippet = (
                    lead_answer[:500] if lead_answer else "Gemini File Search match."
                )
            content_value = snippet

        flattened.append(
            {
                "virtual_record_id": virtual_record_id,
                "block_index": block_index,
                "block_type": block_type,
                "content": content_value,
                "metadata": {
                    "recordId": record_id,
                    "recordName": record_name,
                    "recordType": record_type,
                    "connector": connector_name,
                    "orgId": org_id,
                    "virtualRecordId": virtual_record_id,
                    "mimeType": mime_type,
                    "extension": record_extension,
                    "pageNum": page_number,
                    "webUrl": chip_web_url,
                    "origin": origin,
                    "previewRenderable": bool(preview_renderable),
                    "hideWeburl": hide_weburl,
                    "source": "gemini_file_search",
                    "mediaId": media_id,
                },
                "citationType": "document",
            }
        )

    return flattened, companions, lead_answer
