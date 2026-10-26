"""The date a document states for itself, from the dates named-entity extraction found."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.models.blocks import Block, BlockSubType, BlockType, DataFormat
from app.modules.named_entities.document_dates import DocumentDates, find_document_dates
from app.modules.named_entities.domain.kinds import EntityKind
from app.modules.named_entities.extractor import (
    NamedEntityExtractor,
    NamedEntityRequest,
)

UPLOADED = 1792090099000  # 2026-10-15 18:48:19 UTC
_DATE_KINDS = frozenset({EntityKind.DATE, EntityKind.DATE_TIME, EntityKind.DATE_RANGE, EntityKind.DURATION})


def _day(year: int, month: int, day: int) -> int:
    return int(datetime(year, month, day, tzinfo=UTC).timestamp() * 1000)


def _blocks(*texts: str, table_rows: tuple[int, ...] = (), code: tuple[int, ...] = ()) -> list[Block]:
    return [
        Block(index=i, type=BlockType.TABLE_ROW, data={"row_natural_language_text": text})
        if i in table_rows
        else Block(index=i, type=BlockType.TEXT, sub_type=BlockSubType.CODE, format=DataFormat.CODE, data=text)
        if i in code
        else Block(index=i, type=BlockType.TEXT, data=text)
        for i, text in enumerate(texts)
    ]


async def _dates(
    *texts: str, uploaded_ms: int | None = UPLOADED, table_rows: tuple[int, ...] = (), code: tuple[int, ...] = (),
) -> DocumentDates | None:
    blocks = _blocks(*texts, table_rows=table_rows, code=code)
    # Value kinds only, so the deterministic pass runs and no model is asked.
    extraction = await NamedEntityExtractor().extract(NamedEntityRequest(blocks=blocks, enabled=_DATE_KINDS))
    return find_document_dates(extraction, blocks, uploaded_ms=uploaded_ms)


class TestHeaderDates:
    @pytest.mark.asyncio
    async def test_an_emails_date_line_dates_it(self) -> None:
        dates = await _dates(
            "Fwd: Question about token accounting discrepancy",
            "From: FinOps <finops@windward.ai>\nTo: Kimberly Park <kim@redwood.com>\n"
            "Date: Tue, Feb 11, 2025 at 8:04 AM\nSubject: Question about token accounting",
            "Hi Kimberly, the March 30, 2025 invoice does not match.",
        )
        assert dates == DocumentDates(created_ms=_day(2025, 2, 11), modified_ms=_day(2025, 2, 11))

    @pytest.mark.asyncio
    async def test_a_reply_in_the_thread_is_its_last_change(self) -> None:
        dates = await _dates(
            "Re: SLA redlines",
            "From: Elaine <elaine@trident.com>\nDate: Wed, 18 Nov 2026 09:32:10 -0800\nSubject: SLA redlines",
            "Michael, attached are our redlines.",
            "From: Michael <michael@redwood.com>\nDate: Fri, 20 Nov 2026 10:00:00 -0800\nSubject: Re: SLA redlines",
            "Thanks, reviewing now.",
            uploaded_ms=_day(2027, 1, 5),
        )
        assert dates == DocumentDates(created_ms=_day(2026, 11, 18), modified_ms=_day(2026, 11, 20))

    @pytest.mark.asyncio
    async def test_a_thread_last_changed_with_its_latest_message_however_far_down(self) -> None:
        body = "Thanks for the call; notes on the rollout follow in detail. " * 40
        dates = await _dates(
            "Re: Greenfield follow-up",
            "From: Evelyn <evelyn@redwood.com>\nDate: Thu, 5 Feb 2026 15:12:00 -0800\nSubject: follow-up",
            body,
            "From: Timothy <tim@greenfield.com>\nDate: Thu, 12 Feb 2026 11:03:00 -0800\nSubject: Re: follow-up",
            "Sounds good.",
        )
        assert dates == DocumentDates(created_ms=_day(2026, 2, 5), modified_ms=_day(2026, 2, 12))

    @pytest.mark.asyncio
    async def test_a_line_break_left_as_a_literal_backslash_n_still_breaks_the_line(self) -> None:
        dates = await _dates(
            "Disputed payouts",
            "From: AP <ap@acme.com>\\nTo: Marissa <m@redwood.com>\\nDate: 2026-05-03T09:14:00-07:00\\nSubject: VAT",
        )
        assert dates == DocumentDates(created_ms=_day(2026, 5, 3), modified_ms=_day(2026, 5, 3))

    @pytest.mark.asyncio
    async def test_a_meeting_header_dates_the_notes(self) -> None:
        dates = await _dates(
            "Orbital Networks — GTM co-sell sprint",
            "Meeting header: 2026-12-20 15:00 UTC | Duration: ~62 minutes | Record: Yes\nAttendees: Alex, Priya",
            uploaded_ms=_day(2027, 1, 5),
        )
        assert dates == DocumentDates(created_ms=_day(2026, 12, 20), modified_ms=_day(2026, 12, 20))

    @pytest.mark.asyncio
    async def test_front_matter_gives_created_and_last_updated(self) -> None:
        dates = await _dates(
            "Rollout Orchestrator Service",
            "Created: March 3, 2025\nLast updated: 12 June 2025\nOwner: Platform",
            "The orchestrator owns the rollout state machine.",
        )
        assert dates == DocumentDates(created_ms=_day(2025, 3, 3), modified_ms=_day(2025, 6, 12))

    @pytest.mark.asyncio
    async def test_only_a_last_updated_line_dates_both(self) -> None:
        dates = await _dates("Runbook", "Last updated: 2025-06-12", "Steps follow.")
        assert dates == DocumentDates(created_ms=_day(2025, 6, 12), modified_ms=_day(2025, 6, 12))


class TestNoDocumentDate:
    @pytest.mark.asyncio
    async def test_a_mentioned_date_is_not_the_documents(self) -> None:
        assert await _dates(
            "Postmortem: Latency regression after tokenizer update (2026-01-27)",
            "On 2026-01-27 we observed a significant p95 latency increase.",
            "The fix ships before the March 3, 2026 deadline.",
        ) is None

    @pytest.mark.asyncio
    @pytest.mark.parametrize("label", ["Due date", "Effective date", "Release date", "Next review date", "Start date"])
    async def test_a_label_naming_another_event_is_not_the_documents(self, label: str) -> None:
        assert await _dates("Policy", f"{label}: 2025-03-03", "Body.") is None

    @pytest.mark.asyncio
    async def test_two_days_for_one_header_are_ambiguous(self) -> None:
        assert await _dates("Schedule", "Date: 2025-03-01\nDate: 2025-04-01", "Body.") is None

    @pytest.mark.asyncio
    async def test_a_date_without_a_year_is_ambiguous(self) -> None:
        assert await _dates("Notes", "Date: Feb 11", "Body.") is None

    @pytest.mark.asyncio
    async def test_a_date_after_the_upload_is_not_believed(self) -> None:
        assert await _dates("Agenda", "Date: 2027-03-01", "Body.") is None

    @pytest.mark.asyncio
    async def test_a_label_below_the_documents_opening_does_not_count(self) -> None:
        filler = "A paragraph of body text that says nothing about when it was written. " * 30
        assert await _dates("Report", filler, "Date: 2025-03-03") is None

    @pytest.mark.asyncio
    async def test_a_table_row_is_no_header(self) -> None:
        assert await _dates("Ledger", "Date: 2025-03-03, Amount: 500", table_rows=(1,)) is None

    @pytest.mark.asyncio
    async def test_a_message_header_below_the_opening_does_not_date_it_alone(self) -> None:
        filler = "Background on the migration and why it matters to the team. " * 30
        forwarded = "From: Ana <ana@redwood.com>\nDate: Mon, 3 Mar 2025 09:00:00 -0800\nSubject: plan"
        assert await _dates("Migration notes", filler, forwarded) is None

    @pytest.mark.asyncio
    async def test_a_date_header_in_quoted_output_is_not_the_documents(self) -> None:
        assert await _dates(
            "support",
            "mira: Customer hitting 429s. Headers below.",
            "mira: ```\nHTTP/1.1 429 Too Many Requests\nDate: Tue, 14 May 2024 08:19:02 GMT\n```",
        ) is None

    @pytest.mark.asyncio
    async def test_a_date_header_in_a_code_listing_is_not_the_documents(self) -> None:
        assert await _dates(
            "support",
            "sara: Log excerpt:",
            "HTTP/1.1 429 Too Many Requests\nDate: Tue, 23 May 2025 03:04:12 GMT\nX-RateLimit-Limit: 60",
            code=(2,),
        ) is None

    @pytest.mark.asyncio
    async def test_a_relative_date_is_not_the_documents(self) -> None:
        assert await _dates("Standup", "Date: next Monday", "Body.") is None
