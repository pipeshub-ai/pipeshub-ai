"""Whether content was pushed by an API caller, and what that forbids at index time.

Pushed blocks are checked once, at the push. At index time the images they hold
must already be inlined: indexing never fetches a remote URL for them, whatever
the parser backend, so the content cannot change without a push and no caller
can make the indexer open connections.

Any one marker is enough. The content marker is primary: the stored document is
already parsed at every entry point, so it costs no read and survives any move
or copy of the record.
"""

from collections.abc import Mapping

from app.connectors.sources.localKB.push.contract import (
    PUSH_FORMAT_KEY,
    PUSH_ORIGIN_FIELD,
    is_pushed_revision,
)
from app.exceptions.indexing_exceptions import IndexingError
from app.exceptions.non_retryable import NonRetryableError
from app.models.blocks import BlocksContainer, BlockType

INLINE_IMAGE_PREFIX = "data:image/"


class PushImageNotInlinedError(IndexingError, NonRetryableError):
    """A pushed record holds an image that is not inline. Retrying cannot change the stored bytes."""


def content_is_push_origin(blocks_dict: object) -> bool:
    """The stored blocks document carries the marker only the push writes."""
    return isinstance(blocks_dict, Mapping) and PUSH_FORMAT_KEY in blocks_dict


def record_is_push_origin(record: object) -> bool:
    """A record, or its stored document, that a push created.

    The ``pushOrigin`` flag is second; the ``pb<N>:`` revision tag covers records
    written before the flag existed.
    """
    if isinstance(record, Mapping):
        return record.get(PUSH_ORIGIN_FIELD) is True or is_pushed_revision(record.get("externalRevisionId"))
    return getattr(record, "push_origin", False) is True or is_pushed_revision(
        getattr(record, "external_revision_id", None)
    )


def is_push_origin(*, content: object = None, record: object = None) -> bool:
    return content_is_push_origin(content) or (record is not None and record_is_push_origin(record))


def assert_images_inline(container: BlocksContainer) -> None:
    """Fail the record if any image URI of pushed content is not ``data:image/``.

    The sink guard leaves a remote image out; this is the second layer, so a
    record is never indexed with a remote URI whatever went wrong before it.
    """
    for block in container.blocks:
        if block.type != BlockType.IMAGE or not isinstance(block.data, dict):
            continue
        uri = block.data.get("uri")
        if uri is not None and not (isinstance(uri, str) and uri.startswith(INLINE_IMAGE_PREFIX)):
            raise PushImageNotInlinedError("A pushed record holds an image that is not inline", details={"block_index": block.index})
