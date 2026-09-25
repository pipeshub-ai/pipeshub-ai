"""A small GitLab organisation most behaviour tests start from.

acme                      ops-bot (the connector's token) Owner, alice Reporter
├── acme/web   (id 11)    bob Guest, dave Developer (no public email)
└── acme/platform         carol Developer
    └── acme/platform/api (id 12)

The PipesHub user who set the connector up is owner@example.com; ops-bot is
the GitLab account behind the token.
"""

from __future__ import annotations

from gitlab_server_fake import DEVELOPER, GUEST, OWNER, REPORTER, FakeGitLab

CONNECTOR_ID = "gitlab-conn-1"
CONFIG_PATH = f"/services/connectors/{CONNECTOR_ID}/config"

BOT, ALICE, BOB, CAROL, DAVE = 1, 2, 3, 4, 5
WEB, API = 11, 12

WEB_FILES = {
    "README.md": "# web\n",
    "src/app.py": "print('app')\n",
    "src/util/helpers.py": "def helper():\n    return 1\n",
    ".gitignore": "*.pyc\n",
    "node_modules/left-pad/index.js": "module.exports = 1\n",
}


def build_acme(gitlab: FakeGitLab) -> FakeGitLab:
    gitlab.add_user(BOT, "ops-bot")
    gitlab.add_user(ALICE, "alice", email="alice@example.com")
    gitlab.add_user(BOB, "bob", email="bob@example.com")
    gitlab.add_user(CAROL, "carol", email="carol@example.com")
    gitlab.add_user(DAVE, "dave")
    gitlab.token_for(BOT)
    gitlab.add_group("acme", {BOT: OWNER, ALICE: REPORTER})
    gitlab.add_group("acme/platform", {CAROL: DEVELOPER})
    gitlab.add_project(WEB, "acme/web", members={BOB: GUEST, DAVE: DEVELOPER}, files=WEB_FILES)
    gitlab.add_project(API, "acme/platform/api", files={"main.go": "package main\n"})
    return gitlab


def web_issue_ids(*iids: int) -> set[str]:
    return {str(WEB * 1000 + iid) for iid in iids}


def web_mr_ids(*iids: int) -> set[str]:
    return {str(WEB * 100000 + iid) for iid in iids}


def blob(path: str, project: str = "acme/web") -> str:
    return f"/{project}/-/blob/HEAD/{path}"


def tree(path: str, project: str = "acme/web") -> str:
    return f"/{project}/-/tree/HEAD/{path}"
