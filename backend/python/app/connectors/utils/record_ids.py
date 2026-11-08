from uuid import NAMESPACE_URL, uuid5

_RECORD_NAMESPACE = uuid5(NAMESPACE_URL, "pipeshub:connector-record")


def new_record_id(connector_id: str, external_record_id: str) -> str:
    """The id a connector gives a record it is about to create.

    The sync and ``stream_record`` (indexing reads a parent while the sync goes on)
    can both find a child unstored and create it. Nothing in the graph keeps one
    record per external id, so random ids gave them two records for one child, and
    every full sync then unlinked the one its lookup did not return. Derived from
    the source id, both writers write the same record.
    """
    return str(uuid5(_RECORD_NAMESPACE, f"{connector_id}\x1f{external_record_id}"))
