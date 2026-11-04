from fastapi import FastAPI


class _Pusher:
    async def start(self) -> None:
        return None

    async def stop(self) -> None:
        return None


class PendingPusher:
    def __init__(self, service_name: str) -> None:
        self._service_name = service_name
        self._pusher: _Pusher | None = None

    def bind(self, config_service: object, logger: object) -> _Pusher:
        self._pusher = _Pusher()
        return self._pusher

    @property
    def pusher(self) -> _Pusher | None:
        return self._pusher


def setup_telemetry(app: FastAPI, service_name: str) -> PendingPusher:
    return PendingPusher(service_name)
