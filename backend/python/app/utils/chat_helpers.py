import asyncio
import base64
import hashlib
import logging
import re
import time
from collections import defaultdict
from collections.abc import Iterable
from itertools import groupby
from typing import Any, Dict, List, Optional
from urllib.parse import quote
from uuid import uuid4

from app.config.configuration_service import ConfigurationService
from app.config.constants.arangodb import CollectionNames, RecordRelations
from app.config.constants.service import config_node_constants
from app.models.blocks import BlockType, GroupSubType, GroupType, SemanticMetadata
from app.modules.reconciliation.service import ReconciliationMetadata
from app.models.entities import (
    CodeFileRecord,
    Connectors,
    DealRecord,
    FileRecord,
    LinkPublicStatus,
    LinkRecord,
    MailRecord,
    MeetingRecord,
    MessageRecord,
    OriginTypes,
    ProjectRecord,
    Record,
    RecordType,
    TicketRecord,
)
from app.modules.qna.prompt_templates import (
    agent_block_group_prompt,
    block_group_prompt,
    qna_prompt_context,
    qna_prompt_simple,
    table_prompt,
)
from app.connectors.sources.atlassian.jira.enrichment.record_identifiers import is_jira_ticket_record
from app.modules.transformers.blob_storage import BlobStorage
from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider
from app.services.vector_db.const.const import VECTOR_DB_COLLECTION_NAME
from app.utils.image_utils import get_extension_from_mimetype
from app.utils.jinja_templates import compiled_template
from app.utils.logger import create_logger

valid_group_labels = [
        GroupType.LIST.value,
        GroupType.ORDERED_LIST.value,
        GroupType.FORM_AREA.value,
        GroupType.INLINE.value,
        GroupType.KEY_VALUE_AREA.value,
        GroupType.TEXT_SECTION.value,
        GroupType.CODE.value,
        GroupType.CONVERSATION.value
    ]

MAX_IMAGES_IN_CONVERSATION = 50


class ImageBudget:
    """Conversation-wide image counter shared across every image source
    (user attachments, history replay, search/fetch/prefetch tool
    results). A single instance is threaded through a turn so the same
    50-image cap applies no matter which source contributed the image —
    without this, each source enforcing its own local limit could let the
    conversation total balloon past what any provider will actually
    accept as multimodal input.
    """

    def __init__(self, max_images: int = MAX_IMAGES_IN_CONVERSATION) -> None:
        self.max_images = max_images
        self.used = 0

    @property
    def remaining(self) -> int:
        return max(0, self.max_images - self.used)

    def can_add(self) -> bool:
        return self.used < self.max_images

    def try_consume(self, count: int = 1) -> int:
        """Consume up to `count` from the budget. Returns the amount
        actually consumed (may be less than `count` near the cap)."""
        actual = min(count, self.remaining)
        self.used += actual
        return actual


def image_dict_to_part(image: dict[str, Any]) -> Any | None:
    """Convert a `collected_images` entry (`{"image_url": {"url": ...}, ...}`)
    into an `ImagePart` for a multipart `ToolOutput`/`UserMessage`. Shared by
    every tool (`retrieval.py`, `citations.py`) and hook
    (`attachment_resolver.py`'s `shape_image_injection`/
    `shape_retrieved_image_injection`) that needs to hand collected images to
    the agent loop, so the dict-to-Part conversion lives in exactly one
    place. Local import avoids a module-level dependency from this
    low-level formatting module onto `agent_loop_lib`.
    """
    from app.agent_loop_lib.core.messages import ImagePart, ImageSource  # noqa: PLC0415

    image_url = image.get("image_url") or {}
    url = image_url.get("url", "") if isinstance(image_url, dict) else str(image_url)
    if not url:
        return None
    # A `data:` URI must become a real base64 source, not a `type="url"` one
    # carrying the whole URI: Anthropic's image block takes `source.url` only
    # for a fetchable http(s) URL and rejects a data URI there, so collapsing
    # both cases into "url" made every collected image a 400 on that provider.
    # The OpenAI-family formatters rebuild the same data URI via
    # `image_data_url()`, so they are unaffected by the split.
    if url.startswith("data:"):
        header, _, payload = url[len("data:"):].partition(",")
        if payload and ";base64" in header.lower():
            media_type = header.split(";", 1)[0] or None
            return ImagePart(
                source=ImageSource(type="base64", media_type=media_type, data=payload)
            )
    return ImagePart(source=ImageSource(type="url", data=url))



def group_child_results(doc: dict[str, Any]) -> list[dict[str, Any]] | None:
    """Children of a *group* flattened-result, or None when it is not a group.

    `block_type` alone cannot tell the two apart: GroupType.CODE and
    BlockType.CODE are both the string "code", so a code block and a code group
    carry the same label. Only a group's content is a ``(summary, children)``
    pair, so the shape is the reliable test -- keying off the label alone
    unpacks a leaf's source string character by character.

    None and [] are distinct on purpose: None means "treat this as a leaf",
    while [] means "a group that contributed nothing", which must stay skipped.
    """
    content = doc.get("content")
    if isinstance(content, tuple) and len(content) == 2:
        children = content[1]
        return children if isinstance(children, list) else []
    return None

def _safe_stringify_content(value: Any) -> str:
    """Convert citation content to string without raising.

    A code block's ``data`` is a dict; stringifying it whole would put the
    BM25 ``subtokens`` padding in front of the model as a Python repr, so the
    source text is unwrapped first.
    """
    if isinstance(value, dict) and "text" in value:
        value = value.get("text") or ""
    try:
        return str(value)
    except Exception as exc:
        logger.warning("Failed to cast citation content to string: %s", exc)
        return ""


def block_qualified_name(block: dict[str, Any]) -> str:
    """Qualified name of a code block, or "" for anything else."""
    meta = block.get("code_metadata")
    if not isinstance(meta, dict):
        return ""
    return meta.get("qualified_name") or ""


def format_code_locator(file_path: str, qualified_name: str) -> str:
    """`path#qualified_name` — human-readable locator for a code block."""
    if file_path and qualified_name:
        return f"{file_path}#{qualified_name}"
    return file_path or qualified_name or ""

def build_block_web_url(frontend_url: str, record_id: str, block_index: int) -> str:
    """Construct a block-level preview URL: {frontend_url}/record/{record_id}/preview#blockIndex={block_index}"""
    base = frontend_url.rstrip("/") if frontend_url else ""
    return f"{base}/record/{record_id}/preview#blockIndex={block_index}"


def build_record_page_web_url(frontend_url: str, record_id: str) -> str:
    """Construct the record landing URL for metadata/header fields (Summary, Topics, etc.)."""
    base = frontend_url.rstrip("/") if frontend_url else ""
    if not base or not record_id:
        return ""
    return f"{base}/record/{record_id}"


def flattened_result_sort_key(result: dict[str, Any]) -> tuple[str, int]:
    """Sort flattened search results; None block_index (e.g. record summaries) sorts before block 0."""
    block_index = result.get("block_index")
    return (
        result.get("virtual_record_id") or "",
        -1 if block_index is None else block_index,
    )




def is_base64_image(s: str) -> bool:
    """
    Check if a string is a valid base64-encoded image.
    
    Accepts both:
    - Data URLs: "data:image/png;base64,iVBORw0KGgo..."
    - Raw base64 strings: "iVBORw0KGgo..."
    """
    if not isinstance(s, str) or not s.strip():
        return False

    # Handle data URL format
    data_url_pattern = r'^data:image/(png|jpeg|jpg|gif|webp|bmp|svg\+xml|tiff);base64,(.+)$'
    match = re.match(data_url_pattern, s.strip(), re.IGNORECASE)

    if match:
        b64_data = match.group(2)
    else:
        b64_data = s.strip()

    # Validate base64 characters
    if not re.match(r'^[A-Za-z0-9+/]*={0,2}$', b64_data):
        return False

    # Check padding
    if len(b64_data) % 4 != 0:
        return False

    # 272 chars -> 204 bytes: more than the longest magic number (8) and the
    # SVG sniff's decoded[:200], and a multiple of 4 so the decoder accepts it.
    try:
        decoded = base64.b64decode(b64_data[:272])
    except Exception:
        return False

    # Check for known image magic bytes
    image_signatures = {
        b'\x89PNG\r\n\x1a\n': 'PNG',
        b'\xff\xd8\xff': 'JPEG',
        b'GIF87a': 'GIF',
        b'GIF89a': 'GIF',
        b'RIFF': 'WEBP',  # WEBP starts with RIFF
        b'BM': 'BMP',
        b'II*\x00': 'TIFF',
        b'MM\x00*': 'TIFF',
    }

    for signature, fmt in image_signatures.items():
        if decoded.startswith(signature):
            return True

    # SVG is XML text — check for <svg tag after decoding
    try:
        text = decoded[:200].decode('utf-8', errors='ignore').lower().strip()
        if '<svg' in text or '<?xml' in text:
            return True
    except Exception:
        pass

    return False


_multimodal_logger = logging.getLogger(__name__)


# Bounded page size for vector scrolls: providers cap a single request
# (OpenSearch 10k, RediSearch MAXSEARCHRESULTS), so callers must page.
SCROLL_PAGE_SIZE = 1000

# Ceiling on points reconstructed for one virtual record. The walk below runs on
# a request path and every point expands into a block and a payload, so one
# oversized record would otherwise be an OOM; a record this large cannot be
# rendered as citations anyway.
MAX_SCROLL_POINTS = 50_000

async def build_multimodal_user_content(
    text_content: str,
    attachments: list[dict],
    blob_store: "BlobStorage",
    org_id: str,
) -> list[dict] | str:
    """Build multimodal content for a HumanMessage from a previous user query.

    Filters attachments to image types, fetches their base64 data from blob
    storage, and returns an OpenAI-style content list mixing text and image_url
    blocks.  Falls back to the plain *text_content* string when no images are
    resolved (avoids unnecessary list wrapping for non-multimodal turns).
    """
    if not attachments or not blob_store or not org_id:
        return text_content

    image_attachments = [
        att for att in attachments
        if isinstance(att, dict)
        and (att.get("mimeType") or "").lower().startswith("image/")
    ]
    if not image_attachments:
        return text_content

    image_urls: list[str] = []
    fetch_tasks = []

    for att in image_attachments:
        vrid = att.get("virtualRecordId") or ""
        if vrid:
            fetch_tasks.append((vrid, att))

    for vrid, att in fetch_tasks:
        try:
            record = await blob_store.get_record_from_storage(vrid, org_id)
            if not record:
                continue
            block_containers = record.get("block_containers", {})
            blocks = block_containers.get("blocks", []) if isinstance(block_containers, dict) else []
            for block in blocks:
                if not isinstance(block, dict):
                    continue
                block_type = block.get("type", "")
                if block_type != "image":
                    continue
                data = block.get("data")
                if isinstance(data, dict):
                    uri = data.get("uri", "")
                elif isinstance(data, str):
                    uri = data
                else:
                    continue
                if uri and is_base64_image(uri):
                    image_urls.append(uri)
        except Exception as exc:
            _multimodal_logger.warning(
                "Failed to fetch image attachment vrid=%s for conversation history: %s",
                vrid, exc,
            )

    if not image_urls:
        return text_content

    content_parts: list[dict] = [{"type": "text", "text": text_content}]
    for url in image_urls:
        content_parts.append({"type": "image_url", "image_url": {"url": url}})
    return content_parts


class CitationRefMapper:
    """Builds a bidirectional mapping between tiny citation refs (ref1, ref2, ...) and full block web URLs.

    get_or_create_ref() is idempotent — same URL always returns the same ref.
    The mapper is designed to be shared as a single mutable instance across
    retrieval tool calls, respond nodes, and tool execution hops.
    """

    def __init__(self):
        self._counter: int = 0
        self._url_to_ref: dict[str, str] = {}
        self._ref_to_url: dict[str, str] = {}

    def get_or_create_ref(self, full_url: str) -> str:
        """Return existing ref if URL already mapped, else create a new one."""
        if full_url in self._url_to_ref:
            return self._url_to_ref[full_url]
        self._counter += 1
        ref = f"ref{self._counter}"
        self._url_to_ref[full_url] = ref
        self._ref_to_url[ref] = full_url
        return ref

    @property
    def ref_to_url(self) -> dict[str, str]:
        """Snapshot of ref→URL mapping (safe to pass downstream without exposing mutability)."""
        return dict(self._ref_to_url)

    @property
    def url_to_ref(self) -> dict[str, str]:
        """Snapshot of URL→ref mapping."""
        return dict(self._url_to_ref)


_RECORD_ID_LABEL_RE = re.compile(r"((?:Linked )?Record ID): ([^\s,;)\]]+)")
_RECORD_ID_ASSIGN_RE = re.compile(r"(record_id)=(\S+)")
_NODE_ID_ASSIGN_RE = re.compile(r"(node_id)=([\"']?)([^\s\"',)]+)\2")


class RecordIdShortener:
    """TEMPORARY token-savings experiment: shorten full record IDs to a
    short sequential label (`R1`, `R2`, ...) wherever a record id appears in
    LLM-facing text, and resolve that label back to the full ID when the
    model passes it to a knowledge-graph tool. One instance is kept per
    request on `AgentContext.tool_state["record_id_shortener"]` (see
    `_seed_tool_state()` in `agents/agent_loop/context.py`) so the same
    record always gets the same short id within a conversation turn,
    regardless of which tool (retrieval, search, navigate, lookup_record,
    list_files, fetch_record) surfaces it first.

    get_or_create_short_id() is idempotent — same full id always returns the
    same short id, assigned in first-seen order (`R1`, `R2`, `R3`, ...).
    Sequential labels can never collide with each other or with a full id,
    so unlike a UUID-prefix scheme no collision handling is needed.
    """

    def __init__(self) -> None:
        self._full_to_short: dict[str, str] = {}
        self._short_to_full: dict[str, str] = {}
        self._counter: int = 0

    def get_or_create_short_id(self, full_id: str) -> str:
        if full_id in self._full_to_short:
            return self._full_to_short[full_id]
        self._counter += 1
        short = f"R{self._counter}"
        self._full_to_short[full_id] = short
        self._short_to_full[short] = full_id
        return short

    def shorten_if_known(self, full_id: str) -> str:
        """Return the short label if already mapped, otherwise the full ID unchanged."""
        return self._full_to_short.get(full_id, full_id)

    def resolve(self, short_or_full_id: str) -> str:
        """Full id for a known short id; unrecognized input (a full id the
        model copied verbatim from a path that predates shortening) passes
        through unchanged."""
        return self._short_to_full.get(short_or_full_id, short_or_full_id)

    def shorten_record_ids_in_text(self, text: str) -> str:
        """Replace every `Record ID: <id>` / `Linked Record ID: <id>`
        occurrence in `text` with a short id, creating new mappings as
        needed. Only ever touches header/metadata lines carrying that exact
        label — never block content."""
        if not text:
            return text

        def _sub(match: "re.Match[str]") -> str:
            label, full_id = match.group(1), match.group(2)
            return f"{label}: {self.get_or_create_short_id(full_id)}"

        return _RECORD_ID_LABEL_RE.sub(_sub, text)

    def shorten_record_id_assigns_in_text(self, text: str) -> str:
        """Replace every `record_id=<id>` occurrence (list_files/navigate
        row format) with `record_id=<short id>`."""
        if not text:
            return text

        def _sub(match: "re.Match[str]") -> str:
            label, full_id = match.group(1), match.group(2)
            return f"{label}={self.get_or_create_short_id(full_id)}"

        return _RECORD_ID_ASSIGN_RE.sub(_sub, text)

    def shorten_node_id_assigns_in_text(self, text: str) -> str:
        """Replace every `node_id=<id>` / `node_id="<id>"` occurrence
        (navigate breadcrumbs and `navigate(node_id=...)` hints) with the
        short id, preserving any surrounding quotes."""
        if not text:
            return text

        def _sub(match: "re.Match[str]") -> str:
            label, quote, full_id = match.group(1), match.group(2), match.group(3)
            return f"{label}={quote}{self.get_or_create_short_id(full_id)}{quote}"

        return _NODE_ID_ASSIGN_RE.sub(_sub, text)

    def shorten_all_record_ids(self, text: str) -> str:
        """Apply every known shortening pattern (`Record ID:`, `record_id=`,
        `node_id=`) to `text` in one pass. Use this at tool-output
        boundaries that mix formats (navigate/lookup_record renderers)."""
        text = self.shorten_record_ids_in_text(text)
        text = self.shorten_record_id_assigns_in_text(text)
        text = self.shorten_node_id_assigns_in_text(text)
        return text


def get_record_id_shortener_if_enabled(state: dict[str, Any]) -> "RecordIdShortener | None":
    """Single point of truth for every knowledge-tool lazy-creation site:
    reuse the per-request `RecordIdShortener` already on `state`, or mint
    one — but only when the request opted in via
    `state["enable_record_id_shortening"]` (`ChatQuery.enableRecordIdShortening`,
    default False — see `AgentContext.enable_record_id_shortening`).

    Returns `None` when the flag is off (the default) so call sites can
    gate every shortening/resolution call with a plain `if shortener:`
    instead of re-checking the flag themselves.
    """
    shortener = state.get("record_id_shortener")
    if isinstance(shortener, RecordIdShortener):
        return shortener
    if not state.get("enable_record_id_shortening"):
        return None
    shortener = RecordIdShortener()
    state["record_id_shortener"] = shortener
    return shortener


# Create a logger for this module
logger = create_logger("chat_helpers")

TEXT_FRAGMENT_DIRECTIVE_PREFIX = "#:~:text="

GRAPH_CONTEXT_ENRICHMENT_CONNECTORS: frozenset[Connectors] = frozenset({
    Connectors.JIRA,
    Connectors.JIRA_PERSONAL,
    Connectors.JIRA_DATA_CENTER,
    Connectors.JIRA_DATA_CENTER_PERSONAL,
    Connectors.LINEAR,
    Connectors.CONFLUENCE,
})

RECORD_RELATION_ENRICHMENT_TYPES: frozenset[RecordRelations] = frozenset({
    RecordRelations.ATTACHMENT,
    RecordRelations.PARENT_CHILD,
})

_GRAPH_TO_RECORD_FIELDS: dict[str, str] = {
    "recordName": "record_name",
    "recordType": "record_type",
    "connectorName": "connector_name",
    "connectorId": "connector_id",
    "mimeType": "mime_type",
    "externalRecordId": "external_record_id",
    "webUrl": "weburl",
    "origin": "origin",
    "sourceCreatedAtTimestamp": "source_created_at",
    "sourceLastModifiedTimestamp": "source_updated_at",
}

# Matches BlobStorage.VIRTUAL_RECORD_LOOKUP_CHUNK_SIZE: the graph accepts an IN
# list, but an unbounded one turns one slow query into a timeout.
GRAPH_BATCH_CHUNK_SIZE = 500

collection_map = {
                    RecordType.TICKET.value: "tickets",
                    RecordType.PROJECT.value: "projects",
                    RecordType.FILE.value: "files",
                    RecordType.MAIL.value: "mails",
                    RecordType.LINK.value: "links",
                    RecordType.MEETING.value: "meetings",
                    RecordType.DEAL.value: "deals",
                    RecordType.MESSAGE.value: "messages",
                    # filePath lives only on the codeFiles node -- the blob record
                    # is built from a plain Record, which has no such field. Without
                    # this entry every code file renders with no path at all.
                    RecordType.CODE_FILE.value: "codeFiles",
                }

def create_record_instance_from_dict(record_dict: dict[str, Any], graph_doc: dict[str, Any] | None = None) -> Record | None:
    """
    Creates a Record subclass instance from a dictionary.

    Args:
        record_dict: Dictionary with record data from blob storage
        graph_doc: Optional dictionary with type-specific data from graph DB

    Returns:
        Record subclass instance or None
    """
    if not record_dict:
        return None

    if not graph_doc:
        return Record(
                id=record_dict.get("id", ""),
                record_name=record_dict.get("record_name", ""),
                record_type=RecordType(record_dict.get("record_type")),
                connector_name=Connectors(record_dict.get("connector_name")) if record_dict.get("connector_name") else Connectors.KNOWLEDGE_BASE,
                mime_type=record_dict.get("mime_type", ""),
                external_record_id=record_dict.get("external_record_id", ""),
                weburl=record_dict.get("weburl", ""),
                location=record_dict.get("location"),
                version=record_dict.get("version", 1),
                origin=OriginTypes(record_dict.get("origin")) if record_dict.get("origin") else OriginTypes.UPLOAD,
                connector_id=record_dict.get("connector_id", ""),
                source_created_at=record_dict.get("source_created_at") or None,
                source_updated_at=record_dict.get("source_updated_at") or None,
                semantic_metadata=SemanticMetadata(**record_dict.get("semantic_metadata", {})),
            )

    record_type = record_dict.get("record_type")

    base_args = {
        "id": record_dict.get("id", ""),
        "org_id": record_dict.get("org_id", ""),
        "record_name": record_dict.get("record_name", ""),
        "external_record_id": record_dict.get("external_record_id", ""),
        "version": record_dict.get("version", 1),
        "origin": OriginTypes(record_dict.get("origin")) if record_dict.get("origin") else OriginTypes.UPLOAD,
        "connector_name": Connectors(record_dict.get("connector_name")) if record_dict.get("connector_name") else Connectors.KNOWLEDGE_BASE,
        "connector_id": record_dict.get("connector_id", ""),
        "mime_type": record_dict.get("mime_type", ""),
        "source_created_at": record_dict.get("source_created_at") or None,
        "source_updated_at": record_dict.get("source_updated_at") or None,
        "location": record_dict.get("location"),
        "weburl": record_dict.get("weburl", ""),
        "semantic_metadata": SemanticMetadata(**record_dict.get("semantic_metadata", {})),
    }

    try:
        if record_type == RecordType.TICKET.value and graph_doc:
            specific_args = {
                "record_type": RecordType.TICKET,
                "status": graph_doc.get("status"),
                "priority": graph_doc.get("priority"),
                "type": graph_doc.get("type"),
                "delivery_status": graph_doc.get("deliveryStatus"),
                "assignee": graph_doc.get("assignee"),
                "assignee_email": graph_doc.get("assigneeEmail"),
                "reporter_name": graph_doc.get("reporterName"),
                "reporter_email": graph_doc.get("reporterEmail"),
                "creator_name": graph_doc.get("creatorName"),
                "creator_email": graph_doc.get("creatorEmail"),
                "labels": graph_doc.get("labels"),
            }
            return TicketRecord(**base_args, **specific_args)

        elif record_type == RecordType.PROJECT.value and graph_doc:
            specific_args = {
                "record_type": RecordType.PROJECT,
                "status": graph_doc.get("status"),
                "priority": graph_doc.get("priority"),
                "lead_name": graph_doc.get("leadName"),
                "lead_email": graph_doc.get("leadEmail"),
            }
            return ProjectRecord(**base_args, **specific_args)

        elif record_type == RecordType.FILE.value and graph_doc:
            specific_args = {
                "record_type": RecordType.FILE,
                "is_file": graph_doc.get("isFile", True),
                "extension": graph_doc.get("extension"),
            }
            return FileRecord(**base_args, **specific_args)

        elif record_type == RecordType.CODE_FILE.value and graph_doc:
            specific_args = {
                "record_type": RecordType.CODE_FILE,
                "file_path": graph_doc.get("filePath"),
                "file_hash": graph_doc.get("fileHash"),
                "extension": graph_doc.get("extension"),
                "language": graph_doc.get("language"),
                "file_role": graph_doc.get("fileRole"),
            }
            return CodeFileRecord(**base_args, **specific_args)

        elif record_type == RecordType.MAIL.value and graph_doc:
            specific_args = {
                "record_type": RecordType.MAIL,
                "subject": graph_doc.get("subject"),
                "from_email": graph_doc.get("from"),
                "to_emails": graph_doc.get("to"),
                "cc_emails": graph_doc.get("cc"),
                "bcc_emails": graph_doc.get("bcc"),
            }
            return MailRecord(**base_args, **specific_args)

        elif record_type == RecordType.LINK.value and graph_doc:
            specific_args = {
                "record_type": RecordType.LINK,
                "url": graph_doc.get("url", ""),
                "title": graph_doc.get("title"),
                "is_public": LinkPublicStatus(graph_doc.get("isPublic", "unknown")),
                "linked_record_id": graph_doc.get("linkedRecordId"),
            }
            return LinkRecord(**base_args, **specific_args)

        elif record_type == RecordType.MEETING.value and graph_doc:
            specific_args = {
                "record_type": RecordType.MEETING,
                "host_email": graph_doc.get("hostEmail"),
                "host_id": graph_doc.get("hostId"),
                "meeting_type": graph_doc.get("meetingType"),
                "duration_minutes": graph_doc.get("durationMinutes"),
                "start_time": graph_doc.get("startTime"),
                "end_time": graph_doc.get("endTime"),
                "timezone": graph_doc.get("timezone"),
                "recording_url": graph_doc.get("recordingUrl"),
            }
            return MeetingRecord(**base_args, **specific_args)

        elif record_type == RecordType.DEAL.value and graph_doc:
            specific_args = {
                "record_type": RecordType.DEAL,
                "name": graph_doc.get("name"),
                "amount": float(graph_doc.get("amount")) if graph_doc.get("amount") is not None else None,
                "expected_revenue": graph_doc.get("expectedRevenue"),
                "expected_close_date": graph_doc.get("expectedCloseDate"),
                "conversion_probability": graph_doc.get("conversionProbability"),
                "type": graph_doc.get("type"),
                "owner_id": graph_doc.get("ownerId"),
                "is_won": graph_doc.get("isWon"),
                "is_closed": graph_doc.get("isClosed"),
                "created_date": graph_doc.get("createdDate"),
                "close_date": graph_doc.get("closeDate"),
            }
            return DealRecord(**base_args, **specific_args)

        elif record_type == RecordType.MESSAGE.value and graph_doc:
            specific_args = {
                "record_type": RecordType.MESSAGE,
                "thread_id": graph_doc.get("threadId"),
                "has_replies": graph_doc.get("hasReplies"),
                "is_reply": graph_doc.get("isReply", False),
                "author_id": graph_doc.get("authorId"),
                "record_group_type": graph_doc.get("recordGroupType"),
            }
            return MessageRecord(**base_args, **specific_args)
        else:
            return None
    except Exception as e:
        logger.error(f"Error creating record instance: {str(e)}")
        return None


# Connector rollout gates

def _supports_graph_context_enrichment(connector_name: str | None) -> bool:
    """Rollout gate for dependent parent + record relation enrichment."""
    if not connector_name:
        return False
    try:
        return Connectors(connector_name) in GRAPH_CONTEXT_ENRICHMENT_CONNECTORS
    except ValueError:
        return False

# doc_index: record_id -> graph doc

def _build_record_id_to_graph_doc_index(
    virtual_to_record_map: dict[str, dict[str, Any]] | None,
) -> dict[str, dict[str, Any]]:
    """Index graph base docs by record id/_key for O(1) parent lookups."""
    index: dict[str, dict[str, Any]] = {}
    if not virtual_to_record_map:
        return index
    for doc in virtual_to_record_map.values():
        if not isinstance(doc, dict):
            continue
        doc_id = doc.get("id")
        doc_key = doc.get("_key")
        if doc_id:
            index.setdefault(doc_id, doc)
        if doc_key:
            index.setdefault(doc_key, doc)
    return index

def _extend_record_id_index_from_hit_records(
    index: dict[str, dict[str, Any]],
    virtual_record_id_to_result: dict[str, dict[str, Any]],
) -> None:
    for record in virtual_record_id_to_result.values():
        if not record or not isinstance(record, dict):
            continue
        record_id = record.get("id")
        if not record_id:
            continue
        if record_id not in index:
            index[record_id] = {
                "id": record_id,
                "_key": record_id,
                "recordName": record.get("record_name"),
                "externalRecordId": record.get("external_record_id"),
            }

def _resolve_dependent_graph_fields(
    vrid: str,
    virtual_to_record_map: dict[str, dict[str, Any]] | None,
) -> tuple[bool, str | None]:
    """Read isDependentNode and parentNodeId from retrieval's virtual_to_record_map."""
    graph_doc = (virtual_to_record_map or {}).get(vrid)
    if not graph_doc or not isinstance(graph_doc, dict):
        return False, None
    return bool(graph_doc.get("isDependentNode", False)), graph_doc.get("parentNodeId") or None

def _record_name_from_graph_doc(doc: dict[str, Any]) -> str:
    name = doc.get("recordName") or doc.get("record_name")
    if name:
        return str(name)
    external_id = doc.get("externalRecordId") or doc.get("external_record_id")
    if external_id:
        return str(external_id)
    return "Unknown"

# Linked record context (shared by dependent parent + record relations)

def _merge_graph_into_blob_record(
    blob_record: dict[str, Any],
    base_doc: dict[str, Any] | None,
) -> dict[str, Any]:
    """Merge graph base doc fields into a blob record (blob values take priority via setdefault)."""
    if not base_doc or not isinstance(base_doc, dict):
        return blob_record
    merged = dict(blob_record)
    record_key = base_doc.get("id") or base_doc.get("_key")
    if record_key:
        merged["id"] = record_key
    for graph_key, record_key_name in _GRAPH_TO_RECORD_FIELDS.items():
        val = base_doc.get(graph_key)
        if val:
            merged.setdefault(record_key_name, val)
    return merged

def _build_record_dict_from_graph_base(base_doc: dict[str, Any]) -> dict[str, Any]:
    """Convert a graph base doc (camelCase) to a blob-like record_dict (snake_case)."""
    record_dict: dict[str, Any] = {
        "id": base_doc.get("id") or base_doc.get("_key", ""),
        "version": base_doc.get("version", 1),
        "semantic_metadata": {},
    }
    for graph_key, record_key_name in _GRAPH_TO_RECORD_FIELDS.items():
        record_dict[record_key_name] = base_doc.get(graph_key) or ""
    record_dict["source_created_at"] = base_doc.get("sourceCreatedAtTimestamp")
    record_dict["source_updated_at"] = base_doc.get("sourceLastModifiedTimestamp")
    return record_dict

async def _fetch_type_specific_doc(
    graph_provider: IGraphDBProvider,
    record_id: str,
    record_type: str | None,
) -> dict[str, Any] | None:
    """Fetch the type-specific graph doc (tickets, mails, etc.) for a record."""
    if not record_type or not record_id:
        return None
    collection = collection_map.get(record_type)
    if not collection:
        return None
    try:
        doc = await graph_provider.get_document(record_id, collection)
        return doc if doc else None
    except Exception:
        return None


async def _fetch_type_specific_docs_batched(
    graph_provider: IGraphDBProvider,
    record_ids: Iterable[str],
    doc_index: dict[str, dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    """Resolve type-specific docs for many records, one query per collection.

    Each record otherwise costs its own `get_document`, and a turn enriches every
    linked record it found. Records whose type has no entry in `collection_map`
    are skipped rather than queried, matching `_fetch_type_specific_doc`.

    A collection whose query fails is simply absent from the result; the caller
    falls back to the per-record path for those ids.
    """
    by_collection: dict[str, list[str]] = {}
    for rid in record_ids:
        collection = collection_map.get((doc_index.get(rid) or {}).get("recordType"))
        if collection:
            by_collection.setdefault(collection, []).append(rid)
    if not by_collection:
        return {}

    async def _one(collection: str, ids: list[str]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for start in range(0, len(ids), GRAPH_BATCH_CHUNK_SIZE):
            out.extend(
                await graph_provider.get_nodes_by_field_in(
                    collection, "id", ids[start:start + GRAPH_BATCH_CHUNK_SIZE]
                )
                or []
            )
        return out

    collections = list(by_collection)
    results = await asyncio.gather(
        *[_one(c, by_collection[c]) for c in collections], return_exceptions=True
    )
    resolved: dict[str, dict[str, Any]] = {}
    for collection, result in zip(collections, results):
        if isinstance(result, Exception):
            logger.debug(
                "Linked record context: type-doc batch failed for %s: %s", collection, result
            )
            continue
        for node in result:
            key = (node or {}).get("id") or (node or {}).get("_key")
            if key:
                resolved[key] = node
    return resolved

def _base_record_context_metadata_from_graph(
    base_graph_doc: dict[str, Any],
    frontend_url: str | None = None,
) -> str:
    """Format metadata from the graph base record doc only (records collection)."""
    record_id = base_graph_doc.get("id") or base_graph_doc.get("_key") or "N/A"
    record_name = base_graph_doc.get("recordName") or "N/A"
    connector_name = base_graph_doc.get("connectorName") or "N/A"
    record_type = base_graph_doc.get("recordType") or "N/A"
    external_id = base_graph_doc.get("externalRecordId") or "N/A"
    connector_id = base_graph_doc.get("connectorId") or "N/A"
    mime_type = base_graph_doc.get("mimeType")
    web_url = base_graph_doc.get("webUrl")

    lines = [
        f"Record ID: {record_id}",
        f"Name: {record_name}",
        f"Connector: {connector_name}",
        f"Type: {record_type}",
        f"External ID: {external_id}",
        f"Connector ID: {connector_id or 'N/A'}",
    ]
    if mime_type:
        lines.append(f"MIME Type: {mime_type}")
    if web_url:
        if not str(web_url).startswith("http") and frontend_url:
            web_url = f"{frontend_url.rstrip('/')}/{str(web_url).lstrip('/')}"
        lines.append(f"Web URL: {web_url}")
    return "\n".join(lines)

async def _build_linked_record_context_metadata(
    record_id: str,
    graph_provider: IGraphDBProvider,
    doc_index: dict[str, dict[str, Any]],
    frontend_url: str | None,
    *,
    vrid: str | None = None,
    blob_store: Any = None,
    org_id: str = "",
    lookup_result: dict[str, Any] | None = None,
    type_doc: dict[str, Any] | None = None,
) -> str | None:
    """Build linked-record context (metadata + type fields + summary, no blocks).

    The base doc is guaranteed present in doc_index by the caller. When the record
    is indexed (has a vrid), the blob supplies the summary; otherwise metadata only.

    `lookup_result` and `type_doc` are pre-resolved by the caller in one batched
    query each. Both fall back to the per-record path when absent, so a batch
    miss costs a query rather than losing the field.
    """
    base_doc = doc_index.get(record_id)
    if not base_doc or not isinstance(base_doc, dict):
        return None
    try:
        blob_record = None
        if vrid and blob_store and org_id:
            try:
                blob_record = await blob_store.get_record_from_storage(
                    vrid, org_id, lookup_result=lookup_result
                )
            except Exception as e:
                logger.debug(
                    "Linked record context: blob fetch failed for %s (vrid=%s): %s",
                    record_id, vrid, e,
                )

        if blob_record and isinstance(blob_record, dict):
            record_dict = _merge_graph_into_blob_record(blob_record, base_doc)
        else:
            record_dict = _build_record_dict_from_graph_base(base_doc)

        type_graph_doc = type_doc
        if type_graph_doc is None:
            type_graph_doc = await _fetch_type_specific_doc(
                graph_provider, record_id, record_dict.get("record_type")
            )
        record_instance = create_record_instance_from_dict(record_dict, type_graph_doc)
        if record_instance:
            return record_instance.to_llm_linked_context(frontend_url)

        return _base_record_context_metadata_from_graph(base_doc, frontend_url=frontend_url)
    except Exception as e:
        logger.warning(
            "Linked record context: failed for %s: %s", record_id, str(e),
        )
        return None

# Graph context enrichment (dependent parents + record relations)

def _relation_display_label(relation: RecordRelations, outgoing: bool) -> str:
    if relation == RecordRelations.ATTACHMENT:
        return "ATTACHMENT" if outgoing else "PARENT"
    if relation == RecordRelations.PARENT_CHILD:
        return "CHILD" if outgoing else "PARENT"
    return relation.value


async def _relations_per_record(
    graph_provider: IGraphDBProvider,
    record_ids: list[str],
    relation_types: list[str],
) -> dict[str, dict[str, list[dict[str, Any]]]]:
    """Per-record equivalent of `get_record_relations_batch`, used when the
    batch fails. Concurrent, and a failing pair costs only itself."""
    async def one(record_id: str, relation_type: str, *, outgoing: bool) -> list[dict[str, Any]]:
        fetch = (
            graph_provider.get_parent_record_ids_by_relation_type
            if outgoing
            else graph_provider.get_child_record_ids_by_relation_type
        )
        return await fetch(record_id, relation_type)

    jobs = [
        (record_id, relation_type, outgoing)
        for record_id in record_ids
        for relation_type in relation_types
        for outgoing in (True, False)
    ]
    results = await asyncio.gather(
        *[one(rid, rel, outgoing=out) for rid, rel, out in jobs],
        return_exceptions=True,
    )

    out: dict[str, dict[str, list[dict[str, Any]]]] = {
        record_id: {"parents": [], "children": []} for record_id in record_ids
    }
    for (record_id, relation_type, outgoing), result in zip(jobs, results):
        if isinstance(result, BaseException):
            logger.warning(
                "Graph context enrichment: %s edges failed for %s: %s",
                relation_type, record_id, result,
            )
            continue
        bucket = "parents" if outgoing else "children"
        for edge in result or []:
            out[record_id][bucket].append({**edge, "relationType": relation_type})
    return out


async def _fetch_edges_for_records(
    graph_provider: IGraphDBProvider,
    record_ids: list[str],
) -> dict[str, list[tuple[str, str]]]:
    """Return {record_id: [(related_record_id, display_label), ...]} from graph edges.

    Graph API naming is edge-direction based, not familial role:
      parents  → records this hit points to (_from == hit)
      children → records pointing to this hit (_to == hit)

    Batched across records and relation types: the per-record form cost one
    query per relation type per direction, so a turn enriching every hit spent
    4x its hit count on round trips.
    """
    if not record_ids:
        return {}
    by_value = {rel.value: rel for rel in RECORD_RELATION_ENRICHMENT_TYPES}
    try:
        relations = await graph_provider.get_record_relations_batch(
            record_ids, list(by_value),
        )
    except Exception as e:
        # Returning {} here drops linked-record context for every hit in the
        # turn. The per-record form this replaced used return_exceptions=True
        # and lost only the failing pair, so fall back to it rather than
        # degrading the whole turn on one bad batch.
        logger.warning(
            "Graph context enrichment: batch edge query failed, per-record fallback: %s", e
        )
        relations = await _relations_per_record(graph_provider, record_ids, list(by_value))

    out: dict[str, list[tuple[str, str]]] = {}
    for record_id in record_ids:
        buckets = relations.get(record_id) or {}
        edges: list[tuple[str, str]] = []
        for bucket, outgoing in (("parents", True), ("children", False)):
            for edge in buckets.get(bucket) or []:
                if not isinstance(edge, dict):
                    continue
                related_id = edge.get("record_id")
                relation = by_value.get(edge.get("relationType"))
                if related_id and relation:
                    edges.append((related_id, _relation_display_label(relation, outgoing)))
        out[record_id] = edges
    return out


async def resolve_frontend_url(
    config_service: ConfigurationService | None,
) -> str | None:
    """Resolve frontend public URL from config service."""
    if not config_service:
        return None
    try:
        endpoints_config = await config_service.get_config(
            config_node_constants.ENDPOINTS.value, default={},
        )
        if isinstance(endpoints_config, dict):
            return endpoints_config.get("frontend", {}).get("publicEndpoint")
    except Exception:
        pass
    return None


def _classify_hits(
    virtual_record_id_to_result: dict[str, dict[str, Any]],
    virtual_to_record_map: dict[str, dict[str, Any]] | None,
) -> tuple[dict[str, str], list[tuple[str, str, dict[str, Any]]]]:
    """Classify hits into dependent parents and relation-eligible records.

    Returns:
        (dependent_vrid_to_parent_id, relation_eligible list of (vrid, record_id, record))
    """
    dependent_vrid_to_parent_id: dict[str, str] = {}
    relation_eligible: list[tuple[str, str, dict[str, Any]]] = []

    for vrid, record in virtual_record_id_to_result.items():
        if not record or not isinstance(record, dict):
            continue
        connector_name = record.get("connector_name")
        if not connector_name and virtual_to_record_map:
            connector_name = (virtual_to_record_map.get(vrid) or {}).get("connectorName")
        if not _supports_graph_context_enrichment(connector_name):
            continue

        is_dependent, parent_id = _resolve_dependent_graph_fields(vrid, virtual_to_record_map)
        if is_dependent and parent_id:
            dependent_vrid_to_parent_id[vrid] = parent_id
        elif not is_dependent:
            hit_record_id = record.get("id")
            if hit_record_id:
                relation_eligible.append((vrid, hit_record_id, record))

    return dependent_vrid_to_parent_id, relation_eligible


def _build_relation_buckets(
    relation_eligible: list[tuple[str, str, dict[str, Any]]],
    edge_results: list,
) -> tuple[list[tuple[str, dict[str, Any], dict[str, dict[str, Any]]]], set[str]]:
    """Build per-record relation buckets from edge results.

    Excludes self-references. Returns (buckets, all_related_ids).
    """
    buckets: list[tuple[str, dict[str, Any], dict[str, dict[str, Any]]]] = []
    all_related_ids: set[str] = set()

    for (vrid, hit_record_id, record), edges in zip(relation_eligible, edge_results):
        if isinstance(edges, Exception):
            continue
        exclude_ids = {hit_record_id}
        bucket: dict[str, dict[str, Any]] = {}
        for related_id, label in edges:
            if related_id not in exclude_ids:
                if related_id not in bucket:
                    bucket[related_id] = {"record_id": related_id, "labels": set()}
                bucket[related_id]["labels"].add(label)
        if bucket:
            all_related_ids.update(bucket)
            buckets.append((vrid, record, bucket))

    return buckets, all_related_ids


async def _resolve_target_metadata(
    all_target_ids: set[str],
    doc_index: dict[str, dict[str, Any]],
    graph_provider: IGraphDBProvider,
    in_context_ids: set[str],
    frontend_url: str | None,
    blob_store: Any,
    org_id: str,
) -> dict[str, str]:
    """Batch-resolve graph docs and context metadata for all target IDs.

    Returns context_map: out-of-context record id -> rendered metadata.
    """
    ids_needing_docs = [rid for rid in all_target_ids if rid not in doc_index]

    async def _fetch_docs() -> dict[str, dict[str, Any]]:
        """One query per chunk instead of one per record.

        Ids the batch does not return are absent from the result, which is what
        the caller already did with a `get_document` that returned None. If the
        batch itself fails, fall back to the per-id path rather than dropping
        every linked record's metadata.
        """
        if not ids_needing_docs:
            return {}
        collection = CollectionNames.RECORDS.value
        try:
            nodes: list[dict[str, Any]] = []
            for start in range(0, len(ids_needing_docs), GRAPH_BATCH_CHUNK_SIZE):
                nodes.extend(
                    await graph_provider.get_nodes_by_field_in(
                        collection, "id", ids_needing_docs[start:start + GRAPH_BATCH_CHUNK_SIZE]
                    )
                    or []
                )
        except Exception as e:
            logger.warning("Linked record context: batch doc fetch failed, per-id fallback: %s", e)
            nodes = []

        resolved: dict[str, dict[str, Any]] = {}
        for node in nodes:
            key = (node or {}).get("id") or (node or {}).get("_key")
            if key:
                resolved[key] = node

        # Recover on id coverage, not on an exception: get_nodes_by_field_in
        # catches its own errors and returns [], so a dead database is
        # indistinguishable from "no rows" and the except above never fires.
        # Anything the batch did not account for is retried individually.
        missing = [rid for rid in ids_needing_docs if rid not in resolved]
        if missing:
            per_id = await asyncio.gather(
                *[graph_provider.get_document(rid, collection) for rid in missing],
                return_exceptions=True,
            )
            resolved.update({
                rid: doc
                for rid, doc in zip(missing, per_id)
                if isinstance(doc, dict) and doc
            })
        return resolved

    doc_results, vrid_result = await asyncio.gather(
        _fetch_docs(),
        graph_provider.get_virtual_record_ids_for_record_ids(list(all_target_ids)),
        return_exceptions=True,
    )

    # Populate doc_index from fetched docs
    if ids_needing_docs and not isinstance(doc_results, Exception):
        for rid, doc in doc_results.items():
            if doc and isinstance(doc, dict):
                doc_index[rid] = doc

    # Build id -> vrid mapping
    id_to_vrid: dict[str, str] = {}
    if not isinstance(vrid_result, Exception) and isinstance(vrid_result, dict):
        id_to_vrid = vrid_result

    # Build context for out-of-context, non-deleted IDs
    out_of_context_ids = [
        rid for rid in all_target_ids
        if rid not in in_context_ids
        and doc_index.get(rid) and not doc_index.get(rid, {}).get("isDeleted")
    ]

    context_map: dict[str, str] = {}
    if out_of_context_ids:
        # Resolve both per-record lookups once for the whole batch. Each linked
        # record otherwise pays its own virtual-record mapping query inside
        # get_record_from_storage, plus its own type-specific get_document.
        blob_lookups: dict[str, dict[str, Any]] = {}
        vrids_to_resolve = [
            id_to_vrid[rid] for rid in out_of_context_ids if rid in id_to_vrid
        ]
        if blob_store and org_id and vrids_to_resolve:
            try:
                resolved_lookups = await blob_store.get_document_ids_by_virtual_record_ids(
                    vrids_to_resolve
                )
                # Anything but a mapping means the pre-resolve did not happen;
                # passing it through would hand the fetch a bogus lookup instead
                # of letting it resolve the id itself.
                if isinstance(resolved_lookups, dict):
                    blob_lookups = resolved_lookups
            except Exception as e:
                logger.warning(
                    "Linked record context: batch virtual-record lookup failed, "
                    "resolving per record: %s", e,
                )
        type_docs = await _fetch_type_specific_docs_batched(
            graph_provider, out_of_context_ids, doc_index
        )

        ctx_results = await asyncio.gather(
            *[
                _build_linked_record_context_metadata(
                    rid, graph_provider, doc_index, frontend_url,
                    vrid=id_to_vrid.get(rid),
                    blob_store=blob_store if rid in id_to_vrid else None,
                    org_id=org_id,
                    lookup_result=blob_lookups.get(id_to_vrid.get(rid) or ""),
                    type_doc=type_docs.get(rid),
                )
                for rid in out_of_context_ids
            ],
            return_exceptions=True,
        )
        for rid, ctx in zip(out_of_context_ids, ctx_results):
            if not isinstance(ctx, Exception) and ctx:
                context_map[rid] = ctx

    return context_map


def _annotate_dependent_parents(
    dependent_vrid_to_parent_id: dict[str, str],
    flattened_results: list[dict[str, Any]],
    in_context_ids: set[str],
    doc_index: dict[str, dict[str, Any]],
    context_map: dict[str, str],
) -> None:
    """Attach parent_node_relation to flattened_results for dependent records."""
    unique_parent_ids = set(dependent_vrid_to_parent_id.values())
    parent_id_to_metadata: dict[str, dict[str, Any]] = {}

    for parent_id in unique_parent_ids:
        if parent_id in in_context_ids:
            doc = doc_index.get(parent_id)
            record_name = _record_name_from_graph_doc(doc) if doc else "Unknown"
            parent_id_to_metadata[parent_id] = {
                "record_id": parent_id,
                "record_name": record_name,
            }
        elif parent_id in context_map:
            parent_id_to_metadata[parent_id] = {
                "record_id": parent_id,
                "context_metadata": context_map[parent_id],
            }

    annotated = 0
    for result in flattened_results:
        vrid = result.get("virtual_record_id")
        if not vrid or vrid not in dependent_vrid_to_parent_id:
            continue
        parent_id = dependent_vrid_to_parent_id[vrid]
        if parent_id in parent_id_to_metadata:
            result["parent_node_relation"] = parent_id_to_metadata[parent_id]
            annotated += 1

    logger.info(
        "Dependent parent enrichment: %d dependents, %d rows annotated",
        len(dependent_vrid_to_parent_id), annotated,
    )


def _annotate_record_relations(
    relation_buckets: list[tuple[str, dict[str, Any], dict[str, dict[str, Any]]]],
    doc_index: dict[str, dict[str, Any]],
    in_context_ids: set[str],
    context_map: dict[str, str],
) -> None:
    """Attach record_relations to hit records from relation buckets."""
    enriched_count = 0
    for vrid, record, bucket in relation_buckets:
        relations: list[dict[str, Any]] = []
        for rid, entry in bucket.items():
            doc = doc_index.get(rid)
            if not doc or doc.get("isDeleted"):
                continue
            rel: dict[str, Any] = {
                "record_id": rid,
                "record_name": _record_name_from_graph_doc(doc),
                "labels": sorted(entry["labels"]),
            }
            if rid not in in_context_ids and rid in context_map:
                rel["context_metadata"] = context_map[rid]
            relations.append(rel)
        if relations:
            record["record_relations"] = relations
            enriched_count += 1

    logger.info("Record relation enrichment: %d records enriched", enriched_count)


async def enrich_records_with_graph_context(
    virtual_record_id_to_result: dict[str, dict[str, Any]],
    graph_provider: IGraphDBProvider | None = None,
    flattened_results: list[dict[str, Any]] | None = None,
    virtual_to_record_map: dict[str, dict[str, Any]] | None = None,
    doc_index: dict[str, dict[str, Any]] | None = None,
    blob_store: Any = None,
    org_id: str = "",
    config_service: "ConfigurationService | None" = None,
) -> None:
    """
    Unified graph context enrichment for search results. Performs both:
      1. Dependent parent annotation (isDependentNode -> parent metadata on flattened_results)
      2. Record relation enrichment (graph edges -> record_relations on hit records)

    All graph/blob calls are batched and deduplicated across both paths.
    """
    if not graph_provider or flattened_results is None:
        return

    if doc_index is None:
        doc_index = _build_record_id_to_graph_doc_index(virtual_to_record_map)
        _extend_record_id_index_from_hit_records(doc_index, virtual_record_id_to_result)

    frontend_url = await resolve_frontend_url(config_service)
    in_context_ids: set[str] = {
        rec["id"] for rec in virtual_record_id_to_result.values()
        if isinstance(rec, dict) and rec.get("id")
    }

    # Step 1: Classify hits into dependent vs relation-eligible
    dependent_vrid_to_parent_id, relation_eligible = _classify_hits(
        virtual_record_id_to_result, virtual_to_record_map,
    )
    if not dependent_vrid_to_parent_id and not relation_eligible:
        return

    # Step 2: Fetch edges for relation-eligible hits
    edge_results: list = []
    if relation_eligible:
        eligible_ids = [rid for _, rid, _ in relation_eligible]
        edges_by_record = await _fetch_edges_for_records(graph_provider, eligible_ids)
        edge_results = [edges_by_record.get(rid, []) for rid in eligible_ids]

    # Step 3: Build relation buckets from edges
    relation_buckets, all_related_ids = _build_relation_buckets(
        relation_eligible, edge_results,
    )

    # Step 4: Collect all IDs needing resolution (parents + related)
    all_target_ids = all_related_ids | set(dependent_vrid_to_parent_id.values())
    if not all_target_ids:
        return

    # Step 5: Batch resolve docs and build context metadata
    context_map = await _resolve_target_metadata(
        all_target_ids, doc_index, graph_provider,
        in_context_ids, frontend_url, blob_store, org_id,
    )

    # Step 6: Distribute results
    if dependent_vrid_to_parent_id:
        _annotate_dependent_parents(
            dependent_vrid_to_parent_id, flattened_results,
            in_context_ids, doc_index, context_map,
        )
    if relation_buckets:
        _annotate_record_relations(
            relation_buckets, doc_index, in_context_ids, context_map,
        )


def build_parent_info(result: dict[str, Any]) -> str:
    """Build parent record metadata string from a result's parent_node_relation."""
    parent_rel = result.get("parent_node_relation")
    if not parent_rel:
        return ""

    lines = ["\n* This record depends on:"]
    context_metadata = parent_rel.get("context_metadata")
    if context_metadata:
        lines.append(context_metadata)
    else:
        record_id = parent_rel.get("record_id", "")
        record_name = parent_rel.get("record_name", "Unknown")
        lines.append(f"  Record ID: {record_id} | Name: {record_name}")

    parent_info = "\n".join(lines) + "\n"
    return parent_info

def build_record_relations_info(record: dict[str, Any]) -> str:
    """Build related records grouped by relation label (ATTACHMENT/CHILD/PARENT).

    Each label is rendered once as a heading with all its records listed
    underneath, so a label never repeats per row. A record reached via more than
    one relation type appears under each of its labels.
    """
    relations = record.get("record_relations")
    if not relations:
        return ""

    label_to_rels: dict[str, list[dict[str, Any]]] = {}
    for rel in relations:
        if not isinstance(rel, dict):
            continue
        for label in (rel.get("labels") or ["RELATED"]):
            label_to_rels.setdefault(label, []).append(rel)

    if not label_to_rels:
        return ""

    lines = ["\n* Related records:"]
    for label in sorted(label_to_rels):
        lines.append(f"  {label}:")
        for rel in label_to_rels[label]:
            context_metadata = rel.get("context_metadata")
            if context_metadata:
                ctx_lines = context_metadata.split("\n")
                lines.append(f"    - {ctx_lines[0]}")
                # Indent the remaining fields one tab deeper than the "Record ID"
                # line so full-metadata records stand apart from id+name-only rows.
                lines.extend(f"          {ctx_line}" for ctx_line in ctx_lines[1:])
            else:
                record_id = rel.get("record_id", "")
                record_name = rel.get("record_name", "Unknown")
                lines.append(f"    - Record ID: {record_id} | Name: {record_name}")
    return "\n".join(lines) + "\n"

# FK table enrichment (runs before doc_index in chatbot; extends virtual_record_id_to_result)

async def enrich_virtual_record_id_to_result_with_fk_children(
    virtual_record_id_to_result: Dict[str, Dict[str, Any]],
    blob_store: BlobStorage,
    org_id: str,
    graph_provider: Optional[IGraphDBProvider] = None,
    flattened_results: Optional[List[Dict[str, Any]]] = None,
) -> None:
    """
    For each SQL_TABLE record in virtual_record_id_to_result that has child_record_ids
    (FK-related tables) or parent tables (via FK edges), fetch their full blob and add 
    to virtual_record_id_to_result. Also adds DDL block_group to flattened_results
    so the agent context includes DDL for FK-related tables.
    
    Additionally, enriches flattened_results with fk_parent_relations and fk_child_relations
    (each containing record_id, table name, source column, and target column metadata)
    so the agent knows which related tables it can fetch via tools.

    Field naming conventions:
    - Graph DB (ArangoDB) returns camelCase: recordName, recordType, webUrl, hideWeburl, etc.
    - Blob storage returns snake_case (Pydantic model_dump): record_name, record_type, weburl, etc.
    - After graph_rec merge, rec dict is normalized to snake_case keys.
    - Metadata dicts sent to the frontend use camelCase (virtualRecordId, recordName, webUrl, etc.).
    """
    if not graph_provider:
        logger.debug("FK enrichment skipped: no graph_provider provided")
        return
    
    from app.config.constants.arangodb import RecordRelations
    
    related_record_ids = set()
    sql_table_record_ids = []
    record_id_to_fk_relations: Dict[str, Dict[str, List[Dict[str, Any]]]] = {}
    
    flattened_len_before = len(flattened_results) if flattened_results is not None else 0
    logger.debug(
        "FK enrichment: checking %d records; flattened_results=%d items",
        len(virtual_record_id_to_result),
        flattened_len_before,
    )
    if flattened_results is None:
        logger.warning("FK enrichment: flattened_results is None - FK DDL blocks will not be added to context")

    # Build mapping of vrid -> record_id for existing SQL_TABLE records
    # Records come from get_record() which normalizes to snake_case via graphDb_record merge
    vrid_to_record_id: Dict[str, str] = {}
    for vrid, record in virtual_record_id_to_result.items():
        if not record or not isinstance(record, dict):
            continue
        if record.get("record_type") != "SQL_TABLE":
            continue
        record_id = record.get("id")
        if record_id:
            sql_table_record_ids.append(record_id)
            vrid_to_record_id[vrid] = record_id
            logger.debug("FK enrichment: found SQL_TABLE record_id=%s, vrid=%s, name=%s", 
                        record_id, vrid, record.get("record_name"))
    
    logger.debug("FK enrichment: found %d SQL_TABLE records to check for FK relations", len(sql_table_record_ids))
    
    # Query both child and parent tables via FK edges
    for record_id in sql_table_record_ids:
        child_relations = []
        parent_relations = []
        
        try:
            child_relations = await graph_provider.get_child_record_ids_by_relation_type(
                record_id, RecordRelations.FOREIGN_KEY.value
            )
            logger.debug("FK enrichment: record %s has %d child tables", record_id, len(child_relations))
            for rel in child_relations:
                if rel.get("record_id"):
                    related_record_ids.add(rel["record_id"])
        except Exception as e:
            logger.warning("Could not fetch child record IDs for %s: %s", record_id, str(e))
        
        try:
            parent_relations = await graph_provider.get_parent_record_ids_by_relation_type(
                record_id, RecordRelations.FOREIGN_KEY.value
            )
            logger.debug("FK enrichment: record %s has %d parent tables", record_id, len(parent_relations))
            for rel in parent_relations:
                if rel.get("record_id"):
                    related_record_ids.add(rel["record_id"])
        except Exception as e:
            logger.warning("Could not fetch parent record IDs for %s: %s", record_id, str(e))
        
        record_id_to_fk_relations[record_id] = {
            "children": list(child_relations) if not isinstance(child_relations, list) else child_relations,
            "parents": list(parent_relations) if not isinstance(parent_relations, list) else parent_relations,
        }
    
    logger.debug("FK enrichment: total %d related records to fetch", len(related_record_ids))
    
    # Enrich existing flattened_results with FK relations
    if flattened_results is not None:
        for result in flattened_results:
            vrid = result.get("virtual_record_id")
            if not vrid or vrid not in vrid_to_record_id:
                continue
            record_id = vrid_to_record_id[vrid]
            if record_id not in record_id_to_fk_relations:
                continue
            fk_relations = record_id_to_fk_relations[record_id]
            result["fk_parent_relations"] = fk_relations["parents"]
            result["fk_child_relations"] = fk_relations["children"]
    
    if not related_record_ids:
        return
    
    record_id_to_vrid = await graph_provider.get_virtual_record_ids_for_record_ids(list(related_record_ids))
    logger.debug("FK enrichment: resolved %d record_ids to virtual_record_ids", len(record_id_to_vrid))
    
    # Build set of vrids already in flattened_results
    vrids_in_flattened = set()
    if flattened_results is not None:
        vrids_in_flattened = {r.get("virtual_record_id") for r in flattened_results if r.get("virtual_record_id")}
    
    for record_id, vrid in record_id_to_vrid.items():
        already_in_flattened = vrid in vrids_in_flattened
        
        if vrid in virtual_record_id_to_result:
            rec = virtual_record_id_to_result[vrid]
            if already_in_flattened:
                continue
        else:
            try:
                # Blob returns snake_case keys (Pydantic model_dump)
                rec = await blob_store.get_record_from_storage(virtual_record_id=vrid, org_id=org_id)
                if not rec:
                    logger.warning("FK enrichment: could not fetch blob for vrid %s", vrid)
                    virtual_record_id_to_result[vrid] = None
                    continue
                # Graph DB returns camelCase — normalize to snake_case on rec
                try:
                    graph_rec = await graph_provider.get_document(
                        record_id, CollectionNames.RECORDS.value
                    )
                    if graph_rec and isinstance(graph_rec, dict):
                        rec["id"] = record_id
                        rec["org_id"] = graph_rec.get("orgId")
                        rec["record_name"] = graph_rec.get("recordName") 
                        rec["record_type"] = graph_rec.get("recordType")
                        rec["version"] = graph_rec.get("version")
                        rec["origin"] = graph_rec.get("origin")
                        rec["connector_name"] = graph_rec.get("connectorName")
                        rec["connector_id"] = graph_rec.get("connectorId")
                        rec["preview_renderable"] = graph_rec.get("previewRenderable", True)
                        rec["mime_type"] = graph_rec.get("mimeType")
                        rec["weburl"] = graph_rec.get("webUrl")
                        rec["hide_weburl"] = graph_rec.get("hideWeburl", False)
                        rec["source_created_at"] = graph_rec.get("sourceCreatedAtTimestamp")
                        rec["source_updated_at"] = graph_rec.get("sourceLastModifiedTimestamp")

                except Exception as graph_e:
                    logger.debug("FK enrichment: could not fetch graph metadata for record_id=%s: %s", record_id, graph_e)
                virtual_record_id_to_result[vrid] = rec
                logger.debug("FK enrichment: fetched blob for %s (record_id=%s)", rec.get("record_name"), record_id)
            except Exception as e:
                logger.debug("Could not fetch blob for FK related vrid %s: %s", vrid, str(e))
                virtual_record_id_to_result[vrid] = None
                continue
        
        if not rec:
            continue
        
        # Add DDL block_group to flattened_results so it appears in agent context.
        # After graph_rec merge above, all rec keys are snake_case.
        # Blob uses: block_containers -> block_groups, blocks (snake_case).
        if flattened_results is not None:
            block_containers = rec.get("block_containers", {})
            block_groups = block_containers.get("block_groups", [])
            rec_name = rec.get("record_name", "")
            if not block_groups:
                logger.warning("FK enrichment: no block_groups for vrid=%s", vrid)
            added = False
            for bg_index, bg in enumerate(block_groups):
                bg_type = bg.get("type", "")
                if bg_type != BlockType.TABLE.value and bg_type != "table":
                    continue
                data = bg.get("data") or {}
                if isinstance(data, dict):
                    table_summary = data.get("table_summary", "")
                    ddl = data.get("ddl", "")
                    if ddl:
                        table_summary = f"DDL:\n{ddl}\n\n{table_summary}"
                else:
                    table_summary = str(data or "")
                
                # Extract first 2 sample rows
                blocks = block_containers.get("blocks", [])
                sample_rows = []
                for block in blocks[:2]:
                    if block.get("type") == "table_row":
                        block_data = block.get("data", {})
                        if isinstance(block_data, dict):
                            row_text = block_data.get("row_natural_language_text", "")
                            if row_text:
                                sample_rows.append(row_text)
                
                if sample_rows:
                    table_summary = f"{table_summary}\n\nSample Rows:\n" + "\n".join(sample_rows)
                
                # Get FK relations for this record if available, otherwise fetch them
                fk_parent_relations = []
                fk_child_relations = []
                if record_id in record_id_to_fk_relations:
                    fk_parent_relations = record_id_to_fk_relations[record_id]["parents"]
                    fk_child_relations = record_id_to_fk_relations[record_id]["children"]
                else:
                    try:
                        fk_child_relations = await graph_provider.get_child_record_ids_by_relation_type(
                            record_id, RecordRelations.FOREIGN_KEY.value
                        )
                        fk_child_relations = list(fk_child_relations) if not isinstance(fk_child_relations, list) else fk_child_relations
                    except Exception as e:
                        logger.debug("Could not fetch child record IDs for %s: %s", record_id, str(e))
                    try:
                        fk_parent_relations = await graph_provider.get_parent_record_ids_by_relation_type(
                            record_id, RecordRelations.FOREIGN_KEY.value
                        )
                        fk_parent_relations = list(fk_parent_relations) if not isinstance(fk_parent_relations, list) else fk_parent_relations
                    except Exception as e:
                        logger.debug("Could not fetch parent record IDs for %s: %s", record_id, str(e))
                    record_id_to_fk_relations[record_id] = {
                        "children": fk_child_relations,
                        "parents": fk_parent_relations,
                    }
                    
                
                # Build flattened result entry
                # rec keys are snake_case; metadata dict uses camelCase for frontend
                enhanced_metadata = get_enhanced_metadata(rec, bg, {})
                enhanced_metadata["virtualRecordId"] = vrid
                enhanced_metadata["source"] = "FK_ENRICHMENT"
                flattened_results.append({
                    "virtual_record_id": vrid,
                    "record_id": record_id,
                    "record_name": rec_name,
                    "block_index": bg_index,
                    "isBlockGroup": True,
                    "block_group_index": bg_index,
                    "block_type": GroupType.TABLE.value,
                    "content": (table_summary, []),
                    "fk_parent_relations": fk_parent_relations,
                    "fk_child_relations": fk_child_relations,
                    "metadata": enhanced_metadata,
                })
                logger.debug(
                    "FK enrichment: added DDL block_group for %s to flattened_results (len now=%d)",
                    rec_name or vrid,
                    len(flattened_results),
                )
                added = True
                break  # Only add the first table block_group (DDL)
            if not added:
                logger.warning(
                    "FK enrichment: no table block_group found for vrid=%s (block_groups=%d)",
                    vrid,
                    len(block_groups),
                )

    if flattened_results is not None:
        fk_count = sum(1 for r in flattened_results if (r.get("metadata") or {}).get("source") == "FK_ENRICHMENT")
        logger.info(f"FK enrichment:complete for SQL tables")
        logger.debug(
            f"FK enrichment: done. flattened_results len before=%d after=%d (FK_ENRICHMENT blocks=%d)",
            flattened_len_before,
            len(flattened_results),
            fk_count,
        )

async def get_flattened_results(result_set: List[Dict[str, Any]], blob_store: BlobStorage, org_id: str, is_multimodal_llm: bool, virtual_record_id_to_result: Dict[str, Dict[str, Any]],virtual_to_record_map: Dict[str, Dict[str, Any]]=None,from_tool: bool = False,from_retrieval_service: bool = False,graph_provider: Optional[IGraphDBProvider] = None) -> List[Dict[str, Any]]:
    flattened_results = []
    image_index = 0
    seen_chunks = set()
    adjacent_chunks = {}
    new_type_results = []
    old_type_results = []
    # Cache for reconciliation metadata per virtual_record_id (block_id -> index mapping)
    virtual_record_id_to_recon_metadata: Dict[str, Optional[Dict[str, Any]]] = {}
    # Cache for fragment maps per virtual_record_id (container_index → fragment children)
    fragment_maps: Dict[str, Dict[int, list]] = {}
    if from_retrieval_service:
        new_type_results = result_set
    else:
        for result in result_set:
            meta = result.get("metadata")
            is_block_group = meta.get("isBlockGroup")
            if is_block_group is not None:
                new_type_results.append(result)
            else:
                old_type_results.append(result)

    sorted_new_type_results = sorted(new_type_results, key=lambda x: not x.get("metadata", {}).get("isBlockGroup", False))
    rows_to_be_included = defaultdict[Any, list](list)

    records_to_fetch = set()
    for result in sorted_new_type_results:
        virtual_record_id = result["metadata"].get("virtualRecordId")

        if virtual_record_id and virtual_record_id not in virtual_record_id_to_result:
            records_to_fetch.add(virtual_record_id)

    # Fetch frontend URL once for all records
    #!!!
    frontend_url = None
    try:
        endpoints_config = await blob_store.config_service.get_config(
            config_node_constants.ENDPOINTS.value,
            default={}
        )
        if isinstance(endpoints_config, dict):
            frontend_url = endpoints_config.get("frontend", {}).get("publicEndpoint")
    except Exception as e:
        logger.warning(f"Failed to fetch frontend URL from config service: {str(e)}")

    # One mapping query for the whole batch instead of one (plus a fallback) per
    # record; each get_record then goes straight to the download.
    batched_lookups: dict[str, Any] = {}
    if records_to_fetch:
        try:
            batched_lookups = await blob_store.get_document_ids_by_virtual_record_ids(
                list(records_to_fetch)
            )
        except Exception as e:
            logger.warning("Batch virtual-record lookup failed, resolving per record: %s", str(e))

    # Type-specific metadata (ticket status, mail sender, ...) is one graph query
    # per record inside get_record. The record type is already known here, so
    # resolve the whole batch with one query per collection instead.
    type_docs: dict[str, dict[str, Any]] = {}
    if records_to_fetch and graph_provider:
        # Records reach here carrying `_key` rather than `id` (the graph write
        # path moves `id` into `_key`), so keying on `id` alone matched nothing
        # and the batch silently no-opped back to one query per record.
        by_record_id = {
            key: gdb
            for vrid in records_to_fetch
            if isinstance(gdb := (virtual_to_record_map or {}).get(vrid), dict)
            and (key := gdb.get("id") or gdb.get("_key"))
        }
        if by_record_id:
            type_docs = await _fetch_type_specific_docs_batched(
                graph_provider, list(by_record_id), by_record_id
            )

    await asyncio.gather(*[get_record(virtual_record_id,virtual_record_id_to_result,blob_store,org_id,virtual_to_record_map,graph_provider,frontend_url,batched_lookups.get(virtual_record_id),type_docs) for virtual_record_id in records_to_fetch])
    # Prefetch reconciliation metadata in parallel (records were fully fetched above).
    vrids_needing_recon: set = set[Any]()

    for result in sorted_new_type_results:
        vrid = result["metadata"].get("virtualRecordId")
        meta = result.get("metadata")
        if meta.get("blockIndex") is None and meta.get("blockId") and vrid and vrid not in virtual_record_id_to_recon_metadata:
            vrids_needing_recon.add(vrid)

    async def _prefetch_recon(vrid: str):
        try:
            recon = await blob_store.get_reconciliation_metadata(vrid, org_id)
            virtual_record_id_to_recon_metadata[vrid] = recon
        except Exception as e:
            logger.warning("Failed to prefetch reconciliation metadata for %s: %s", vrid, str(e))
            virtual_record_id_to_recon_metadata[vrid] = None

    if vrids_needing_recon:
        await asyncio.gather(*[_prefetch_recon(vrid) for vrid in vrids_needing_recon])

    for result in sorted_new_type_results:
        virtual_record_id = result["metadata"].get("virtualRecordId")
        if not virtual_record_id:
            continue
        result["virtual_record_id"] = virtual_record_id

        meta = result.get("metadata")

        if meta.get("isRecordSummary"):
            chunk_id = f"{virtual_record_id}-record_summary"
            if chunk_id in seen_chunks:
                continue
            seen_chunks.add(chunk_id)
            record = virtual_record_id_to_result.get(virtual_record_id)
            if record is None:
                continue
            content_text = result.get("content", "")
            if not content_text:
                continue
            flattened_results.append({
                "content": content_text,
                "block_type": BlockType.RECORD_SUMMARY.value,
                "virtual_record_id": virtual_record_id,
                "block_index": None,
                "metadata": get_enhanced_metadata(record=record,block=None, meta=meta),
                "score": float(result.get("score", 0.0)),
                "citationType": "vectordb|document",
            })
            continue

        if virtual_record_id not in adjacent_chunks:
            adjacent_chunks[virtual_record_id] = []

        index = meta.get("blockIndex")
        is_block_group = meta.get("isBlockGroup")

        if index is None:
            block_id = meta.get("blockId")
            if block_id:
                recon_metadata = virtual_record_id_to_recon_metadata.get(virtual_record_id)
                if recon_metadata:
                    block_id_to_index = recon_metadata.get("block_id_to_index", {})
                    rm = ReconciliationMetadata.from_dict(recon_metadata)
                    index_val = rm.block_id_to_index.get(block_id)
                    if index_val is not None:
                        index = index_val
                        meta["blockIndex"] = index

        # Skip if index is still None - cannot access blocks without a valid index
        if index is None:
            logger.warning(
                f"Skipping result with None blockIndex - "
                f"virtual_record_id: {virtual_record_id}, "
                f"is_block_group: {is_block_group}, "
                f"metadata keys: {list(meta.keys()) if meta else 'None'}, "
                f"full metadata: {meta}"
            )
            continue
            
        if is_block_group:
            chunk_id = f"{virtual_record_id}-{index}-block_group"
        else:
            chunk_id = f"{virtual_record_id}-{index}"

        if chunk_id in seen_chunks:
            continue
        seen_chunks.add(chunk_id)

        record = virtual_record_id_to_result[virtual_record_id]
        if record is None:
            continue
        block_container = record.get("block_containers",{})
        blocks = block_container.get("blocks",[])
        block_groups = block_container.get("block_groups",[])

        if is_block_group:
            if index >= len(block_groups):
                logger.warning(
                    "Block group index %d out of bounds (len=%d), vrid=%s",
                    index, len(block_groups), virtual_record_id,
                )
                continue
            block = block_groups[index]
        else:
            if index >= len(blocks):
                qdrant_content = result.get("content", "")
                bg_index = 0
                if record.get("record_type") == RecordType.SQL_TABLE.value and qdrant_content:
                    rows_to_be_included[f"{virtual_record_id}_{bg_index}"].append(
                        (index, float(result.get("score", 0.0)), qdrant_content)
                    )
                    logger.debug(f"Index Out of Bounds: Added row to rows_to_be_included for {qdrant_content}")
                else:
                    logger.warning(
                        "Block index %d out of bounds (len=%d), vrid=%s",
                        index, len(blocks), virtual_record_id,
                    )
                continue
            block = blocks[index]

        block_type = block.get("type")
        result["block_type"] = block_type

        # Fragment block: split from a container due to inline images inside a group.
        # Route through the container's parent group rather than treating as standalone.
        parent_block_idx = block.get("parent_block_index")
        if parent_block_idx is not None:
            if parent_block_idx >= len(blocks):
                continue
            container = blocks[parent_block_idx]
            container_parent_index = container.get("parent_index")
            if container_parent_index is None:
                continue
            if container.get("type") == BlockType.TABLE_ROW.value:
                # Fragment of a split table row → add the container to rows_to_be_included.
                container_chunk_id = f"{virtual_record_id}-{parent_block_idx}"
                if container_chunk_id not in seen_chunks:
                    seen_chunks.add(container_chunk_id)
                    rows_to_be_included[f"{virtual_record_id}_{container_parent_index}"].append(
                        (parent_block_idx, float(result.get("score", 0.0)), None)
                    )
            else:
                target_index = container_parent_index
                group_chunk_id = f"{virtual_record_id}-{target_index}-block_group"
                if group_chunk_id in seen_chunks:
                    continue
                fmap = fragment_maps.setdefault(virtual_record_id, _build_fragment_map(blocks))
                group_text_result = get_group_label_n_first_child(block_groups, target_index)
                group_blocks = (
                    build_group_blocks(
                        block_groups, blocks, target_index, virtual_record_id, record, result,
                        is_multimodal_llm=is_multimodal_llm, fragment_map=fmap,
                    )
                    if group_text_result else None
                )

                if not group_text_result or not group_blocks:
                    continue
                seen_chunks.add(group_chunk_id)
                label, first_child_block_index = group_text_result
                result["content"] = ("", group_blocks)
                result["block_type"] = label
                result["virtual_record_id"] = virtual_record_id
                result["block_index"] = first_child_block_index
                result["block_group_index"] = target_index
                result["metadata"] = get_enhanced_metadata(record, blocks[first_child_block_index], meta)
                flattened_results.append(result)
            continue

        if block_type == BlockType.TEXT.value and block.get("parent_index") is None:
            result["content"] = block.get("data","")
            adjacent_chunks[virtual_record_id].append(index-1)
            adjacent_chunks[virtual_record_id].append(index+1)
        elif block_type == BlockType.CODE.value and block.get("parent_index") is None:
            # Without this a top-level code hit is dropped here and never reaches
            # build_message_content_array, however well it scored.
            result["content"] = _safe_stringify_content(block.get("data", ""))
            result["qualified_name"] = block_qualified_name(block)
            adjacent_chunks[virtual_record_id].append(index-1)
            adjacent_chunks[virtual_record_id].append(index+1)
        elif block_type == BlockType.IMAGE.value:
            data = block.get("data")
            if data:
                if from_retrieval_service:
                    result["content"] = f"image_{image_index}"
                    image_index += 1
                else:
                    if is_multimodal_llm:
                        image_uri = data.get("uri")
                        if image_uri:
                            existing = result.get("content", "")
                            if existing and not is_base64_image(existing):
                                result["image_description"] = existing
                            result["content"] = image_uri
                        else:
                            continue
                    else:
                        if result.get("content") and is_base64_image(result.get("content")):
                            continue

                    adjacent_chunks[virtual_record_id].append(index-1)
                    adjacent_chunks[virtual_record_id].append(index+1)
            else:
                continue
        elif block_type == BlockType.TABLE_ROW.value:
            block_group_index = block.get("parent_index")
            rows_to_be_included[f"{virtual_record_id}_{block_group_index}"].append((index,float(result.get("score",0.0)), None))
            continue
        elif block_type == GroupType.TABLE.value:
            table_data = block.get("data",{})
            table_metadata = block.get("table_metadata", {})
            children = block.get("children")

            # Handle both old and new children formats
            if children:
                if isinstance(children, dict) and 'block_ranges' in children:
                    # New range-based format
                    block_ranges = children.get('block_ranges', [])
                    first_block_index = block_ranges[0].get('start') if block_ranges else None
                    last_block_index = block_ranges[-1].get('end') if block_ranges else None
                    # Get all block indices from ranges
                    all_block_indices = []
                    for range_obj in block_ranges:
                        start = range_obj.get('start')
                        end = range_obj.get('end')
                        if start is not None and end is not None:
                            all_block_indices.extend(range(start, end + 1))
                else:
                    # Old format (list of BlockContainerIndex)
                    first_block_index = children[0].get("block_index") if len(children) > 0 else None
                    last_block_index = children[-1].get("block_index") if len(children) > 0 else None
                    all_block_indices = [child.get("block_index") for child in children if child.get("block_index") is not None]
            else:
                first_block_index = None
                last_block_index = None
                all_block_indices = []

            result["block_index"] = first_block_index
            if first_block_index is not None:
                adjacent_chunks[virtual_record_id].append(first_block_index-1)
                adjacent_chunks[virtual_record_id].append(last_block_index+1)

                num_of_cells = table_metadata.get("num_of_cells", None) if isinstance(table_metadata, dict) else None
                if num_of_cells is None:
                    is_large_table = True
                else:
                    is_large_table = num_of_cells > MAX_CELLS_IN_TABLE_THRESHOLD
                table_summary = table_data.get("table_summary","")
                ddl = table_data.get("ddl", "") or ""
                if ddl:
                    table_summary = f"DDL:\n{ddl}\n\n{table_summary}"

                if not is_large_table:
                    child_results=[]
                    for child_block_index in all_block_indices:
                        child_id = f"{virtual_record_id}-{child_block_index}"
                        if child_id in seen_chunks:
                            continue
                        seen_chunks.add(child_id)
                        if child_block_index < len(blocks):
                            child_block = blocks[child_block_index]
                            row_text = child_block.get("data", {}).get("row_natural_language_text", "")
                            if row_text:
                                child_results.append({
                                    "content": row_text,
                                    "block_type": BlockType.TABLE_ROW.value,
                                    "virtual_record_id": virtual_record_id,
                                    "block_index": child_block_index,
                                    "metadata": get_enhanced_metadata(record, child_block, meta),
                                    "score": float(result.get("score",0.0)),
                                    "citationType": "vectordb|document",
                                })
                            else:
                                # Container TABLE_ROW with image-split fragments:
                                # emit each fragment in reading order under the container's block_index.
                                fmap = fragment_maps.setdefault(virtual_record_id, _build_fragment_map(blocks))
                                container_idx = child_block.get("index")
                                if container_idx is not None and container_idx in fmap:
                                    for frag in sorted(fmap[container_idx], key=lambda b: b.get("index", 0)):
                                        frag_type = frag.get("type")
                                        if frag_type == BlockType.TEXT.value:
                                            frag_data = frag.get("data", "")
                                            if frag_data:
                                                child_results.append({
                                                    "content": _safe_stringify_content(frag_data),
                                                    "block_type": BlockType.TEXT.value,
                                                    "virtual_record_id": virtual_record_id,
                                                    "block_index": child_block_index,
                                                    "metadata": get_enhanced_metadata(record, child_block, meta),
                                                    "score": float(result.get("score", 0.0)),
                                                    "citationType": "vectordb|document",
                                                })
                                        elif frag_type == BlockType.IMAGE.value and is_multimodal_llm:
                                            uri = (frag.get("data") or {}).get("uri")
                                            if uri:
                                                child_results.append({
                                                    "content": uri,
                                                    "block_type": BlockType.IMAGE.value,
                                                    "virtual_record_id": virtual_record_id,
                                                    "block_index": child_block_index,
                                                    "metadata": get_enhanced_metadata(record, child_block, meta),
                                                    "score": float(result.get("score", 0.0)),
                                                    "citationType": "vectordb|document",
                                                })

                    table_result = {
                        "content":(table_summary,child_results),
                        "block_type": GroupType.TABLE.value,
                        "virtual_record_id": virtual_record_id,
                        "block_index": first_block_index,
                        "block_group_index": index,
                        "metadata": get_enhanced_metadata(record,block,meta),
                    }
                    flattened_results.append(table_result)
                    continue
                else:
                    rows_to_be_included[f"{virtual_record_id}_{index}"]=[]
                    continue
            else:
                continue
        elif block.get("parent_index") is not None:
            parent_index = block.get("parent_index")

            target_index = parent_index

            fmap = fragment_maps.setdefault(virtual_record_id, _build_fragment_map(blocks))
            group_text_result = get_group_label_n_first_child(block_groups, target_index)
            group_blocks = (
                build_group_blocks(
                    block_groups, blocks, target_index, virtual_record_id, record, result,
                    is_multimodal_llm=is_multimodal_llm, fragment_map=fmap,
                )
                if group_text_result
                else None
            )

            if not group_text_result or not group_blocks:
                continue

            label, first_child_block_index = group_text_result
            result["content"] = ("", group_blocks)
            result["block_type"] = label
            result["virtual_record_id"] = virtual_record_id
            result["block_index"] = first_child_block_index
            result["block_group_index"] = target_index
            result["metadata"] = get_enhanced_metadata(record, blocks[first_child_block_index], meta)
            flattened_results.append(result)
            continue
        else:
            continue


        if "block_index" not in result:
            result["block_index"] = index
        enhanced_metadata = get_enhanced_metadata(record,block,meta)
        result["metadata"] = enhanced_metadata
        flattened_results.append(result)

    for key,rows_tuple in rows_to_be_included.items():
        sorted_rows_tuple = sorted(rows_tuple)
        virtual_record_id,block_group_index = key.split("_")
        block_group_index = int(block_group_index)
        record = virtual_record_id_to_result[virtual_record_id]
        if record is None:
            continue
        block_container = record.get("block_containers",{})
        blocks = block_container.get("blocks",[])
        block_groups = block_container.get("block_groups",[])
        block_group = block_groups[block_group_index]
        data = block_group.get("data", {})
        table_summary = data.get("table_summary","")
        ddl = data.get("ddl", "") or ""
        if ddl:
            table_summary = f"DDL:\n{ddl}\n\n{table_summary}"
        child_results = []
        for row_index, row_score, qdrant_content in sorted_rows_tuple:
            if row_index < len(blocks):
                block = blocks[row_index]
                block_type = block.get("type")
                if block_type == BlockType.TABLE_ROW.value:
                    block_text = block.get("data",{}).get("row_natural_language_text","")
                    if block_text:
                        enhanced_metadata = get_enhanced_metadata(record,block,{})
                        child_results.append({
                            "content": block_text,
                            "block_type": block_type,
                            "metadata": enhanced_metadata,
                            "virtual_record_id": virtual_record_id,
                            "block_index": row_index,
                            "citationType": "vectordb|document",
                            "score": row_score,
                        })
                    else:
                        # Container TABLE_ROW with image-split fragments:
                        # emit each fragment in reading order under the container's block_index.
                        fmap = fragment_maps.setdefault(virtual_record_id, _build_fragment_map(blocks))
                        container_idx = block.get("index")
                        if container_idx is not None and container_idx in fmap:
                            enhanced_metadata = get_enhanced_metadata(record, block, {})
                            for frag in sorted(fmap[container_idx], key=lambda b: b.get("index", 0)):
                                frag_type = frag.get("type")
                                if frag_type == BlockType.TEXT.value:
                                    frag_data = frag.get("data", "")
                                    if frag_data:
                                        child_results.append({
                                            "content": _safe_stringify_content(frag_data),
                                            "block_type": BlockType.TEXT.value,
                                            "metadata": enhanced_metadata,
                                            "virtual_record_id": virtual_record_id,
                                            "block_index": row_index,
                                            "citationType": "vectordb|document",
                                            "score": row_score,
                                        })
                                elif frag_type == BlockType.IMAGE.value and is_multimodal_llm:
                                    uri = (frag.get("data") or {}).get("uri")
                                    if uri:
                                        child_results.append({
                                            "content": uri,
                                            "block_type": BlockType.IMAGE.value,
                                            "metadata": enhanced_metadata,
                                            "virtual_record_id": virtual_record_id,
                                            "block_index": row_index,
                                            "citationType": "vectordb|document",
                                            "score": row_score,
                                        })
            elif qdrant_content:
                # Block not in blob (SQL row limit) — use Qdrant page_content
                logger.debug(f"Using Qdrant page_content for row {row_index} of virtual record {virtual_record_id}")
                synthetic_block = {
                    "type": BlockType.TABLE_ROW.value,
                    "data": {"row_natural_language_text": qdrant_content},
                    "index": row_index,
                }
                enhanced_metadata = get_enhanced_metadata(record, synthetic_block, {})
                child_results.append({
                    "content": qdrant_content,
                    "block_type": BlockType.TABLE_ROW.value,
                    "metadata": enhanced_metadata,
                    "virtual_record_id": virtual_record_id,
                    "block_index": row_index,
                    "citationType": "vectordb",
                    "score": row_score,
                })
        if sorted_rows_tuple:
            first_child_block_index = sorted_rows_tuple[0][0]
            adjacent_chunks[virtual_record_id].append(first_child_block_index-1)
            if len(sorted_rows_tuple) > 1:
                last_child_block_index = sorted_rows_tuple[-1][0]
                adjacent_chunks[virtual_record_id].append(last_child_block_index+1)

        # Skip creating table_result if no rows were found
        if not sorted_rows_tuple:
            continue

        table_result = {
            "content":(table_summary,child_results),
            "block_type": GroupType.TABLE.value,
            "virtual_record_id": virtual_record_id,
            "block_index": first_child_block_index,
            "block_group_index": block_group_index,
            "metadata": get_enhanced_metadata(record,block_group,{}),
        }
        flattened_results.append(table_result)



    if not from_tool and not from_retrieval_service:
        for virtual_record_id,adjacent_chunks_list in adjacent_chunks.items():
            for index in adjacent_chunks_list:
                chunk_id = f"{virtual_record_id}-{index}"
                if chunk_id in seen_chunks:
                    continue
                seen_chunks.add(chunk_id)
                record = virtual_record_id_to_result[virtual_record_id]
                if record is None:
                    continue
                blocks  = record.get("block_containers",{}).get("blocks",[])
                if index < len(blocks) and index >= 0:
                    block = blocks[index]
                    block_type = block.get("type")
                    if block_type == BlockType.TEXT.value:
                        block_text = block.get("data","")
                        enhanced_metadata = get_enhanced_metadata(record,block,{})
                        flattened_results.append({
                            "content": block_text,
                            "block_type": block_type,
                            "metadata": enhanced_metadata,
                            "virtual_record_id": virtual_record_id,
                            "block_index": index,
                            "citationType": "vectordb|document",
                        })

    # Store point_id_to_blockIndex mappings separately for old type results
    # This mapping is used to convert point_id from search results to block index
    point_id_to_blockIndex_mappings = {}

    for result in old_type_results:
        virtual_record_id = result.get("metadata",{}).get("virtualRecordId")
        meta = result.get("metadata",{})

        if virtual_record_id not in virtual_record_id_to_result:
            record,point_id_to_blockIndex = await create_record_from_vector_metadata(meta,org_id,virtual_record_id,blob_store)
            virtual_record_id_to_result[virtual_record_id] = record
            point_id_to_blockIndex_mappings[virtual_record_id] = point_id_to_blockIndex

        point_id = meta.get("point_id")
        point_id_to_blockIndex = point_id_to_blockIndex_mappings.get(virtual_record_id, {})
        if point_id not in point_id_to_blockIndex:
            logger.warning("Missing point_id mapping: virtual_record_id=%s point_id=%s", virtual_record_id, str(point_id))
            continue
        index = point_id_to_blockIndex[point_id]
        chunk_id = f"{virtual_record_id}-{index}"
        if chunk_id in seen_chunks:
            continue
        seen_chunks.add(chunk_id)

        record = virtual_record_id_to_result[virtual_record_id]
        if record is None:
            continue
        block_container = record.get("block_containers",{})
        blocks = block_container.get("blocks",[])
        block_groups = block_container.get("block_groups",[])

        block = blocks[index]
        block_type = block.get("type")
        result["block_type"] = block_type
        result["virtual_record_id"] = virtual_record_id
        result["block_index"] = index
        enhanced_metadata = get_enhanced_metadata(record,block,meta)
        result["metadata"] = enhanced_metadata
        flattened_results.append(result)

    return flattened_results

def get_enhanced_metadata(record:dict[str, Any],block:dict[str, Any]|None,meta:dict[str, Any]) -> dict[str, Any]:
        try:
            virtual_record_id = record.get("virtual_record_id", "")
            block_type = block.get("type") if block else BlockType.RECORD_SUMMARY.value
            citation_metadata = block.get("citation_metadata") if block else None
            if citation_metadata:
                page_num =  citation_metadata.get("page_number",None)
            else:
                page_num = None
            data = block.get("data") if block else None
            if data:
                if block_type == GroupType.TABLE.value:
                    # Handle both dict and string data types
                    if isinstance(data, dict):
                        # Use table_summary instead of table_markdown, with fallback for backward compatibility
                        block_text = data.get("table_summary", "") or data.get("table_markdown", "")
                    else:
                        block_text = str(data)
                elif block_type == BlockType.TABLE_ROW.value:
                    # Handle both dict and string data types
                    if isinstance(data, dict):
                        block_text = data.get("row_natural_language_text","")
                    else:
                        block_text = str(data)
                elif block_type == BlockType.TEXT.value:
                    block_text = data
                elif block_type == BlockType.IMAGE.value:
                    block_text = "image"
                else:
                    block_text = meta.get("blockText","")
            else:
                block_text = ""

            mime_type = record.get("mime_type")
            if not mime_type:
                mime_type = meta.get("mimeType")

            extension = meta.get("extension")
            if extension is None:
                extension = get_extension_from_mimetype(mime_type)

            block_num = meta.get("blockNum")
            if block_num is None:
                if extension == "xlsx" or extension == "tsv":
                    # Guard against non-dict data
                    if isinstance(data, dict):
                        block_num = [data.get("row_number", 1)]
                    else:
                        block_num = [1]
                elif extension == "csv":
                    if isinstance(data, dict):
                        block_num = [data.get("row_number", 1)-1]
                    else:
                        block_num = [0]
                else:
                    block_num = [block.get("index", 0) + 1] if block else None

            preview_renderable = meta.get("previewRenderable")
            if preview_renderable is None:
                preview_renderable = record.get("preview_renderable", True)

            hide_weburl = meta.get("hideWeburl")
            if hide_weburl is None:
                hide_weburl = record.get("hide_weburl", False)



            web_url = meta.get("webUrl") or record.get("weburl", "")
            origin = meta.get("origin") or record.get("origin", "")
            recordId = meta.get("recordId") or record.get("id", "")
            record_type = record.get("record_type", "")
            if hide_weburl and recordId:
                web_url = f"/record/{recordId}"
            elif (
                web_url
                and origin != "UPLOAD"
                and record_type != RecordType.MAIL.value
                and block_type != BlockType.RECORD_SUMMARY.value
            ):
                web_url = generate_text_fragment_url(web_url, block_text)
            if not web_url and recordId:
                web_url = f"/record/{recordId}"

            enhanced_metadata = {
                        "orgId": meta.get("orgId") or record.get("org_id", ""),
                        "recordId": recordId,
                        "virtualRecordId": virtual_record_id,
                        "recordName": meta.get("recordName") or record.get("record_name", ""),
                        "recordType": record_type,
                        "recordVersion": record.get("version", ""),
                        "origin": origin,
                        "connector": meta.get("connector") or record.get("connector_name", ""),
                        "connectorId": meta.get("connectorId") or record.get("connector_id", ""),
                        "blockText": block_text,
                        "blockType": str(block_type),
                        "bounding_box": extract_bounding_boxes(block.get("citation_metadata")) if block else None,
                        "pageNum":[page_num],
                        "extension": extension,
                        "mimeType": mime_type,
                        "blockNum":block_num,
                        "webUrl": web_url,
                        "previewRenderable": preview_renderable,
                        "hideWeburl": hide_weburl,
                    }
            if extension == "xlsx" or meta.get("sheetName"):
                if isinstance(data, dict):
                    enhanced_metadata["sheetName"] = data.get("sheet_name", "")
                else:
                    enhanced_metadata["sheetName"] = meta.get("sheetName", "")
            if extension == "xlsx" or meta.get("sheetNum"):
                if isinstance(data, dict):
                    enhanced_metadata["sheetNum"] = data.get("sheet_number", 1)
                else:
                    enhanced_metadata["sheetNum"] = meta.get("sheetNum", 1)
            return enhanced_metadata
        except Exception as e:
            raise e

def extract_bounding_boxes(citation_metadata) -> list[dict[str, float]]:
        """Safely extract bounding box data from citation metadata"""
        if not citation_metadata or not citation_metadata.get("bounding_boxes"):
            return None

        bounding_boxes = citation_metadata.get("bounding_boxes")
        if not isinstance(bounding_boxes, list):
            return None

        try:
            result = []
            for point in bounding_boxes:
                if "x" in point and "y" in point:
                    result.append({"x": point.get("x"), "y": point.get("y")})
                else:
                    return None
            return result
        except Exception as e:
            raise e

async def get_record(virtual_record_id: str,virtual_record_id_to_result: dict[str, dict[str, Any]],blob_store: BlobStorage,org_id: str,virtual_to_record_map: dict[str, dict[str, Any]]=None,graph_provider: IGraphDBProvider | None = None,frontend_url: str | None = None,lookup_result: dict[str, Any] | None = None,type_docs: dict[str, dict[str, Any]] | None = None) -> None:
    try:
        record = await blob_store.get_record_from_storage(virtual_record_id=virtual_record_id, org_id=org_id, lookup_result=lookup_result)
        if record:
            graphDb_record = (virtual_to_record_map or {}).get(virtual_record_id)
            if graphDb_record:
                record_type = graphDb_record.get("recordType")
                record_key = graphDb_record.get("id") or graphDb_record.get("_key")

                record["id"] = record_key
                record["org_id"] = org_id
                record["record_name"] = graphDb_record.get("recordName")
                record["record_type"] = record_type
                record["version"] = graphDb_record.get("version")
                record["origin"] = graphDb_record.get("origin")
                record["connector_name"] = graphDb_record.get("connectorName")
                record["connector_id"] = graphDb_record.get("connectorId")
                record["weburl"] = graphDb_record.get("webUrl")
                record["preview_renderable"] = graphDb_record.get("previewRenderable", True)
                record["hide_weburl"] = graphDb_record.get("hideWeburl", False)
                record["mime_type"] = graphDb_record.get("mimeType")
                record["source_created_at"] = graphDb_record.get("sourceCreatedAtTimestamp")
                record["source_updated_at"] = graphDb_record.get("sourceLastModifiedTimestamp")
                if graphDb_record.get("location"):
                    record["location"] = graphDb_record["location"]
                graph_external_id = graphDb_record.get("externalRecordId")
                if graph_external_id:
                    record["external_record_id"] = graph_external_id

                # Fetch type-specific metadata and generate formatted string
                graph_doc = (type_docs or {}).get(record_key)
                if graph_doc is None and graph_provider and record_key:
                    try:
                        # Determine collection name based on record type

                        collection = collection_map.get(record_type)

                        if collection:
                            graph_doc = await graph_provider.get_document(
                                document_key=record_key,
                                collection=collection
                            )
                    except Exception as e:
                        # Log but don't fail - graceful degradation
                        logger.error(f"Error fetching type-specific metadata for record {record_key}: {str(e)}")

                record_instance = create_record_instance_from_dict(record, graph_doc)
                if record_instance:
                    if isinstance(record_instance, DealRecord):
                        record["context_metadata"] = await record_instance.to_llm_context_with_graph(
                            frontend_url=frontend_url,
                            graph_provider=graph_provider,
                        )
                    else:
                        record["context_metadata"] = record_instance.to_llm_context(
                            frontend_url=frontend_url
                        )
                else:
                    record["context_metadata"] = ""

                # Code blocks are addressed by (file path, symbol id), and the
                # path exists only on the codeFiles node -- not in the blob.
                if graph_doc and graph_doc.get("filePath"):
                    record["file_path"] = graph_doc.get("filePath")

            record["frontend_url"] = frontend_url or ""
            record["virtual_record_id"] = virtual_record_id
            virtual_record_id_to_result[virtual_record_id] = record
        else:
            virtual_record_id_to_result[virtual_record_id] = None

    except Exception as e:
        raise e

async def create_record_from_vector_metadata(metadata: dict[str, Any], org_id: str, virtual_record_id: str,blob_store: BlobStorage) -> tuple[dict[str, Any], dict[str, int]]:
    try:
        # Lazy import to avoid circular dependency: chat_helpers -> ContainerUtils -> RetrievalService -> chat_helpers
        from app.containers.utils.utils import ContainerUtils
        summary = metadata.get("summary", "")
        categories = [metadata.get("categories", "")]
        topics = metadata.get("topics", "")
        sub_category_level_1 = metadata.get("subcategoryLevel1","")
        sub_category_level_2 = metadata.get("subcategoryLevel2","")
        sub_category_level_3 = metadata.get("subcategoryLevel3","")
        languages = metadata.get("languages", "")
        departments = metadata.get("departments", "")
        semantic_metadata = {
            "summary": summary,
            "categories": categories,
            "topics": topics,
            "sub_category_level_1": sub_category_level_1,
            "sub_category_level_2": sub_category_level_2,
            "sub_category_level_3": sub_category_level_3,
            "languages": languages,
            "departments": departments,
        }

        extension = get_extension_from_mimetype(metadata.get("mimeType",""))

        record = {
            "id": metadata.get("recordId", ""),
            "org_id": org_id,
            "record_name": metadata.get("recordName", ""),
            "record_type": metadata.get("recordType", ""),
            "external_record_id": metadata.get("externalRecordId", virtual_record_id),
            "external_revision_id": metadata.get("externalRevisionId", virtual_record_id),
            "version": metadata.get("version",""),
            "origin": metadata.get("origin",""),
            "connector_name": metadata.get("connector") or metadata.get("connectorName",""),
            "connector_id": metadata.get("connectorId", ""),
            "virtual_record_id": virtual_record_id,
            "mime_type": metadata.get("mimeType",""),
            "created_at": metadata.get("createdAtTimestamp", ""),
            "updated_at": metadata.get("updatedAtTimestamp", ""),
            "source_created_at": metadata.get("sourceCreatedAtTimestamp", ""),
            "source_updated_at": metadata.get("sourceLastModifiedTimestamp", ""),
            "weburl": metadata.get("webUrl", ""),
            "semantic_metadata": semantic_metadata,
            "extension": extension,
        }
        blocks = []
        container_utils = ContainerUtils()

        vector_db_service = await container_utils.get_vector_db_service(blob_store.config_service)

# Create filter
        payload_filter = await vector_db_service.filter_collection(must={
            "virtualRecordId": virtual_record_id,
        })

        # Page until the provider says there is no more. A single large-limit
        # call silently truncates: OpenSearch caps size at 10k and RediSearch at
        # MAXSEARCHRESULTS, so an oversized limit returns a partial set that
        # looks complete.
        points = []
        next_offset = None
        seen_offsets: set = set()
        while True:
            try:
                result = await vector_db_service.scroll(
                    collection_name=VECTOR_DB_COLLECTION_NAME,
                    scroll_filter=payload_filter,
                    limit=SCROLL_PAGE_SIZE,
                    offset=next_offset,
                )
            except RuntimeError as e:
                # A provider result ceiling (RediSearch MAXSEARCHRESULTS) is a
                # hard stop, not a reason to fail the whole rebuild: reconstruct
                # from what we have and say plainly that it is partial.
                logger.error(
                    "Vector scroll for %s stopped early after %d points: %s",
                    virtual_record_id,
                    len(points),
                    e,
                )
                break
            points.extend(result.points)
            next_offset = result.next_offset
            if not next_offset:
                break
            # Termination depends entirely on the provider advancing the cursor.
            # A provider that repeats one refetches and re-appends the same page
            # for ever, on a request path, so treat a repeat as the end.
            if next_offset in seen_offsets:
                logger.error(
                    "Vector scroll for %s repeated offset %r after %d points; "
                    "stopping with a partial result",
                    virtual_record_id,
                    next_offset,
                    len(points),
                )
                break
            seen_offsets.add(next_offset)
            if len(points) >= MAX_SCROLL_POINTS:
                logger.error(
                    "Vector scroll for %s hit the %d point ceiling; "
                    "result is partial",
                    virtual_record_id,
                    MAX_SCROLL_POINTS,
                )
                break

        point_id_to_blockIndex = {}
        new_payloads = []

        for i,point in enumerate(points):
            payload = point.payload
            if payload:
                meta = payload.get("metadata")
                page_content = payload.get("page_content")
                block = create_block_from_metadata(meta,page_content)
                point_id_to_blockIndex[point.id] = i
                blocks.append(block)
                new_payloads.append({"metadata":{
                    "virtualRecordId": virtual_record_id,
                    "blockIndex": block.get("index"),
                    "orgId": org_id,
                    "isBlockGroup": False,
                    "isBlock": False,
                },
                "page_content": payload.get("page_content")
                })

        sorted_blocks = sorted(blocks, key=lambda x: x.get("index", 0))
        for i,block in enumerate(sorted_blocks):
            block["index"] = i

        record["block_containers"] = {
            "blocks": sorted_blocks,
            "block_groups": []
        }

        return record,point_id_to_blockIndex
    except Exception as e:
        raise e


def create_block_from_metadata(metadata: dict[str, Any],page_content: str) -> dict[str, Any]:
    try:
        page_num = metadata.get("pageNum")
        if isinstance(page_num, (list,tuple)):
            page_num = page_num[0] if page_num else None

        citation_metadata = {
            "page_number": page_num,
            "bounding_boxes": metadata.get("bounding_box")
        }

        block_type = metadata.get("blockType","text")

        extension = metadata.get("extension")
        if block_type == BlockType.IMAGE.value:
            # Image points never carry the raw base64 URI in page_content (only a
            # text description, or empty — see VectorStore._build_image_points).
            # Wrap as a dict with no "uri" so downstream image handling in
            # _process_flattened_results takes its existing "missing uri" skip
            # path instead of crashing on `str.get`.
            data = {"uri": None, "description": page_content}
        elif extension == "docx":
            data = page_content
        else:
            data = metadata.get("blockText",page_content)

        # Create the Block structure
        return {
            "id": str(uuid4()),  # Generate unique ID
            "index": metadata.get("blockNum")[0] if metadata.get("blockNum") and len(metadata.get("blockNum")) > 0 else 0, # TODO: blockNum indexing might be different for different file types
            "type": block_type,
            "format": "txt",
            "comments": [],
            "source_creation_date": metadata.get("sourceCreatedAtTimestamp"),
            "source_update_date": metadata.get("sourceLastModifiedTimestamp"),
            "data": data,
            "weburl": metadata.get("webUrl"),
            "citation_metadata": citation_metadata,
        }
    except Exception as e:
        raise e

MAX_CELLS_IN_TABLE_THRESHOLD = 250  # Equivalent to ~700 words assuming ~2-3 words per cell


def _find_first_block_index_recursive(block_groups: list[dict[str, Any]], children: dict[str, Any] | list[dict[str, Any]]) -> int | None:
    """Recursively search through the first child to find the first block_index.

    Args:
        block_groups: List of block groups
        children: BlockGroupChildren object or List of child container indices (old format)

    Returns:
        First block_index found in the first child, or None if not found
    """
    if not children:
        return None

    # Handle new range-based format
    if isinstance(children, dict) and 'block_ranges' in children:
        block_ranges = children.get('block_ranges', [])
        if block_ranges:
            # Return the first index from the first range
            return block_ranges[0].get('start')

        # If no block ranges, check block group ranges
        block_group_ranges = children.get('block_group_ranges', [])
        if block_group_ranges:
            first_bg_index = block_group_ranges[0].get('start')
            if first_bg_index is not None and 0 <= first_bg_index < len(block_groups):
                nested_group = block_groups[first_bg_index]
                nested_children = nested_group.get("children")
                if nested_children:
                    return _find_first_block_index_recursive(block_groups, nested_children)
        return None

    # Handle old format (list of BlockContainerIndex)
    if isinstance(children, list) and len(children) > 0:
        first_child = children[0]
        block_index = first_child.get("block_index")
        if block_index is not None:
            return block_index

        block_group_index = first_child.get("block_group_index")
        if block_group_index is not None and 0 <= block_group_index < len(block_groups):
            nested_group = block_groups[block_group_index]
            nested_children = nested_group.get("children", [])
            if nested_children:
                return _find_first_block_index_recursive(block_groups, nested_children)

    return None


def _extract_text_content_recursive(
    block_groups: list[dict[str, Any]],
    blocks: list[dict[str, Any]],
    children: dict[str, Any] | list[dict[str, Any]],
    virtual_record_id: str = None,
    seen_chunks: set = None,
    depth: int = 0,
) -> str:
    """Recursively extract text content from children and nested children.

    Args:
        block_groups: List of block groups
        blocks: List of blocks
        children: BlockGroupChildren object or List of child container indices (old format)
        virtual_record_id: Optional virtual record ID for tracking seen chunks
        seen_chunks: Optional set to track seen chunks

    Returns:
        Concatenated text content from all children and nested children
    """
    content = ""
    indent = "  " * depth

    # Handle new range-based format
    if isinstance(children, dict) and ('block_ranges' in children or 'block_group_ranges' in children):
        # Process block ranges
        block_ranges = children.get('block_ranges', [])
        for range_obj in block_ranges:
            start = range_obj.get('start')
            end = range_obj.get('end')
            if start is not None and end is not None:
                for block_index in range(start, end + 1):
                    # Track seen chunks
                    if virtual_record_id is not None and seen_chunks is not None:
                        child_id = f"{virtual_record_id}-{block_index}"
                        seen_chunks.add(child_id)

                    # Extract text from block
                    if 0 <= block_index < len(blocks):
                        child_block = blocks[block_index]
                        if child_block.get("type") == BlockType.TEXT.value:
                            content += f"{indent}{child_block.get('data', '')}\n"

        # Process block group ranges
        block_group_ranges = children.get('block_group_ranges', [])
        for range_obj in block_group_ranges:
            start = range_obj.get('start')
            end = range_obj.get('end')
            if start is not None and end is not None:
                for block_group_index in range(start, end + 1):
                    # Track seen chunks
                    if virtual_record_id is not None and seen_chunks is not None:
                        child_id = f"{virtual_record_id}-{block_group_index}-block_group"
                        seen_chunks.add(child_id)

                    # Recursively process nested children
                    if 0 <= block_group_index < len(block_groups):
                        nested_group = block_groups[block_group_index]
                        nested_children = nested_group.get("children")
                        if nested_children:
                            content += _extract_text_content_recursive(
                                block_groups, blocks, nested_children, virtual_record_id, seen_chunks, depth + 1
                            )
        return content

    # Handle old format (list of BlockContainerIndex)
    if not isinstance(children, list):
        return content

    for child in children:
        block_index = child.get("block_index")
        block_group_index = child.get("block_group_index")

        # Track seen chunks if virtual_record_id is provided
        if virtual_record_id is not None and seen_chunks is not None:
            if block_index is not None:
                child_id = f"{virtual_record_id}-{block_index}"
                seen_chunks.add(child_id)
            elif block_group_index is not None:
                child_id = f"{virtual_record_id}-{block_group_index}-block_group"
                seen_chunks.add(child_id)

        # If child has a direct block_index, extract text from that block
        if block_index is not None and 0 <= block_index < len(blocks):
            child_block = blocks[block_index]
            if child_block.get("type") == BlockType.TEXT.value:
                content += f"{indent}{child_block.get('data', '')}\n"

        # If child has a block_group_index, recursively process nested children
        elif block_group_index is not None and 0 <= block_group_index < len(block_groups):
            nested_group = block_groups[block_group_index]
            nested_children = nested_group.get("children", [])
            if nested_children:
                content += _extract_text_content_recursive(
                    block_groups, blocks, nested_children, virtual_record_id, seen_chunks, depth + 1
                )

    return content


def get_group_label_n_first_child(block_groups: list[dict[str, Any]], parent_index: int) -> tuple[str, int] | None:
    """Extract grouped text content and first child index for supported group types.

    Returns (label, first_child_block_index, content) or None if invalid or unsupported.
    """
    if parent_index is None or parent_index < 0 or parent_index >= len(block_groups):
        return None

    parent_block = block_groups[parent_index]
    label = parent_block.get("type")


    if label not in valid_group_labels:
        return None

    children = parent_block.get("children", [])
    if not children:
        return None

    first_child_block_index = _find_first_block_index_recursive(block_groups, children)
    if first_child_block_index is None:
        logger.warning(
            "⚠️ get_group_label_n_first_child: first_child_block_index is None for parent_index=%s",
            parent_index
        )
        return None

    return label, first_child_block_index


def _build_fragment_map(blocks: list[dict[str, Any]]) -> dict[int, list[dict[str, Any]]]:
    """Build a reverse map: container block index → its image-split fragment children.

    Fragment blocks have ``parent_block_index`` set to the index of the container
    block they were split from (due to inline images inside a list or table row).
    This map lets callers expand a container without a second O(n) scan.
    """
    fmap: dict[int, list[dict[str, Any]]] = {}
    for block in blocks:
        pbi = block.get("parent_block_index")
        if pbi is not None:
            fmap.setdefault(pbi, []).append(block)
    return fmap


def _render_blocks_with_images(
    blocks_list: list[dict[str, Any]],
    is_multimodal_llm: bool,
    image_budget: "ImageBudget | None" = None,
    collected_images: list[dict[str, Any]] | None = None,
    allow_inline_images: bool = True,
) -> list[dict[str, Any]]:
    """Render a list of block entries (with possible IMAGE types) into LLM content entries.

    Groups consecutive entries sharing the same block_index so that the
    `[idx|ref]` header is emitted only once per container, with all
    fragment content listed underneath it.

    When `collected_images` is provided (tool-result callers, see
    `build_message_content_array`), inline table/group images are routed
    into that side-channel instead of being embedded directly as
    `image_url` content blocks — `ToolMessage` only gets images via its
    multipart `content`, never buried inside a text-typed tool result
    string. Direct-embedding callers (attachments) leave `collected_images`
    `None` and keep the original inline behavior.

    `allow_inline_images` is False for callers that can deliver neither way
    (a tool result with no `collected_images` sink joins text only), so an
    image that would be dropped downstream does not silently consume a slot
    of the shared `image_budget`.
    """
    if image_budget is None:
        image_budget = ImageBudget(MAX_IMAGES_IN_CONVERSATION)
    content: list[dict[str, Any]] = []
    for _block_idx, group_iter in groupby(blocks_list, key=lambda b: b.get("block_index")):
        group = list(group_iter)
        first = group[0]
        block_idx = first.get("block_index")
        citation_ref = first.get("citation_ref", "")

        has_images_in_group = any(
            g.get("block_type") == BlockType.IMAGE.value for g in group
        )

        if len(group) == 1 and not has_images_in_group:
            content.append({
                "type": "text",
                "text": f"[{block_idx}|{citation_ref}] {first.get('content')}\n",
            })
        else:
            content.append({
                "type": "text",
                "text": f"[{block_idx}|{citation_ref}]\n",
            })
            for item in group:
                if item.get("block_type") == BlockType.IMAGE.value:
                    if is_multimodal_llm:
                        img_uri = item.get("content", "")
                        item_ref = item.get("citation_ref", "")
                        if img_uri and is_base64_image(img_uri):
                            deliverable = collected_images is not None or allow_inline_images
                            if deliverable and image_budget.can_add():
                                image_budget.try_consume(1)
                                if collected_images is not None:
                                    collected_images.append({
                                        "ref": item_ref,
                                        "block_index": item.get("block_index"),
                                        "image_url": {"url": img_uri},
                                        "virtual_record_id": item.get("virtual_record_id"),
                                    })
                                    # Side-channel callers still need a text
                                    # anchor in `content` — without it, the
                                    # image (delivered separately via
                                    # `collected_images`) has no `[ref]`
                                    # citation the model can point back to.
                                    content.append({
                                        "type": "text",
                                        "text": f"    [{item_ref}] (image)\n",
                                    })
                                else:
                                    content.append({
                                        "type": "image_url",
                                        "image_url": {"url": img_uri}
                                    })
                            elif not deliverable:
                                # No sink and no way to inline (a tool result
                                # joins text only). Emit the anchor without
                                # spending budget an undeliverable image would
                                # otherwise take from a later one.
                                content.append({
                                    "type": "text",
                                    "text": f"    [{item_ref}] (image)\n",
                                })
                            else:
                                content.append({
                                    "type": "text",
                                    "text": f"    [{item_ref}] (image block - visual content "
                                            "not shown due to conversation image limit)\n",
                                })
                    continue
                content.append({
                    "type": "text",
                    "text": f"    {item.get('content')}\n",
                })
    return content


def build_group_blocks(block_groups: list[dict[str, Any]], blocks: list[dict[str, Any]], parent_index: int, virtual_record_id: str = None, record: dict[str, Any] = None, result: dict[str, Any] = None, is_multimodal_llm: bool = False, fragment_map: dict[int, list[dict[str, Any]]] | None = None) -> list[dict[str, Any]]:
    if parent_index < 0 or parent_index >= len(block_groups):
        return None
    parent_block = block_groups[parent_index]

    children = parent_block.get("children")
    if not children:
        return []

    result_blocks = []

    # Handle new range-based format
    if isinstance(children, dict) and 'block_ranges' in children:
        block_ranges = children.get('block_ranges', [])
        for range_obj in block_ranges:
            start = range_obj.get('start')
            end = range_obj.get('end')
            if start is not None and end is not None:
                for block_index in range(start, end + 1):
                    if 0 <= block_index < len(blocks):
                        if blocks[block_index].get("type") == BlockType.IMAGE.value:
                            continue
                        result_blocks.append(blocks[block_index])
    # Handle old format (list of BlockContainerIndex)
    elif isinstance(children, list):
        for child in children:
            block_index = child.get("block_index")
            if block_index is not None and 0 <= block_index < len(blocks):
                if blocks[block_index].get("type") == BlockType.IMAGE.value:
                    continue
                result_blocks.append(blocks[block_index])

    child_results = []
    meta = result.get("metadata", {})
    for block in result_blocks:
        data = block.get("data")
        if data:
            data = _safe_stringify_content(data)
        if not data:
            # Container block (image-split): emit each fragment child in reading order.
            # All fragments share the container's block_index → single citation ID downstream.
            if fragment_map is not None:
                container_block_index = block.get("index")
                if container_block_index is not None and container_block_index in fragment_map:
                    for frag in sorted(fragment_map[container_block_index], key=lambda b: b.get("index", 0)):
                        frag_type = frag.get("type")
                        if frag_type == BlockType.TEXT.value:
                            frag_data = frag.get("data", "")
                            if frag_data:
                                child_results.append({
                                    "content": _safe_stringify_content(frag_data),
                                    "block_type": BlockType.TEXT.value,
                                    "virtual_record_id": virtual_record_id,
                                    "block_index": container_block_index,
                                    "metadata": get_enhanced_metadata(record, block, meta),
                                    "score": float(result.get("score", 0.0)),
                                    "citationType": "vectordb|document",
                                })
                        elif frag_type == BlockType.IMAGE.value and is_multimodal_llm:
                            uri = (frag.get("data") or {}).get("uri")
                            if uri:
                                child_results.append({
                                    "content": uri,
                                    "block_type": BlockType.IMAGE.value,
                                    "virtual_record_id": virtual_record_id,
                                    "block_index": container_block_index,
                                    "metadata": get_enhanced_metadata(record, block, meta),
                                    "score": float(result.get("score", 0.0)),
                                    "citationType": "vectordb|document",
                                })
            continue
        child_results.append({
            "content": data,
            "block_type": block.get("type"),
            "virtual_record_id": virtual_record_id,
            "block_index": block.get("index"),
            "qualified_name": block_qualified_name(block),
            "metadata": get_enhanced_metadata(record, block, meta),
            "score": float(result.get("score",0.0)),
            "citationType": "vectordb|document",
        })
    return child_results


def record_to_message_content(
    record: dict[str, Any],
    ref_mapper: CitationRefMapper | None = None,
    is_multimodal_llm: bool = False,
    *,
    start_block: int = 0,
    max_blocks: int | None = None,
    collected_images: list[dict[str, Any]] | None = None,
    image_budget: "ImageBudget | None" = None,
) -> tuple[list[dict[str, Any]], CitationRefMapper]:
    """
    Convert a record JSON object to message content format matching get_message_content.

    Args:
        record: The record JSON object containing block_containers and other metadata
        ref_mapper: Optional shared CitationRefMapper for tiny-ref generation
        is_multimodal_llm: Whether the LLM supports image/vision input
        start_block: Skip blocks with index < start_block (for windowed reads).
        max_blocks: Maximum number of renderable blocks to include. When the
            record has more renderable blocks than this window a continuation
            hint is appended: "Showing blocks N-M of T. Call
            dynamic_fetch_full_record with start_block=M+1 for the rest."
            None means no cap (today's default behaviour).
        collected_images: When provided, IMAGE blocks are routed into this
            list (`{"ref", "block_index", "image_url", "virtual_record_id"}`
            dicts) instead of being embedded inline as `image_url` content
            entries — used by tool callers (e.g. `_FetchFullRecordTool`)
            that must deliver images via `ToolMessage`'s multipart content
            rather than buried in the returned content list. `None` (the
            default) preserves the original inline-embedding behavior for
            direct UserMessage callers (attachment resolution).
        image_budget: Conversation-wide `ImageBudget` to enforce the
            50-image cap across all sources. Defaults to a fresh
            (unbounded-in-practice) per-call budget when not shared by the
            caller.

    Returns:
        Tuple of (content list, ref_mapper)
    """
    if ref_mapper is None:
        ref_mapper = CitationRefMapper()
    if image_budget is None:
        image_budget = ImageBudget(MAX_IMAGES_IN_CONVERSATION)

    try:
        content = []
        context_metadata = record.get("context_metadata", "")
        content.append({
            "type": "text",
            "text": f"""<record>\n{context_metadata}\n\nRecord blocks (sorted):\n\n"""
        })
        # Process blocks
        block_containers = record.get("block_containers", {})
        blocks = block_containers.get("blocks", [])
        block_groups = block_containers.get("block_groups", [])
        fragment_map = _build_fragment_map(blocks)

        seen_block_groups = set()
        rec_frontend_url = record.get("frontend_url", "")
        rec_record_id = record.get("id", "")
        record_file_path = record.get("file_path", "") or ""

        # Windowing: track how many renderable (non-fragment) blocks we have
        # rendered so we can truncate at max_blocks and emit a continuation hint.
        _renderable_rendered = 0
        _truncated_at: int | None = None  # first block_index not rendered due to cap

        # Process individual blocks
        for block in blocks:
            block_index = block.get("index", 0)
            block_type = block.get("type")

            # Skip fragment blocks — they are rendered via their container's group expansion.
            if block.get("parent_block_index") is not None:
                continue

            # Windowing: skip blocks before start_block.
            if block_index < start_block:
                continue

            # Windowing: stop once we have hit the max_blocks cap.
            if max_blocks is not None and _renderable_rendered >= max_blocks:
                if _truncated_at is None:
                    _truncated_at = block_index
                continue

            block_web_url = build_block_web_url(rec_frontend_url, rec_record_id, block_index)
            ref = ref_mapper.get_or_create_ref(block_web_url)
            data = block.get("data", "")

            if block_type == BlockType.IMAGE.value:
                if is_multimodal_llm and isinstance(data, dict):
                    image_uri = data.get("uri", "")
                    if image_uri and is_base64_image(image_uri):
                        if image_budget.can_add():
                            image_budget.try_consume(1)
                            if collected_images is not None:
                                collected_images.append({
                                    "ref": ref,
                                    "block_index": block_index,
                                    "image_url": {"url": image_uri},
                                    "virtual_record_id": record.get("virtual_record_id"),
                                })
                                content.append({
                                    "type": "text",
                                    "text": f"[{ref}] (image)\n\n",
                                })
                            else:
                                content.append({
                                    "type": "text",
                                    "text": f"[{ref}]"
                                })
                                content.append({
                                    "type": "image_url",
                                    "image_url": {"url": image_uri}
                                })
                        else:
                            captions = ((block.get("image_metadata") or {}).get("captions")) or []
                            description = " ".join(captions).strip()
                            fallback_text = (
                                f"[{ref}] (image) {description}\n\n" if description
                                else f"[{ref}] (image block - visual content not shown due to "
                                     "conversation image limit)\n\n"
                            )
                            content.append({"type": "text", "text": fallback_text})
                        _renderable_rendered += 1
                continue
            elif block_type == BlockType.TEXT.value and block.get("parent_index") is None:
                content.append({
                    "type": "text",
                    "text": f"[{ref}] {data}\n\n"
                })
                _renderable_rendered += 1
            elif block_type == BlockType.CODE.value and block.get("parent_index") is None:
                # Top-level code -- module functions, imports, module-level
                # statements. These belong to no group, so without this branch
                # they fall to `else: continue` and a file with no classes
                # reaches the model empty.
                locator = format_code_locator(record_file_path, block_qualified_name(block))
                header = f"[{ref}] {locator}\n" if locator else f"[{ref}] "
                content.append({
                    "type": "text",
                    "text": f"{header}{_safe_stringify_content(data)}\n\n"
                })
                _renderable_rendered += 1
            elif block_type == BlockType.TABLE_ROW.value:
                block_group_index = block.get("parent_index")
                block_group_id = f"{record.get('virtual_record_id', '')}-{block_group_index}"
                if block_group_id in seen_block_groups:
                    continue
                seen_block_groups.add(block_group_id)
                if block_group_index is not None:
                    corresponding_block_group = block_groups[block_group_index]

                    block_type = corresponding_block_group.get("type")
                    data = corresponding_block_group.get("data", {})

                    if block_type == GroupType.TABLE.value:
                        children = corresponding_block_group.get("children")
                        rows_to_be_included_list = []
                        if children:
                            if isinstance(children, dict) and 'block_ranges' in children:
                                for range_obj in children.get('block_ranges', []):
                                    start = range_obj.get('start')
                                    end = range_obj.get('end')
                                    if start is not None and end is not None:
                                        rows_to_be_included_list.extend(range(start, end + 1))
                            elif isinstance(children, list):
                                rows_to_be_included_list = [child.get("block_index") for child in children if child.get("block_index") is not None]

                        child_results = []
                        has_row_images = False
                        for row_index in rows_to_be_included_list:
                            if row_index < len(blocks):
                                block = blocks[row_index]
                                block_data = block.get("data", {})
                                if isinstance(block_data, dict):
                                    row_text = block_data.get("row_natural_language_text", "")
                                else:
                                    row_text = str(block_data)
                                if row_text:
                                    child_block_web_url = build_block_web_url(rec_frontend_url, rec_record_id, row_index)
                                    child_results.append({
                                        "content": row_text,
                                        "block_type": BlockType.TABLE_ROW.value,
                                        "block_index": row_index,
                                        "block_web_url": child_block_web_url,
                                        "citation_ref": ref_mapper.get_or_create_ref(child_block_web_url),
                                    })
                                else:
                                    # Container TABLE_ROW with image-split fragments:
                                    # emit each fragment in reading order under the container's block_index.
                                    container_idx = block.get("index")
                                    if container_idx is not None and container_idx in fragment_map:
                                        child_block_web_url = build_block_web_url(rec_frontend_url, rec_record_id, row_index)
                                        child_citation_ref = ref_mapper.get_or_create_ref(child_block_web_url)
                                        for frag in sorted(fragment_map[container_idx], key=lambda b: b.get("index", 0)):
                                            frag_type = frag.get("type")
                                            if frag_type == BlockType.TEXT.value:
                                                frag_data = frag.get("data", "")
                                                if frag_data:
                                                    child_results.append({
                                                        "content": _safe_stringify_content(frag_data),
                                                        "block_type": BlockType.TEXT.value,
                                                        "block_index": row_index,
                                                        "block_web_url": child_block_web_url,
                                                        "citation_ref": child_citation_ref,
                                                    })
                                            elif frag_type == BlockType.IMAGE.value and is_multimodal_llm:
                                                uri = (frag.get("data") or {}).get("uri")
                                                if uri:
                                                    has_row_images = True
                                                    child_results.append({
                                                        "content": uri,
                                                        "block_type": BlockType.IMAGE.value,
                                                        "block_index": row_index,
                                                        "block_web_url": child_block_web_url,
                                                        "citation_ref": child_citation_ref,
                                                    })

                        if child_results:
                            if not has_row_images:
                                template = compiled_template(table_prompt)
                                rendered_form = template.render(
                                    block_group_index=block_group_index,
                                    block_group_web_url="",
                                    table_summary="",
                                    table_rows=child_results,
                                )
                                content.append({
                                    "type": "text",
                                    "text": f"{rendered_form}\n\n"
                                })
                            else:
                                header = f"[Table #{block_group_index}]\n"
                                content.append({
                                    "type": "text",
                                    "text": header,
                                })
                                content.extend(_render_blocks_with_images(
                                    child_results, is_multimodal_llm, image_budget, collected_images,
                                ))
                            _renderable_rendered += 1
            elif(block.get("parent_index") is not None):
                parent_index = block.get("parent_index")
                block_group_id = f"{record.get('virtual_record_id', '')}-{parent_index}"
                if block_group_id in seen_block_groups:
                    continue
                template = compiled_template(block_group_prompt)
                if parent_index >= len(block_groups):
                    continue
                block_group = block_groups[parent_index]
                block_group_type = block_group.get("type")
                if block_group_type not in valid_group_labels:
                    continue

                virtual_record_id = record.get("virtual_record_id", "")
                group_blocks = build_group_blocks(block_groups, blocks, parent_index, virtual_record_id, record, {}, is_multimodal_llm=is_multimodal_llm, fragment_map=fragment_map)

                if not group_blocks:
                    continue
                seen_block_groups.add(block_group_id)
                has_images = any(gb.get("block_type") == BlockType.IMAGE.value for gb in group_blocks)
                for gb in group_blocks:
                    gb["block_web_url"] = build_block_web_url(rec_frontend_url, rec_record_id, gb.get("block_index", 0))
                    gb["citation_ref"] = ref_mapper.get_or_create_ref(gb["block_web_url"])

                if not has_images:
                    rendered_form = template.render(
                        block_group_index=parent_index,
                        block_group_web_url="",
                        label=block_group.get("type"),
                        blocks=group_blocks,
                        file_path=record_file_path,
                    )
                    content.append({
                        "type": "text",
                        "text": f"{rendered_form}\n\n"
                    })
                else:
                    header = f"[{block_group.get('type')} #{parent_index}]\n"
                    content.append({
                        "type": "text",
                        "text": header,
                    })
                    content.extend(_render_blocks_with_images(
                        group_blocks, is_multimodal_llm, image_budget, collected_images,
                    ))
                _renderable_rendered += 1
            else:
                continue

        # Windowing continuation hint — appended when truncation happened.
        if _truncated_at is not None:
            total_blocks = len([b for b in blocks if b.get("parent_block_index") is None])
            end_block = _truncated_at - 1
            content.append({
                "type": "text",
                "text": (
                    f"\n[Showing blocks {start_block}–{end_block} of approximately "
                    f"{total_blocks} renderable blocks. Call knowledgegraph__fetch_record "
                    f"with start_block={_truncated_at} for the next slice.]\n"
                ),
            })

        fk_parent = record.get("fk_parent_record_ids")
        fk_child = record.get("fk_child_record_ids")
        if fk_parent or fk_child:
            fk_lines = ["\nForeign Key Related Tables:"]
            if fk_parent:
                for fk in fk_parent:
                    parent_table = fk.get("parentTable", "")
                    src_col = fk.get("sourceColumn", "")
                    tgt_col = fk.get("targetColumn", "")
                    rid = fk.get("record_id", "")
                    fk_lines.append(
                        f"  - Parent Table: {parent_table} (Record ID: {rid}, "
                        f"FK: {src_col} -> {tgt_col})"
                    )
            if fk_child:
                for fk in fk_child:
                    child_table = fk.get("childTable", "")
                    src_col = fk.get("sourceColumn", "")
                    tgt_col = fk.get("targetColumn", "")
                    rid = fk.get("record_id", "")
                    fk_lines.append(
                        f"  - Child Table: {child_table} (Record ID: {rid}, "
                        f"FK: {src_col} -> {tgt_col})"
                    )
            content.append({
                "type": "text",
                "text": "\n".join(fk_lines) + "\n"
            })

        return content, ref_mapper
    except Exception as e:
        raise Exception(f"Error in record_to_message_content: {e}") from e


def record_to_text(record: dict[str, Any]) -> str:
    """Convert a record JSON object into a single plain-text string.

    Text-only counterpart of record_to_message_content: same <record> header,
    block/table/group traversal, and foreign-key footer, but with the per-block
    Citation ID and Block Index scaffolding removed (this string is returned as-is
    by the get_record_content endpoint, so citation refs have no consumer) and
    images omitted. Uses agent_block_group_prompt — the ref-free group template
    already used by Record.to_llm_full_context — for tables and groups.
    """
    try:
        content: list[str] = []
        context_metadata = record.get("context_metadata", "")
        content.append(f"""<record>\n{context_metadata}\n\nRecord blocks (sorted):\n\n""")

        block_containers = record.get("block_containers", {})
        blocks = block_containers.get("blocks", [])
        block_groups = block_containers.get("block_groups", [])
        fragment_map = _build_fragment_map(blocks)

        seen_block_groups = set()

        for block in blocks:
            block_type = block.get("type")

            # Skip fragment blocks — they are rendered via their container's group expansion.
            if block.get("parent_block_index") is not None:
                continue

            data = block.get("data", "")

            if block_type == BlockType.IMAGE.value:
                continue
            elif block_type == BlockType.TEXT.value and block.get("parent_index") is None:
                content.append(f"* Block Type: {block_type}\n* Block Content: {data}\n\n")
            elif block_type == BlockType.CODE.value and block.get("parent_index") is None:
                locator = format_code_locator(
                    record.get("file_path", "") or "", block_qualified_name(block)
                )
                header = f"* Symbol: {locator}\n" if locator else ""
                content.append(
                    f"* Block Type: {block_type}\n{header}"
                    f"* Block Content: {_safe_stringify_content(data)}\n\n"
                )
            elif block_type == BlockType.TABLE_ROW.value:
                block_group_index = block.get("parent_index")
                block_group_id = f"{record.get('virtual_record_id', '')}-{block_group_index}"
                if block_group_id in seen_block_groups:
                    continue
                seen_block_groups.add(block_group_id)
                if block_group_index is not None:
                    corresponding_block_group = block_groups[block_group_index]

                    block_type = corresponding_block_group.get("type")
                    data = corresponding_block_group.get("data", {})

                    if block_type == GroupType.TABLE.value:
                        children = corresponding_block_group.get("children")
                        rows_to_be_included_list = []
                        if children:
                            if isinstance(children, dict) and 'block_ranges' in children:
                                for range_obj in children.get('block_ranges', []):
                                    start = range_obj.get('start')
                                    end = range_obj.get('end')
                                    if start is not None and end is not None:
                                        rows_to_be_included_list.extend(range(start, end + 1))
                            elif isinstance(children, list):
                                rows_to_be_included_list = [child.get("block_index") for child in children if child.get("block_index") is not None]

                        child_results = []
                        for row_index in rows_to_be_included_list:
                            if row_index < len(blocks):
                                block = blocks[row_index]
                                block_data = block.get("data", {})
                                if isinstance(block_data, dict):
                                    row_text = block_data.get("row_natural_language_text", "")
                                else:
                                    row_text = str(block_data)
                                if row_text:
                                    child_results.append({"content": row_text})
                                else:
                                    # Container TABLE_ROW with image-split fragments:
                                    # keep the text fragments in reading order (images omitted).
                                    container_idx = block.get("index")
                                    if container_idx is not None and container_idx in fragment_map:
                                        for frag in sorted(fragment_map[container_idx], key=lambda b: b.get("index", 0)):
                                            if frag.get("type") == BlockType.TEXT.value:
                                                frag_data = frag.get("data", "")
                                                if frag_data:
                                                    child_results.append({"content": _safe_stringify_content(frag_data)})

                        if child_results:
                            template = compiled_template(agent_block_group_prompt)
                            rendered_form = template.render(
                                block_group_index=block_group_index,
                                label=GroupType.TABLE.value,
                                blocks=child_results,
                            )
                            content.append(f"{rendered_form}\n\n")
            elif(block.get("parent_index") is not None):
                parent_index = block.get("parent_index")
                block_group_id = f"{record.get('virtual_record_id', '')}-{parent_index}"
                if block_group_id in seen_block_groups:
                    continue
                template = compiled_template(agent_block_group_prompt)
                if parent_index >= len(block_groups):
                    continue
                block_group = block_groups[parent_index]
                block_group_type = block_group.get("type")
                if block_group_type not in valid_group_labels:
                    continue

                virtual_record_id = record.get("virtual_record_id", "")
                group_blocks = build_group_blocks(block_groups, blocks, parent_index, virtual_record_id, record, {}, is_multimodal_llm=False, fragment_map=fragment_map)

                if not group_blocks:
                    continue
                seen_block_groups.add(block_group_id)
                rendered_form = template.render(
                    block_group_index=parent_index,
                    label=block_group.get("type"),
                    blocks=group_blocks,
                    file_path=record.get("file_path", "") or "",
                )
                content.append(f"{rendered_form}\n\n")
            else:
                continue

        fk_parent = record.get("fk_parent_record_ids")
        fk_child = record.get("fk_child_record_ids")
        if fk_parent or fk_child:
            fk_lines = ["\nForeign Key Related Tables:"]
            if fk_parent:
                for fk in fk_parent:
                    parent_table = fk.get("parentTable", "")
                    src_col = fk.get("sourceColumn", "")
                    tgt_col = fk.get("targetColumn", "")
                    rid = fk.get("record_id", "")
                    fk_lines.append(
                        f"  - Parent Table: {parent_table} (Record ID: {rid}, "
                        f"FK: {src_col} -> {tgt_col})"
                    )
            if fk_child:
                for fk in fk_child:
                    child_table = fk.get("childTable", "")
                    src_col = fk.get("sourceColumn", "")
                    tgt_col = fk.get("targetColumn", "")
                    rid = fk.get("record_id", "")
                    fk_lines.append(
                        f"  - Child Table: {child_table} (Record ID: {rid}, "
                        f"FK: {src_col} -> {tgt_col})"
                    )
            content.append("\n".join(fk_lines) + "\n")

        return "".join(content)
    except Exception as e:
        raise Exception(f"Error in record_to_text: {e}") from e


def context_includes_jira_tickets(
    flattened_results: list[dict[str, Any]],
    virtual_record_id_to_result: dict[str, Any],
) -> bool:
    vrids = {r.get("virtual_record_id") for r in flattened_results if r.get("virtual_record_id")}
    return any(
        is_jira_ticket_record(virtual_record_id_to_result.get(vrid))
        for vrid in vrids
    )


def get_message_content(
    flattened_results: list[dict[str, Any]],
    virtual_record_id_to_result: dict[str, Any],
    user_data: str,
    query: str,
    ref_mapper: CitationRefMapper | None = None,
) -> tuple[list[dict[str, Any]], CitationRefMapper]:
    """Build the user message content for the no-tools (Ollama/simple) path.

    Renders ``qna_prompt_simple`` with deduplicated, citation-enriched chunks.
    """
    if ref_mapper is None:
        ref_mapper = CitationRefMapper()
    content = []
    chunks = []
    seen_blocks: set[str] = set()
    for result in flattened_results:
        virtual_record_id = result.get("virtual_record_id")
        block_index = result.get("block_index")
        result_id = f"{virtual_record_id}_{block_index}"

        if result_id not in seen_blocks:
            seen_blocks.add(result_id)
            block_type = result.get("block_type")

            if block_type == BlockType.IMAGE.value:
                continue

            block_web_url = ""
            record = virtual_record_id_to_result.get(virtual_record_id) or {}
            frontend_url = record.get("frontend_url", "")
            record_id = record.get("id", "")
            block_web_url = build_block_web_url(frontend_url, record_id, block_index) if frontend_url and record_id else ""
            citation_ref = ref_mapper.get_or_create_ref(block_web_url) if block_web_url else ""

            if block_type == GroupType.TABLE.value:
                table_summary, _ = result.get("content")
                content_text = f"Table: {table_summary}"
            else:
                content_text = result.get("content", "")

            chunks.append({
                "metadata": {
                    "blockText": content_text,
                    "recordName": result.get("metadata", {}).get("recordName", ""),
                    "block_web_url": block_web_url,
                    "citation_ref": citation_ref,
                }
            })

    rendered_form = compiled_template(qna_prompt_simple).render(query=query, chunks=chunks)
    content.append({"type": "text", "text": rendered_form})
    return content, ref_mapper

def build_message_content_array(
    flattened_results: list[dict[str, Any]],
    virtual_record_id_to_result: dict[str, Any],
    is_multimodal_llm: bool = False,
    ref_mapper: CitationRefMapper | None = None,
    from_tool: bool = True,
    record_id_shortener: "RecordIdShortener | None" = None,
    collected_images: list[dict[str, Any]] | None = None,
    image_budget: "ImageBudget | None" = None,
) -> tuple[list[list[dict[str, Any]]], CitationRefMapper]:
    """
    Args (new):
        collected_images: When `from_tool=True` and provided, IMAGE blocks
            (standalone and inline table/group images) are routed into
            this list instead of being silently dropped or embedded
            inline — the side-channel a tool wrapper (e.g. `retrieval.py`)
            reads to build a multipart `ToolOutput`. `None` preserves the
            pre-existing behavior for each `from_tool` value.
        image_budget: Conversation-wide `ImageBudget` (50-image cap by
            default) shared across every image source in the turn.
            Defaults to a fresh per-call budget when not supplied.
    """
    if ref_mapper is None:
        ref_mapper = CitationRefMapper()
    if image_budget is None:
        image_budget = ImageBudget(MAX_IMAGES_IN_CONVERSATION)
    all_contents = []
    content = []
    seen_virtual_record_ids = set()
    seen_blocks = set()
    current_frontend_url = ""
    current_record_id = ""
    current_file_path = ""
    # True so the first record's blocks get "Record blocks (sorted):"; later records reopen
    # pending via the i > 0 branch before the next record's metadata.
    pending_record_blocks_sorted_header = True
    record_page_url_for_summary: str | None = None
    summary_citation_insert_index: int | None = None
    current_record_has_blocks = False
    # Table/group inline images only go through the collected_images side
    # channel when the caller both wants tool-result delivery (from_tool)
    # AND supplied somewhere to put them — preserves the from_tool=False
    # direct-embed behavior (currently unused in practice, kept for API
    # parity with the top-level IMAGE-block branch below).
    _group_collected_images = collected_images if from_tool else None

    def insert_summary_citation_if_needed() -> None:
        nonlocal record_page_url_for_summary, summary_citation_insert_index, current_record_has_blocks
        if (
            record_page_url_for_summary
            and not current_record_has_blocks
            and summary_citation_insert_index is not None
        ):
            overview_ref = ref_mapper.get_or_create_ref(record_page_url_for_summary)
            content.insert(summary_citation_insert_index, {
                "type": "text",
                "text": (
                    f"* Citation ID for summary: {overview_ref}\n"
                ),
            })
        record_page_url_for_summary = None
        summary_citation_insert_index = None
        current_record_has_blocks = False

    def prepend_record_blocks_sorted_header(text: str) -> str:
        nonlocal pending_record_blocks_sorted_header
        if pending_record_blocks_sorted_header:
            pending_record_blocks_sorted_header = False
            return f"Record blocks (sorted):\n{text}"
        return text

    for i,result in enumerate(flattened_results):
        virtual_record_id = result.get("virtual_record_id")
        if virtual_record_id not in seen_virtual_record_ids:
            if i > 0:
                insert_summary_citation_if_needed()
                content.append({
                    "type": "text",
                    "text": "</record>"
                })
                pending_record_blocks_sorted_header = True
                all_contents.append(content)
                content = []
            seen_virtual_record_ids.add(virtual_record_id)
            record = virtual_record_id_to_result[virtual_record_id]
            if record is None:
                continue

            current_frontend_url = record.get("frontend_url", "")
            current_record_id = record.get("id", "")
            current_file_path = record.get("file_path", "") or ""

            template = compiled_template(qna_prompt_context)
            rendered_form = template.render(
                context_metadata=record.get("context_metadata", ""),
            )
            record_header_text = rendered_form
            parent_info = build_parent_info(result)
            if parent_info:
                record_header_text = f"{record_header_text}{parent_info}"
            relations_info = build_record_relations_info(record)
            if relations_info:
                record_header_text = f"{record_header_text}{relations_info}"
            if record_id_shortener is not None:
                record_header_text = record_id_shortener.shorten_record_ids_in_text(record_header_text)
            content.append({
                "type": "text",
                "text": record_header_text
            })
            record_page_url_for_summary = build_record_page_web_url(
                current_frontend_url, current_record_id
            ) or None
            summary_citation_insert_index = len(content)
            current_record_has_blocks = False

        result_id = f"{virtual_record_id}_{result.get('block_index')}"
        if result_id not in seen_blocks:
            seen_blocks.add(result_id)
            block_type = result.get("block_type")
            block_index = result.get("block_index")
            block_web_url = build_block_web_url(current_frontend_url, current_record_id, block_index)
            result["block_web_url"] = block_web_url
            ref = ref_mapper.get_or_create_ref(block_web_url)
            result["citation_ref"] = ref
            if block_type == BlockType.IMAGE.value:
                image_content = result.get("content")
                if is_base64_image(image_content) and is_multimodal_llm:
                    current_record_has_blocks = True
                    if image_budget.can_add():
                        image_budget.try_consume(1)
                        if from_tool and collected_images is not None:
                            # ToolMessage only carries images via its
                            # multipart content (see agent_loop_lib
                            # messages.py) — never inline them into the
                            # text-typed content list a tool result's text
                            # is built from.
                            collected_images.append({
                                "ref": ref,
                                "block_index": block_index,
                                "image_url": {"url": image_content},
                                "virtual_record_id": virtual_record_id,
                            })
                            content.append({
                                "type": "text",
                                "text": prepend_record_blocks_sorted_header(
                                    f"[{block_index}|{ref}] (image)\n\n"
                                ),
                            })
                        elif not from_tool:
                            content.append({
                                "type": "text",
                                "text": prepend_record_blocks_sorted_header(
                                    f"[{block_index}|{ref}]"
                                ),
                            })
                            content.append({
                                "type": "image_url",
                                "image_url": {"url": image_content}
                            })
                        else:
                            # from_tool=True with no collected_images sink:
                            # the caller has no way to carry an image
                            # through its tool result, so fall back to a
                            # text-only placeholder rather than inlining an
                            # image_url block that would just be dropped by
                            # a text-only join downstream.
                            content.append({
                                "type": "text",
                                "text": prepend_record_blocks_sorted_header(
                                    f"[{block_index}|{ref}] (image) "
                                    f"{result.get('image_description', '')}\n\n"
                                ),
                            })
                    elif result.get("image_description"):
                        content.append({
                            "type": "text",
                            "text": prepend_record_blocks_sorted_header(
                                f"[{block_index}|{ref}] (image) {result.get('image_description')}\n\n"
                            ),
                        })
                    else:
                        content.append({
                            "type": "text",
                            "text": prepend_record_blocks_sorted_header(
                                f"[{block_index}|{ref}] (image block - visual content not "
                                "shown due to conversation image limit)\n\n"
                            ),
                        })
                else:
                    if is_base64_image(image_content):
                        continue
                    current_record_has_blocks = True
                    content.append({
                        "type": "text",
                        "text": prepend_record_blocks_sorted_header(
                            f"[{block_index}|{ref}] (image) {image_content}\n\n"
                        ),
                    })
            elif block_type == GroupType.TABLE.value:
                table_summary,child_results = result.get("content")
                block_group_index = result.get("block_group_index")
                fk_info = build_fk_info(result)
                if not child_results:
                    child_results = []
                has_row_images = any(cr.get("block_type") == BlockType.IMAGE.value for cr in child_results)
                for child in child_results:
                    child["block_web_url"] = build_block_web_url(current_frontend_url, current_record_id, child.get("block_index", 0))
                    child["citation_ref"] = ref_mapper.get_or_create_ref(child["block_web_url"])
                current_record_has_blocks = True
                if not has_row_images:
                    template = compiled_template(table_prompt)
                    rendered_form = template.render(
                        block_group_index=block_group_index,
                        block_group_web_url="",
                        table_summary=table_summary,
                        table_rows=child_results,
                    )
                    content.append({
                        "type": "text",
                        "text": prepend_record_blocks_sorted_header(f"{rendered_form}{fk_info}\n\n"),
                    })
                else:
                    header = f"[Table #{block_group_index}: {table_summary}]\n"
                    content.append({
                        "type": "text",
                        "text": prepend_record_blocks_sorted_header(f"{header}{fk_info}"),
                    })
                    content.extend(_render_blocks_with_images(
                        child_results, is_multimodal_llm, image_budget, _group_collected_images,
                        allow_inline_images=not from_tool,
                    ))
            elif block_type == BlockType.TEXT.value:
                current_record_has_blocks = True
                content.append({
                    "type": "text",
                    "text": prepend_record_blocks_sorted_header(
                        f"[{block_index}|{ref}] {result.get('content')}\n\n"
                    ),
                })
            elif block_type == BlockType.CODE.value:
                # A code hit is addressed by (file path, symbol id) so the model
                # can pass it straight to the codegraph tools.
                current_record_has_blocks = True
                locator = format_code_locator(
                    current_file_path, result.get("qualified_name", "") or ""
                )
                prefix = f"[{block_index}|{ref}]"
                if locator:
                    prefix = f"{prefix} {locator}"
                content.append({
                    "type": "text",
                    "text": prepend_record_blocks_sorted_header(
                        f"{prefix}\n{_safe_stringify_content(result.get('content'))}\n\n"
                    ),
                })
            elif block_type in valid_group_labels:
                block_group_index = result.get("block_group_index")
                group_blocks = result.get("content")[1] if isinstance(result.get("content"), tuple) else []
                if not group_blocks:
                    continue
                has_images = any(gb.get("block_type") == BlockType.IMAGE.value for gb in group_blocks)
                for gb in group_blocks:
                    gb["block_web_url"] = build_block_web_url(current_frontend_url, current_record_id, gb.get("block_index", 0))
                    gb["citation_ref"] = ref_mapper.get_or_create_ref(gb["block_web_url"])

                if not has_images:
                    template = compiled_template(block_group_prompt)
                    rendered_form = template.render(
                        block_group_index=block_group_index,
                        block_group_web_url="",
                        label=block_type,
                        blocks=group_blocks,
                        file_path=current_file_path,
                    )
                    current_record_has_blocks = True
                    content.append({
                        "type": "text",
                        "text": prepend_record_blocks_sorted_header(f"{rendered_form}\n\n"),
                    })
                else:
                    # Emit blocks in reading order to preserve text/image interleaving.
                    header = f"[{block_type} #{block_group_index}]\n"
                    current_record_has_blocks = True
                    content.append({
                        "type": "text",
                        "text": prepend_record_blocks_sorted_header(header),
                    })
                    content.extend(_render_blocks_with_images(
                        group_blocks, is_multimodal_llm, image_budget, _group_collected_images,
                        allow_inline_images=not from_tool,
                    ))
            else:
                continue
        else:
            continue

    if content:
        insert_summary_citation_if_needed()
        content.append({
            "type": "text",
            "text": "</record>"
        })
        all_contents.append(content)

    return all_contents, ref_mapper


def build_fk_info(result: dict[str, Any]) -> str:
    """Build FK relations info string from a result's fk_parent_relations and fk_child_relations."""
    fk_parent_relations = result.get("fk_parent_relations", [])
    fk_child_relations = result.get("fk_child_relations", [])
    fk_info = ""
    if fk_parent_relations or fk_child_relations:
        fk_info = "\n* FK Relations (use fetch_full_record tool with these record_ids to get related table data):"
        if fk_parent_relations:
            fk_info += "\n  - Parent Tables (this table references):"
            for rel in fk_parent_relations:
                parent_table = rel.get("parentTable", "")
                source_col = rel.get("sourceColumn", "")
                target_col = rel.get("targetColumn", "")
                record_id = rel.get("record_id", "")
                fk_info += f"\n    - {parent_table} (via source column:{source_col} -> target column:{target_col}) [record_id: {record_id}]"
        if fk_child_relations:
            fk_info += "\n  - Child Tables (reference this table):"
            for rel in fk_child_relations:
                child_table = rel.get("childTable", "")
                source_col = rel.get("sourceColumn", "")
                target_col = rel.get("targetColumn", "")
                record_id = rel.get("record_id", "")
                fk_info += f"\n    - {child_table} (via source column:{source_col} -> target column:{target_col}) [record_id: {record_id}]"
        logger.debug(f"FK info: {fk_info}")
    return fk_info




def count_tokens_in_content_list(content: list[dict[str, Any]],enc) -> int:
    total_tokens = 0
    for item in content:
        if item.get("type") == "text":
            total_tokens += count_tokens_text(item.get("text", ""), enc)

    return total_tokens


# Vision providers charge image inputs separately; base64 in data URIs is not meaningful cl100k text.
_DEFAULT_VISION_IMAGE_TOKEN_ESTIMATE = 1700


def count_tokens_in_messages(messages: list[Any],enc) -> int:
    """
    Count the total number of tokens in a messages array.
    Supports both dict messages and LangChain message objects.

    Args:
        messages: List of message dictionaries or LangChain message objects

    Returns:
        Total number of tokens across all messages
    """
    logger.debug(
        "count_tokens_in_messages: starting token count for %d messages",
        len(messages) if messages else 0,
    )

    total_tokens = 0

    for message in messages:
        # Handle LangChain message objects (AIMessage, HumanMessage, ToolMessage, etc.)
        if hasattr(message, "content"):
            content = getattr(message, "content", "")
        # Handle dict messages
        elif isinstance(message, dict):
            content = message.get("content", "")
        else:
            # Skip unknown types
            logger.debug("count_tokens_in_messages: skipping unknown message type")
            continue

        # Handle different content types
        if isinstance(content, str):
            total_tokens += count_tokens_text(content,enc)
        elif isinstance(content, list):
            # Handle content as list of content objects (like in get_message_content)
            for content_item in content:
                if isinstance(content_item, dict):
                    if content_item.get("type") == "text":
                        text_content = content_item.get("text", "")
                        total_tokens += count_tokens_text(text_content,enc)
                    # Skip image_url and other non-text content for token counting
                elif isinstance(content_item, str):
                    total_tokens += count_tokens_text(content_item,enc)
        else:
            # Convert other types to string
            total_tokens += count_tokens_text(str(content),enc)

    return total_tokens


def count_tokens_text(text: str,enc) -> int:
    """Count tokens in text using tiktoken or fallback heuristic"""
    if not text:
        return 0
    if enc is not None:
        try:
            return len(enc.encode(text))
        except Exception:
            logger.warning("tiktoken encoding failed, falling back to heuristic.")
            pass
    else:
        try:
            import tiktoken  # type: ignore
            try:
                enc = tiktoken.get_encoding("cl100k_base")
                return len(enc.encode(text))
            except Exception:
                logger.warning("tiktoken encoding failed, falling back to heuristic.")
                pass
        except Exception:
            logger.warning("tiktoken encoding failed, falling back to heuristic.")
            pass

    return max(1, len(text) // 4)

def count_tokens(messages: list[Any], message_contents: list[list[dict[str, Any]]]) -> tuple[int, int]:
    # Lazy import tiktoken; fall back to a rough heuristic if unavailable
    enc = None
    try:
        import tiktoken  # type: ignore
        try:
            enc = tiktoken.get_encoding("cl100k_base")
        except Exception:
            logger.warning("tiktoken encoding failed, falling back to heuristic.")
            enc = None
    except Exception:
        logger.warning("tiktoken import failed, falling back to heuristic.")
        enc = None


    current_message_tokens = count_tokens_in_messages(messages,enc)
    new_tokens = 0

    flattened_message_contents = [item for sublist in message_contents for item in sublist]

    for message in flattened_message_contents:
        text_content = message.get("text", "") if message.get("type") == "text" else ""
        if text_content:
            new_tokens += count_tokens_text(text_content,enc)

    return current_message_tokens, new_tokens



FRAGMENT_WORD_COUNT = 4

_FRAGMENT_WORD_PATTERN = re.compile(r"(?:(?<= )|^)[A-Za-z]+(?: [A-Za-z]+)+(?![A-Za-z'-])")

def extract_start_end_text(snippet: str | None) -> tuple[str, str]:
    if not snippet:
        return "", ""

    PATTERN = _FRAGMENT_WORD_PATTERN

    # --- Find start_text: first match with at least FRAGMENT_WORD_COUNT words, else longest ---
    all_matches = list(PATTERN.finditer(snippet))
    if not all_matches:
        return "", ""

    best_match = next(
        (m for m in all_matches if len(m.group().strip().split()) >= FRAGMENT_WORD_COUNT),
        max(all_matches, key=lambda m: len(m.group().strip().split())),
    )
    first_text = best_match.group().strip()
    if not first_text:
        return "", ""

    words = first_text.split()
    start_text = " ".join(words[:FRAGMENT_WORD_COUNT])
    start_text_end = best_match.start() + len(first_text.split()[0])

    # Compute exact end position of start_text in snippet
    leading_spaces = len(best_match.group()) - len(best_match.group().lstrip())
    start_text_begin = best_match.start() + leading_spaces
    start_text_end = start_text_begin + len(start_text)

    # --- Find end_text: last matching segment after start_text_end ---
    remaining = snippet[start_text_end:]

    last_text = None
    for m in PATTERN.finditer(remaining):
        stripped = m.group().strip()
        if stripped:
            last_text = stripped

    if last_text:
        words = last_text.split()
        end_text = " ".join(words[-FRAGMENT_WORD_COUNT:])
    elif len(first_text.split()) > FRAGMENT_WORD_COUNT:
        word_count = len(first_text.split())
        diff = word_count - FRAGMENT_WORD_COUNT
        diff = min(FRAGMENT_WORD_COUNT, diff)
        # Fall back to last 4 words of the first segment
        end_text = " ".join(first_text.split()[-diff:])
    else:
        end_text = ""

    return start_text, end_text.strip()

# Values are fragment URLs that embed a few URL-encoded snippet words in
# #:~:text=… — not whole record blocks. Keys are digests of the snippet.
# TTL + maxsize keep retention bounded across orgs in a long-lived process.
_FRAGMENT_URL_CACHE: dict[tuple[str, bytes], tuple[str, float]] = {}
_FRAGMENT_URL_CACHE_MAXSIZE = 8192
_FRAGMENT_URL_CACHE_TTL_SECONDS = 300.0


def generate_text_fragment_url(base_url: str, text_snippet: str) -> str:
    """Memoized wrapper over `_build_text_fragment_url`.

    The live citation overlay re-derives every citation's URL on each refresh,
    so the same (base_url, snippet) pair is re-scanned many times per turn.
    Snippets are keyed by digest so the cache does not retain full record text
    as keys; cached values still hold the short start/end words used in the
    text-fragment directive. Entries expire after
    `_FRAGMENT_URL_CACHE_TTL_SECONDS` and the map is cleared wholesale when
    full — eviction bookkeeping would cost more than the recompute it saves.
    """
    if not isinstance(base_url, str) or not isinstance(text_snippet, str):
        return _build_text_fragment_url(base_url, text_snippet)

    key = (base_url, hashlib.sha1(text_snippet.encode("utf-8", "surrogatepass")).digest())
    now = time.monotonic()
    cached = _FRAGMENT_URL_CACHE.get(key)
    if cached is not None:
        result, expires_at = cached
        if expires_at > now:
            return result
        _FRAGMENT_URL_CACHE.pop(key, None)

    result = _build_text_fragment_url(base_url, text_snippet)
    if len(_FRAGMENT_URL_CACHE) >= _FRAGMENT_URL_CACHE_MAXSIZE:
        _FRAGMENT_URL_CACHE.clear()
    _FRAGMENT_URL_CACHE[key] = (result, now + _FRAGMENT_URL_CACHE_TTL_SECONDS)
    return result


def _build_text_fragment_url(base_url: str, text_snippet: str) -> str:
    """
    Generate a URL with text fragment for direct navigation to specific text.

    Format: url#:~:text=start_text,end_text

    Args:
        base_url: The base URL of the page
        text_snippet: The text to highlight/navigate to

    Returns:
        URL with text fragment, or base_url if encoding fails
    """
    if not base_url or not text_snippet:
        return base_url

    # Preserve URLs that already have a text fragment
    if TEXT_FRAGMENT_DIRECTIVE_PREFIX in base_url:
        return base_url

    try:
        snippet = text_snippet.strip()
        if not snippet:
            return base_url

        while snippet and not snippet[-1].isalnum():
            snippet = snippet[:-1]
        if not snippet:
            return base_url

        start_text, end_text = extract_start_end_text(snippet)

        if not start_text:
            return base_url

        encoded_start = quote(start_text, safe="';:[]")
        encoded_end = ""

        if end_text:
            encoded_end = quote(end_text, safe="';:[]")

        if '#' in base_url:
            base_url = base_url.split('#')[0]

        return f"{base_url}#:~:text={encoded_start}{(',' + encoded_end) if encoded_end else ''}"

    except Exception:
        return base_url




