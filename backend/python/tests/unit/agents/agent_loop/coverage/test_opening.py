"""Which cited drafts open with a limitation or a status ahead of their substance."""

from __future__ import annotations

import pytest

from app.agents.agent_loop.coverage.opening import (
    FramingHit,
    asks_about_status,
    concedes_absence,
    framing_exempt,
    framing_hits,
    limitation_lead,
)

_ARCHIVE_QUESTION = (
    "For the Orchard Lane clinic pilot, can you put together a signed audit archive for the June export, "
    "with per-file checksums and steps to verify it offline?"
)
_CITE = "[1](ref1)"
_BODY = (
    "\n\n**What the archive contains**\n"
    f"- A notarised index for the June window {_CITE}.\n"
    "- Per-file checksums and a resume marker per file [2](ref2).\n"
)


class TestFires:
    def test_the_assistants_own_inability_after_the_record_is_named(self) -> None:
        draft = (
            "**Answer:** The Orchard Lane ticket (HD-5521) describes this archive. It is for site "
            "`orchard-07` in us-east. I can't export or sign the archive myself, and the ticket doesn't show "
            "it was sent. It does say what the archive contains." + _BODY
        )
        assert limitation_lead(_ARCHIVE_QUESTION, draft) == (
            "I can't export or sign the archive myself, and the ticket doesn't show it was sent."
        )

    def test_describe_but_not_hand_over(self) -> None:
        draft = (
            "The matching ticket is Orchard Lane's HD-5521. The export covers June. The ticket defines the "
            "archive below. I can describe what the archive contains, not hand it over." + _BODY
        )
        assert limitation_lead(_ARCHIVE_QUESTION, draft) == "I can describe what the archive contains, not hand it over."

    def test_a_first_sentence_that_leads_with_the_status(self) -> None:
        draft = (
            "The archive is specified in ticket HD-5521, but the records I found don't show it as built or "
            f"sent. The export is `export-june-orchard07` {_CITE}." + _BODY
        )
        assert "don't show it as built or sent" in limitation_lead(_ARCHIVE_QUESTION, draft)

    def test_after_a_leading_heading_and_with_web_urls(self) -> None:
        draft = (
            "# Audit archive\n\nI can't produce the audit archive myself. The ticket HD-5521 specifies it "
            "[1](http://localhost:3000/record/r1/preview#blockIndex=6)."
        )
        assert limitation_lead(_ARCHIVE_QUESTION, draft) == "I can't produce the audit archive myself."


class TestDoesNotFire:
    def test_substance_first_with_the_status_after_it(self) -> None:
        draft = (
            f"The archive is a notarised index plus per-file checksums {_CITE}. The ticket doesn't show it "
            "was sent, and I can't sign it myself." + _BODY
        )
        assert limitation_lead(_ARCHIVE_QUESTION, draft) is None

    def test_a_status_in_a_later_sentence_of_the_opening(self) -> None:
        question = "What change cut the nightly import's peak memory?"
        draft = (
            "The change was a streaming parser, proposed in the data-platform thread. It is not confirmed as "
            f"shipped. The thread ends with a trial run queued {_CITE}."
        )
        assert limitation_lead(question, draft) is None

    def test_a_caveat_about_the_answer_itself_after_its_substance(self) -> None:
        question = "Across all quarterly reviews, which office logged the most open findings?"
        draft = (
            "In the one review that lists findings by office, Leeds logged the most: 4 of its 9 findings. "
            f"I could not produce a reliable tally across every review {_CITE}."
        )
        assert limitation_lead(question, draft) is None

    @pytest.mark.parametrize("draft", [
        "I can't provide the audit archive: the knowledge base has no record of it.",
        "The records don't show the archive as sent, and I can't send it myself.",
    ])
    def test_a_not_found_answer_without_citations(self, draft: str) -> None:
        assert limitation_lead(_ARCHIVE_QUESTION, draft) is None

    def test_a_not_found_lead_with_related_cited_context(self) -> None:
        question = "Which kiosk settings are in the offline preset for the lobby terminals?"
        draft = (
            "The knowledge base doesn't list any kiosk settings for an offline preset, so I can't give "
            f"defaults for them.\n\n**What the guide says:** the offline preset is a temporary display mode {_CITE}."
        )
        assert limitation_lead(question, draft) is None

    @pytest.mark.parametrize("question", [
        "Was the Orchard Lane audit archive sent?",
        "What is the status of the Orchard Lane audit archive?",
        "When will the signed archive for Orchard Lane ship?",
        "Can you check whether the Orchard Lane archive was sent?",
    ])
    def test_a_status_lead_when_the_question_asks_about_status(self, question: str) -> None:
        draft = f"The audit archive has not been sent yet. The vendor's ETA was 2026-05-20 {_CITE}."
        assert asks_about_status(question)
        assert limitation_lead(question, draft) is None

    def test_a_state_the_question_itself_names(self) -> None:
        question = "Why were the invoices not sent to the customer's billing contact?"
        draft = f"The invoices were not sent because the billing contact's address bounced {_CITE}."
        assert limitation_lead(question, draft) is None

    def test_a_quote_in_the_records_is_not_the_assistant(self) -> None:
        question = "What did Halden's finance team call mandatory before signing?"
        draft = (
            "Dual approval on payments was mandatory. The finance lead said, \"We cannot sign without dual "
            f"approval\" {_CITE}."
        )
        assert limitation_lead(question, draft) is None

    def test_the_archive_question_is_not_a_status_question(self) -> None:
        assert not asks_about_status(_ARCHIVE_QUESTION)


def _link(block: int) -> str:
    return f"[{block}](http://localhost:3000/record/7c1f20aa/preview#blockIndex={block})"


_STATUS_LED = f"The audit archive has not been sent yet. The vendor's ETA was 2026-05-20 {_link(1)}."


class TestFramingHits:
    def test_an_inability_in_the_third_sentence(self) -> None:
        draft = (
            "**Answer:** The Orchard Lane ticket (HD-5521) describes this archive. It is for site "
            "`orchard-07` in us-east. I can't export or sign the archive myself, and the ticket doesn't show "
            "it was sent.\n\n"
            f"- **Signed sample:** a notarised index for the whole June window {_link(6)}."
        )
        assert framing_hits(_ARCHIVE_QUESTION, draft) == [FramingHit(
            "opening", "I can't export or sign the archive myself, and the ticket doesn't show it was sent.",
        )]

    def test_no_record_gives_in_the_third_sentence_of_a_bold_lead(self) -> None:
        question = "For the Halden checkout outage, which incident category and response targets should support use?"
        draft = (
            "**Set the category to Payments / Gateway, with the payments team as primary owner. The response "
            "targets depend on the severity declared in the paging tool. No record I found gives a declared "
            "severity for the Halden HD-7731 outage.**\n\n**Category and owner**\n"
            f"- The HD-7731 record notes an internal decision to file it as Payments/Gateway {_link(54)}."
        )
        assert framing_hits(question, draft) == [FramingHit("opening", "No record I found gives")]

    @pytest.mark.parametrize(("lead", "hit"), [
        ("The knowledge base has no single count of findings by office.", "has no single"),
        ("I can't tell which of the two audits you mean.", "can't tell which"),
        ("I could not reconcile the two checklists.", "could not reconcile"),
        ("The sources disagree on the closing date.", "The sources disagree"),
        ("Nothing I found resolves which figure is current.", "Nothing I found resolves"),
    ])
    def test_the_other_opening_families(self, lead: str, hit: str) -> None:
        draft = f"Leeds logged the most open findings. {lead} Leeds has about 9 {_link(1)}."
        assert framing_hits("Which office logged the most open findings?", draft) == [FramingHit("opening", hit)]

    def test_an_opening_pattern_after_the_third_sentence_is_left_alone(self) -> None:
        draft = f"Leeds logged the most. It had 9. York had 8 {_link(1)}. The sources disagree on two findings."
        assert framing_hits("Which office logged the most open findings?", draft) == []

    @pytest.mark.parametrize(("body", "said"), [
        ('Her reply says "done already", which doesn\'t say whether she enabled it on this tenant.',
         "doesn't say whether"),
        ('Marta also asked to say "brief interruption" and "steps we are taking" rather than "downtime".',
         'rather than "'),
        ("Tom's plan added that as a checklist item. I found no record that it was signed off.", "no record that it"),
        ("She said it in the review, not as a direct quote.", "not as a direct quote"),
        ("The guide doesn't use the term burst buffer.", "doesn't use the term"),
    ])
    def test_a_dispute_anywhere_in_the_draft(self, body: str, said: str) -> None:
        draft = (
            "The change was turning on chunked uploads with `upload_chunking=true`. A trial cut timeouts to "
            f"zero {_link(7)}.\n\n**Context.** Priya reported timeouts on large attachments {_link(8)}.\n\n{body}"
        )
        hits = framing_hits("Which setting ended the upload timeouts?", draft)
        assert [hit.kind for hit in hits] == ["dispute"]
        assert said in hits[0].quote and "[" not in hits[0].quote

    @pytest.mark.parametrize("said", [
        "The closest match is the v1 list value `payments_gateway.auth_lag`.",
        "The best match in the records is DOC-311.",
        "The closing date isn't in the records I read.",
    ])
    def test_closest_match_and_isnt_in_the_do_not_fire(self, said: str) -> None:
        draft = f"File it as Payments / Gateway. It is defined as slow card authorisation {_link(96)}. {said}"
        assert not framing_exempt(draft)
        assert framing_hits("What incident subtype should we file?", draft) == []

    @pytest.mark.parametrize("draft", [
        "No record I found gives a declared severity for HD-7731, and I can't tell which runbook applies.",
        f"I couldn't find a declared severity for HD-7731. No record I found gives one {_link(3)}.",
        f"The knowledge base doesn't list a severity for HD-7731. The sources disagree on the owner {_link(3)}.",
        f"There is no record of an offline preset for the kiosks. The guide doesn't say whether one exists {_link(2)}.",
    ])
    def test_a_draft_with_no_citation_or_that_says_the_item_is_absent(self, draft: str) -> None:
        assert framing_exempt(draft)
        assert framing_hits("What severity was declared for HD-7731?", draft) == []

    def test_a_status_question_skips_only_the_status_rule(self) -> None:
        question = "Was the Orchard Lane audit archive sent?"
        assert framing_hits(question, _STATUS_LED) == []
        disputed = f"{_STATUS_LED} The ticket doesn't say whether the archive was signed."
        assert [hit.kind for hit in framing_hits(question, disputed)] == ["dispute"]

    def test_a_status_lead_on_any_other_question_still_fires(self) -> None:
        assert framing_hits(_ARCHIVE_QUESTION, _STATUS_LED) == [
            FramingHit("opening", "The audit archive has not been sent yet."),
        ]


@pytest.mark.parametrize(("draft", "concedes"), [
    (f"**The door policies do not establish a quorum** for badge exceptions {_link(2)}.", True),
    (f"The badge exception owner remains unconfirmed {_link(2)}. Facilities is the likely team.", True),
    (f"Facilities owns badge exceptions {_link(2)}. The policies do not name a deputy.", False),
])
def test_concedes_absence_reads_the_first_sentence(draft: str, concedes: bool) -> None:
    assert concedes_absence(draft) is concedes
