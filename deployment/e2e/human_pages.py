"""The pages seed_human.py uploads to a person's test stack, and smoke_human.py reads back."""

from __future__ import annotations

KNOWLEDGE: dict[str, tuple[str, bool, dict[str, str]]] = {
    "kbTeam": ("Team handbook", True, {
        "travel-policy.md": "# Travel policy\n\nBook flights at least 14 days ahead. Economy class for trips under 6 "
                            "hours; premium economy above 6 hours. Hotels up to 180 EUR a night in Berlin and 220 EUR "
                            "in London. Submit receipts in the expense tool within 30 days.\n",
        "on-call-runbook.md": "# On-call runbook\n\nThe on-call engineer answers a page within 15 minutes. Severity 1: "
                              "open an incident channel named inc-<date>-<topic>, page the incident commander, post "
                              "an update every 30 minutes. Hand over at 09:00 Berlin time every Monday.\n",
        "product-faq.md": "# Product FAQ\n\nLumen is Kestrel Logistics' internal codename for the route "
                          "planner. Trip history is kept for 90 days. Exports come as CSV or PDF. "
                          "Support hours are 08:00 to 18:00 CET on weekdays.\n",
    }),
    "kbAdmin": ("Admin-only notes", False, {
        "salary-bands.md": "# Salary bands (confidential)\n\nBand L3: 70k to 85k EUR. Band L4: 85k to 105k EUR. Band "
                           "L5: 105k to 130k EUR. Only org admins may read this page.\n",
    }),
}
