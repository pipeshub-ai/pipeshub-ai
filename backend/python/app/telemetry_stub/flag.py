TELEMETRY_COLLECTION_FLAG = "TELEMETRY_COLLECTION_FLAG"


def telemetry_enabled() -> bool:
    return False


def set_collection_consent(given: bool) -> None:  # noqa: FBT001
    return None


def telemetry_collecting() -> bool:
    return False
