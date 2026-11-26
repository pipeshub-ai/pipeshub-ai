# Agent harness: user guide

Checklist item C18.05. This page is for people who chat with agents in PipesHub. It covers runs, approvals and
the approvals inbox, artifacts, repository work, versions, schedules and feedback. Labels in **bold** are the
English labels in the app.

The agent harness is in beta: its pages, its Labs switches and the chat's **New agent harness** switch carry a
**Beta** badge, and none of it appears until an admin turns on Labs → **Agent harness**, which is off by default.

What you see depends on what your admin turned on. Durable runs need Labs → **Agent harness**. Coding engines need the operator to enable engine runs. Schedules need Labs → **Scheduled and Triggered
Agent Runs (Experimental)**, which is off by default. If a control on this page is missing, ask your admin. The
switches are listed in [FLAGS.md](../FLAGS.md).

## Runs

You start a run the way you always chat: send a message. PipesHub decides whether the turn runs on the agent
harness. A durable run keeps going if a server restarts, and picks up where it stopped. When that happens the chat
says: "This task was interrupted (a server restarted) and carried on from where it stopped."

The composer has a **New agent harness** switch ("Durable runs, live timeline and approvals. Turn off to use the
classic chat."). Turn it off to use the classic chat for that conversation.

### Following a run

- The reply shows what the agent did as it works, in plain steps, for example "Used 3 tools" or "Edited 2 files".
  Steps are collapsed; open one to see its result.
- **View activity** opens the **Activity** panel: "What the agent ran in its sandbox. Read-only." It has sections
  for **Plan**, **Commands**, **Steps** and **Files changed**. A command's **Full log** can be downloaded.
- If your connection drops, the chat shows **Reconnecting…** and carries on from the last thing it
  showed.

### Stopping a run

Press **Stop generating** in the composer, or type `/stop`. The reply ends with **Stopped**. A coding task inside a
reply has its own **Stop** button: "Stop this task only; the rest of the reply goes on".

### Coding engines

A coding engine (for example Codex, Claude Code or OpenCode) is a coding agent that works in a sandbox with its own
files and shell. It is used for long work: code, files, many steps.

- Type `/engine` to pick the **Coding engine for this chat**. Choose **None – built-in agent loop** to use no
  engine. Tick **Make this my default** to keep the choice for new chats.
- The chip above the composer shows the engine the next message uses, for example "Codex for this chat". **Use my
  default engine** clears it.
- Your own default is under Workspace → **AI accounts** → **Coding engine**.
- If your admin turned on **Hand long work to the coding engine**, a long or many-step request may move to the
  engine even when you did not pick one.

### Slash commands

Type `/` in the composer for the command menu. It groups commands as **Built-in**, **Your commands**, **Team**,
**Organization** and **Sandbox**. Some useful ones:

| Command | What it does |
|---|---|
| `/new` | Start a new chat |
| `/stop` | Stop the running reply |
| `/resume` | Find an earlier chat |
| `/engine` | Coding engine for your next message |
| `/diff` | Files this chat changed |
| `/undo` | Restore the files from before the last reply |
| `/download` | A file, or everything as .zip |
| `/run` | Run a command, without the model |
| `/repo` | Work on a GitHub repository in this chat |
| `/feedback` | Rate the last answer |

A command that cannot run here is greyed out, with the reason, for example "Sandbox commands need the new harness".

## Approvals and questions

Some tool calls ask you first. By default:

- reading runs without asking;
- a toolset's writes run without asking; deleting data asks;
- an MCP tool that changes data asks, and one that deletes data is blocked;
- code the agent runs in its own sandbox, with no network, runs without asking;
- after the agent has read private data, sending anything out of your organization asks;
- PipesHub's own web search always runs without asking, whatever the agent read: the search goes only to your
  configured search provider;
- opening a web page runs without asking too, and so does following a link on a page it opened. After the agent
  has read private data, it opens only an address that came from a search result, a page it opened, or from you
  (your message, a file you attached, a link to a record it read); an address the agent made up itself is not
  opened, with no card to answer. The step says so, and the agent searches or uses a link it was given instead;
- the web tools never send secrets, never reach private or internal addresses, and what they bring back is marked
  as written outside your organization, so a later send still asks.


### In the chat

When a run needs you, it pauses and shows a card: **Paused, waiting for you**. "The run continues when you decide."

- **Allow once** runs this one call.
- **Allow for this conversation** stops asking for this tool in this chat.
- **Deny** refuses it.

When several calls wait at once, you can decide them together (**Allow all**, **Deny all**) or **Review each**. The
card shows what will be sent before you decide. Once decided, the card keeps a receipt, for example "Allowed once by
you" or "Denied by you · nothing was sent".

The agent can also ask you a question. Answer it in the card, choose **Something else** to type your own answer, or
**Skip**.

### Requests in your Inbox

Your **Inbox** lists the tool calls your tasks are waiting for you to decide and the questions they asked you.
**Review in chat** or **Answer in chat** opens the conversation the request came from.

## Artifacts

Files a run makes (documents, spreadsheets, charts, code) appear under the reply as artifacts. For each one you can
**Preview artifact**, **Download artifact** or **View source code**. When the agent updates a file later in the
chat, the older copy says **Newer version available**.

All your artifacts are on the **All Artifacts** page (marked Beta), in the tabs **All artifacts**, **Mine** and
**Shared with me**. **Open in chat** goes back to the chat that made one; **Share artifact** shares it.

The chat's workspace files are also reachable with `/files`, `/open` and `/download`.

## Working on a repository

This needs a coding engine and a GitHub connection.

1. Choose **Work on a repository** from the composer's **+** menu, or type `/repo`.
2. Pick the **GitHub connection**, the **Repository** and **Start from branch**, then **Use repository**. If you
   have no connection yet, **Connect GitHub** first.
3. Ask for the change.

What happens, as the dialog says:

- PipesHub copies the repository into the chat's workspace before the engine starts.
- The engine works on its own branch, `pipeshub/session-…`, and can push only there.
- It never sees your GitHub token. It cannot push to your branch, tags or other branches.
- A pull request opens into your branch only when you ask for one.

The reply then shows, for example, **Pushed to** a branch, or **Opened pull request** with a **View on GitHub**
link. `/diff` and the Activity panel's **Files changed** show the changes. Only GitHub is supported today.

## Versions

There are two kinds of version.

**Workspace versions** are snapshots of a chat's files. Open **Workspace versions** and choose **Restore** on one:
"Restore version N as a new version? Nothing is deleted." `/undo` restores the files from before the last reply in
the same way. If a task is still writing, try again when it is done.

**Agent versions** belong to the agent's builder. A chat runs the agent's live version, and the reply names it, for
example "Support agent · version 4". Builders publish and roll back versions; see the
[builder guide](./builder-guide.md#versions-publish-and-roll-back).

## Schedules

Workspace → **Schedules** runs an agent "on a schedule or when a webhook is called. Every run acts as you, with your
access checked again each time." It appears when your admin has turned schedules on.

To make one, choose **New schedule** and fill in:

- **Name**, **Agent** and **What to ask** (sent as your message at every run).
- **When it runs**: **On a schedule (cron)**, at most once every 5 minutes, or **When a webhook is called**. You see
  the webhook's address and secret once, after you create it.
- **Time zone**: it starts as your browser's time zone. An unknown zone is refused.
- **If PipesHub was down**: skip the missed runs, run once for the latest, or run once for each (up to 24).
- **If the last run is still going**: skip, wait for it, or run both.
- **Budget per run, USD**: at most 5 USD. Empty means the default.
- **Deadline, hours**: an approval left unanswered until then ends the run. Empty means 8 hours.
- **Tell me when a run ends**: in PipesHub, by email or in Slack.

Each schedule has **Pause**, **Resume** and **Run now**. A run that needs an approval waits for you until its
deadline. A schedule keeps the agent version it runs until you move it to another one.

A schedule's runs answer in the language PipesHub is set to when you make it, unless **What to ask** is written in
another one. A schedule made through the API (`POST /api/v1/agent-harness/schedules`) takes that language from the
request's `Accept-Language` header; without it, each run answers in the language **What to ask** is written in.
There is no organization default language.

## Memories

Agents can remember things about you across chats: a preference you state, or a fact about your work. A memory kept
from a chat that only your organization's own content reached is kept at once; anything else waits for you to
accept it. **Memories** in the workspace sidebar (or `/memory` in a chat) lists them; edit or delete any of them.
Only you see your memories.

**Remember things about me**, at the top of that page, is on by default. Turn it off and your chats neither use nor
keep memories, from your next message; a run already going keeps nothing more. The memories already listed stay until
you delete them. Turn it on again whenever you like.

## Feedback

Under every reply, **Helpful** and **Not helpful** open a short form. Pick what was good or wrong (for example
"Incorrect information" or "Helpful citations"), add a note if you like, and **Submit**. `/feedback` does the same
for the last answer.

On a reply from a durable run, the form also offers **Share this question and answer with your admins to improve
answers**. It is off each time unless you tick it. Answers you rate **Not helpful** and share go to your admins'
list of cases for improving the agent.

## See also

- [Builder guide](./builder-guide.md): for people who build agents.
- [Fork chat](../../fork-chat.md): start a new chat from any message.
