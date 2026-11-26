from ..backend import NOOP

SANDBOX_SECONDS = NOOP
WARM_CLAIMS = NOOP
WARM_POOL_DEPTH = NOOP


class BackendBrokerMetrics:
    def metered(self, interval: object) -> None:
        return None

    def warm_claim(self, outcome: object) -> None:
        return None

    def pool_depth(self, depth: int) -> None:
        return None
