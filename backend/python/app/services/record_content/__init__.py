"""Record-content resolution kernel.

Public surface:
- `RecordContentResolver` — the single façade agent tools call.
- `ResolvedRecordContent` — value object returned on success.
- Error hierarchy — `RecordContentError` and its subclasses.
"""

from .authorizer import TieredRecordAuthorizer
from .interface import IRecordAuthorizer, IRecordContentResolver, IRecordContentStrategy
from .models import (
    AttachmentFailure,
    RecordAccessDeniedError,
    RecordContentError,
    RecordContentUnavailableError,
    RecordNotFoundError,
    RecordTooLargeError,
    ResolvedRecordContent,
)
from .resolver import RecordContentResolver
from .strategies import BlobBackedContentStrategy, ConnectorBackedContentStrategy

__all__ = [
    "RecordContentResolver",
    "TieredRecordAuthorizer",
    "BlobBackedContentStrategy",
    "ConnectorBackedContentStrategy",
    "IRecordAuthorizer",
    "IRecordContentResolver",
    "IRecordContentStrategy",
    "ResolvedRecordContent",
    "AttachmentFailure",
    "RecordContentError",
    "RecordNotFoundError",
    "RecordAccessDeniedError",
    "RecordTooLargeError",
    "RecordContentUnavailableError",
]
