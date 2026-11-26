from ..backend import NOOP

GATEWAY_REQUESTS = NOOP
GATEWAY_REQUEST_DURATION = NOOP
GATEWAY_REFUSALS = NOOP
TOOL_GATEWAY_CALLS = NOOP
TOOL_GATEWAY_CALL_DURATION = NOOP
READ_CACHE_LOOKUPS = NOOP


def is_gateway_token_refusal(reason: str | None) -> bool:
    return False


class BackendGatewayMetrics:
    def request(self, observed) -> None:
        return None

    def tool_call(self, observed) -> None:
        return None


class BackendReadCacheMetrics:
    def lookup(self, observed) -> None:
        return None


READ_CACHE_METRICS = BackendReadCacheMetrics()
