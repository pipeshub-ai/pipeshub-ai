"""The net's settings are numbers in a range; anything else raises at startup naming the setting."""
import pytest

from app.services.graph_db.postgres.kg import flush, net


def test_the_defaults_are_a_one_percent_sample_and_an_hourly_verify(monkeypatch) -> None:
    for name in (net.SAMPLE_ENV, net.EVERY_ENV):
        monkeypatch.delenv(name, raising=False)
    assert net.sample_rate() == 0.01 and net.verify_every() == 3600.0
    monkeypatch.setenv(net.SAMPLE_ENV, "")
    assert net.sample_rate() == 0.01


@pytest.mark.parametrize(("name", "value", "want"), [("PG_KG_SHADOW_SAMPLE", "0", 0.0), ("PG_KG_SHADOW_SAMPLE", "1", 1.0), ("PG_KG_SHADOW_SAMPLE", "0.25", 0.25)])
def test_a_valid_share_is_read(monkeypatch, name, value, want) -> None:
    monkeypatch.setenv(name, value)
    assert net.sample_rate() == want


@pytest.mark.parametrize(("name", "value"), [("PG_KG_SHADOW_SAMPLE", "2"), ("PG_KG_SHADOW_SAMPLE", "-0.1"), ("PG_KG_SHADOW_SAMPLE", "lots"), ("PG_KG_SHADOW_SAMPLE", "nan"),
                                              ("PG_KG_VERIFY_EVERY_S", "-1"), ("PG_KG_VERIFY_EVERY_S", "hourly"), ("PG_KG_VERIFY_EVERY_S", "inf")])
def test_anything_else_raises_at_startup_and_names_the_setting(monkeypatch, name, value) -> None:
    monkeypatch.setenv(name, value)
    with pytest.raises(ValueError, match=name):
        flush.check_env()


def test_samples_in_flight_are_counted_per_event_loop_so_a_dead_loop_leaves_none_busy(monkeypatch) -> None:
    import asyncio
    monkeypatch.setenv(net.SAMPLE_ENV, "1")
    started: list[int] = []

    async def hangs(run, db, org, ids, access) -> None:
        started.append(1)
        await asyncio.sleep(3600)
    monkeypatch.setattr(net, "_sample", hangs)

    async def fill() -> None:
        for _ in range(net.MAX_SAMPLES + 3):
            net.maybe_sample(None, None, "o", ["a"], {})
        await asyncio.sleep(0)

    async def again() -> None:
        net.maybe_sample(None, None, "o", ["a"], {})
        await asyncio.sleep(0)
    asyncio.run(fill())
    assert len(started) == net.MAX_SAMPLES                                       # the limit held in the first loop
    asyncio.run(again())
    assert len(started) == net.MAX_SAMPLES + 1                                   # a new loop starts clean: nothing is left "in flight" from the dead one


def test_samples_take_their_ids_from_the_decided_nodes_and_at_most_the_limit(monkeypatch) -> None:
    import asyncio
    monkeypatch.setenv(net.SAMPLE_ENV, "1")
    monkeypatch.setattr(net, "SAMPLE_IDS", 3)
    got: list[list[str]] = []

    async def spy(run, db, org, ids, access) -> None:
        got.append(ids)

    async def go() -> None:
        net.maybe_sample(None, None, "o", {f"d{i}" for i in range(10)}, {})
        await net.settle()
    monkeypatch.setattr(net, "_sample", spy)
    asyncio.run(go())
    assert len(got) == 1 and len(got[0]) == 3 and all(i.startswith("d") for i in got[0])


def test_the_concurrent_samples_leave_most_of_the_default_pool_to_the_requests() -> None:
    from app.core.db import DbConfig
    pool = DbConfig("connectors", dsn="postgresql://x@h/d").pool_max
    assert net.MAX_SAMPLES * 4 <= pool                                           # a quarter of the pool at most, each sample holding one connection
