from ..backend import NOOP

EGRESS_BYTES = NOOP
EGRESS_VOLUME_ALERTS = NOOP
EGRESS_RUN_BYTES = NOOP


class PrometheusVolumeMetrics:
    def transferred(self, alias: str, direction: str, count: int) -> None:
        return None

    def alerted(self, kind: str, direction: str) -> None:
        return None

    def run_ended(self, scope: str, direction: str, count: int) -> None:
        return None
