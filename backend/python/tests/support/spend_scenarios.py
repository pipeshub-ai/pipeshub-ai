# ruff: noqa: PERF401 - the case table appends inside loops on purpose
"""The outage and poison-document scenario families and the bounds each must meet.

``cases(tier)`` lists ``(id, run_kwargs, check)``: ``run_kwargs`` go to ``spend_sim.run_world``, ``check`` asserts the
bounds on the ``SimResult``. The seeds are fixed (33331, 33343, 33359) so a failure reproduces.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from typing import Any

from tests.support.spend_sim import SimResult

SEEDS = tuple(int(x) for x in os.environ["SPEND_SIM_SEEDS"].split(",")) if os.environ.get("SPEND_SIM_SEEDS") else (33331, 33343, 33359)
HOUR = 3600.0
Check = Callable[[SimResult], None]
Case = tuple[str, dict[str, Any], Check]


def no_failure(max_sends: int | None = None) -> Check:
    def check(res: SimResult) -> None:
        assert not res.failed(), f"FAILED {dict(res.failed())} of {res.total()}"
        if max_sends is not None:
            assert res.max_sends() <= max_sends, f"max sends {res.max_sends()} > {max_sends}"

    return check


def no_failure_and_no_hold(res: SimResult) -> None:
    no_failure()(res)
    assert res.holds == 0, f"{res.holds} hold(s): the key must not go on hold"


def lone_job_fails_by_the_absolute_cap(res: SimResult) -> None:
    (job,) = res.jobs.values()
    assert job.done == "FAILED20" and job.sends == 20
    assert job.at is not None and job.at >= 5.5 * HOUR


def poison_crowded(res: SimResult) -> None:
    poison = [j for j in res.jobs.values() if j.poison]
    assert [(j.done, j.sends) for j in poison] == [("FAILED6", 6)], "the poison document ends at exactly 6 charged sends"
    assert not res.failed(lambda j: not j.poison), "no healthy job is failed"
    assert res.holds == 0


def poison_lone(res: SimResult) -> None:
    (job,) = res.jobs.values()
    assert (job.done, job.sends) == ("FAILED20", 20)


def hold_and_redrive(res: SimResult) -> None:
    assert res.holds >= 1, "the key must go on hold"
    assert not res.failed(), f"the jobs are redriven, not failed: {dict(res.failed())}"
    assert all(j.done == "OK" for j in res.jobs.values()), "every job completes after the outage"
    kinds = [n["type"] for n in res.notices]
    assert kinds.count("INDEXING_PROVIDER_ON_HOLD") == res.holds and kinds.count("INDEXING_PROVIDER_HOLD_RELEASED") == res.holds


def dead_entry_lone_job_ok(res: SimResult) -> None:
    (job,) = res.jobs.values()
    assert job.done == "OK" and job.at is not None and job.at <= 1.6 * HOUR


def stagger_no_poison_verdict(res: SimResult) -> None:
    """Outage longer than the 24 h wall: the wall works as designed, but no job is ever failed as poison."""
    assert not res.failed().get("FAILED6"), "a staggered large-call outage must not be read as poison"
    assert res.max_sends() <= 20, "money stays bounded by the absolute cap"
    assert all(j.sends < 20 for j in res.jobs.values() if j.done == "FAILEDwall"), "a wall FAIL is not an absolute-cap FAIL"


def multi_poison(k: int) -> Check:
    def check(res: SimResult) -> None:
        assert not res.failed(lambda j: not j.poison), "no healthy job is failed"
        assert res.holds == 0
        poison = [j for j in res.jobs.values() if j.poison]
        assert len(poison) == k and all(j.done in ("FAILEDwall", "FAILED20", "FAILED6") for j in poison)
        assert all(j.sends <= 20 for j in poison)
        if k >= 3:
            assert all(j.sends <= 11 for j in poison if j.done == "FAILEDwall")

    return check


def cases(tier: str, *, full: bool | None = None) -> list[Case]:
    """``tier`` is ``unit`` (seed 33331, up to 200 jobs) or ``integration`` (all three seeds up to 200 jobs,
    and the 1000-job cases with the first seed). ``full`` (``SPEND_SIM_FULL=1``) runs the 1000-job cases with every seed."""
    heavy = tier == "integration"
    full = (os.environ.get("SPEND_SIM_FULL") == "1") if full is None else full
    seeds = SEEDS if heavy else SEEDS[:1]
    out: list[Case] = []
    for seed in seeds:
        tag = f"s{seed}"
        big = heavy and (full or seed == SEEDS[0])
        job_counts = (1, 20, 200, 1000) if big else (1, 20, 200)
        # 1. full outages of 1 h and 4 h: nobody FAILS
        for kind in ("fast5xx", "hang", "reset", "blackhole"):
            for hours in (1, 4):
                for jobs in job_counts:
                    if not heavy and jobs == 200 and not (kind in ("hang", "fast5xx") and hours == 4):
                        continue
                    out.append((f"full-{kind}-{hours}h-{jobs}-{tag}", {"seed": seed, "jobs": jobs, "outage_s": hours * HOUR, "kind": kind}, no_failure()))
        # 2. 20 h outages: >= 20 jobs never FAIL (max 11 sends); a lone job FAILs by the absolute cap
        for kind in ("fast5xx", "hang"):
            for jobs in ((20, 200, 1000) if big else (20, 200) if heavy else (20,)):
                out.append((f"full-{kind}-20h-{jobs}-{tag}", {"seed": seed, "jobs": jobs, "outage_s": 20 * HOUR, "kind": kind}, no_failure(max_sends=11)))
            out.append((f"lone-{kind}-20h-{tag}", {"seed": seed, "jobs": 1, "outage_s": 20 * HOUR, "kind": kind}, lone_job_fails_by_the_absolute_cap))
        # 3. partial outages, other traffic of the jobs' class at 600/h
        for p in (0.3, 0.45, 0.55):
            for kind in ("fast5xx", "hang"):
                if not heavy and kind == "fast5xx":
                    continue
                out.append((f"partial-{kind}-p{p}-{tag}", {
                    "seed": seed, "jobs": 1000 if big else (200 if heavy else 50), "outage_s": 4 * HOUR, "kind": kind, "p": p, "job_cls": "l", "large_per_h": 600,
                }, no_failure()))
        # 4. small calls succeed, real calls fail (another size class): nobody FAILS and no key goes on hold
        for kind in ("fast5xx", "hang"):
            out.append((f"small-succeed-real-fail-{kind}-{tag}", {
                "seed": seed, "jobs": 20, "outage_s": 4 * HOUR, "kind": kind, "job_cls": "l", "small_per_h": 300, "fail_classes": ("l",),
            }, no_failure_and_no_hold))
        # ... and the heavy case: the same class serves the key's other traffic, so the jobs are charged: a hold, redrives
        # (it needs dense other traffic: with less, the 20 failures that settle together open the breaker (half of the
        # last 20 outcomes) before the jobs can be charged. 5 000/h for 3 h is enough here. Integration tier only.)
        if heavy:
            out.append((f"heavy-small-traffic-hang-{tag}", {
                "seed": seed, "jobs": 20, "outage_s": 4 * HOUR, "kind": "hang", "job_cls": "s", "small_per_h": 5000, "background_fails": False,
                "background_until": 3 * HOUR,
            }, hold_and_redrive))
        # 5. poison documents
        out.append((f"poison-crowded-{tag}", {
            "seed": seed, "jobs": 1, "poison_k": 1, "healthy": 299, "kind": "hang", "job_cls": "l", "large_per_h": 600,
        }, poison_crowded))
        out.append((f"poison-lone-{tag}", {"seed": seed, "jobs": 1, "poison_k": 1, "kind": "hang", "job_cls": "l"}, poison_lone))
        # r4b: scenario 4 (a dead waiting entry), STAGGER, MULTI
        for kind in ("fast5xx", "hang"):
            out.append((f"dead-entry-{kind}-{tag}", {"seed": seed, "jobs": 1, "outage_s": HOUR, "kind": kind, "dead_entry": True}, dead_entry_lone_job_ok))
        for kind in ("fast5xx", "hang"):
            for n in ((10, 40) if heavy else (10,)):
                out.append((f"stagger-{n}-8h-outage-{kind}-{tag}", {
                    "seed": seed, "jobs": n, "outage_s": 8 * HOUR, "kind": kind, "job_cls": "l", "small_per_h": 1000,
                    "arrivals_over_s": 8 * HOUR, "fail_classes": ("l",), "background_until": 2 * HOUR,
                }, no_failure()))
                out.append((f"stagger-{n}-30h-outage-{kind}-{tag}", {
                    "seed": seed, "jobs": n, "outage_s": 30 * HOUR, "kind": kind, "job_cls": "l", "small_per_h": 1000,
                    "arrivals_over_s": 8 * HOUR, "fail_classes": ("l",), "background_until": 2 * HOUR,
                }, stagger_no_poison_verdict))
        for k in (3, 5):
            out.append((f"multi-poison-{k}-{tag}", {
                "seed": seed, "jobs": k, "poison_k": k, "healthy": 300, "kind": "hang", "job_cls": "l", "small_per_h": 600, "background_until": 2 * HOUR,
            }, multi_poison(k)))
    return out
