"""Documents the named-entity suite indexes, and what each one must yield.

Every value here is chosen so a wrong reading is visible: a sign, a word scale,
an ordinal day, a code that is also an English word, a link carrying
credentials. A nonce in each body keeps re-runs from deduplicating against an
earlier upload.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class NerDocument:
    slug: str
    filename: str
    body: str


def corpus(nonce: str) -> list[NerDocument]:
    return [
        NerDocument(
            slug="contract",
            filename=f"msa-{nonce}.md",
            body=f"""# Master Services Agreement — Acme Robotics and Globex Corporation

This agreement is made on 14 March 2026 between Acme Robotics, Inc. (Berlin, Germany)
and Globex Corp. (Austin, Texas). Ada Lovelace, VP Engineering at Acme Robotics, signs
for Acme; Hiro Tanaka signs for Globex.

The annual fee is $1,250,000.00 payable in Q3 2026, with a 12.5% discount if paid
before 30 June 2026. A late fee of EUR 3,400 applies. The delivered robot arm weighs 45 kg.

Contact ada@acme-robotics.example or visit https://acme-robotics.example/pricing.
Do not store card 4111111111111111 or SSN 123-45-6789.
Reference {nonce}.
""",
        ),
        NerDocument(
            slug="settlement",
            filename=f"settlement-{nonce}.md",
            body=f"""# Vendor settlement — Siemens AG and Infosys Pvt Ltd

Signed on 3rd March 2026 by Jane.Doe@Example.com for Siemens AG and Priya Shah for Infosys Pvt Ltd.
Siemens raised USD 5 million in the round; the India arm budgets ₹5 crore.
A refund of -$742 was issued, and a ($1,180) loss was booked. Margin fell -3% YoY.
Pay $5 100 times. TOP 10 accounts and CAD 3D drawings are listed in the annex.
Portal: https://bob:hunter2@portal.example.com/view?id=77&utm_source=mail&token=s3cr3t#top
Reference {nonce}.
""",
        ),
    ]


# Strings no stored entity may contain: a card, an SSN, and a link's credentials.
SECRETS = ("4111111111111111", "123-45-6789", "hunter2", "s3cr3t", "utm_source")
