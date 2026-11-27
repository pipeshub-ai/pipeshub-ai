# Zoom connector against the Zoom simulator: integration tests

The `zoom_sim` suite (`zoom_sim_*_test.py`, marker `zoom_sim`) runs the real PipesHub stack against the
connectors-simulator's Zoom simulator (`connsim serve zoom`), so it can build what no shared Zoom account can: meetings
with alternative hosts named by id, instances 180 days old, a user who leaves, a token Zoom revokes, a rate limit, a
transcript that is not ready. Zoom has no account a test may write to, so there is no live suite.

## Running it

First set up the integration suite as [integration-tests/README.md](../../README.md) describes. Then:

```bash
cd deployment/docker-compose
export SIM_TLS_DIR=$(mktemp -d)
sim-tls/make-certs.sh "$SIM_TLS_DIR"
docker compose -f docker-compose.integration.neo4j.yml -f docker-compose.integration.zoom-sim.yml up -d
cd ../../integration-tests
set -a; . connectors/zoom_sim.env; set +a
pytest -m zoom_sim -v
```

The `zoom-sim` image is the connectors-simulator's (`docker build -t pipeshub/connectors-simulator:it .` there, at a
commit with `connsim/sources/zoom`). When several stacks run on one host, set `ZOOM_SIM_APP_CONTAINER` to the
PipesHub container under test.

## How the connector reaches the simulator

The connector's clients call `https://api.zoom.us/v2` and `https://zoom.us/oauth/token`, and the download URLs Zoom
returns, and nothing in the product moves those hosts. The overlay gives `zoom-tls` (nginx) both names as network
aliases and a certificate from a CA made for the run (`sim-tls/make-certs.sh`, shared with the other TLS-front overlays); the PipesHub container trusts that CA
through `SSL_CERT_FILE`. The front passes each request on unchanged, `Host` header and percent-encoded paths included
(a meeting UUID with a slash is encoded twice, `%252F`).

**It fails closed.** Without the aliases the two names resolve to the real Zoom. Two checks keep that from happening:
`zoom-guard` is healthy only while both names resolve, on the stack's network, to `zoom-tls`'s own address, and
`pipeshub-ai` starts only after it is; and the `zoom_front` fixture asks the PipesHub container's own resolver, over
`docker exec`, before any connector exists, and fails (never skips) when either name resolves anywhere else. Every
connector call is then counted by the simulator (`ZoomWorld.enable` fails when it saw none).

## How a test decides

`sim_world.py`: a test loads an account (`sim_instances.py`), creates a connector that signs in as the account's OAuth
user, syncs, changes the account, syncs again. After each sync the simulator's oracle (`GET /_sim/zoom/expected`,
computed from the account alone) says which meeting instances a complete sync holds and who may read each, and
PipesHub's permission query (`filter_accessible_record_ids`, what search relies on) is asked for every person of the
account, each of whom has a PipesHub account with the same address. The rules encoded:

- the host owns the record; alternative hosts (listed by address or by user id), participants who have an address and
  invitees may read it; a participant without an address gives nobody access;
- a person who is not one of the account's active users passes no connector gate and reads nothing, however the meeting
  names them (the guests, a partner, a deactivated or pending user);
- the scope is what Zoom's report lists for an active host: meetings with two or more unique participants, the last 180
  days, and since the host's account was made; every record sits in its host's record group;
- a host Zoom no longer lists as active loses the connector (their own access) and nothing else: their meetings stay,
  readable by the people Zoom names who are still active, until a full sync unlinks what is out of scope (the
  comparison holds such meetings as kept, narrowly: only those stored before, only until the full sync, and nobody
  gains access);
- once the scheduled meeting is deleted Zoom no longer says who its alternative hosts and invitees were, so they stop
  reading; its past instances stay.

MISSING, STALE, DUPLICATE, PARENT, `LEAK:<role>`, `DENIED:<role>`, SIM_UNMODELED and SIM_SPEC fail a test, except the
differences named in `KNOWN_GAPS` (sim_world.py; empty). Where the connector does not follow a rule yet, the test is a
strict xfail naming the cause (`LATER_ACCESS_IS_NOT_READ`, `DELETED_MEETING_KEEPS_ITS_READERS`, `LEAVERS_KEEP_ACCESS`
in `zoom_sim_access_test.py`), so it fails once the cause is fixed. After a run whose reads failed nothing stored may be
lost (`assert_holdings_kept`) and nobody may read what Zoom does not show them (no LEAK, STALE, DUPLICATE or PARENT);
with the fault gone the next clean run must match Zoom ("When a read fails" below says which kind of run). A fault that
never fired fails the test.

`unit/test_zoom_sim_compare.py` is the comparison's own test, without a stack: a planted over-share, a missing grant,
a missing, stale, duplicate and misfiled record are each caught.

## What the connector reads again

Each incremental sync lists every active host's report from the day before that host's checkpoint to today (never
further back than Zoom's six months), and reads the detail, participants and recording of each meeting it lists. A
meeting is therefore read again only while it is inside that overlap. Who may read it also depends on the scheduled
meeting's settings (alternative hosts, invitees), which can change after the meeting ended; a change to an older
meeting is seen by the next full sync, which lists the last six months again (`LATER_ACCESS_IS_NOT_READ`). A full sync
keeps the stored meetings older than what it lists, with their grants: Zoom's report cannot list them, so they are
unread, not gone. Cost per sync: a page or two of the report per host; one meeting, participants and recording read
per meeting in the overlap.

## When a read fails

The connector handles a failed read the same whatever Zoom answered (a 4xx, a 5xx, a 429, no answer, or a 2xx that does
not hold the list asked for), by what was being read:

- The user list: the users listed before the failed page are synced, and the run is incomplete. A list with no one in
  it is a failed one: the account that signed in is always active.
- A host's report, read 30 days at a time: the meetings of the ranges before the failed page are written, the rest
  are not; the host's checkpoint does not move, so the next run lists them again; the run is incomplete. The other
  hosts are synced as usual.
- A page token Zoom sends again ends the listing as a failed page.
- A meeting's detail or participants: the meeting is stored with the readers that were read (the participants of the
  pages read before the failure), added to the ones stored, never replacing them. Its run is not failed for it and its
  host's checkpoint moves on, so an incremental sync re-reads it only while it is inside the overlap; the next full sync
  reads its readers in full and replaces its grants. That is the trade-off #6420 chose: a meeting whose reads always
  fail must not switch off a full sync's removals for good. A deleted meeting's detail read (Zoom's 3001) is such a
  failed read, so its alternative hosts and invitees keep reading (`DELETED_MEETING_KEEPS_ITS_READERS`).
- A meeting whose processing raises is not written; the run is incomplete.

An incomplete run ends with `ZoomIncompleteSyncError`, the product's `IncompleteSyncError`: the run is not reported
`succeeded`, and `full_sync_coverage` is incomplete, so an incomplete full sync unlinks nothing and the runner owes
another full sync. No failed read deletes a record or withdraws a grant. The recording link is optional: a failed read
stores the meeting without it and does not fail the run.

## Cases

Each test file's docstring lists its cases (`TC-ZMSIM-SYNC`, `-ACL`, `-FAIL`, `-CONTENT`, `-AUTH`, `-LIFE`).

## Not covered here

- A redirected download is modelled (`download_redirect`, CONTENT-008) as Zoom's page describes it ("the URL may be a
  redirect"); the page does not say what the redirect target expects, so the case checks only that the access token is
  not sent there.
- A participant added to an instance that ended more than a day ago is not seen (Zoom's participant report does not
  change after a meeting ends); the test uses yesterday's meeting.
- The third refused refresh (AUTH-005) deactivating the connector waits for the platform's five-minute scans: ten
  minutes of suite; it is the platform's and unit-tested (`test_token_refresh_service.py` and its family).
- 401, a dropped connection and a cut body on each read: the connector does not tell them from the 403, 429 and 503 the
  matrix runs (see "When a read fails"); `test_zoom_connector.py` and `test_zoom_full_sync_unlink.py` cover them
  without a stack. Retry-After as an HTTP date is the shared transport's.
- Users removed (as against deactivated): Zoom's user list shows the same thing.
