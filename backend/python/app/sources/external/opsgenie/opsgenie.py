# ruff: noqa
from __future__ import annotations

from typing import Any, Dict, List, Optional, Union, cast

import opsgenie_sdk  # type: ignore[reportMissingImports]

from app.sources.client.opsgenie.opsgenie import OpsgenieResponse

class OpsgenieDataSource:
    """
    Strict, typed wrapper over opsgenie-sdk for common Opsgenie business operations.

    Accepts either an opsgenie_sdk `ApiClient` instance *or* any object with `.get_sdk() -> ApiClient`.
    """

    def __init__(self, client_or_sdk: Union[opsgenie_sdk.ApiClient, object]) -> None:  # type: ignore[reportUnknownMemberType]
        super().__init__()
        if hasattr(client_or_sdk, "get_sdk"):  # type: ignore[reportUnknownArgumentType]
            sdk_obj = getattr(client_or_sdk, "get_sdk")()  # type: ignore[reportUnknownArgumentType]
            self._sdk: opsgenie_sdk.ApiClient = cast(opsgenie_sdk.ApiClient, sdk_obj)  # type: ignore[reportUnknownMemberType]
        else:
            self._sdk = cast(opsgenie_sdk.ApiClient, client_or_sdk)  # type: ignore[reportUnknownMemberType]

    # ---- helpers ----
    @staticmethod
    def _params(**kwargs: object) -> Dict[str, object]:
        out: Dict[str, object] = {}
        for k, v in kwargs.items():
            if v is None:
                continue
            if isinstance(v, (list, dict)) and len(v) == 0:  # type: ignore[reportUnknownArgumentType]
                continue
            out[k] = v
        return out
    def list_alerts(self, limit: Optional[int] = None, offset: Optional[int] = None, sort: Optional[str] = None, order: Optional[str] = None, search_identifier: Optional[str] = None, search_identifier_type: Optional[str] = None, query: Optional[str] = None) -> OpsgenieResponse:
        """List all alerts with optional filters.  [alerts]"""
        api = opsgenie_sdk.AlertApi(self._sdk)  # type: ignore[reportUnknownMemberType]
        params = self._params(limit=limit, offset=offset, sort=sort, order=order, search_identifier=search_identifier, search_identifier_type=search_identifier_type, query=query)
        result: Any = api.list_alerts(**params)  # type: ignore[reportUnknownMemberType]
        return OpsgenieResponse(success=True, data=result)  # type: ignore[reportUnknownArgumentType]
    def get_alert(self, identifier: str) -> OpsgenieResponse:
        """Get a specific alert.  [alerts]"""
        api = opsgenie_sdk.AlertApi(self._sdk)  # type: ignore[reportUnknownMemberType]
        result: Any = api.get_alert(identifier=identifier)  # type: ignore[reportUnknownMemberType]
        return OpsgenieResponse(success=True, data=result)  # type: ignore[reportUnknownArgumentType]
    def create_alert(self, message: str, alias: Optional[str] = None, description: Optional[str] = None, responders: Optional[List[Dict[str, str]]] = None, tags: Optional[List[str]] = None, entity: Optional[str] = None, source: Optional[str] = None, priority: Optional[str] = None, user: Optional[str] = None, note: Optional[str] = None) -> OpsgenieResponse:
        """Create a new alert.  [alerts]"""
        api = opsgenie_sdk.AlertApi(self._sdk)  # type: ignore[reportUnknownMemberType]
        payload_kwargs = self._params(message=message, alias=alias, description=description, responders=responders, tags=tags, entity=entity, source=source, priority=priority, user=user, note=note)
        body = opsgenie_sdk.CreateAlertPayload(**payload_kwargs)  # type: ignore[reportUnknownMemberType]
        result: Any = api.create_alert(body=body)  # type: ignore[reportUnknownMemberType]
        return OpsgenieResponse(success=True, data=result)  # type: ignore[reportUnknownArgumentType]
    def close_alert(self, identifier: str, user: Optional[str] = None, source: Optional[str] = None, note: Optional[str] = None) -> OpsgenieResponse:
        """Close an alert.  [alerts]"""
        api = opsgenie_sdk.AlertApi(self._sdk)  # type: ignore[reportUnknownMemberType]
        payload_kwargs = self._params(user=user, source=source, note=note)
        body = opsgenie_sdk.CloseAlertPayload(**payload_kwargs)  # type: ignore[reportUnknownMemberType]
        result: Any = api.close_alert(identifier=identifier, body=body)  # type: ignore[reportUnknownMemberType]
        return OpsgenieResponse(success=True, data=result)  # type: ignore[reportUnknownArgumentType]
    def acknowledge_alert(self, identifier: str, user: Optional[str] = None, source: Optional[str] = None, note: Optional[str] = None) -> OpsgenieResponse:
        """Acknowledge an alert.  [alerts]"""
        api = opsgenie_sdk.AlertApi(self._sdk)  # type: ignore[reportUnknownMemberType]
        payload_kwargs = self._params(user=user, source=source, note=note)
        body = opsgenie_sdk.AcknowledgeAlertPayload(**payload_kwargs)  # type: ignore[reportUnknownMemberType]
        result: Any = api.acknowledge_alert(identifier=identifier, body=body)  # type: ignore[reportUnknownMemberType]
        return OpsgenieResponse(success=True, data=result)  # type: ignore[reportUnknownArgumentType]
    def add_note_to_alert(self, identifier: str, note: str, user: Optional[str] = None, source: Optional[str] = None) -> OpsgenieResponse:
        """Add a note to an alert.  [alerts]"""
        api = opsgenie_sdk.AlertApi(self._sdk)  # type: ignore[reportUnknownMemberType]
        payload_kwargs = self._params(note=note, user=user, source=source)
        body = opsgenie_sdk.AddNoteToAlertPayload(**payload_kwargs)  # type: ignore[reportUnknownMemberType]
        result: Any = api.add_note(identifier=identifier, body=body)  # type: ignore[reportUnknownMemberType]
        return OpsgenieResponse(success=True, data=result)  # type: ignore[reportUnknownArgumentType]
    def list_alert_notes(self, identifier: str, limit: Optional[int] = None, offset: Optional[int] = None, order: Optional[str] = None, direction: Optional[str] = None) -> OpsgenieResponse:
        """List notes of an alert.  [alerts]"""
        api = opsgenie_sdk.AlertApi(self._sdk)  # type: ignore[reportUnknownMemberType]
        params = self._params(limit=limit, offset=offset, order=order, direction=direction)
        result: Any = api.list_notes(identifier=identifier, **params)  # type: ignore[reportUnknownMemberType]
        return OpsgenieResponse(success=True, data=result)  # type: ignore[reportUnknownArgumentType]
    def list_incidents(self, limit: Optional[int] = None, offset: Optional[int] = None, sort: Optional[str] = None, order: Optional[str] = None, query: Optional[str] = None) -> OpsgenieResponse:
        """List all incidents.  [incidents]"""
        api = opsgenie_sdk.IncidentApi(self._sdk)  # type: ignore[reportUnknownMemberType]
        params = self._params(limit=limit, offset=offset, sort=sort, order=order, query=query)
        result: Any = api.list_incidents(**params)  # type: ignore[reportUnknownMemberType]
        return OpsgenieResponse(success=True, data=result)  # type: ignore[reportUnknownArgumentType]
    def get_incident(self, identifier: str) -> OpsgenieResponse:
        """Get a specific incident.  [incidents]"""
        api = opsgenie_sdk.IncidentApi(self._sdk)  # type: ignore[reportUnknownMemberType]
        result: Any = api.get_incident(identifier=identifier)  # type: ignore[reportUnknownMemberType]
        return OpsgenieResponse(success=True, data=result)  # type: ignore[reportUnknownArgumentType]
    def create_incident(self, message: str, description: Optional[str] = None, responders: Optional[List[Dict[str, str]]] = None, tags: Optional[List[str]] = None, details: Optional[Dict[str, str]] = None, priority: Optional[str] = None, note: Optional[str] = None, service_id: Optional[str] = None, notify_stakeholders: Optional[bool] = None) -> OpsgenieResponse:
        """Create a new incident.  [incidents]"""
        api = opsgenie_sdk.IncidentApi(self._sdk)  # type: ignore[reportUnknownMemberType]
        payload_kwargs = self._params(message=message, description=description, responders=responders, tags=tags, details=details, priority=priority, note=note, serviceId=service_id, notifyStakeholders=notify_stakeholders)
        body = opsgenie_sdk.CreateIncidentPayload(**payload_kwargs)  # type: ignore[reportUnknownMemberType]
        result: Any = api.create_incident(body=body)  # type: ignore[reportUnknownMemberType]
        return OpsgenieResponse(success=True, data=result)  # type: ignore[reportUnknownArgumentType]
    def list_schedules(self) -> OpsgenieResponse:
        """List all schedules.  [schedules]"""
        api = opsgenie_sdk.ScheduleApi(self._sdk)  # type: ignore[reportUnknownMemberType]
        result: Any = api.list_schedules()  # type: ignore[reportUnknownMemberType]
        return OpsgenieResponse(success=True, data=result)  # type: ignore[reportUnknownArgumentType]
    def get_schedule(self, identifier: str) -> OpsgenieResponse:
        """Get a specific schedule.  [schedules]"""
        api = opsgenie_sdk.ScheduleApi(self._sdk)  # type: ignore[reportUnknownMemberType]
        result: Any = api.get_schedule(identifier=identifier)  # type: ignore[reportUnknownMemberType]
        return OpsgenieResponse(success=True, data=result)  # type: ignore[reportUnknownArgumentType]
    def list_teams(self) -> OpsgenieResponse:
        """List all teams.  [teams]"""
        api = opsgenie_sdk.TeamApi(self._sdk)  # type: ignore[reportUnknownMemberType]
        result: Any = api.list_teams()  # type: ignore[reportUnknownMemberType]
        return OpsgenieResponse(success=True, data=result)  # type: ignore[reportUnknownArgumentType]
    def get_team(self, identifier: str) -> OpsgenieResponse:
        """Get a specific team.  [teams]"""
        api = opsgenie_sdk.TeamApi(self._sdk)  # type: ignore[reportUnknownMemberType]
        result: Any = api.get_team(identifier=identifier)  # type: ignore[reportUnknownMemberType]
        return OpsgenieResponse(success=True, data=result)  # type: ignore[reportUnknownArgumentType]
    def list_users(self, limit: Optional[int] = None, offset: Optional[int] = None, sort: Optional[str] = None, order: Optional[str] = None, query: Optional[str] = None) -> OpsgenieResponse:
        """List all users.  [users]"""
        api = opsgenie_sdk.UserApi(self._sdk)  # type: ignore[reportUnknownMemberType]
        params = self._params(limit=limit, offset=offset, sort=sort, order=order, query=query)
        result: Any = api.list_users(**params)  # type: ignore[reportUnknownMemberType]
        return OpsgenieResponse(success=True, data=result)  # type: ignore[reportUnknownArgumentType]
    def get_user(self, identifier: str) -> OpsgenieResponse:
        """Get a specific user.  [users]"""
        api = opsgenie_sdk.UserApi(self._sdk)  # type: ignore[reportUnknownMemberType]
        result: Any = api.get_user(identifier=identifier)  # type: ignore[reportUnknownMemberType]
        return OpsgenieResponse(success=True, data=result)  # type: ignore[reportUnknownArgumentType]
    def list_services(self, limit: Optional[int] = None, offset: Optional[int] = None) -> OpsgenieResponse:
        """List all services.  [services]"""
        api = opsgenie_sdk.ServiceApi(self._sdk)  # type: ignore[reportUnknownMemberType]
        params = self._params(limit=limit, offset=offset)
        result: Any = api.list_services(**params)  # type: ignore[reportUnknownMemberType]
        return OpsgenieResponse(success=True, data=result)  # type: ignore[reportUnknownArgumentType]
    def get_service(self, identifier: str) -> OpsgenieResponse:
        """Get a specific service.  [services]"""
        api = opsgenie_sdk.ServiceApi(self._sdk)  # type: ignore[reportUnknownMemberType]
        result: Any = api.get_service(identifier=identifier)  # type: ignore[reportUnknownMemberType]
        return OpsgenieResponse(success=True, data=result)  # type: ignore[reportUnknownArgumentType]
    def list_heartbeats(self) -> OpsgenieResponse:
        """List all heartbeats.  [heartbeats]"""
        api = opsgenie_sdk.HeartbeatApi(self._sdk)  # type: ignore[reportUnknownMemberType]
        result: Any = api.list_heart_beats()  # type: ignore[reportUnknownMemberType]
        return OpsgenieResponse(success=True, data=result)  # type: ignore[reportUnknownArgumentType]
