"""The storage-protocol tag every indexing consumer carries in its name.

An indexing pod that writes the stored record by compare-and-set (this release) names its
Kafka ``client_id`` and its Redis consumer with ``/sp3``. A member of an indexing consumer
group without it is an older pod, whose in-place writes do not follow the protocol. The
test is a substring match, never an end-of-name match: the consumers append their own
suffixes.
"""

STORAGE_PROTOCOL_TAG = "/sp3"


def tagged(client_id: str) -> str:
    """*client_id* carrying the tag."""
    return client_id if STORAGE_PROTOCOL_TAG in client_id else f"{client_id}{STORAGE_PROTOCOL_TAG}"


def is_tagged(name: str) -> bool:
    return STORAGE_PROTOCOL_TAG in name
