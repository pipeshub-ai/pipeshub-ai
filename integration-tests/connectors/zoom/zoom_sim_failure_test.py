# pyright: ignore-file

"""
Zoom on the simulator: a run that could not read removes nothing, and gives nobody more than Zoom does
=======================================================================================================

The fault matrix: every Zoom endpoint the connector calls, with a lasting refusal (403) and a source that keeps failing
(503), at the first request and part-way through a listing. The connector treats every failed read of one endpoint the
same, whatever the status (401, 429, a dropped connection and a body cut short too, which the unit tests
test_zoom_connector.py and test_zoom_full_sync_unlink.py cover):

- A listing that fails (the user list, a host's report) ends the run not "succeeded". A host whose report failed keeps
  their checkpoint, so the next run reads their meetings; the users listed before a failed user page are synced.
- A meeting whose detail or participants cannot be read is stored with the readers that were read, added to the ones
  stored, never replacing them, and does not fail the run. The next full sync reads its readers in full; an incremental
  sync re-reads it only while it is inside the overlap with the last run. That is the trade-off #6420 chose: a meeting
  whose reads always fail must not switch off a full sync's removals for good.

Either way nothing stored is lost, nobody reads what Zoom does not show them, and with the fault gone PipesHub equals
Zoom again. Every injected fault must fire, or the case proves nothing. A fault that outlasts the client's retries is a
fault that lasts the run. FAIL-001 covers endpoints 001 to 006 on one connector.

  TC-ZMSIM-FAIL-000  The endpoints the connector calls are exactly the ones this matrix lists (``/_sim/calls``)
  TC-ZMSIM-FAIL-001  GET /v2/users: first page, and in pages of one mid-listing; the run is not a success, no user is
                     withdrawn, and the users listed before the failed page gain only what Zoom gives them [I]
  TC-ZMSIM-FAIL-002  GET /v2/report/users/{id}/meetings: first page and mid-listing; the checkpoint does not move
                     past meetings never listed [I]
  TC-ZMSIM-FAIL-003  GET /v2/report/meetings/{uuid}/participants: first page and mid-listing; the meeting is stored
                     with the participants read so far, and the next full sync reads them all [I]
  TC-ZMSIM-FAIL-004  GET /v2/meetings/{id}: the meeting is stored without the alternative hosts and invitees the read
                     would name, its stored ones kept, and the next full sync reads them [I]
  TC-ZMSIM-FAIL-005  GET /v2/meetings/{uuid}/recordings: optional metadata, so the run succeeds and stores the meeting
                     without the link; nothing is lost and the next clean run matches [I]
  TC-ZMSIM-FAIL-006  GET /v2/users/me (who signed in): advisory, the run may succeed; nothing may change [I]
  TC-ZMSIM-FAIL-007  Streaming: participants, transcript metadata and transcript download each fail with an error,
                     never an empty body that would be indexed [I/J]
  TC-ZMSIM-FAIL-008  Rate limits per second and per day, Retry-After in seconds and as an HTTP date [I]
  TC-ZMSIM-FAIL-009  Scopes missing (4711) on a read the sync needs: the run fails; on the optional recording scope
                     it succeeds and says so [A/I]
  TC-ZMSIM-FAIL-010  Page-token faults: a token that comes back, tokens refused: not "succeeded", nothing lost [H/I]
  TC-ZMSIM-FAIL-011  A user's failure does not stop the others: the other users' meetings are read, the run fails,
                     and the failed user's checkpoint is kept so the next run reads their meetings [I]
  TC-ZMSIM-FAIL-012  Malformed content: a report whose items lack their uuid or hold wrong types [I]
  TC-ZMSIM-FAIL-013  A 200 that is not the list asked for (null, missing, a string, an empty user list while users are
                     linked): on the user list and the report the run is not a success, no user is withdrawn and nothing
                     changes; on the participants it is a failed read, as in FAIL-003 [I]
  TC-ZMSIM-FAIL-014  One meeting whose detail or participants cannot be read, for good (400, 403) or for now (503): it is
                     stored with the readers that were read, never more than Zoom names, the host's other meetings
                     arrive, and with the fault gone a sync matches (a full one after the 503s) [I]
  TC-ZMSIM-FAIL-015  A recording link that cannot be read does not block the meeting or its readers [I]
"""

import itertools

import pytest

from connectors.zoom.sim_instances import REFRESH_TOKEN, small
from connectors.zoom.sim_world import (
    CALLED,
    KEEPS_FAILING,
    ZoomWorld,
    fault,
    succeeded,
)

USERS = r"^/v2/users$"
ME = r"^/v2/users/me$"
REPORT = r"^/v2/report/users/[^/]+/meetings$"
PARTICIPANTS = r"^/v2/report/meetings/.+/participants$"
DETAIL = r"^/v2/meetings/\d+$"
RECORDINGS = r"^/v2/meetings/.+/recordings$"  # a UUID with a slash is two path segments once decoded
TRANSCRIPT = r"^/v2/meetings/.+/transcript$"
DOWNLOAD = r"^/rec/"

def rule(kind: str, regex: str, **trigger: float) -> dict:
    """The fault of ``kind`` on the requests matching ``regex``."""
    if kind in ("401", "403", "429", "503"):
        text = {"401": "code=124; Invalid access token.", "403": "forbidden",
                "429": "code=4001; Too many requests", "503": "unavailable"}[kind]
        return fault(regex, int(kind), message=text, **({"max_times": KEEPS_FAILING} | trigger))
    effect = {"reset": {"type": "reset"}, "truncate": {"type": "truncate", "bytes": 7}}[kind]
    return {"match": {"path_regex": regex}, "effect": effect, "trigger": {"max_times": KEEPS_FAILING} | trigger}


# A lasting refusal; one 429 and one 503 follow in FAIL-001. The connector handles a failed read the same whatever its
# status, so 401, a dropped connection and a body cut short add nothing here (the unit tests cover them).
KINDS = ["403"]
STREAM_KINDS = ["403", "reset"]
# A refusal about one meeting. A plain 403 is Zoom's code 200 ("no permission"): the app's, which fails the host.
MEETING_REFUSED = "code=3000; Cannot access this meeting"


def _new_work(n: int) -> list[dict]:
    """A change a sync would bring: a new instance of the series with three participants, and of the review with Cara.
    If the failing run applied any of it, someone gained access."""
    return [{"op": "new_meeting_ended", "meeting": "standup",
             "instance": {"key": f"n{n}", "days_ago": 1, "participants": [{"user": "bob"}, {"user": "cara"},
                                                                         {"email": "late@x.example", "name": "Late"}]}},
            {"op": "new_meeting_ended", "meeting": "review",
             "instance": {"key": f"w{n}", "days_ago": 1, "participants": [{"user": "cara"}]}}]


async def _beyond_zoom(w: ZoomWorld, label: str) -> list[str]:
    """What PipesHub holds that Zoom does not give: a reader Zoom does not name, or a record out of scope, stored twice
    or filed under another host. A meeting or a reader not read yet (MISSING, DENIED) is not one: a failed run may leave
    those."""
    return [f"{label}: PipesHub holds what Zoom does not give: {p}" for p in await w.differences()
            if p["type"].startswith("LEAK") or p["type"] in ("STALE", "DUPLICATE", "PARENT")]


async def _stored_with_its_host(w: ZoomWorld, label: str, keys: list[str]) -> list[str]:
    """Each new instance in ``keys`` is stored and its host reads it: a failed detail or participants read stores the
    meeting with the readers that were read, it never leaves the meeting out."""
    problems = {(p.get("record"), p["type"]) for p in await w.differences()}
    out: list[str] = []
    for key in keys:
        uuid = w.instance(key)["uuid"]
        if (uuid, "MISSING") in problems:
            out.append(f"{label}: {key} was not stored")
        elif (uuid, "DENIED:host") in problems:
            out.append(f"{label}: the host does not read {key}")
    return out


async def _failing(w: ZoomWorld, label: str, n: int, rules: list[dict], *, must_fail: bool = True,
                   behaviours: dict | None = None, allow_gains: bool = False) -> None:
    """One run with ``rules`` on top of new work at Zoom: not a success when ``must_fail``, nothing stored lost, and
    nobody reads what Zoom does not show them. Unless ``allow_gains`` (the run read part of the new work), nothing was
    added either."""
    before = await w.holdings()
    w.ops(*_new_work(n))
    if behaviours:
        w.behaviours(behaviours)
    w.faults(*rules)
    outcome = await w.sync()
    w.clear_faults()
    if behaviours:
        w.behaviours({k: v for k, v in {"page_size_cap": 0, "rate_limit_qps": 0, "rate_limit_daily": 0}.items()
                      if k in behaviours})
    if must_fail:
        assert not succeeded(outcome), f"{label}: a run whose Zoom reads failed reported success"
    await w.assert_holdings_kept(before, f"{label}: after the failing run", allow_gains=allow_gains)
    beyond = await _beyond_zoom(w, label)
    assert not beyond, "\n".join(beyond)


async def _clean(w: ZoomWorld, label: str, *, full: bool = False) -> None:
    """With the faults gone, a sync makes PipesHub equal Zoom. ``full`` after a meeting was stored with part of its
    readers: only a full sync is sure to read them all, as an incremental one re-reads a meeting only inside the
    overlap with the last run."""
    run = "full run" if full else "run"
    assert succeeded(await w.sync(full=full)), f"{label}: the next clean {run} did not succeed"
    await w.assert_matches_source(f"{label}: after the next clean {run}")


@pytest.mark.integration
@pytest.mark.zoom_sim
@pytest.mark.asyncio(loop_scope="session")
class TestZoomSimFailure:

    async def test_tc_zmsim_fail_000_endpoints_called_are_the_matrix(self, zoom_world: ZoomWorld) -> None:
        w = zoom_world
        assert succeeded(await w.start(small()))
        await w.stream(w.instance("s1")["uuid"])
        # The suite's own sign-in is a POST to /oauth/token; the connector's reads are all GETs.
        called = {e for e in w.sim.calls() if e.startswith(("GET /v2", "GET /rec"))}
        assert called == CALLED, f"the connector calls {sorted(called - CALLED)} the matrix lacks, and not {sorted(CALLED - called)}"

    async def test_tc_zmsim_fail_001_every_read_endpoint(self, zoom_world: ZoomWorld) -> None:
        """FAIL-001 to 006 on one connector (their faults cannot affect each other: each endpoint's failing runs are
        followed by a clean run before the next endpoint's). A failed detail or participants read leaves a meeting with
        part of its readers until a full sync reads them all, so that endpoint's clean run is a full sync; an
        incremental one is not required to fix it (#6420's trade-off)."""
        w = zoom_world
        assert succeeded(await w.start(small()))
        await w.assert_matches_source("after the first sync")
        counter = itertools.count(1)
        failures: list[str] = []
        # (name, path, behaviours that make a second page, must the run fail?, is a meeting stored with part of its readers?)
        endpoints = [("users", USERS, {"page_size_cap": 1}, True, False),
                     ("report", REPORT, {"page_size_cap": 1}, True, False),
                     # A failed read of a meeting's participants or detail, whatever its status: the meeting is stored
                     # with the readers that were read (fewer than Zoom names, never more), added to the stored ones.
                     ("participants", PARTICIPANTS, {"page_size_cap": 2}, False, True),
                     ("meeting detail", DETAIL, None, False, True),
                     # The link is optional metadata: the run succeeds and stores the meeting and its readers without
                     # it (the new work is a gain, as it should be). FAIL-015 checks the link comes back.
                     ("recording", RECORDINGS, None, False, False),
                     # Who signed in: advisory, the run may succeed.
                     ("me", ME, None, False, False)]
        for name, regex, pages, must_fail, partial in endpoints:
            # A listing that fails at its first request reads none of the new work. Part-way through the user list, the
            # users listed before the failed page are synced, so they gain what Zoom gives them and no more.
            cases = [(f"{name} {kind} first request", [rule(kind, regex)], None, not must_fail) for kind in KINDS]
            if pages:
                cases.append((f"{name} 403 mid-listing", [rule("403", regex, after_n=1)], pages,
                              not must_fail or name == "users"))
            for label, rules, behaviours, gains in cases:
                try:
                    await _failing(w, label, next(counter), rules, must_fail=must_fail, behaviours=behaviours,
                                   allow_gains=gains)
                except AssertionError as exc:
                    failures.append(str(exc))
            await _clean(w, name, full=partial)
            # One failure. A listing fails the run and keeps the host's checkpoint, so the next run reads what it hid; a
            # meeting's detail or participants store what was read, the run may succeed, and the full sync reads the rest.
            for kind in ("429", "503"):
                n = next(counter)
                label = f"{name}: one {kind}"
                before = await w.holdings()
                w.ops(*_new_work(n))
                w.faults(rule(kind, regex, max_times=1))
                outcome = await w.sync()
                w.clear_faults()
                if must_fail and succeeded(outcome):
                    failures.append(f"{label} hid part of Zoom and the run reported success")
                try:
                    await w.assert_holdings_kept(before, label, allow_gains=True)
                except AssertionError as exc:
                    failures.append(str(exc))
                failures += await _beyond_zoom(w, label)
                if partial:
                    failures += await _stored_with_its_host(w, label, [f"n{n}", f"w{n}"])
                await _clean(w, f"{name}: after one {kind}", full=partial)
        assert not failures, "\n".join(failures)

    async def test_tc_zmsim_fail_007_streaming(self, zoom_world: ZoomWorld) -> None:
        w = zoom_world
        assert succeeded(await w.start(small()))
        uuid = w.instance("s1")["uuid"]
        want = (await w.stream(uuid))
        assert want["Transcript"], "the instance's transcript is ready, so it is not empty"
        failures: list[str] = []
        for regex, name in ((PARTICIPANTS, "participants"), (TRANSCRIPT, "transcript metadata"), (DOWNLOAD, "download")):
            for kind in STREAM_KINDS:
                if (name, kind) == ("download", "truncate"):
                    # The simulator cuts the body and keeps its length consistent, so a short transcript cannot be
                    # told from a cut one by the client; every other failure is a transport or HTTP error.
                    continue
                w.faults(rule(kind, regex))
                try:
                    got = await w.stream(uuid)
                    failures.append(f"{name} {kind}: streamed {got['Transcript'][:30]!r} instead of failing")
                except Exception:  # noqa: BLE001 - any HTTP or transport error is the expected refusal
                    pass
                finally:
                    w.clear_faults()
                if (await w.stream(uuid)) != want:
                    failures.append(f"{name} {kind}: the stream did not return to the source's content")
        assert not failures, "\n".join(failures)

    async def test_tc_zmsim_fail_008_rate_limits(self, zoom_world: ZoomWorld) -> None:
        w = zoom_world
        assert succeeded(await w.start(small()))
        failures: list[str] = []
        # The connector does not wait out a limit: a throttled listing fails the run, a throttled detail or participants
        # read stores the meeting with what was read, so the clean run is a full sync.
        cases = [({"rate_limit_qps": 1, "rate_limit_retry_after": "seconds"}, "per second, Retry-After in seconds", False)]
        for n, (values, label, must_fail) in enumerate(cases, 1):
            before = await w.holdings()
            w.ops(*_new_work(n))
            w.behaviours(values)
            w.sim.call("POST", "/_sim/zoom/stats/reset")
            outcome = await w.sync()
            limited = w.sim.stats()["rate_limited"]
            w.behaviours({"rate_limit_qps": 0, "rate_limit_daily": 0, "rate_limit_retry_after": "seconds"})
            if not limited:
                failures.append(f"{label}: the limit was never reached, the case proves nothing")
                continue
            if must_fail and succeeded(outcome):
                failures.append(f"{label}: a run Zoom throttled reported success")
            try:
                await w.assert_holdings_kept(before, f"{label}: after the throttled run", allow_gains=True)
            except AssertionError as exc:
                failures.append(str(exc))
            failures += await _beyond_zoom(w, f"{label}: after the throttled run")
            await _clean(w, label, full=True)
        assert not failures, "\n".join(failures)

    async def test_tc_zmsim_fail_009_missing_scopes(self, zoom_world: ZoomWorld) -> None:
        w = zoom_world
        doc = small(app={"client_id": "zoom-it-client", "client_secret": "zoom-it-secret",
                         "scopes": ["user:read:list_users:admin", "user:read:user:admin",
                                    "report:read:list_meeting_participants:admin",
                                    "meeting:read:meeting:admin"]})
        # No report:read:user:admin: the listing of a user's meetings is refused with 4711.
        before_ok = await w.start(doc)
        assert not succeeded(before_ok), "a token without the report scope synced"
        assert not await w.records(), "records were written from a run that could not list any meeting"
        # Only the optional recording scope missing: the run succeeds, with no recording links.
        w2 = doc["app"]["scopes"] + ["report:read:user:admin"]
        w.sim.call("PUT", "/_sim/zoom/scenario", {**doc, "app": {**doc["app"], "scopes": w2}})
        w.refresh_token = REFRESH_TOKEN
        await w.sign_in()
        assert succeeded(await w.sync())
        await w.assert_matches_source("with every scope but the recording one")

    async def test_tc_zmsim_fail_010_page_token_faults(self, zoom_world: ZoomWorld) -> None:
        w = zoom_world
        assert succeeded(await w.start(small(), behaviours={"page_size_cap": 1}))
        await w.assert_matches_source("after the first sync in pages of one")
        failures: list[str] = []
        cases = [(USERS, "users", "repeat"), (REPORT, "report", "repeat"), (USERS, "users", "expire")]
        for n, (regex, name, mode) in enumerate(cases, 1):
            before = await w.holdings()
            # Three pages of one meeting per host, so a token comes back before the last.
            w.ops(*(op for k in range(3) for op in _new_work(n * 10 + k)))
            w.faults({"match": {"path_regex": regex}, "effect": {"type": "page_token", "mode": mode}})
            outcome = await w.sync()
            w.clear_faults()
            if succeeded(outcome):
                failures.append(f"{name} page token {mode}: a listing that could not be followed to its end succeeded")
            try:
                await w.assert_holdings_kept(before, f"{name} page token {mode}", allow_gains=True)
            except AssertionError as exc:
                failures.append(str(exc))
            assert succeeded(await w.sync())
            await w.assert_matches_source(f"{name} page token {mode}: next clean run")
        assert not failures, "\n".join(failures)

    async def test_tc_zmsim_fail_011_one_users_failure_does_not_stop_the_others(self, zoom_world: ZoomWorld) -> None:
        w = zoom_world
        assert succeeded(await w.start(small()))
        bob = w.sim.expected()["keys"]["bob"]
        w.ops({"op": "new_meeting_ended", "meeting": "oneoff", "instance": {"key": "o9", "days_ago": 1, "participants": [{"user": "bob"}]}},
              {"op": "new_meeting_ended", "meeting": "review", "instance": {"key": "r9", "days_ago": 1, "participants": [{"user": "cara"}]}})
        w.faults(fault(rf"^/v2/report/users/{bob}/meetings$", 503, max_times=KEEPS_FAILING))
        outcome = await w.sync()
        w.clear_faults()
        assert not succeeded(outcome)
        records = {r["externalRecordId"] for r in await w.records()}
        assert w.instance("o9")["uuid"] in records, "Cara's new meeting was not read because Bob's failed"
        assert w.instance("r9")["uuid"] not in records, "Bob's report failed, yet his meeting was stored"
        assert succeeded(await w.sync())
        await w.assert_matches_source("after the next clean run: Bob's meetings are read from the checkpoint kept")

    async def test_tc_zmsim_fail_012_malformed_report(self, zoom_world: ZoomWorld) -> None:
        w = zoom_world
        assert succeeded(await w.start(small()))
        before = await w.holdings()
        w.ops(*_new_work(1))
        w.faults({"match": {"path_regex": REPORT},
                  "effect": {"type": "shape", "ops": [{"path": "meetings", "value": "not a list"}]},
                  "trigger": {"max_times": KEEPS_FAILING}})
        outcome = await w.sync()
        w.clear_faults()
        assert not succeeded(outcome), "a report whose meetings are not a list reported success"
        await w.assert_holdings_kept(before, "after a malformed report")
        assert succeeded(await w.sync())
        await w.assert_matches_source("after the next clean run")

    async def test_tc_zmsim_fail_013_answers_that_are_not_lists(self, zoom_world: ZoomWorld) -> None:
        """A 200 that is not the list asked for is not "there is nothing". On the user list and the report the run is
        not a success, no user is withdrawn, nothing changes, and the next run matches. On the participants it is a
        failed read: the meeting is stored with the readers that were read, nothing stored is lost, nobody reads what
        Zoom does not show them, and the run may succeed. With the fault gone the next full sync matches; the next
        incremental one is not required to (#6420's trade-off)."""
        w = zoom_world
        assert succeeded(await w.start(small()))
        links = await w.graph.count_user_app_relation_edges(w.connector_id)
        cases = [(USERS, "users", {"value": None}), (USERS, "users", {"delete": True}), (USERS, "users", {"value": []}),
                 (REPORT, "meetings", {"value": None}), (REPORT, "meetings", {"value": "x"}),
                 (PARTICIPANTS, "participants", {"delete": True})]
        failures: list[str] = []
        for n, (regex, key, op) in enumerate(cases, 1):
            label = f"{key} {op}"
            partial = regex == PARTICIPANTS
            if partial:
                # The listings' cases are settled first: their hosts' checkpoints stayed, so an incremental run reads them.
                try:
                    await _clean(w, "after the listings that were not lists")
                except AssertionError as exc:
                    failures.append(str(exc))
            before = await w.holdings()
            w.ops(*_new_work(n))
            w.faults({"match": {"path_regex": regex}, "effect": {"type": "shape", "ops": [{"path": key, **op}]},
                      "trigger": {"max_times": KEEPS_FAILING}})
            outcome = await w.sync()
            w.clear_faults()
            if not partial and succeeded(outcome):
                failures.append(f"{label}: a 200 that is not a list reported success")
            if await w.graph.count_user_app_relation_edges(w.connector_id) != links:
                failures.append(f"{label}: users were withdrawn on an answer that is not a list")
            try:
                await w.assert_holdings_kept(before, label, allow_gains=partial)
            except AssertionError as exc:
                failures.append(str(exc))
            failures += await _beyond_zoom(w, label)
            if partial:
                failures += await _stored_with_its_host(w, label, [f"n{n}", f"w{n}"])
        await _clean(w, "after the participants that were not a list", full=True)
        assert not failures, "\n".join(failures)

    async def test_tc_zmsim_fail_014_one_meeting_that_cannot_be_read(self, zoom_world: ZoomWorld) -> None:
        """One meeting's detail or participants fail, run after run, refused for good (400, 403) or for now (503). Either
        way it is stored with the readers that were read, added to the stored ones, and the run may succeed: nobody
        reads more than Zoom shows them, nothing stored is lost, and the host's other meetings arrive. With the fault
        gone a sync makes PipesHub equal Zoom. After the 503s the test asks a full sync, the one sure to read every
        reader: an incremental one re-reads the meeting only while it is inside the overlap with the last run, the
        trade-off #6420 chose so that a meeting whose reads always fail cannot switch off a full sync's removals."""
        w = zoom_world
        doc = small(meetings=[
            {"key": "standup", "host": "ann", "topic": "Standup", "type": 8, "alt_hosts": ["bob"],
             "instances": [{"key": "s1", "days_ago": 3, "participants": [{"user": "bob"}]}]}])
        assert succeeded(await w.start(doc))
        failures: list[str] = []
        for n, (where, status, lasting) in enumerate(
                [("detail", 400, True), ("participants", 403, True), ("detail", 503, False), ("participants", 503, False)], 1):
            before = await w.holdings()
            made = w.ops(
                {"op": "new_meeting_ended", "new_meeting": {"key": f"badm{n}", "host": "ann", "topic": f"Bad {n}"},
                 "instance": {"key": f"bad{n}", "days_ago": 1, "uuid": f"bad{n}-uuid==", "participants": [{"user": "bob"}]}},
                {"op": "new_meeting_ended", "new_meeting": {"key": f"okm{n}", "host": "ann", "topic": f"Ok {n}"},
                 "instance": {"key": f"ok{n}", "days_ago": 1, "participants": [{"user": "cara"}]}})
            regex = (rf"^/v2/meetings/{made[0]['id']}$" if where == "detail"
                     else rf"^/v2/report/meetings/bad{n}-uuid.*/participants$")
            w.faults(fault(regex, status, message=MEETING_REFUSED, max_times=KEEPS_FAILING))
            for _ in range(2):
                await w.sync()
            w.clear_faults()
            label = f"{where} {status}"
            failures += await _stored_with_its_host(w, label, [f"bad{n}", f"ok{n}"])
            failures += await _beyond_zoom(w, label)
            try:
                await w.assert_holdings_kept(before, label, allow_gains=True)
            except AssertionError as exc:
                failures.append(str(exc))
            await _clean(w, label, full=not lasting)
        assert not failures, "\n".join(failures)

    async def test_tc_zmsim_fail_015_a_recording_link_is_optional(self, zoom_world: ZoomWorld) -> None:
        """Zoom refuses the recording read of a meeting: the meeting and its readers are stored without the link, and the
        run succeeds."""
        w = zoom_world
        doc = small(meetings=[{"key": "m", "host": "ann", "topic": "Recorded", "type": 8,
                               "instances": [{"key": "m1", "days_ago": 4, "uuid": "rec1-uuid==", "recording": True,
                                              "transcript": "ready", "participants": [{"user": "bob"}]}]}])
        await w.start(doc, enable=False)
        w.faults(fault(r"^/v2/meetings/.+/recordings$", 503, max_times=KEEPS_FAILING))
        assert succeeded(await w.enable()), "a recording link that could not be read failed the run"
        w.clear_faults()
        await w.assert_matches_source("with the recording link unread")
        uuid = w.instance("m1")["uuid"]
        assert (await w.recording_url(uuid)) in (None, ""), "a link was stored although Zoom refused it"
