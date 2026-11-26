# pyright: ignore-file

"""The Zoom accounts the zoom_sim suite loads into the simulator (``PUT /_sim/zoom/scenario``).

Dates are ``days_ago`` before the simulator's ``now``, which every account sets to the real clock when it is loaded:
the connector asks for the last 180 days of the real calendar, and the simulator refuses a report window outside its
own six months, so the two clocks must agree. A test that crosses UTC midnight moves the connector's day by one; the
cases are placed away from the day boundaries that matter (the 180-day edge has its own account).
"""

from __future__ import annotations

import copy
import datetime
from typing import Any

DOMAIN = "acme.example"
APP = {"client_id": "zoom-it-client", "client_secret": "zoom-it-secret"}
REFRESH_TOKEN = "zoom-it-refresh-0"
SLASHY = "/sl+ash//uuid=="  # begins with '/' and holds '//': a path needs it encoded twice
GUEST = "guest@x.example"
PARTNER = "ext@partner.example"

# Everyone who may read something in any account below: each has a PipesHub account (made once per session), so the
# permission query can be asked for them. GUEST and PARTNER are outside Zoom's user list.
PEOPLE = [f"{k}@{DOMAIN}" for k in ("ann", "bob", "cara", "dan", "eve", "fay", "gus", "hal")] + [GUEST, PARTNER]


def now_iso() -> str:
    return datetime.datetime.now(datetime.UTC).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")


def users() -> list[dict[str, Any]]:
    return [
        {"key": "ann", "first_name": "Ann", "last_name": "Owner", "role": "owner"},
        {"key": "bob", "first_name": "Bob", "last_name": "Stone", "role": "admin"},
        {"key": "cara", "first_name": "Cara", "last_name": "Diaz"},
        {"key": "dan", "first_name": "Dan", "last_name": "Gone", "status": "inactive"},
        {"key": "eve", "first_name": "Eve", "last_name": "New", "status": "pending"},
        {"key": "fay", "first_name": "Fay", "last_name": "Lee"},
    ]


def base(**overrides: Any) -> dict[str, Any]:
    """The default account: a recurring series with every kind of instance, a review, one-offs, a meeting of an
    inactive host, and a meeting held by the host alone (Zoom's report leaves it out)."""
    doc: dict[str, Any] = {
        "seed": 11, "domain": DOMAIN, "now": now_iso(), "app": dict(APP), "oauth": {"refresh_token": REFRESH_TOKEN},
        "users": users(),
        "meetings": [
            {"key": "standup", "host": "ann", "topic": "Standup", "type": 8, "alt_hosts": ["bob"],
             "invitees": ["cara", PARTNER],
             "instances": [
                 {"key": "s1", "days_ago": 3, "transcript": "ready", "recording": True,
                  "participants": [{"user": "bob"}, {"email": "Guest@X.example", "name": "Guest"},
                                   {"name": "Phone caller"}]},
                 {"key": "s2", "days_ago": 10, "uuid": SLASHY, "transcript": "ready", "participants": [{"user": "bob"}]},
                 {"key": "s3", "days_ago": 40, "participants": [{"user": "cara"}]},
                 {"key": "s4", "days_ago": 2, "participants": []},
                 {"key": "s5", "days_ago": 95, "transcript": "not_ready", "participants": [{"user": "bob"}]},
                 {"key": "s6", "days_ago": 147, "transcript": "expired", "participants": [{"user": "cara"}]}]},
            {"key": "review", "host": "bob", "topic": "Design review", "type": 2,
             "instances": [{"key": "r1", "days_ago": 5, "participants": [{"user": "ann"}], "recording": True,
                            "transcript": "ready"}]},
            {"key": "oneoff", "host": "cara", "topic": "One-off", "type": 2,
             "instances": [{"key": f"o{n}", "days_ago": n, "participants": [{"user": "ann"}]} for n in range(1, 4)]},
            {"key": "gone", "host": "dan", "topic": "Old host", "type": 2,
             "instances": [{"key": "g1", "days_ago": 4, "participants": [{"user": "ann"}]}]}]}
    doc.update(overrides)
    return copy.deepcopy(doc)


def small(**overrides: Any) -> dict[str, Any]:
    """Three active users and four meetings: for the cases about one mechanism, so a failure comes from it."""
    doc = base(
        users=[u for u in users() if u["key"] in ("ann", "bob", "cara")],
        meetings=[
            {"key": "standup", "host": "ann", "topic": "Standup", "type": 8, "alt_hosts": ["bob"], "invitees": ["cara"],
             "instances": [{"key": "s1", "days_ago": 3, "transcript": "ready", "participants": [{"user": "bob"}]},
                           {"key": "s2", "days_ago": 10, "participants": [{"user": "cara"}]}]},
            {"key": "review", "host": "bob", "topic": "Design review", "type": 2,
             "instances": [{"key": "r1", "days_ago": 5, "participants": [{"user": "ann"}]}]},
            {"key": "oneoff", "host": "cara", "topic": "One-off", "type": 2,
             "instances": [{"key": "o1", "days_ago": 2, "participants": [{"user": "ann"}, {"email": GUEST, "name": "Guest"}]}]}])
    doc.update(overrides)
    return doc


def large(users_count: int = 50, meetings_per_user: int = 10) -> dict[str, Any]:
    """``users_count`` active users, each hosting ``meetings_per_user`` one-off meetings of the last 25 days with two
    people in them, an alternative host and an invitee: ``users_count * meetings_per_user`` meetings in the window."""
    keys = [f"u{n:02d}" for n in range(users_count)]
    roster = [{"key": k, "first_name": k.upper(), "role": "owner" if n == 0 else "member"} for n, k in enumerate(keys)]
    meetings = []
    for n, host in enumerate(keys):
        for m in range(meetings_per_user):
            other = keys[(n + 1 + m) % users_count]
            meetings.append({"key": f"{host}m{m}", "host": host, "topic": f"{host} meeting {m}", "type": 2,
                             "alt_hosts": [keys[(n + 2) % users_count]], "invitees": [keys[(n + 3) % users_count]],
                             "instances": [{"key": f"{host}i{m}", "days_ago": 1 + (m * 2) % 24,
                                            "participants": [{"user": other}]}]})
    return base(users=roster, meetings=meetings)
