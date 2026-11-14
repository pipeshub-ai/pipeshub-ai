"""Outlook .msg files as HTML pages, and the guarantee that reading one never fetches its images."""
from __future__ import annotations

import datetime as dt
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import compressed_rtf
import pytest

from app.modules.parsers.email import msg_to_html as msg_module
from app.modules.parsers.email.msg_to_html import msg_to_html
from app.modules.parsers.email.rtf_deencapsulate import (
    deencapsulate_html,
    is_encapsulated_html,
)
from app.services.parsing.interface import UnreadableFileError, damaged_file_reason
from tests.support.new_type_samples import (
    msg_with_html_body,
    msg_with_text_body,
    not_a_msg,
)


def test_an_html_mail_reads_with_its_headers_body_and_attachment_list() -> None:
    page = msg_to_html(msg_with_html_body())
    assert "<h1>Re: test internal</h1>" in page
    assert "<strong>From:</strong> &quot;bedfortest at TEST&quot; &lt;bedfortest@tutanota.de&gt;" in page
    assert "<strong>To:</strong> Arne Möhle &lt;arne.moehle@tutao.onmicrosoft.com&gt;" in page
    assert "<strong>Date:</strong> 2020-10-06T09:57:46+00:00" in page
    assert "Sicher gesendet mit Tutanota" in page
    assert "<li>serveimage.jpg (image/jpeg)</li>" in page


def test_a_plain_text_mail_reads_with_its_body() -> None:
    page = msg_to_html(msg_with_text_body())
    assert "This is a message" in page
    assert "<h1>This is the subject</h1>" in page
    # An unsent draft's empty date (1601-01-01) is not shown.
    assert "Date:" not in page
    assert "Attachments" not in page


def test_a_file_that_is_not_a_mail_is_damaged() -> None:
    with pytest.raises(UnreadableFileError) as caught:
        msg_to_html(not_a_msg())
    assert caught.value.details["user_reason"] == damaged_file_reason("msg")


@pytest.mark.parametrize("content", [b"", b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 600, b"x" * 100])
def test_truncated_or_random_bytes_are_damaged(content: bytes) -> None:
    with pytest.raises(UnreadableFileError):
        msg_to_html(content)


def test_a_cut_off_mail_is_damaged() -> None:
    data = msg_with_html_body()
    with pytest.raises(UnreadableFileError):
        msg_to_html(data[: len(data) // 3])


def test_a_mail_over_the_cap_is_refused_before_it_is_read(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(msg_module, "MSG_MAX_BYTES", 1024)
    with patch("oxmsg.Message.load") as load, pytest.raises(UnreadableFileError) as caught:
        msg_to_html(msg_with_text_body())
    load.assert_not_called()
    assert "larger than" in caught.value.message


class _Props:
    def __init__(self, binary: dict[int, bytes] | None = None, strings: dict[int, str] | None = None,
                 ints: dict[int, int] | None = None) -> None:
        self._b, self._s, self._i = binary or {}, strings or {}, ints or {}

    def binary_prop_value(self, pid: int) -> bytes | None:
        return self._b.get(pid)

    def str_prop_value(self, pid: int) -> str | None:
        return self._s.get(pid)

    def int_prop_value(self, pid: int) -> int | None:
        return self._i.get(pid)


def _message(**kw: object) -> SimpleNamespace:
    defaults: dict[str, object] = {
        "subject": "Subject <b>", "sender": 'A <a@x.test>', "sent_date": dt.datetime(2026, 1, 2, tzinfo=dt.UTC),
        "html_body": None, "body": None, "recipients": (), "attachments": (), "properties": _Props(),
    }
    return SimpleNamespace(**{**defaults, **kw})


def _recipient(name: str, address: str, kind: int) -> SimpleNamespace:
    return SimpleNamespace(name=name, email_address=address, properties=_Props(ints={0x0C15: kind}))


def _page(message: SimpleNamespace) -> str:
    with patch("oxmsg.Message.load", return_value=message):
        return msg_to_html(b"\xd0\xcf\x11\xe0")


def test_to_and_cc_are_listed_and_bcc_is_not() -> None:
    page = _page(_message(recipients=(
        _recipient("To Person", "to@x.test", 1),
        _recipient("Cc Person", "cc@x.test", 2),
        _recipient("Hidden", "bcc@x.test", 3),
    ), body="hello"))
    assert "To Person &lt;to@x.test&gt;" in page
    assert "Cc Person &lt;cc@x.test&gt;" in page
    assert "bcc@x.test" not in page and "Hidden" not in page


def test_headers_are_escaped() -> None:
    page = _page(_message(subject="<script>alert(1)</script>", body="x"))
    assert "<script>" not in page
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in page


_ENCAPSULATED = (
    rb"{\rtf1\ansi\ansicpg1252\fromhtml1 \deff0{\fonttbl{\f0\fswiss Arial;}}"
    rb"{\*\htmltag19 <html>}{\*\htmltag50 <body>}"
    rb"\htmlrtf {\htmlrtf0 {\*\htmltag64 <p>}Encapsulated sentinel caf\'e9 \{x\}"
    rb"{\*\htmltag72 </p>}\htmlrtf\par\htmlrtf0}"
    rb"\htmlrtf RTF-ONLY-TEXT\htmlrtf0 "
    rb"{\*\htmltag84 <img src=\"https://tracker.test/pixel.gif\">}"
    rb"{\*\htmltag58 </body>}{\*\htmltag27 </html>}}"
)


def test_the_html_kept_inside_the_rtf_body_is_read_back() -> None:
    assert is_encapsulated_html(_ENCAPSULATED)
    html = deencapsulate_html(_ENCAPSULATED)
    assert "<p>Encapsulated sentinel café {x}</p>" in html
    assert "RTF-ONLY-TEXT" not in html
    page = _page(_message(properties=_Props(binary={0x1009: compressed_rtf.compress(_ENCAPSULATED)}),
                          body="plain fallback"))
    assert "Encapsulated sentinel café" in page
    assert "plain fallback" not in page
    assert "tracker.test" not in page


def test_an_rtf_body_without_html_is_read_as_text_when_there_is_no_plain_body() -> None:
    rtf = rb"{\rtf1\ansi\deff0{\fonttbl{\f0 Arial;}}\f0 RTF body sentinel\par}"
    page = _page(_message(properties=_Props(binary={0x1009: compressed_rtf.compress(rtf)})))
    assert "RTF body sentinel" in page


def test_a_hostile_rtf_body_costs_time_in_proportion_to_its_size() -> None:
    rtf = rb"{\rtf1\fromhtml1 " + rb"{" * 50_000 + rb"\*\htmltag1 x" + rb"}" * 50_000 + rb"}"
    assert deencapsulate_html(rtf) == "x"


@pytest.mark.parametrize(
    "html",
    [
        '<img src="https://tracker.test/a.gif">',
        '<img src="//tracker.test/a.gif">',
        '<img src="&#104;ttps://tracker.test/a.gif">',
        '<img srcset="https://tracker.test/a.gif 2x">',
        '<picture><source srcset="https://tracker.test/a.webp"><img src="data:image/png;base64,AAAA"></picture>',
        '<svg><image href="https://tracker.test/a.svg"/></svg>',
        '<svg><image xlink:href="https://tracker.test/b.svg"/></svg>',
        '<table background="https://tracker.test/bg.png"><tr><td>x</td></tr></table>',
        '<body background="https://tracker.test/bg.png">x</body>',
    ],
)
def test_remote_images_are_removed_from_the_body(html: str) -> None:
    page = _page(_message(html_body=f"<html><body>{html}<p>kept</p></body></html>"))
    assert "tracker.test" not in page
    assert "kept" in page


def test_an_inline_image_is_kept() -> None:
    page = _page(_message(html_body='<p>x</p><img src="data:image/png;base64,AAAA">'))
    assert 'src="data:image/png;base64,AAAA"' in page


def test_attachments_are_listed_by_name_type_and_size_and_not_read() -> None:
    attachment = MagicMock()
    attachment.file_name = "report<1>.pdf"
    attachment.mime_type = "application/pdf"
    attachment.properties = _Props(ints={0x0E20: 2048})
    page = _page(_message(body="x", attachments=(attachment,)))
    assert "<li>report&lt;1&gt;.pdf (application/pdf, 2,048 bytes)</li>" in page
    assert not [c for c in attachment.mock_calls if "file_bytes" in str(c)]


async def _spy_fetches(parse: object) -> list[bool]:
    from app.modules.parsers.image_parser.image_parser import ImageParser

    calls: list[bool] = []

    async def spy(urls: list[str], *_a: object, allow_remote_fetch: bool, **_k: object) -> list[None]:
        calls.append(allow_remote_fetch)
        return [None] * len(urls)

    # The strip is defence in depth: with it bypassed, the remote image reaches the parser, and
    # the parser must still not fetch it.
    with patch.object(ImageParser, "urls_to_base64", side_effect=spy), \
         patch.object(msg_module, "_strip_remote", lambda _soup: None):
        await parse()  # type: ignore[operator]
    return calls


_TRACKED_MAIL = _message(html_body='<p>Hello</p><img src="https://tracker.test/a.gif"><img src="data:image/png;base64,iVBORw0KGgo=">')


async def test_the_parsing_service_never_fetches_a_mail_s_images() -> None:
    from app.modules.parsers.email.msg_parser import MsgParser
    from app.modules.parsers.html_parser.selectolax_html_parser import (
        SelectolaxHtmlParser,
    )

    parser = MsgParser(SelectolaxHtmlParser(allow_remote_fetch=False))
    with patch("oxmsg.Message.load", return_value=_TRACKED_MAIL), \
         patch("app.modules.parsers.parse_pool.run_blocking", side_effect=lambda fn, *a, **_k: fn(*a)):
        calls = await _spy_fetches(lambda: parser.parse(b"\xd0\xcf\x11\xe0", "mail.msg"))
    assert calls and not any(calls)


async def test_the_in_process_pipeline_never_fetches_a_mail_s_images() -> None:
    from app.events.processor import Processor
    from app.modules.parsers.html_parser.selectolax_html_parser import (
        SelectolaxHtmlParser,
    )
    from app.modules.parsers.image_parser.image_parser import ImageParser

    processor = Processor.__new__(Processor)
    processor.logger = MagicMock()
    processor.parsers = {"html": SelectolaxHtmlParser(), "png": ImageParser(MagicMock())}
    processor.graph_provider = AsyncMock()
    processor.graph_provider.get_document = AsyncMock(return_value=None)

    async def run() -> None:
        from app.exceptions.indexing_exceptions import RecordDeletedError

        with pytest.raises(RecordDeletedError):
            async for _ in processor.process_msg_document("mail.msg", "r1", 1, "SMB", "org", b"\xd0\xcf\x11\xe0", "v1"):
                pass

    with patch("oxmsg.Message.load", return_value=_TRACKED_MAIL), \
         patch("app.modules.parsers.parse_pool.run_blocking", side_effect=lambda fn, *a, **_k: fn(*a)):
        calls = await _spy_fetches(run)
    assert calls and not any(calls)


@pytest.mark.parametrize(
    "html",
    [
        '<embed src="https://tracker.test/e.swf">',
        '<object data="https://tracker.test/o.bin"></object>',
        '<iframe src="https://tracker.test/f.html"></iframe>',
        '<frameset><frame src="https://tracker.test/f.html"></frameset>',
        '<div style="background:url(https://tracker.test/s.png)">kept</div>',
        '<div style="background-image: URL( \'//tracker.test/s.png\' )">kept</div>',
        '<style>body{background:url(https://tracker.test/c.png)}</style>',
        '<base href="https://tracker.test/">',
        '<link rel="stylesheet" href="https://tracker.test/s.css">',
    ],
)
def test_embedded_and_styled_remote_content_is_removed(html: str) -> None:
    page = _page(_message(html_body=f"<html><body>{html}<p>kept</p></body></html>"))
    assert "tracker.test" not in page
    assert "kept" in page
