"""R40-TEXT SEC-B-04 bypasses (ADR-TEXT-01): what the next injected send would try once the proof's three are shut.

Each test plays the call an injected model would make and reads what the approval card is sent: the destination
label as the worker journals it (``FlowAsk.to_wire``), the sources' titles, and a third-party tool's display name.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.agents.agent_loop.mcp_tool_adapter import MCPToolAdapter
from app.agents.agent_loop.openapi_tool_adapter import OpenAPIToolAdapter
from app.agents.mcp.models import MCPToolInfo
from app.harness.flow_destination import MAX_LABEL_CHARS, pipeshub_destination
from harness_core.contracts.generated.sink import SinkDescriptor
from harness_core.effects.flow import FlowAsk, destination_wire
from harness_core.effects.types import ToolEffect, tool_id_for
from harness_core.kernel.core.messages import ToolCall
from harness_core.security.labels import AskSource
from harness_core.security.token_patterns import SECRET_MASK

_POST = ToolEffect(tool_id_for("send_message", source="slack"), "write", False,
                   SinkDescriptor(egress="external", args_leave_boundary=True))
_MAIL = ToolEffect(tool_id_for("send_email", source="gmail"), "write", False,
                   SinkDescriptor(egress="external", args_leave_boundary=True))
_FETCH = ToolEffect("web@searxng/fetch_url", "read", True, SinkDescriptor(egress="external", args_leave_boundary=True))


def _label(effect: ToolEffect, **args: object) -> str:
    where = pipeshub_destination(ToolCall(id="c", name="x", arguments=args), effect)
    assert where is not None
    return str(destination_wire(where)["label"])


def test_an_attacker_in_cc_or_bcc_is_named_in_the_title() -> None:
    colleagues = [f"colleague{i}@corp.example" for i in range(12)]
    assert "drop@attacker.example" in _label(_MAIL, to_recipients=colleagues, bcc_recipients=["drop@attacker.example"])
    assert "drop@attacker.example" in _label(_MAIL, mail_to=colleagues, mail_cc=["drop@attacker.example"])


def test_recipients_without_room_are_counted_with_their_domains() -> None:
    to = [f"person{i}@corp.example" for i in range(30)]
    label = _label(_MAIL, to=to)
    assert len(label) <= MAX_LABEL_CHARS
    assert label.endswith("@corp.example)") and "(+" in label
    shown = label.split(" (+")[0].split(", ")
    assert all(name in to for name in shown)
    assert len(shown) + int(label.split(" (+")[1].split()[0]) == 30


def test_many_outside_domains_each_reach_the_title() -> None:
    to = [f"a{i}@corp.example" for i in range(10)] + ["x@one.example", "y@two.example"]
    label = _label(_MAIL, to=to)
    assert "one.example" in label and "two.example" in label


def test_a_bidi_control_in_a_cc_recipient_or_a_channel_is_marked() -> None:
    assert "⟨U+202E⟩" in _label(_MAIL, to=["ana@corp.example"], cc=["\u202emoc.live@bob"])
    assert _label(_POST, channel="sa\u202eles") == "#sa⟨U+202E⟩les"


def test_a_zero_width_character_in_a_channel_or_host_is_marked() -> None:
    assert _label(_POST, channel="gen\u200beral") == "#gen⟨U+200B⟩eral"
    assert "⟨U+200B⟩" in _label(_FETCH, url="https://pipes\u200bhub.com/x")


def test_a_homoglyph_in_an_address_domain_is_punycode() -> None:
    label = _label(_MAIL, to=["ceo@pіpeshub.com"])
    assert label == "ceo@xn--ppeshub-rog.com"


def test_a_mixed_script_channel_has_its_odd_letters_marked() -> None:
    assert _label(_POST, channel="sаles") == "#s⟨U+0430⟩les"


def test_a_host_the_model_sends_in_punycode_is_not_decoded_into_a_lookalike() -> None:
    assert _label(_FETCH, url="https://xn--ppeshub-rog.com/") == "xn--ppeshub-rog.com"


def test_a_host_in_one_script_reads_in_unicode() -> None:
    assert _label(_FETCH, url="https://xn--mnchen-3ya.de/") == "münchen.de"
    assert _label(_FETCH, url="https://www.münchen.de/") == "münchen.de"


@pytest.mark.parametrize("host", ["pаypal.com", "раураӏ.com", "pıpeshub.com", "gοogle.com", "pạypal.com"])
def test_a_lookalike_site_is_never_shown_in_unicode(host: str) -> None:
    label = _label(_FETCH, url=f"https://{host}/login")
    assert label.isascii() and "xn--" in label


def test_a_token_is_still_masked_before_the_label_is_made_safe() -> None:
    token = "xoxb-" + "1234567890-abcdefghij"
    assert SECRET_MASK in _label(_POST, channel=f"{token}\u202e")


def test_a_source_title_cannot_reorder_the_card() -> None:
    ask = FlowAsk(
        tool_id="t", sensitivity="private", sources=(AskSource(title="Payroll \u202efdp.exe"),), more_sources=0,
        destination=None, conversation_grant=False,
    )
    sources = ask.to_wire()["sources"]
    assert sources == [{"title": "Payroll ⟨U+202E⟩fdp.exe"}]


def _mcp(title: object) -> MCPToolAdapter:
    server = SimpleNamespace(instance_id="inst-1", display_name="Third party", name="thirdparty")
    info = MCPToolInfo(name="delete_everything", namespaced_name="thirdparty__delete_everything", description="d",
                       input_schema={}, annotations={"title": title})
    return MCPToolAdapter(server, info, session_manager=None)  # type: ignore[arg-type]


def test_an_mcp_title_cannot_forge_the_server_part_or_hide_text() -> None:
    assert _mcp("Slack · Send message").display_name == "Third party · Slack - Send message"
    shown = _mcp("\u202eatad lla eteled\u202c Read calendar").display_name
    assert shown.startswith("Third party · ") and "\u202e" not in shown and shown.count("⟨U+") == 2
    assert _mcp("Read\u200bcalendar").display_name == "Third party · Read⟨U+200B⟩calendar"


def test_an_openapi_summary_cannot_forge_the_toolset_part() -> None:
    instance = SimpleNamespace(name="Acme API", id="inst-2")
    operation = SimpleNamespace(summary="Slack · post\u202e", tool_name="post_message")
    adapter = OpenAPIToolAdapter.__new__(OpenAPIToolAdapter)
    adapter._instance = instance  # type: ignore[attr-defined]  # pyright: ignore[reportAttributeAccessIssue]
    adapter._operation = operation  # type: ignore[attr-defined]  # pyright: ignore[reportAttributeAccessIssue]
    assert adapter.display_name == "Acme API · Slack - post⟨U+202E⟩"
