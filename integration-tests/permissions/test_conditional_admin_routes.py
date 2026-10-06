"""Routes whose admin check depends on what is asked for enforce that rule.

Some routes ask whether the caller is an admin but do not simply refuse
members: a team connector needs an admin while a personal one is its creator's
alone, a connector list shows an admin more, a sign-in app's secret is left out
for members, a built-in skill needs an admin where a member's own does not.
Only unit tests covered these, with the check itself mocked, so a route that
lost its check would have passed everything.

``helper/admin_route_conditional_table.py`` holds one row per such handler and one
case per (thing asked for, caller) pair its rule tells apart. Each case is sent
here to the running stack, as the admin, as the member who created the thing,
or as another member, and must get the pinned answer: the refusal for a caller
on the wrong side of the rule, and proof of getting past the check for one on
the right side.

``unit/test_admin_route_inventory.py`` keeps the table in step with the source,
so a new conditional route cannot skip this suite.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterator

import pytest

from helper.admin_route_conditional_table import (
    ADMIN,
    CONDITIONAL_ROUTES,
    MEMBER,
    Case,
    ConditionalRoute,
    fill,
)
from helper.admin_route_conditional_world import Reply, World, oauth_state, send
from helper.http.session_client import SessionClient
from helper.pipeshub_client import PipeshubClient
from helper.second_user import SecondUser, create_second_user, delete_second_user

logger = logging.getLogger("conditional-admin")

pytestmark = [pytest.mark.integration, pytest.mark.permissions]

ALL_CASES = [
    pytest.param(route, case, id=f"{route.id} [{case.id}]")
    for route in CONDITIONAL_ROUTES
    for case in route.cases
]
_REGISTRY = next(r for r in CONDITIONAL_ROUTES if r.handler.endswith("::get_connector_registry"))


@pytest.fixture(scope="module")
def people(pipeshub_client: PipeshubClient) -> Iterator[tuple[SecondUser, SecondUser]]:
    """The member who creates things of their own, and a member who creates nothing."""
    pipeshub_client._ensure_access_token()
    made: list[SecondUser] = []
    try:
        for _ in range(2):
            made.append(create_second_user(pipeshub_client))
        yield made[0], made[1]
    finally:
        for user in made:
            delete_second_user(pipeshub_client, user, strict=True)


@pytest.fixture(scope="module")
def world(
    pipeshub_client: PipeshubClient,
    user_session_client: SessionClient,
    people: tuple[SecondUser, SecondUser],
) -> Iterator[World]:
    creator, member = people
    made = World(
        gateway_url=user_session_client.base_url,
        admin_token=lambda: user_session_client.token,
        creator=creator,
        member=member,
        settings_client=pipeshub_client,
        timeout=user_session_client.timeout_seconds,
    )
    try:
        yield made
    finally:
        made.close()


@pytest.fixture
def no_leftovers(world: World) -> Iterator[None]:
    yield
    world.discard_fresh()


def _ask(world: World, route: ConditionalRoute, case: Case) -> tuple[Reply, dict[str, str]]:
    """Send the case; the reply, and the values its placeholders stood for."""
    used: dict[str, str] = {}

    def lookup(key: str) -> str:
        if key not in used:
            if key == "target":
                used[key] = world.value(case.target)
            elif key == "target_name":
                used[key] = world.value(f"{case.target}_name")
            elif key == "target_state":
                used[key] = oauth_state(world.value(case.target))
            else:
                used[key] = world.value(key)
        return used[key]

    # First, so that what the reply is searched for exists before the request is made.
    for text in (case.words, *case.absent):
        fill(text, lookup)
    params = fill(route.params if case.params is None else case.params, lookup)
    body = fill(route.body if case.body is None else case.body, lookup)
    reply = send(
        route.method,
        f"{world.url(route.service)}{fill(route.path, lookup)}",
        world.token(case.caller),
        params=params,
        body=body,
    )
    return reply, used


def _shown(reply: Reply) -> str:
    """The start of an error reply. A successful one can hold credentials, so it is never echoed."""
    if reply.status < 400:
        return "(reply body withheld: it may contain credentials)"
    return f"Reply starts: {reply.text[:200]!r}"


@pytest.mark.parametrize("route, case", ALL_CASES)
def test_the_route_enforces_its_rule(
    route: ConditionalRoute, case: Case, world: World, no_leftovers: None
) -> None:
    reply, used = _ask(world, route, case)

    def value_of(text: str) -> str:
        return fill(text, used.__getitem__)

    problems = []
    if reply.status != case.status:
        problems.append(f"HTTP {reply.status}, not {case.status}")
    if case.words and value_of(case.words) not in reply.text:
        problems.append(f"the reply does not say {case.words!r}")
    problems.extend(
        f"the reply contains {text!r}" for text in case.absent if value_of(text) in reply.text
    )
    if problems:
        expected = "allowed past the check" if case.allowed else "refused, or kept from seeing it"
        pytest.fail(
            f"{route.id} as the {case.caller} on {case.target!r} (expected: {expected}): "
            f"{'; '.join(problems)}. {_shown(reply)}"
        )


def test_the_registry_lists_the_same_types_for_a_member_and_the_admin(world: World) -> None:
    """The handler reads the admin flag; this pins that nothing hangs on it."""
    url = f"{world.url(_REGISTRY.service)}{_REGISTRY.path}"
    listed = {}
    for caller in (MEMBER, ADMIN):
        reply = send("GET", url, world.token(caller), params={"limit": "200"})
        assert reply.status == 200, f"The {caller}'s connector registry returned HTTP {reply.status}."
        listed[caller] = sorted(str(c.get("type")) for c in json.loads(reply.text).get("connectors") or [])
    assert listed[MEMBER], "The member's connector registry is empty, so the comparison proves nothing."
    assert listed[MEMBER] == listed[ADMIN], (
        f"A member is offered {len(listed[MEMBER])} connector types and the admin "
        f"{len(listed[ADMIN])}. Only the admin sees: "
        f"{sorted(set(listed[ADMIN]) - set(listed[MEMBER]))}; only the member: "
        f"{sorted(set(listed[MEMBER]) - set(listed[ADMIN]))}."
    )
