"""A small corpus with known right answers, for testing what search returns.

Three documents, each about something different, written so that the correct
answer to each query below is unambiguous. That is the whole design constraint:
a search-quality test is only worth having if a human would agree, without
argument, which document should come back.

Two properties are deliberate.

**No shared vocabulary between documents.** Each one owns its subject, so a hit
on the wrong document cannot be explained away as the corpus being ambiguous.

**The semantic query shares no content words with its answer.** "paying someone
back for money they spent out of pocket" and "reimbursement ... expense claim"
mean the same thing and have no words in common. A system doing only literal
matching cannot pass that test by accident, which is what makes it a test of
semantic search rather than of string matching.
"""

from __future__ import annotations

from dataclasses import dataclass

# A token that exists nowhere else — not in the corpus, not in the product, not
# in English. Its only possible source is the one document that contains it, so
# a search for it has exactly one defensible answer.
UNIQUE_TOKEN = "zarquon7731"


@dataclass(frozen=True)
class Document:
    slug: str
    filename: str
    body: bytes


EXPENSES = Document(
    slug="expenses",
    filename="expense-reimbursement.md",
    body=b"""# Expense Reimbursement

Staff who buy equipment with their own funds may submit a reimbursement
request. Attach the receipt to the expense claim within thirty days of
purchase. Claims are settled with the next payroll run.

Reimbursement is capped at four hundred pounds per quarter without prior
written approval from a line manager.
""",
)

SERVERS = Document(
    slug="servers",
    filename="server-maintenance.md",
    body=f"""# Server Maintenance Window

Scheduled downtime runs on the first Sunday of each month between 02:00 and
05:00. During the window the database cluster is patched and restarted.

The maintenance runbook is filed under reference {UNIQUE_TOKEN} and must be
followed in order. Do not begin a patch without confirming the standby node is
healthy.
""".encode(),
)

BIRDS = Document(
    slug="birds",
    filename="falcon-telemetry.md",
    body=b"""# Falcon Telemetry

Each bird carries a transmitter weighing under twelve grams. The tracking unit
reports position every four minutes while the falcon is in flight.

Batteries are replaced at the start of each season. A transmitter that stops
reporting for more than a day should be treated as lost.
""",
)

CORPUS = (EXPENSES, SERVERS, BIRDS)
