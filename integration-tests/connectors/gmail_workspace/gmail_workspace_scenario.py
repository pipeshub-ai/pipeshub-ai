"""The simulated Workspace the Gmail Workspace suite syncs: gmail_sim.env's users, and mail a reader can count.

Mailbox of the test user ("owner"), after the scenario loads:
  kickoff        INBOX, from admin, 2 attachments (plan.pdf, notes.txt), thread with reply
  kickoff-re     SENT, owner's reply in that thread
  vendor         INBOX, from outside the domain, 1 attachment
  filtered       no INBOX (a filter archived it on arrival)
  old            INBOX, 200 days old (outside a 90-day Received Date filter)
  trashed        in the trash: not synced
  spam           in spam: not synced
Mailbox of the second user ("second"): one message from the owner ("for-second").
"""

from __future__ import annotations

from typing import Any

from connectors.gmail.gmail_env import WorkspaceEnv


def workspace_scenario(env: WorkspaceEnv) -> dict[str, Any]:
    return {
        "domain": env.domain,
        "users": [
            {"key": "admin", "email": env.admin_email, "name": "Workspace Admin", "admin": True},
            {"key": "owner", "email": env.test_user, "name": "Gmail Test User"},
            {"key": "second", "email": env.second_user, "name": "Gmail Second User"},
        ],
        "mail": [
            {"key": "kickoff", "from": "admin", "to": ["owner"], "subject": "IT kickoff", "days_ago": 6,
             "body": "Kickoff notes for the integration test.",
             "attachments": [{"name": "plan.pdf", "mime": "application/pdf", "size": 4096},
                             {"name": "notes.txt", "mime": "text/plain", "size": 256}]},
            {"key": "kickoff-re", "from": "owner", "to": ["admin"], "subject": "Re: IT kickoff",
             "reply_to": "kickoff", "days_ago": 5},
            {"key": "vendor", "from": "ext:billing@partner.example", "to": ["owner"], "subject": "IT invoice",
             "days_ago": 4, "attachments": [{"name": "invoice.pdf", "mime": "application/pdf", "size": 2048}]},
            {"key": "filtered", "from": "ext:alerts@monitor.example", "to": ["owner"], "subject": "IT alert",
             "days_ago": 3, "skip_inbox": ["owner"]},
            {"key": "old", "from": "admin", "to": ["owner"], "subject": "IT old mail", "days_ago": 200},
            {"key": "trashed", "from": "admin", "to": ["owner"], "subject": "IT trashed", "days_ago": 2},
            {"key": "spam", "from": "ext:spam@junk.example", "to": ["owner"], "subject": "IT spam", "days_ago": 2},
            {"key": "for-second", "from": "owner", "to": ["second"], "subject": "IT for second", "days_ago": 1},
        ],
        "ops": [{"op": "trash", "user": "owner", "message": "trashed"},
                {"op": "spam", "user": "owner", "message": "spam"}],
    }


#: Owner messages a correct first sync stores (the sender's SENT copy of for-second included).
OWNER_SYNCED = ("kickoff", "kickoff-re", "vendor", "filtered", "old", "for-second")
OWNER_NOT_SYNCED = ("trashed", "spam")
