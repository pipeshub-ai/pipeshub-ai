from abc import ABC, abstractmethod

from app.events.events import EventProcessor


class Scheduler(ABC):
    @abstractmethod
    async def schedule_event(self, event_data: dict) -> None:
        pass

    @abstractmethod
    async def get_scheduled_events(self) -> list:
        pass

    @abstractmethod
    async def remove_processed_event(self, event_data: dict) -> None:
        pass

    @abstractmethod
    async def process_scheduled_events(self, event_processor: EventProcessor) -> None:
        pass

    # @abstractmethod
    # async def stop(self) -> None:
    #     pass
