import asyncio
from unittest.mock import MagicMock
import pytest
from app.services.messaging.kafka.producer.producer import KafkaMessagingProducer


class FakeAIOProducer:
    """Mimics aiokafka: send() buffers and returns a future; nothing resolves until flushed."""
    def __init__(self, fail_on=(), raise_on=()):
        self.inflight = 0
        self.max_inflight = 0
        self.futures = []
        self.fail_on = set(fail_on)
        self.raise_on = set(raise_on)
        self.n = 0

    async def send(self, topic, key=None, value=None):
        i = self.n
        self.n += 1
        if i in self.raise_on:
            raise RuntimeError(f"enqueue refused {i}")
        loop = asyncio.get_running_loop()
        fut = loop.create_future()
        self.inflight += 1
        self.max_inflight = max(self.max_inflight, self.inflight)
        def resolve():
            if fut.done():
                return
            if i in self.fail_on:
                fut.set_exception(RuntimeError(f"delivery failed {i}"))
            else:
                md = MagicMock(); md.topic = topic; md.partition = 0; md.offset = i
                fut.set_result(md)
        loop.call_soon(resolve)
        self.futures.append(fut)
        return fut


def make(fake):
    p = KafkaMessagingProducer(MagicMock(), MagicMock())
    p.producer = fake
    return p


@pytest.mark.asyncio
async def test_all_enqueued_before_any_ack_awaited():
    """The whole batch must be buffered before we wait on acks - that is what lets
    aiokafka coalesce it. send_and_wait per record would cap max_inflight at 1."""
    fake = FakeAIOProducer()
    p = make(fake)
    acked = await p.send_messages("t", [(f"k{i}", {"i": i}) for i in range(10)])
    assert acked == [True] * 10
    assert fake.max_inflight == 10, f"only {fake.max_inflight} buffered at once"


@pytest.mark.asyncio
async def test_partial_delivery_failure_is_positional():
    fake = FakeAIOProducer(fail_on={1, 3})
    p = make(fake)
    acked = await p.send_messages("t", [(f"k{i}", {"i": i}) for i in range(5)])
    assert acked == [True, False, True, False, True]


@pytest.mark.asyncio
async def test_enqueue_error_does_not_sink_the_rest():
    """A record aiokafka refuses (e.g. too large) must fail only its own slot."""
    fake = FakeAIOProducer(raise_on={2})
    p = make(fake)
    acked = await p.send_messages("t", [(f"k{i}", {"i": i}) for i in range(5)])
    assert acked == [True, True, False, True, True]


@pytest.mark.asyncio
async def test_empty_batch_is_a_noop():
    fake = FakeAIOProducer()
    p = make(fake)
    assert await p.send_messages("t", []) == []
    assert fake.n == 0
