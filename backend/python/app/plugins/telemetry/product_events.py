"""Product-behaviour events: what a person did, shipped one by one to the collector.

Each handler turns a platform event into the record the collector has always
received (same event name, same property keys), and where a dashboard counts the
same step, into its activity counter as well.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from app.plugins.telemetry.event_buffer import record_event
from app.plugins.telemetry.modules.activity_metrics import record_service_activity

if TYPE_CHECKING:
    from app import platform_events

Payload = Mapping[str, Any]


def _search_performed(_name: str, p: Payload) -> None:
    record_event("search_performed", {
        "orgId": p["org_id"],
        "userId": p["user_id"],
        "email": p["user_email"],
        "domain": p["email_domain"],
        "status_code": p["status_code"],
        "num_queries": p["query_count"],
        "search_type": p["search_type"],
    })


def _retrieval_performed(_name: str, p: Payload) -> None:
    record_event("retrieve_performed", {
        "orgId": p["org_id"],
        "userId": p["user_id"],
        "domain": p["email_domain"],
        "status_code": p["status_code"],
        "num_queries": p["query_count"],
    })


def _chat_session_started(_name: str, p: Payload) -> None:
    record_event("chat_session_started", {
        "orgId": p["org_id"],
        "userId": p["user_id"],
        "email": p["user_email"],
        "domain": p["email_domain"],
        "mode": p["chat_mode"],
        "search_type": p["search_type"],
        "streaming": p["streaming"],
    })


def _agent_run_started(_name: str, p: Payload) -> None:
    record_event("agent_run", {
        "orgId": p["org_id"],
        "userId": p["user_id"],
        "email": p["user_email"],
        "domain": p["email_domain"],
        "has_tools": p["has_tools"],
        "streaming": p["streaming"],
    })


def _connector_added(_name: str, p: Payload) -> None:
    record_event("connector_added", {
        "orgId": p["org_id"],
        "userId": p["user_id"],
        "email": p["user_email"],
        "domain": p["email_domain"],
        "connector": p["connector"],
        "scope": p["scope"],
    })


def _answer_generated(_name: str, p: Payload) -> None:
    record_event("answer_generated", {
        "orgId": p["org_id"],
        "userId": p["user_id"],
        "domain": p["email_domain"],
        "chat_mode": p["chat_mode"],
        "citation_count": p["citation_count"],
        "connectors": p["connectors"],
        "demo_sources": p["demo_sources"],
        "stopped": p["stopped"],
    })
    # The Grafana counter for the same step: org and domain only. The
    # connector label marks demo answers so the demo funnel has its own line.
    record_service_activity(
        "query_service",
        "answer_generated",
        connector="demo" if p["demo_sources"] else "none",
        status="stopped" if p["stopped"] else ("cited" if p["citation_count"] else "uncited"),
        org=str(p["org_id"] or "unknown"),
        domain=p["email_domain"],
    )


def handlers() -> dict[str, platform_events.Handler]:
    return {
        "search.performed": _search_performed,
        "retrieval.performed": _retrieval_performed,
        "chat.session.started": _chat_session_started,
        "agent.run.started": _agent_run_started,
        "connector.added": _connector_added,
        "answer.generated": _answer_generated,
    }
