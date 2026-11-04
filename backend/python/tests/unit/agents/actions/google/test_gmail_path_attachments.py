"""The Gmail agent tools (send_email, draft_email, reply) never attach a server file
named by path: the call is refused, the file is not opened or stat'ed, and no message
or draft goes out. Attachments by record id and plain sends keep working.
"""

from __future__ import annotations

import ast
import base64
import builtins
import email
import inspect
import io
import json
import os
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.agent_loop_lib.tools.decorators import TOOL_META_ATTR, BoundMethodTool
from app.agents.actions.google.gmail import gmail as gmail_module
from app.agents.actions.google.gmail.gmail import (
    DraftEmailInput,
    Gmail,
    ReplyInput,
    SendEmailInput,
)
from app.agents.actions.google.gmail.utils import GmailUtils

if TYPE_CHECKING:
    from collections.abc import Iterator
    from email.message import Message
    from pathlib import Path

SECRET_TEXT = "DB_PASSWORD=hunter2"
TOOLS = ["send_email", "draft_email", "reply"]


def _gmail(state: object = None) -> Gmail:
    gm = Gmail.__new__(Gmail)
    gm.client = MagicMock()
    gm.client.users_messages_send = AsyncMock(return_value={"id": "sent-1"})
    gm.client.users_drafts_create = AsyncMock(return_value={"id": "draft-1"})
    # reply reads the original for its threading headers before sending
    gm.client.users_messages_get = AsyncMock(return_value={"threadId": "thread-1", "payload": {"headers": []}})
    gm.chat_state = state
    return gm


def _args(tool: str, **extra: object) -> dict[str, Any]:
    args: dict[str, Any] = {"mail_to": ["ada@example.com"], "mail_subject": "Report", "mail_body": "hello"}
    if tool == "reply":
        args["message_id"] = "m-1"
    args.update(extra)
    return args


def _outbound(gm: Gmail) -> list[Any]:
    return list(gm.client.users_messages_send.await_args_list) + list(gm.client.users_drafts_create.await_args_list)


def _mime(call: object) -> Message:
    body = call.kwargs["body"]
    raw = body["raw"] if "raw" in body else body["message"]["raw"]
    return email.message_from_bytes(base64.urlsafe_b64decode(raw))


@pytest.fixture
def secret(tmp_path: Path) -> Path:
    path = tmp_path / "service.env"
    path.write_text(SECRET_TEXT)
    return path


@pytest.fixture
def touched(secret: Path) -> Iterator[list[str]]:
    """Every open()/io.open()/os.stat() aimed at the secret file while the tool runs."""
    seen: list[str] = []
    target = os.path.realpath(str(secret))
    real_open, real_io_open, real_stat = builtins.open, io.open, os.stat

    def _note(path: object, how: str) -> None:
        try:
            if os.path.realpath(os.fspath(path)) == target:
                seen.append(how)
        except (TypeError, ValueError):
            pass

    def spy_open(path: object, *a: object, **k: object) -> object:
        _note(path, "open")
        return real_open(path, *a, **k)

    def spy_io_open(path: object, *a: object, **k: object) -> object:
        _note(path, "io.open")
        return real_io_open(path, *a, **k)

    def spy_stat(path: object, *a: object, **k: object) -> object:
        _note(path, "stat")
        return real_stat(path, *a, **k)

    with patch("builtins.open", spy_open), patch("io.open", spy_io_open), patch("os.stat", spy_stat):
        yield seen


def _path_shapes(secret: Path) -> dict[str, Any]:
    return {
        "list": [str(secret)],
        "bare_string": str(secret),
        "tuple": (str(secret),),
        "path_object": [secret],
        "missing_file": [str(secret.parent / "does-not-exist.txt")],
        "path_among_record_ids": ["rec-1", str(secret)],
        "dict": {"path": str(secret)},
        "relative_traversal": ["../../../../../../etc/passwd"],
        "proc_environ": ["/proc/self/environ"],
    }


SHAPES = [
    "list", "bare_string", "tuple", "path_object", "missing_file",
    "path_among_record_ids", "dict", "relative_traversal", "proc_environ",
]


class TestPathAttachmentsAreRefused:
    @pytest.mark.parametrize("tool", TOOLS)
    @pytest.mark.parametrize("shape", SHAPES)
    async def test_nothing_is_read_and_nothing_is_sent(self, tool: str, shape: str, secret: Path, touched: list[str]) -> None:
        gm = _gmail()
        with patch.object(GmailUtils, "transform_message_body", wraps=GmailUtils.transform_message_body) as builder:
            ok, payload = await getattr(gm, tool)(**_args(tool, mail_attachments=_path_shapes(secret)[shape]))

        assert ok is False
        error = json.loads(payload)["error"]
        assert "attachment_record_ids" in error
        assert str(secret) not in error and SECRET_TEXT not in error
        assert _outbound(gm) == [], "a message or draft went out"
        builder.assert_not_called()
        assert touched == [], f"the file was touched: {touched}"

    @pytest.mark.parametrize("tool", TOOLS)
    async def test_refused_before_any_record_is_resolved(self, tool: str, secret: Path, touched: list[str]) -> None:
        gm = _gmail(state={"org_id": "o1", "user_id": "u1"})
        resolver = AsyncMock()
        with patch.object(gmail_module, "resolve_attachments", resolver):
            ok, payload = await getattr(gm, tool)(
                **_args(tool, mail_attachments=[str(secret)], attachment_record_ids=["rec-1"])
            )

        assert ok is False
        assert "attachment_record_ids" in json.loads(payload)["error"]
        resolver.assert_not_awaited()
        assert _outbound(gm) == []
        assert touched == []

    async def test_positional_call_cannot_slip_a_path_through(self, secret: Path, touched: list[str]) -> None:
        gm = _gmail()
        to, subject = ["ada@example.com"], "Report"

        results = [
            await gm.send_email(to, subject, None, None, "hello", [str(secret)]),
            await gm.draft_email(to, subject, None, None, "hello", [str(secret)]),
            await gm.reply("m-1", to, subject, None, None, "hello", [str(secret)]),
        ]

        assert [ok for ok, _ in results] == [False, False, False]
        assert _outbound(gm) == []
        assert touched == []

    @pytest.mark.parametrize("tool", TOOLS)
    async def test_refused_through_the_wrapper_the_agent_loop_calls(self, tool: str, secret: Path, touched: list[str]) -> None:
        """The loop passes model arguments straight to the method (BoundMethodTool.validate is a
        no-op), so an argument the schema no longer lists still arrives and must be refused."""
        gm = _gmail()
        method = getattr(gm, tool)
        wrapped = BoundMethodTool(method, getattr(method, TOOL_META_ATTR))

        out = await wrapped(**_args(tool, mail_attachments=[str(secret)]))

        assert out.success is False
        assert "attachment_record_ids" in (out.error or "")
        assert SECRET_TEXT not in (out.error or "")
        assert _outbound(gm) == []
        assert touched == []


class TestSchemaNoLongerOffersPaths:
    @pytest.mark.parametrize("tool", TOOLS)
    def test_tool_parameters(self, tool: str) -> None:
        params = getattr(getattr(Gmail, tool), TOOL_META_ATTR).parameters
        names = [p.name for p in params]
        assert "mail_attachments" not in names
        assert "attachment_record_ids" in names
        assert not [p.name for p in params if "path" in (p.description or "").lower()]

    @pytest.mark.parametrize("model", [SendEmailInput, ReplyInput, DraftEmailInput])
    def test_pydantic_inputs(self, model: type) -> None:
        assert "mail_attachments" not in model.model_fields

    def test_no_caller_hands_paths_to_the_mime_builder(self) -> None:
        """transform_message_body still reads paths; its sixth argument must stay None here."""
        tree = ast.parse(inspect.getsource(gmail_module))
        calls = [
            node for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "transform_message_body"
        ]
        assert len(calls) >= 3
        for call in calls:
            assert not [k for k in call.keywords if k.arg == "mail_attachments"]
            sixth = call.args[5]
            assert isinstance(sixth, ast.Constant) and sixth.value is None, ast.dump(sixth)


class TestLegitimateSendsStillWork:
    @pytest.mark.parametrize("tool", TOOLS)
    @pytest.mark.parametrize("empty", [None, []], ids=["none", "empty-list"])
    async def test_empty_attachment_list_is_not_a_refusal(self, tool: str, empty: list | None) -> None:
        gm = _gmail()

        ok, payload = await getattr(gm, tool)(**_args(tool, mail_attachments=empty))

        assert ok is True, payload
        sent = _outbound(gm)
        assert len(sent) == 1
        assert not _mime(sent[0]).is_multipart()

    @pytest.mark.parametrize("tool", TOOLS)
    async def test_record_id_attachments_are_sent_and_no_path_reaches_the_builder(self, tool: str) -> None:
        gm = _gmail(state={"org_id": "o1", "user_id": "u1"})
        bundle = SimpleNamespace(
            failures=[],
            resolved=[SimpleNamespace(
                record_id="rec-1", filename="q3.csv", content=b"a,b\n1,2\n", mime_type="text/csv", size_bytes=8,
            )],
        )
        with (
            patch.object(gmail_module, "resolve_attachments", AsyncMock(return_value=bundle)) as resolver,
            patch.object(GmailUtils, "transform_message_body", wraps=GmailUtils.transform_message_body) as builder,
        ):
            ok, payload = await getattr(gm, tool)(**_args(tool, attachment_record_ids=["rec-1"]))

        assert ok is True, payload
        resolver.assert_awaited_once()
        assert builder.call_args.args[5] is None
        sent = _outbound(gm)
        assert len(sent) == 1
        parts = [p for p in _mime(sent[0]).walk() if p.get_filename()]
        assert [p.get_filename() for p in parts] == ["q3.csv"]
        assert parts[0].get_payload(decode=True) == b"a,b\n1,2\n"


class TestPartialAttachmentsSendNothing:
    @pytest.mark.parametrize("tool", TOOLS)
    async def test_one_unresolved_attachment_stops_the_send(self, tool: str) -> None:
        gm = _gmail(state={"org_id": "o1", "user_id": "u1"})
        bundle = SimpleNamespace(
            failures=[SimpleNamespace(ref="rec-gone", error="Unexpected error: internal detail",
                                      to_dict=lambda: {"ref": "rec-gone"})],
            resolved=[SimpleNamespace(
                record_id="rec-1", filename="q3.csv", content=b"a,b\n", mime_type="text/csv", size_bytes=4,
            )],
        )
        with patch.object(gmail_module, "resolve_attachments", AsyncMock(return_value=bundle)):
            ok, payload = await getattr(gm, tool)(**_args(tool, attachment_record_ids=["rec-1", "rec-gone"]))

        assert ok is False
        assert _outbound(gm) == []
        assert "rec-gone" in payload and "internal detail" not in payload
