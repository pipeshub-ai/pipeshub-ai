#!/usr/bin/env python3
# ruff: noqa
from __future__ import annotations

"""
Opsgenie (opsgenie-sdk) -- Code Generator

Emits an `OpsgenieDataSource` with explicit, typed methods mapped to the *real* opsgenie_sdk APIs.
- Accepts either a raw `opsgenie_sdk.ApiClient` instance or any wrapper exposing `.get_sdk() -> ApiClient`.
- Each method instantiates the appropriate API class (AlertApi, IncidentApi, etc.) from the SDK.

SDK API patterns (all synchronous):
- Alerts:     AlertApi(api_client).list_alerts(), .get_alert(identifier), .create_alert(body=...), .close_alert(identifier, body=...), .acknowledge_alert(identifier, body=...)
- Incidents:  IncidentApi(api_client).list_incidents(query=...), .get_incident(identifier)
- Schedules:  ScheduleApi(api_client).list_schedules(), .get_schedule(identifier)
- Teams:      TeamApi(api_client).list_teams(), .get_team(identifier)
- Users:      UserApi(api_client).list_users(), .get_user(identifier)
- Services:   ServiceApi(api_client).list_services(), .get_service(identifier)
- Heartbeats: HeartbeatApi(api_client).list_heart_beats()

References:
- SDK: https://github.com/opsgenie/opsgenie-python-sdk
- API: https://docs.opsgenie.com/docs/api-overview
"""

import argparse
import textwrap
from typing import List, Tuple

# -----------------------------
# Configuration knobs (CLI-set)
# -----------------------------

DEFAULT_RESPONSE_IMPORT = "from app.sources.client.opsgenie.opsgenie import OpsgenieResponse"
DEFAULT_CLASS_NAME = "OpsgenieDataSource"
DEFAULT_OUT = "opsgenie_data_source.py"


HEADER = '''\
# ruff: noqa
# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownParameterType=false
from __future__ import annotations

from typing import Dict, List, Optional, Union, cast

import opsgenie_sdk

{response_import}

class {class_name}:
    """
    Strict, typed wrapper over opsgenie-sdk for common Opsgenie business operations.

    Accepts either an opsgenie_sdk `ApiClient` instance *or* any object with `.get_sdk() -> ApiClient`.
    """

    def __init__(self, client_or_sdk: Union[opsgenie_sdk.ApiClient, object]) -> None:
        if hasattr(client_or_sdk, "get_sdk"):
            sdk_obj = getattr(client_or_sdk, "get_sdk")()
            self._sdk: opsgenie_sdk.ApiClient = cast(opsgenie_sdk.ApiClient, sdk_obj)
        else:
            self._sdk = cast(opsgenie_sdk.ApiClient, client_or_sdk)

    # ---- helpers ----
    @staticmethod
    def _params(**kwargs: object) -> Dict[str, object]:
        out: Dict[str, object] = {}
        for k, v in kwargs.items():
            if v is None:
                continue
            if isinstance(v, (list, dict)) and len(v) == 0:
                continue
            out[k] = v
        return out
'''

FOOTER = """
"""

# Each tuple: (signature, body, short_doc)
METHODS: List[Tuple[str, str, str]] = []

# ---------- Alerts ----------
METHODS += [
    (
        "def list_alerts(self, limit: Optional[int] = None, offset: Optional[int] = None, sort: Optional[str] = None, order: Optional[str] = None, search_identifier: Optional[str] = None, search_identifier_type: Optional[str] = None, query: Optional[str] = None) -> OpsgenieResponse",
        "            api = opsgenie_sdk.AlertApi(self._sdk)\n"
        "            params = self._params(limit=limit, offset=offset, sort=sort, order=order, search_identifier=search_identifier, search_identifier_type=search_identifier_type, query=query)\n"
        "            result = api.list_alerts(**params)\n"
        "            return OpsgenieResponse(success=True, data=result)",
        "List all alerts with optional filters.  [alerts]",
    ),
    (
        "def get_alert(self, identifier: str) -> OpsgenieResponse",
        "            api = opsgenie_sdk.AlertApi(self._sdk)\n"
        "            result = api.get_alert(identifier=identifier)\n"
        "            return OpsgenieResponse(success=True, data=result)",
        "Get a specific alert.  [alerts]",
    ),
    (
        "def create_alert(self, message: str, alias: Optional[str] = None, description: Optional[str] = None, responders: Optional[List[Dict[str, str]]] = None, tags: Optional[List[str]] = None, entity: Optional[str] = None, source: Optional[str] = None, priority: Optional[str] = None, user: Optional[str] = None, note: Optional[str] = None) -> OpsgenieResponse",
        "            api = opsgenie_sdk.AlertApi(self._sdk)\n"
        "            payload_kwargs = self._params(message=message, alias=alias, description=description, responders=responders, tags=tags, entity=entity, source=source, priority=priority, user=user, note=note)\n"
        "            body = opsgenie_sdk.CreateAlertPayload(**payload_kwargs)\n"
        "            result = api.create_alert(body=body)\n"
        "            return OpsgenieResponse(success=True, data=result)",
        "Create a new alert.  [alerts]",
    ),
    (
        "def close_alert(self, identifier: str, user: Optional[str] = None, source: Optional[str] = None, note: Optional[str] = None) -> OpsgenieResponse",
        "            api = opsgenie_sdk.AlertApi(self._sdk)\n"
        "            payload_kwargs = self._params(user=user, source=source, note=note)\n"
        "            body = opsgenie_sdk.CloseAlertPayload(**payload_kwargs)\n"
        "            result = api.close_alert(identifier=identifier, body=body)\n"
        "            return OpsgenieResponse(success=True, data=result)",
        "Close an alert.  [alerts]",
    ),
    (
        "def acknowledge_alert(self, identifier: str, user: Optional[str] = None, source: Optional[str] = None, note: Optional[str] = None) -> OpsgenieResponse",
        "            api = opsgenie_sdk.AlertApi(self._sdk)\n"
        "            payload_kwargs = self._params(user=user, source=source, note=note)\n"
        "            body = opsgenie_sdk.AcknowledgeAlertPayload(**payload_kwargs)\n"
        "            result = api.acknowledge_alert(identifier=identifier, body=body)\n"
        "            return OpsgenieResponse(success=True, data=result)",
        "Acknowledge an alert.  [alerts]",
    ),
    (
        "def add_note_to_alert(self, identifier: str, note: str, user: Optional[str] = None, source: Optional[str] = None) -> OpsgenieResponse",
        "            api = opsgenie_sdk.AlertApi(self._sdk)\n"
        "            payload_kwargs = self._params(note=note, user=user, source=source)\n"
        "            body = opsgenie_sdk.AddNoteToAlertPayload(**payload_kwargs)\n"
        "            result = api.add_note(identifier=identifier, body=body)\n"
        "            return OpsgenieResponse(success=True, data=result)",
        "Add a note to an alert.  [alerts]",
    ),
    (
        "def list_alert_notes(self, identifier: str, limit: Optional[int] = None, offset: Optional[int] = None, order: Optional[str] = None, direction: Optional[str] = None) -> OpsgenieResponse",
        "            api = opsgenie_sdk.AlertApi(self._sdk)\n"
        "            params = self._params(limit=limit, offset=offset, order=order, direction=direction)\n"
        "            result = api.list_notes(identifier=identifier, **params)\n"
        "            return OpsgenieResponse(success=True, data=result)",
        "List notes of an alert.  [alerts]",
    ),
]

# ---------- Incidents ----------
METHODS += [
    (
        "def list_incidents(self, limit: Optional[int] = None, offset: Optional[int] = None, sort: Optional[str] = None, order: Optional[str] = None, query: Optional[str] = None) -> OpsgenieResponse",
        "            api = opsgenie_sdk.IncidentApi(self._sdk)\n"
        "            params = self._params(limit=limit, offset=offset, sort=sort, order=order, query=query)\n"
        "            result = api.list_incidents(**params)\n"
        "            return OpsgenieResponse(success=True, data=result)",
        "List all incidents.  [incidents]",
    ),
    (
        "def get_incident(self, identifier: str) -> OpsgenieResponse",
        "            api = opsgenie_sdk.IncidentApi(self._sdk)\n"
        "            result = api.get_incident(identifier=identifier)\n"
        "            return OpsgenieResponse(success=True, data=result)",
        "Get a specific incident.  [incidents]",
    ),
    (
        "def create_incident(self, message: str, description: Optional[str] = None, responders: Optional[List[Dict[str, str]]] = None, tags: Optional[List[str]] = None, details: Optional[Dict[str, str]] = None, priority: Optional[str] = None, note: Optional[str] = None, service_id: Optional[str] = None, notify_stakeholders: Optional[bool] = None) -> OpsgenieResponse",
        "            api = opsgenie_sdk.IncidentApi(self._sdk)\n"
        "            payload_kwargs = self._params(message=message, description=description, responders=responders, tags=tags, details=details, priority=priority, note=note, serviceId=service_id, notifyStakeholders=notify_stakeholders)\n"
        "            body = opsgenie_sdk.CreateIncidentPayload(**payload_kwargs)\n"
        "            result = api.create_incident(body=body)\n"
        "            return OpsgenieResponse(success=True, data=result)",
        "Create a new incident.  [incidents]",
    ),
]

# ---------- Schedules ----------
METHODS += [
    (
        "def list_schedules(self) -> OpsgenieResponse",
        "            api = opsgenie_sdk.ScheduleApi(self._sdk)\n"
        "            result = api.list_schedules()\n"
        "            return OpsgenieResponse(success=True, data=result)",
        "List all schedules.  [schedules]",
    ),
    (
        "def get_schedule(self, identifier: str) -> OpsgenieResponse",
        "            api = opsgenie_sdk.ScheduleApi(self._sdk)\n"
        "            result = api.get_schedule(identifier=identifier)\n"
        "            return OpsgenieResponse(success=True, data=result)",
        "Get a specific schedule.  [schedules]",
    ),
]

# ---------- Teams ----------
METHODS += [
    (
        "def list_teams(self) -> OpsgenieResponse",
        "            api = opsgenie_sdk.TeamApi(self._sdk)\n"
        "            result = api.list_teams()\n"
        "            return OpsgenieResponse(success=True, data=result)",
        "List all teams.  [teams]",
    ),
    (
        "def get_team(self, identifier: str) -> OpsgenieResponse",
        "            api = opsgenie_sdk.TeamApi(self._sdk)\n"
        "            result = api.get_team(identifier=identifier)\n"
        "            return OpsgenieResponse(success=True, data=result)",
        "Get a specific team.  [teams]",
    ),
]

# ---------- Users ----------
METHODS += [
    (
        "def list_users(self, limit: Optional[int] = None, offset: Optional[int] = None, sort: Optional[str] = None, order: Optional[str] = None, query: Optional[str] = None) -> OpsgenieResponse",
        "            api = opsgenie_sdk.UserApi(self._sdk)\n"
        "            params = self._params(limit=limit, offset=offset, sort=sort, order=order, query=query)\n"
        "            result = api.list_users(**params)\n"
        "            return OpsgenieResponse(success=True, data=result)",
        "List all users.  [users]",
    ),
    (
        "def get_user(self, identifier: str) -> OpsgenieResponse",
        "            api = opsgenie_sdk.UserApi(self._sdk)\n"
        "            result = api.get_user(identifier=identifier)\n"
        "            return OpsgenieResponse(success=True, data=result)",
        "Get a specific user.  [users]",
    ),
]

# ---------- Services ----------
METHODS += [
    (
        "def list_services(self, limit: Optional[int] = None, offset: Optional[int] = None) -> OpsgenieResponse",
        "            api = opsgenie_sdk.ServiceApi(self._sdk)\n"
        "            params = self._params(limit=limit, offset=offset)\n"
        "            result = api.list_services(**params)\n"
        "            return OpsgenieResponse(success=True, data=result)",
        "List all services.  [services]",
    ),
    (
        "def get_service(self, identifier: str) -> OpsgenieResponse",
        "            api = opsgenie_sdk.ServiceApi(self._sdk)\n"
        "            result = api.get_service(identifier=identifier)\n"
        "            return OpsgenieResponse(success=True, data=result)",
        "Get a specific service.  [services]",
    ),
]

# ---------- Heartbeats ----------
METHODS += [
    (
        "def list_heartbeats(self) -> OpsgenieResponse",
        "            api = opsgenie_sdk.HeartbeatApi(self._sdk)\n"
        "            result = api.list_heart_beats()\n"
        "            return OpsgenieResponse(success=True, data=result)",
        "List all heartbeats.  [heartbeats]",
    ),
]

# -------------------------
# Code emission utilities
# -------------------------


def _emit_method(sig: str, body: str, doc: str) -> str:
    normalized_body = textwrap.indent(textwrap.dedent(body), "        ")
    return f'    {sig}:\n        """{doc}"""\n{normalized_body}\n'


def build_class(
    response_import: str = DEFAULT_RESPONSE_IMPORT, class_name: str = DEFAULT_CLASS_NAME
) -> str:
    parts: List[str] = []
    header = HEADER.replace("{response_import}", response_import).replace(
        "{class_name}", class_name
    )
    parts.append(header)
    for sig, body, doc in METHODS:
        parts.append(_emit_method(sig, body, doc))
    parts.append(FOOTER)
    return "".join(parts)


def write_output(path: str, code: str) -> None:
    with open(path, "w", encoding="utf-8") as f:
        f.write(code)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate OpsgenieDataSource (opsgenie-sdk)."
    )
    parser.add_argument(
        "--out", default=DEFAULT_OUT, help="Output path for the generated data source."
    )
    parser.add_argument(
        "--response-import",
        default=DEFAULT_RESPONSE_IMPORT,
        help="Import line to bring in OpsgenieResponse.",
    )
    parser.add_argument(
        "--class-name",
        default=DEFAULT_CLASS_NAME,
        help="Name of the generated datasource class.",
    )
    parser.add_argument(
        "--print",
        dest="do_print",
        action="store_true",
        help="Also print generated code to stdout.",
    )
    args = parser.parse_args()

    code = build_class(response_import=args.response_import, class_name=args.class_name)
    write_output(args.out, code)
    if args.do_print:
        print(code)


if __name__ == "__main__":
    main()
