"""Which cited drafts open with a limitation or a status ahead of their substance."""

from __future__ import annotations

import pytest

from app.agents.agent_loop.coverage.opening import asks_about_status, limitation_lead

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
