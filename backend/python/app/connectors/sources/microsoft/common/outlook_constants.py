"""Outlook-specific constants shared by Outlook Personal and Outlook (team) connectors."""

import base64
import hashlib
import json
import re
from dataclasses import dataclass

from msgraph.generated.models.message import Message  # type: ignore


class OutlookConnectorNames:
    """Connector names for registry (used only in @ConnectorBuilder decorator)."""
    PERSONAL = "Outlook Personal"
    TEAM = "Outlook"


class OutlookOAuthRedirectURIs:
    """OAuth redirect URI path segments (relative to app base)."""
    PERSONAL = "connectors/oauth/callback/OutlookPersonal"


class OutlookDocs:
    """Documentation URLs."""
    AZURE_AD_SETUP_URL = "https://docs.microsoft.com/en-us/azure/active-directory/develop/quickstart-register-app"
    PIPESHUB_DOCS_URL_PERSONAL = "https://docs.pipeshub.com/connectors/microsoft-365/outlook-personal"
    PIPESHUB_DOCS_URL_TEAM = "https://docs.pipeshub.com/connectors/microsoft-365/outlook"


class OutlookThreadDetection:
    """Email thread detection (conversation index)."""
    ROOT_CONVERSATION_INDEX_LENGTH = 22
    CHILD_INDEX_SUFFIX_LENGTH = 5


def normalize_conversation_index(value: str | bytes | None) -> str:
    """Kiota deserializes Graph Edm.Binary conversationIndex as bytes; MailRecord stores base64 str."""
    if value is None or value == b"" or value == "":
        return ""
    if isinstance(value, bytes):
        return base64.b64encode(value).decode("ascii")
    return value


def mail_content_revision(message: Message) -> str:
    """Record revision of a mail: changes when its indexed content does, not on read/flag/category.

    A draft also carries its changeKey, so an edit anywhere in its body or attachments re-indexes it.
    shortcut: a sent/received mail is hashed on bodyPreview (first 255 chars), not the full body,
    to keep delta pages small; select ``body`` if edits deeper in received mail must re-index.
    """
    parts = [message.subject, message.body_preview, bool(message.has_attachments),
             message.change_key if message.is_draft else None]
    return hashlib.sha256(json.dumps(parts).encode("utf-8")).hexdigest()


# The HTTP status in a failed Graph call's text. Kiota's APIError renders it as "Code: <status>";
# other kiota/msgraph versions end the text in "(status <status>)".
_GRAPH_STATUS = re.compile(r"\bCode: (\d{3})\b|\(status (\d{3})\)")


def graph_error_status(error: object) -> int | None:
    """The HTTP status of a failed Graph call, read from the error text the data source passes on."""
    match = _GRAPH_STATUS.search(str(error))
    return int(match.group(1) or match.group(2)) if match else None


# Graph answers a delta link it no longer accepts with 410 and one of these codes.
# The data source passes errors on only as text, so they are matched in that text.
_EXPIRED_DELTA_LINK = re.compile(r"syncStateNotFound|syncStateInvalid|resyncRequired")


def is_expired_delta_link_error(error: object) -> bool:
    """True when a delta read failed because the saved delta link expired or was invalidated."""
    return graph_error_status(error) == 410 or bool(_EXPIRED_DELTA_LINK.search(str(error)))


# Graph answers a well-known folder the mailbox does not have (e.g. no archive) with 404.
_FOLDER_NOT_FOUND = re.compile(r"ErrorItemNotFound|ErrorFolderNotFound")


def is_folder_not_found_error(error: object) -> bool:
    """True when a mail folder read failed because the folder does not exist."""
    return graph_error_status(error) == 404 or bool(_FOLDER_NOT_FOUND.search(str(error)))


# Graph answers a user without an Exchange Online mailbox (no licence) with 404 and one of these codes.
_NO_MAILBOX = re.compile(r"MailboxNotEnabledForRESTAPI|ResourceNotFound")


def is_no_mailbox_error(error: object) -> bool:
    """True when ``error`` (an error text or a Graph error body) names a missing mailbox; any
    other 404 is not one. Newer Kiota releases leave the code out of the text they pass on."""
    return bool(_NO_MAILBOX.search(str(error)))


class OutlookFolders:
    """Standard Outlook folders: filter option label -> Graph well-known folder name."""
    # Matched through /mailFolders/{well-known name}: display names are localized.
    WELL_KNOWN_NAMES: dict[str, str] = {
        "Inbox": "inbox",
        "Sent Items": "sentitems",
        "Drafts": "drafts",
        "Deleted Items": "deleteditems",
        "Junk Email": "junkemail",
        "Archive": "archive",
        "Outbox": "outbox",
        "Conversation History": "conversationhistory",
    }
    STANDARD_FOLDERS: list[str] = list(WELL_KNOWN_NAMES)
    # Synced only when a folder filter selects it. Deleting a mail or a folder in Outlook
    # moves it here, so syncing it by default kept deleted mail searchable. Recoverable
    # Items (recoverableitemsdeletions) is outside the folder tree and never listed.
    DEFAULT_EXCLUDED: tuple[str, ...] = ("Deleted Items",)


class OutlookSyncConfig:
    """Sync and pagination configuration."""
    DEFAULT_SYNC_INTERVAL_MINUTES = 60
    MESSAGE_PAGE_SIZE = 100
    # After this many runs in a row kept a folder's link for a failing mail, the link is saved anyway.
    MAX_HELD_BACK_RUNS = 3
    # A group's post cursor is read back this far, for posts that show up late.
    GROUP_CURSOR_OVERLAP_MINUTES = 10
    FOLDER_PAGE_SIZE = 50
    MAX_FOLDER_PAGE_SIZE = 100
    USER_GROUPS_SYNC_BATCH_SIZE = 10
    # Requests per second across one connector's Graph calls (same budget as the Azure connectors).
    GRAPH_RATE_LIMIT = 50
    # An attachment above this is saved as a record but not indexed automatically.
    MAX_INDEXED_ATTACHMENT_BYTES = 25 * 1024 * 1024
    ATTACHMENT_CHUNK_SIZE = 1024 * 1024


class OutlookAPIFields:
    """Graph API $select field lists."""
    MESSAGE_SELECT_FIELDS: list[str] = [
        "id",
        "subject",
        "hasAttachments",
        "createdDateTime",
        "lastModifiedDateTime",
        "receivedDateTime",
        "webLink",
        "from",
        "toRecipients",
        "ccRecipients",
        "bccRecipients",
        "conversationId",
        "internetMessageId",
        "conversationIndex",
        # Inputs of mail_content_revision.
        "bodyPreview",
        "changeKey",
        "isDraft",
    ]

    FOLDER_SELECT_FIELDS: list[str] = ["id", "displayName"]
    
    # Team connector - Groups API select fields
    GROUP_SELECT_FIELDS: list[str] = [
        "id",
        "displayName",
        "description",
        "mail",
        "mailNickname",
        "groupTypes",
        "createdDateTime",
        "mailEnabled"
    ]
    
    # Team connector - Group members select fields
    GROUP_MEMBER_SELECT_FIELDS: list[str] = [
        "id",
        "displayName",
        "mail",
        "userPrincipalName"
    ]
    
    # Team connector - User groups select fields (minimal)
    USER_GROUP_SELECT_FIELDS: list[str] = ["id", "displayName"]
    
    # Minimal user ID select field (for user existence check)
    USER_ID_SELECT_FIELDS: list[str] = ["id"]
    
    # $expand on a group post: attachment metadata without contentBytes.
    POST_ATTACHMENTS_METADATA_EXPAND = "attachments($select=id,name,contentType,size,lastModifiedDateTime)"

    # Mail attachment listing without contentBytes (@odata.type is still returned).
    ATTACHMENT_SELECT_FIELDS: list[str] = ["id", "name", "contentType", "size", "lastModifiedDateTime", "isInline"]

    # Team connector - Group conversation threads select fields
    THREAD_SELECT_FIELDS: list[str] = [
        "id",
        "topic",
        "lastDeliveredDateTime",
        "hasAttachments",
        "preview"
    ]
    
    # Team connector - Group conversation posts select fields
    POST_SELECT_FIELDS: list[str] = [
        "id",
        "body",
        "from",
        "receivedDateTime",
        "hasAttachments",
        "conversationId",
        "conversationThreadId"
    ]
    
    # Group info select fields (for URL construction)
    GROUP_INFO_SELECT_FIELDS: list[str] = ["mail", "mailNickname"]
    
    # User filter options select fields (for filter UI)
    USER_FILTER_SELECT_FIELDS: list[str] = [
        "id",
        "mail",
        "userPrincipalName",
        "displayName",
        "givenName",
        "surname",
        "accountEnabled",
        "userType",
    ]

    # User directory fields fetched during user sync (userType drives guest exclusion)
    USER_SYNC_SELECT_FIELDS: list[str] = [
        "id",
        "displayName",
        "givenName",
        "surname",
        "mail",
        "userPrincipalName",
        "userType",
        "accountEnabled",
    ]

    # Group filter options select fields (for filter UI)
    GROUP_FILTER_SELECT_FIELDS: list[str] = [
        "id",
        "displayName",
        "description",
        "mail",
        "mailNickname",
        "groupTypes",
        "mailEnabled",
    ]
    # The groups sync handles: Microsoft 365 (Unified) groups with a mailbox.
    GROUP_FILTER_ODATA = "groupTypes/any(c:c eq 'Unified') and mailEnabled eq true"


class OutlookDefaults:
    """Fallback labels for records / folders."""
    SUBJECT = "No Subject"
    FOLDER_NAME = "Unnamed Folder"
    ATTACHMENT_NAME = "Unnamed Attachment"
    UNKNOWN_FOLDER_LABEL = "Unknown"
    UNKNOWN_FOLDER_OPTION = "Unknown Folder"
    UNKNOWN_USER_LABEL = "Unknown User"
    UNKNOWN_GROUP_LABEL = "Unknown Group"


class OutlookFilterKeys:
    """Connector filter registration keys for load_connector_filters."""
    INDIVIDUAL = "outlookindividual"
    TEAM = "outlook"


class OutlookOAuthConfig:
    """OAuth helper values."""
    CONNECTOR_TYPE_PERSONAL = "Outlook Personal"
    CONFIG_SCOPE_PERSONAL_DEFAULT = "PERSONAL"


class OutlookSyncPointKeys:
    """Sync point segment and payload keys."""
    SEGMENT_FOLDERS = "folders"
    SEGMENT_FOLDER_INVENTORY = "folder_inventory"
    SEGMENT_GROUP_INVENTORY = "group_inventory"
    FOLDER_IDS = "folder_ids"
    GROUP_IDS = "group_ids"
    FILTER_FINGERPRINT = "filter_fingerprint"
    # Which kind of Graph ids a folder's delta link (and the mail it read) was saved with.
    ID_TYPE = "id_type"
    DELTA_LINK = "delta_link"
    LAST_SYNC_TIMESTAMP = "last_sync_timestamp"
    FOLDER_ID = "folder_id"
    FOLDER_NAME = "folder_name"
    # Runs in a row that kept a folder's old delta link because a mail failed.
    HELD_BACK_RUNS = "held_back_runs"
    ENCRYPT_FIELD_DELTA_LINK = "delta_link"
    
    # Team connector - Group conversations sync point keys
    RECORD_TYPE_GROUP_CONVERSATIONS = "group_conversations"
    SEGMENT_GROUP = "group"


class OutlookODataFields:
    """OData / filter field names."""
    RECEIVED_DATE_TIME = "receivedDateTime"
    DISPLAY_NAME = "displayName"


class OutlookHTTPDetails:
    """HTTP detail strings for HTTPException (not log lines)."""
    CLIENT_NOT_INITIALIZED = "External Outlook client not initialized"
    NO_PARENT_MESSAGE = "No parent message ID stored for attachment"
    UNSUPPORTED_RECORD_TYPE = "Unsupported record type for streaming"
    MISSING_GROUP_ID_POST = "Missing group_id for group post"
    MISSING_GROUP_OR_THREAD_FOR_ATTACHMENT = "Missing group_id or thread_id for group attachment"
    MISSING_THREAD_ID_POST = (
        "Missing thread_id for group post. This may be an old record - please re-sync the "
        "connector to update group posts with required metadata."
    )
    USER_CONTEXT_UNKNOWN = "Could not determine user context for this record."
    # itemAttachment and referenceAttachment carry no contentBytes, so Graph
    # answers 200 with an attachment object that has nothing to download.
    ATTACHMENT_NOT_DOWNLOADABLE = "This attachment has no downloadable content."
    GROUP_ATTACHMENT_TOO_LARGE = (
        "This group post attachment is over the 25 MB preview limit. Open the post in Outlook to download it."
    )


class OutlookMediaTypes:
    """Media types for streaming."""
    TEXT_HTML = "text/html"


@dataclass
class MessagesDeltaResult:
    """Result from delta sync of messages."""
    messages: list[Message]
    delta_link: str | None
    # True when these pages are a read of the whole folder (no saved link, or an expired one).
    full_read: bool = False


@dataclass
class OutlookCredentials:
    """Outlook OAuth credentials shared by both Personal and Team connectors."""
    tenant_id: str
    client_id: str
    client_secret: str
    has_admin_consent: bool = False
