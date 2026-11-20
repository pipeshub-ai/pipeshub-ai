"""A reference model of the Force reindex request kept on a record, one record at a time.

Pure Python, no repository code: random interleavings of the user's Force reindex (the walk: a status write that stores the
request, a publish that can fail, the compare-and-set to QUEUED; a request made while the record runs), a connector's plain
update, message loss, a hand-back before and after the refresh branch, a run's start, end and crash, the stale and stranded
sweeps, and a twin's start and completion. After a drain with no loss it reports two things:

* DROP: a Force reindex request that no completed refresh run started after.
* SPURIOUS: a refresh run derived from a stored request (not the user's own message) that starts when every request was
  already satisfied: a re-buy nobody asked for (``notes["spurious_any"]`` counts every derived run, ``spurious`` the ones
  sourced from the sweeps).

Variants: ``base`` (today), ``design`` (an earlier design), ``fix2``, ``fix3`` (an earlier revision), ``fix3m`` (that revision with the
in-place upgrade losing its origin marker and the backstop rewriting a cleared request: must still show SPURIOUS), ``final``
(the design that is built: the upgrade keeps the marker, the backstop writes only for a payload with no request id).

The real-code property test drives the same action alphabet over the repository's handler, sweep and walk.
"""

import random
from collections import Counter

TRACE = False
GATE = {"QUEUED", "IN_PROGRESS", "NOT_STARTED"}


def run(
    seed: int, variant: str, steps: int = 40, p_twin: bool = True
) -> tuple[bool, int, Counter, dict]:
    R = random.Random(seed)
    t = 0
    rec = {"status": "COMPLETED", "intent": None, "done_t": None}
    #: The walk's host and the indexing host do not share a clock: what the walk stamps is its own, a few ticks off (variant ``timed`` only).
    skew = R.choice([0, 0, 6, -6]) if variant == "timed" else 0
    msgs = []  # queued messages
    active = None  # run in progress (one per record: the lease)
    crashed = False  # an IN_PROGRESS row nobody holds
    twin_inflight = False
    reqs = {}  # k -> time requested
    sat = set()
    spurious = 0
    notes = Counter()

    def tick() -> int:
        nonlocal t
        t += 1
        return t

    def unsatisfied_before(ts: int) -> list[int]:
        return [k for k, tk in reqs.items() if k not in sat and tk < ts]

    def user_force() -> None:
        if rec["status"] == "IN_PROGRESS":
            notes["walk_skipped_in_progress"] += (
                1  # event_service.py:1502: the walk excludes IN_PROGRESS rows
            )
            k = tick()
            reqs[k] = k  # the request still counts it must be served
            if variant in ("fix3", "fix3m", "final", "timed") and (
                rec["intent"] is None or rec["intent"][1] < k
            ):
                rec["intent"] = (k, k + skew)  # intent-only write: no status change, no event
            return
        k = tick()
        reqs[k] = k
        # walk: NOT_STARTED before publish (event_service.py:1611-1614)
        rec["status"] = "NOT_STARTED"
        if variant in ("publish", "fix", "fix2", "fix3", "fix3m", "final", "timed") and (
            rec["intent"] is None or rec["intent"][1] < k
        ):
            rec["intent"] = (k, k + skew)
        if R.random() < 0.1:
            notes["publish_failed"] += 1  # record left NOT_STARTED, no event
            return
        msgs.append({"refresh": True, "id": k, "at": k, "orig": True})
        if rec["status"] == "NOT_STARTED":
            rec["status"] = "QUEUED"  # CAS NOT_STARTED->QUEUED after publish

    def plain_update() -> None:
        msgs.append({"refresh": False, "id": None, "at": tick()})
        if not active and rec["status"] != "IN_PROGRESS":
            rec["status"] = "QUEUED"

    def twin_start() -> None:
        nonlocal twin_inflight
        twin_inflight = True

    def twin_complete() -> None:
        nonlocal twin_inflight
        if not twin_inflight:
            return
        twin_inflight = False
        # update_queued_duplicates_status: every QUEUED same-md5 copy -> COMPLETED, no handler run
        if rec["status"] == "QUEUED" and not active:
            if (
                variant in ("fix", "fix2", "fix3", "fix3m", "final", "timed")
                and rec["intent"] is not None
            ):
                return
            rec["status"] = "COMPLETED"
            notes["promoted"] += 1

    def lose() -> None:
        if msgs:
            msgs.pop(R.randrange(len(msgs)))
            notes["lost"] += 1

    def requeued(m: dict, refresh: bool, rid: int | None) -> dict:
        if variant == "fix3m" and m.get("upgraded"):
            c = {k: v for k, v in m.items() if k not in ("src", "upgraded")}
            c.update(
                refresh=True, id=rid
            )  # flags set in place travel with the re-queue; no refreshFromIntent
            return c
        return dict(m, refresh=True, id=rid) if refresh else m

    def consume(allow_loss=True) -> None:
        nonlocal active, crashed
        if active or not msgs:
            return
        m = msgs.pop(0)
        refresh, rid = m["refresh"], m["id"]
        if (
            variant in ("fix2", "fix3", "fix3m", "final", "timed")
            and m.get("src") == "intent"
            and (rec["intent"] is None or rec["intent"][0] != rid)
        ):
            refresh, rid = (
                False,
                None,
            )  # a re-sent intent that is no longer stored was already satisfied
            notes["stale_resend_demoted"] += 1
        if (
            variant in ("fix", "fix2", "fix3", "fix3m", "final", "timed")
            and not refresh
            and rec["intent"] is not None
        ):
            refresh, rid = True, rec["intent"][0]  # handler reads the intent
            m = dict(m, src="intent", upgraded=True)
        if variant == "timed" and refresh and rec["intent"] is not None and rec["done_t"] is not None and rec["done_t"] >= rec["intent"][1]:
            rec["intent"] = None            # the control: "served" decided by comparing two hosts' times; the request never runs
            return
        if refresh:
            # hand-backs that run BEFORE the refresh branch (RecordHeldForDelete record.py:2296-2302,
            # ProviderRateLimitRequeue record.py:2285-2289): nothing written
            if R.random() < 0.15:
                notes["pre_write_handback"] += 1
                if not (allow_loss and R.random() < 0.5):
                    msgs.append(requeued(m, refresh, rid))
                return
            if (
                variant == "fix3m"
                and m.get("src") != "intent"
                and rid is not None
                and rec["intent"] is None
            ):
                rec["intent"] = (rid, tick())
                notes["backstop_rewrite"] += 1
            if (
                variant in ("design", "publish")
                and (rec["intent"] is None or rec["intent"][1] <= m["at"])
                and (rec["intent"] is None or rec["intent"][0] != rid)
            ):
                rec["intent"] = (rid, m["at"])
            # RefreshPurgeHeld hand-back after the write
            if R.random() < 0.2:
                notes["held"] += 1
                if not (allow_loss and R.random() < 0.5):
                    msgs.append(requeued(m, True, rid))
                return
        if TRACE:
            print("  start", m, "intent", rec["intent"], "status", rec["status"])
        active = {
            "refresh": refresh,
            "id": rid,
            "start": tick(),
            "src": m.get("src", "msg"),
            "seen": rec["intent"][0] if rec["intent"] else None,
            "orig": m.get("orig", False),
        }
        rec["status"] = "IN_PROGRESS"
        crashed = False  # a delivery that takes an orphaned row over ends the orphan

    def finish(allow_crash=True) -> None:
        nonlocal active, crashed, spurious
        if not active:
            return
        a, active = active, None
        if allow_crash and R.random() < 0.2:
            crashed = True  # holder died; message gone; row IN_PROGRESS
            notes["crash"] += 1
            return
        if a["refresh"]:
            u = unsatisfied_before(a["start"])
            if not u and not a.get("orig"):
                notes["spurious_any"] += 1
            if not u and a["src"] == "intent":
                spurious += 1
                if TRACE:
                    print(
                        "  SPURIOUS run",
                        a,
                        "reqs",
                        reqs,
                        "sat",
                        sat,
                        "intent",
                        rec["intent"],
                    )
            sat.update(u)
        rec["status"] = "COMPLETED"
        rec["done_t"] = tick()                # the indexing host's time at completion (used by the timed control)
        if TRACE:
            print("  finish", a, "intent", rec["intent"])
        if (
            variant in ("fix3", "fix3m", "final", "timed")
            and rec["intent"] is not None
            and rec["intent"][0] != a["seen"]
        ):
            msgs.append(
                sweep_payload_any()
            )  # run-end follow-up: a request that arrived mid-run
            notes["followup"] += 1
        if rec["intent"] is not None:
            if (
                variant in ("fix2", "fix3", "fix3m", "final", "timed")
                and a["refresh"]
                and rec["intent"][0] == a["seen"]
            ):
                rec["intent"] = None
            elif variant == "fix" and rec["intent"][0] == a["id"]:
                rec["intent"] = None
            elif (
                variant in ("design", "publish")
                and a["refresh"]
                and rec["intent"][0] == a["id"]
            ):
                rec["intent"] = None

    def sweep_payload() -> dict:
        if variant != "base" and rec["intent"] is not None and rec["status"] in GATE:
            return {
                "refresh": True,
                "id": rec["intent"][0],
                "at": tick(),
                "src": "intent",
            }
        return {"refresh": False, "id": None, "at": tick()}

    def sweep_payload_any() -> dict:
        return {"refresh": True, "id": rec["intent"][0], "at": tick(), "src": "intent"}

    def stale_sweep() -> None:
        nonlocal crashed
        if crashed and not active and rec["status"] == "IN_PROGRESS":
            msgs.append(sweep_payload())
            rec["status"] = "QUEUED"
            crashed = False

    def stranded_sweep() -> None:
        if (
            variant in ("fix2", "fix3", "fix3m", "final", "timed")
            and rec["intent"] is not None
            and not active
            and not msgs
            and not crashed
        ):
            msgs.append(sweep_payload_any())
            return
        if active or msgs or rec["status"] not in ("QUEUED", "NOT_STARTED"):
            return
        if twin_inflight and rec["status"] == "QUEUED":
            return  # _still_parked_behind_a_twin
        msgs.append(sweep_payload())

    ext = [
        user_force,
        plain_update,
        lose,
        consume,
        consume,
        finish,
        finish,
        stale_sweep,
        stranded_sweep,
    ]
    if p_twin:
        ext += [twin_start, twin_complete]
    user_force()
    for _ in range(steps):
        R.choice(ext)()
    # drain: no new requests, no losses, no crashes
    for _ in range(500):
        (len(msgs), active is not None, rec["status"], crashed)
        twin_complete()
        consume(allow_loss=False)
        finish(allow_crash=False)
        stale_sweep()
        stranded_sweep()
        if (
            not msgs
            and not active
            and not crashed
            and rec["status"] not in ("QUEUED", "NOT_STARTED", "IN_PROGRESS")
        ):
            break
    dropped = [k for k in reqs if k not in sat]
    return bool(dropped), spurious, notes, rec
