"""What the Zammad tests seed, shared by the fixtures and the tests.

Tickets are the connector's records; Zammad groups are its record groups.
"""

# The admin the tests create on a fresh instance. Zammad makes the first user
# an admin, and the connector grants access by email, so the address has to be
# the PipesHub test user's (see ``conftest.py``).
ADMIN_PASSWORD = "PipeshubIT!2026"

# Each run's tickets are titled "<run id> <title>", so teardown removes exactly
# this run's and an interrupted run leaves nothing behind for the next one.
SUPPORT_GROUP = "IT Support"
BILLING_GROUP = "Billing"

LAPTOP = ("Laptop will not boot", "It shows a black screen after the update.")
PRINTER = ("Printer jams on duplex", "Paper jams whenever duplex printing is on.")
INVOICE = ("Invoice 4471 is wrong", "We were billed twice for the same seat.")

# The attachment hung off the laptop ticket's article.
ATTACHMENT_NAME = "diagnostics.txt"
ATTACHMENT_BODY = "boot log: firmware handover failed at 03:14"
