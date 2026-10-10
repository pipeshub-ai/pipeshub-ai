# Collaborative chats

User and admin guide for sharing chats, working in them together, mentioning people, and creating agents from a chat.

Every feature is behind its own platform flag and is **off by default** (except the email switch). With all flags off, chats behave as they did before.

| Flag | Default | What it turns on |
| --- | --- | --- |
| `ENABLE_COLLABORATIVE_CHATS` | off | Sharing with a level (view or continue), people and teams, hand-over, authors on messages, one answer at a time, the update feed, Leave and per-person archive, chat notifications, the access panel. Everything below needs it. |
| `ENABLE_CHAT_MENTIONS` | off | `@` mentions, notes and the respond mode. Needs `ENABLE_COLLABORATIVE_CHATS`. |
| `ENABLE_CHAT_AGENT_BUILDER` | off | The assistant can draft an agent in a chat. |
| `ENABLE_CHAT_SHARE_EMAILS` | **on** | Emails for shares, ownership transfers and (per user) mentions. Only has an effect when `ENABLE_COLLABORATIVE_CHATS` is on and SMTP is configured. |
| `ALLOW_ORG_WIDE_CHAT_WRITE` | off | Lets an owner share a chat with the whole organization at "Can continue". |

## For users

### Sharing a chat

Open **Share chat** (or the **Access** button in the chat header). You can add people and teams of your organization. Each one gets a level:

| Level | What they can do |
| --- | --- |
| **Can view** | Read the whole chat, including earlier answers. Give feedback, archive it for themselves, leave it. |
| **Can continue** | Everything a viewer can, plus send messages, stop a running answer, regenerate an answer **they** asked for, and answer a question the assistant put to them. |
| **Owner** | Everything: add and remove people, change levels and settings, rename, move into a project, delete, transfer ownership. |

Things to know:

- Only the owner manages sharing. An owner can switch on **Editors can invite**: people who can continue may then add more people and teams (never remove anyone or change a level).
- A team must be one you belong to. Adding a team with 50 or more members asks you to confirm. Sharing with the whole organization asks you to confirm and does not notify anyone; at "Can continue" it also needs the administrator flag `ALLOW_ORG_WIDE_CHAT_WRITE`.
- A chat holds at most 200 people and teams, and you can add at most 50 in one request.
- The share dialog says what recipients get: they read the whole chat, and sources they open are checked against **their own** access, not yours (see [What is never shared](#what-is-never-shared)).
- Chats that were shared before collaboration was introduced are **view-only** for their recipients. The migration downgraded every old "continue" share to view; add people again at "Can continue" if you want them to write.
- Removing a person, removing them from a team, deleting a team, or deactivating a user ends their access. Access follows team membership: a change made in the Teams screen applies immediately; a change made outside PipesHub (for example a connector syncing a directory) can take up to a minute.
- **Transfer ownership** makes an editor the owner; you stay on the chat as someone who can continue. If the owner's account is later deactivated, the others can still read the chat but cannot continue it.
- Chats in a project: people who can open the project can open its chats as viewers; the project owner can raise the ceiling to editors in the project settings, and the chat owner can switch an individual chat between "visible to project members" and "only people I shared it with". Moving a chat into or out of a project shows who gains and loses access before you confirm.

### Hand-over

When you share, you can add a **hand-over note** (up to 500 characters). Each direct recipient gets an in-app notification with the first 140 characters of it. It is never put in an email.

Recipients are notified when a chat is shared, when their level changes, when they become the owner, when a chat they took part in is deleted, and (batched) when there is new activity. You can mute a chat from its notification; at most 500 chats can be muted. Emails never contain the chat title or any content, only who did what. In the inbox, a notification shows the chat's current title (cut to 80 characters) and the person's name, looked up when you open the inbox and only for chats you can read.

Email goes only to people added directly (not to teams or the whole organization), and at most one email per person, per type, per chat every 24 hours. Choose what you receive under **Profile > Chat notifications**: email when a chat is shared with you, email when you become the owner, email when you are mentioned (off by default), and in-app notifications for activity and mentions.

### Working in a shared chat

- **Who said what.** Every message shows who sent it, and an answer shows whose access it used ("Asked by Dana, answered using Dana's access"). The assistant acts only on the current sender's request: it does not act on instructions another participant left in the chat.
- **One answer at a time.** While someone's question is running, others see "Dana is asking". A message you send then waits and goes out automatically when the answer finishes (you can cancel it). Anyone who can continue can stop the running answer.
- **New messages while you typed.** If others added messages after what you last saw, your send is held with "Review them, then send again" so you do not answer a stale picture.
- **Updates.** The chat refreshes itself about every 4 seconds while you are active and every 15 seconds when idle. It is not instant.
- **Regenerate** works only on an answer to a question you asked. **Questions from the assistant** (a confirmation card) can be answered only by the person it was put to; others see it read-only.
- **Leave and archive are per person.** Leaving removes your own access (the owner cannot leave); archiving hides the chat for you only. Both leave everyone else's view unchanged.
- **Why can I see this?** The **Access** panel explains how you get access: shared directly, through a team, through a project, or as owner. The chat's owner and organization administrators can also look up another person's access.

### Notes and mentions

Needs `ENABLE_CHAT_MENTIONS`. Type `@` in the composer to mention the assistant, people (anyone in your organization, matched by first, middle or last name or email; people who are not in the chat are marked "Not in this chat"), teams the chat is shared with, or an agent you can use, in any chat. An exact `@handle` you type by hand works too (not inside code). A message can mention one agent. The last row, "Add people to this chat…", opens the share drawer with what you typed (owners and editors who may invite).

- A message that mentions **only people or teams** is a **note**: the AI does not answer, nobody's turn is blocked, and the people mentioned get a notification. A note cannot carry attachments.
- Mention `@assistant` (also `@pipeshub`, `@ai`, `@agent`, `@bot`) to ask the AI something. `@assistant help` shows what the assistant can access for you.
- The owner chooses the **respond mode** for the chat:
  - **smart** (default): the AI answers unless the message mentions only people or teams.
  - **mention only**: the AI answers only messages that mention it; everything else is a note.
  - **always**: the AI answers every message.
- You can mention up to 10 people, teams or agents in a message. The server checks every mention again: a person must be an active member of your organization, a team must be one the chat is shared with. Someone who is not yet on the chat can be added from the prompt if you are allowed to invite.
- `@everyone`, `@here` and `@all` are reserved and do nothing yet.
- A mentioned agent answers that turn with the sender's access; the answer shows the agent's name and avatar ("Agent" if the viewer cannot read it). Service-account agents are not offered in shared chats.
- A new chat can be shared before the first message: use Share on the new-chat page. The people you pick appear above the composer ("Will be shared with…") and are added when the first message is sent.

### Outbound links and addresses

When a chat has several participants, the assistant does not send a link, address or host that only someone else in the chat typed to the outside (fetching a URL, running code, sending an email, calling a connected tool) on your say-so alone. It shows a **confirmation card** instead, and the action goes ahead once you confirm. This also applies when the address is hidden inside a longer link, such as in a query parameter. Hosts you typed yourself, and your own organization's mail domain, need no card. Public webmail addresses (for example gmail.com) always need one if someone else supplied them. You may see an extra card when the assistant quotes a colleague's link in its own summary; confirm, or ask the assistant to leave it out.

### What is never shared

- **Files and tool results.** A file you attach, and what tools returned in your turn, are opened by others only if you allowed it: the composer says "files you add are shared" before you send, and you can tick **Share tool results from this message**. The owner can also turn on **Share my files and tool results** for their own turns. Without consent other people get "not found" for those files, and consent from one person never covers another person's files. Access ends as soon as the person is removed; it is checked at every open, not copied.
- **Documents and knowledge.** Sharing a chat never grants access to the documents behind it. Every citation and source is re-checked for the person opening it.
- **Chat titles and content in emails and stored notifications.** Emails and the stored notification rows carry the actor, level and a link only. The in-app inbox adds the chat title and the person's name when you open it, and only for chats you can still read; if you lost access, or the chat was deleted, the row keeps the generic text. Notifications pushed live show the generic text until the inbox is next loaded.
- **Agent drafts.** Only the person who asked for a draft sees it. Others see "{name} drafted an agent".
- Previously, sharing a chat also gave recipients read access to the owner's files automatically. That no longer happens once collaborative chats is enabled.

### Create an agent from a chat

Needs `ENABLE_CHAT_AGENT_BUILDER`. Ask the default assistant to build an agent: "make me an agent that drafts offers from our price lists". The assistant does not create anything. It shows you a **draft card** in the chat:

- **Name and handle.** The handle (`@offer-drafter`) is how the agent is mentioned. It is unique in your organization; the card checks it as you type and offers the next free one (`@offer-drafter-2`) if it is taken. A few words are reserved (`pipeshub`, `assistant`, `agent`, `ai`, `bot`, `everyone`, `here`, `all`).
- **Description and instructions.** Edit them before you create.
- **Knowledge.** Sources the assistant proposes from what you can already search. They are ticked only when the request came from you. If the draft was shaped by a document or by something another person wrote, the card says "Suggested from document content" and ticks nothing.
- **Tools.** None are ticked. Each one you tick must already be set up and signed in for you; tools that are not appear disabled.

Click **Create**. The agent is **private**: only you can use it. It cannot be a service account, and it cannot be shared from the card; share it afterwards from the agent's settings if you want to. Afterwards open its chat from the card or the confirmation. Type `@` followed by its handle in any chat to use it there. Clicking Create again on the same draft (after a reload, say) returns the agent you already made instead of a second one. The handle stays reserved even if you later delete the agent.

What gets checked when you click Create:

- The draft is yours, in a conversation you can still read. Nobody else can create an agent from your draft.
- Every knowledge source and tool is usable by you at that moment. If you lost access since the draft was shown, Create is refused and the card marks what to untick.

Asking for drafts is limited to 20 per person per day (the assistant tells you when you are over). The agent records that it was created from a chat (the conversation and message ids, never their content) for auditing. Agents created in the agent builder are unchanged: the same access checks run there in **log-only (shadow) mode**, so nothing is refused yet.

## For administrators

### Turning features on

The five flags above are platform feature flags. An admin turns them on or off in **Workspace → Labs → Feature flags** and clicks **Save**. They can also be set through the configuration API (`POST /api/v1/configurationManager/platform/settings` with `featureFlags`); flags you leave out keep their stored values.

Flags are stored once per deployment; there is no per-organisation setting. The API caches flags for 10 seconds; the query service reads them on each request. Turn features on in this order: `ENABLE_COLLABORATIVE_CHATS`, then `ENABLE_CHAT_MENTIONS`, then `ENABLE_CHAT_AGENT_BUILDER`; turn off in the reverse order. Do not enable anything before the API, the Python services and the graph migrations run the same release (see the runbook).

With a flag off: the routes it owns answer `404` (notes, mentionables, collaborators, feed, notification preferences, access explain), the UI hides the feature, and sends ignore `mentions`, `baseSeq` and `clientMessageId`. Data already written stays and is ignored.

### Preferences

Per-user chat notification preferences live in `userNotificationPreferences` (`GET`/`PATCH /api/v1/notifications/preferences`): `email.chatShared` (default on), `email.ownershipTransferred` (on), `email.chatMentioned` (**off**), `inApp.chatActivity` (on), `inApp.chatMentioned` (on), muted chats (max 500) and which tips the user has seen. `ENABLE_CHAT_SHARE_EMAILS` is the organization-wide switch for all chat emails; a failed SMTP status lookup also suppresses the email for that change rather than failing the share.

### Audit log

Every change to who can open a chat is written to the append-only `auditEvents` collection (kept 400 days; there is no admin API yet, so query Mongo by `orgId` and `targetId` = the chat id):

| `action` | When |
| --- | --- |
| `chat.share` | a person or team was added |
| `chat.unshare` | a person or team was removed |
| `chat.accessChange` | a level changed |
| `chat.settingsChange` | editors-can-invite, content sharing or respond mode changed |
| `chat.ownershipTransfer` | ownership moved |
| `chat.leave` | a recipient left |
| `project.chatAccessChanged` | a project's chat ceiling was changed |

Each entry has the actor, target, principal, before/after and the chat's `aclVersion`. Agent creation writes the log line `agent.audit` (action, agent key, user id, `createdVia`, source conversation id; never names or prompts), and the builder's shadow mode writes `agent.access_violation` when an agent created in the builder names knowledge or tools its creator cannot use. Enforcement for the builder is a separate decision taken from that log.

### Limits

| Limit | Value | Constant |
| --- | --- | --- |
| People and teams per chat | 200 (`COLLABORATOR_LIMIT`, 409) | `SHARED_WITH_MAX` |
| People and teams per share request | 50 | `COLLABORATORS_PER_PUT_MAX` |
| Hand-over note | 500 characters (notification shows 140) | `HANDOVER_NOTE_MAX` |
| Mentions per message | 10 | `MENTIONS_MAX` |
| Note text | 10,000 characters | `NOTE_QUERY_MAX` |
| `@` list size | 20 | `MENTIONABLES_LIMIT_MAX` |
| Agent drafts | 20 per user per day (UTC), counted in Redis; if Redis is down the draft is allowed and a warning is logged | `DAILY_DRAFT_LIMIT` |
| Muted chats per user | 500 (`MUTED_SESSIONS_LIMIT`, 409) | `MUTED_SESSIONS_MAX` |
| Participants named to the model | 50 | `MAX_PARTICIPANTS` |
| Team members notified for a team mention or share | 50 | `TEAM_EXPANSION_MAX_MEMBERS` |
| Chat emails | 1 per recipient, type and chat per 24 h; direct recipients only | notification email dispatcher |
| Sharing changes (share, remove, settings, transfer, leave) | 20 per user per minute (`RATE_LIMITED`, 429, `retryAfter`) | `COLLAB_MUTATE_PER_MINUTE` |
| Feed and readiness polls | 120 per user per minute | `COLLAB_FEED_PER_MINUTE` |
| Access explain / preview | 60 per user per minute | `AUTHZ_EXPLAIN_PER_MINUTE` |
| Agent handle checks (`GET /agents/handle-availability`) | 60 per user per minute (`RATE_LIMITED`, 429, `retryAfter`) | `agents:handle` limiter in `es.routes.ts` |
| Run lease | 120 s, renewed every 30 s: a crashed server frees the chat after 2 minutes | `LEASE_TTL_MS`, `HEARTBEAT_INTERVAL_MS` |
| Team membership freshness | immediate for changes made in the Teams screen; up to **60 s** for changes made elsewhere (30 s team-id cache plus 30 s decision cache) | `TEAM_IDS_CACHE_TTL_SECONDS`, `DECISION_CACHE_TTL_SECONDS` |
| Audit retention | 400 days | `AUDIT_RETENTION_DAYS` |

These limits are shared across API replicas through Redis. If Redis is unreachable, each replica counts on its own until it recovers, so a user can briefly get up to N times the limit. The app polls a shared chat about 15 times a minute while its window has focus and about 5 times a minute while it is visible but unfocused, so many open windows stay well under the 120 per minute feed limit; if a user still reaches it, the app backs off and keeps working.

### Errors you will see in support tickets

| Code | Status | Meaning |
| --- | --- | --- |
| `CONVERSATION_NOT_FOUND` | 404 | No access, or no such chat (the same answer on purpose) |
| `CONVERSATION_READ_ONLY` / `CONVERSATION_OWNER_ONLY` | 403 | Caller can read but not do this |
| `CONVERSATION_BUSY` | 409 | Someone else's answer is running (`details` names who and since when) |
| `CONVERSATION_CHANGED` | 409 | Others added messages after what the sender saw |
| `RUN_LOST` | 409 | The answer was interrupted (lease lost); send again |
| `OWNER_INACTIVE` | 403 | The owner's account is disabled or deleted |
| `OWNER_STATUS_UNAVAILABLE` / `TEAM_RESOLUTION_UNAVAILABLE` | 503 | The owner or team lookup failed. These checks **fail closed** and are not cached: retry in a moment |
| `PROJECT_ACCESS_REQUIRED` | 403 | The chat is in a project the sender cannot open |
| `CONNECTOR_SETUP_REQUIRED` | 412 | An agent chat needs tools the sender has not connected |
| `MESSAGE_IS_NOTE` / `MESSAGE_NOT_NOTE` | 422 | A message and the respond mode disagree; use the notes route (or drop the `@assistant`) |
| `MENTION_NOT_ALLOWED`, `MENTION_SA_AGENT_SHARED`, `MENTION_DIRECTORY_UNAVAILABLE` | 400/403/503 | A mention was refused |
| `HANDLE_TAKEN`, `HANDLE_RESERVED`, `INVALID_KNOWLEDGE`, `INVALID_TOOLSET`, `SERVICE_ACCOUNT_NOT_ALLOWED` | 409/400 | Creating an agent from a draft was refused |

The full list is in the API reference (`ConversationErrorCode`).

### Rolling back

Turn flags off in the order `ENABLE_CHAT_AGENT_BUILDER`, `ENABLE_CHAT_MENTIONS`, `ENABLE_COLLABORATIVE_CHATS`. Data is additive and stays. With `ENABLE_CHAT_AGENT_BUILDER` off the assistant stops drafting, existing cards become read-only and creating from a draft returns 404; agents already created stay.
