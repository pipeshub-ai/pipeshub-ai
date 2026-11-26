# pyright: ignore-file

"""What the drupal_wiki_sim tests know about the simulator's seed wiki, and the people they need accounts for.

The simulator serves one wiki, built from its ``small`` preset: users 1 to 6, groups 10 to 13, spaces 1 to 5 (content)
and 90 to 94 (one of each special type), pages 101 to 502 and attachments 5000 to 5005. Each test puts it back
(``POST /_sim/reset``) and then changes it with ``POST /_sim/drupalwiki/mutate``. The ids below are the seed's; the ids
of what a test creates come back from the simulator (pages from 1000, attachments from 5000 upward, users 7 and up).
"""

from __future__ import annotations

import os

# The simulator's token, which belongs to user 1.
TOKEN = "pat:dash"

# Seed users: id, email. 5 has no email and 6 is disabled.
WORK, PERSONAL, ALICE, BOB, SVC, DAVE = 1, 2, 3, 4, 5, 6
WORK_EMAIL = "work.account@example.com"
PERSONAL_EMAIL = "personal.account@example.com"
ALICE_EMAIL = "alice@example.com"
BOB_EMAIL = "bob@example.com"
DAVE_EMAIL = "dave@example.com"

# Groups: engineering (work, personal, alice), people-ops (work, bob), leadership (personal), contractors (empty).
ENGINEERING, PEOPLE_OPS, LEADERSHIP, CONTRACTORS = 10, 11, 12, 13

# Spaces. 1 and 4 are AUTHENTICATED, 2 ANONYMOUS, 3 and 5 PRIVATE; 90 to 94 are a user space, the trash, the system
# space, the news drafts and the desktop.
ENG_SPACE, PRODUCT_SPACE, PEOPLE_SPACE, HANDBOOK_SPACE, LEADERSHIP_SPACE = 1, 2, 3, 4, 5
USER_SPACE, TRASH_SPACE, SYSTEM_SPACE, NEWS_DRAFTS_SPACE, DESKTOP_SPACE = 90, 91, 92, 93, 94
CONTENT_SPACES = (ENG_SPACE, PRODUCT_SPACE, PEOPLE_SPACE, HANDBOOK_SPACE, LEADERSHIP_SPACE)

# A few pages: 101 is the root of a tree (101 > 102 > 103 > 107), 201 is shared into space 1, 301 is in People Ops
# with an attachment (5004), 501 and 502 are in Leadership. 105 has two attachments (5000, 5001).
ROOT_PAGE, CHILD_PAGE, SHARED_PAGE, PEOPLE_PAGE, LEADERSHIP_PAGE, TWO_FILES_PAGE = 101, 102, 201, 301, 501, 105

# Accounts the tests create in the wiki. Each also gets a PipesHub account, so what PipesHub grants them can be asked.
NEWBIE_EMAIL = "newbie@example.com"
ERIN_EMAIL = "erin@example.com"
FRANK_EMAIL = "frank@example.com"
# Someone with a PipesHub account and none in the wiki.
OUTSIDER_EMAIL = "outsider@example.com"

# The PipesHub login of the tests themselves (org admin). It reads wiki content through the product only when the wiki
# has an account with this address, so the content tests give it one.
ADMIN_EMAIL = os.getenv("PIPESHUB_TEST_USER_EMAIL", "pipeshub-it@example.com")

# PipesHub accounts, made once per session before any connector so that its first sync maps them.
PEOPLE = [ADMIN_EMAIL, WORK_EMAIL, PERSONAL_EMAIL, ALICE_EMAIL, BOB_EMAIL, DAVE_EMAIL, NEWBIE_EMAIL, ERIN_EMAIL, FRANK_EMAIL,
          OUTSIDER_EMAIL]

READ, EDIT, ADMIN = "space_read", "space_create_edit", "space_administrator"
