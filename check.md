Here is a concise map from the repo (Express routes in `es.routes.ts`, client usage in `frontend/app/(main)/chat/api.ts`, `frontend/app/(main)/agents/api.ts`, and chat sidebar / streaming / archived-chats code).

### Scoped agent chat (`/api/v1/agents/:agentKey/...`)

These are the **non-internal** agent **conversation** routes the backend exposes, and whether the **main web app** actually calls them for enterprise-search style agent chat (URL-scoped agent thread, `agentId` on the slot).

| # | Method | Path (after `/api/v1/agents/:agentKey`) | Used in product UI for agent threads |
|---|--------|------------------------------------------|--------------------------------------|
| 1 | POST | `/conversations/stream` | Yes — new agent thread (`ChatApi.streamMessage` with `agentId`, no `conversationId`) |
| 2 | POST | `/conversations/:conversationId/messages/stream` | Yes — follow-up turns (same, with `conversationId`) |
| 3 | GET | `/conversations` | Yes — sidebar + “more chats” (`AgentsApi.fetchAgentConversations`) |
| 4 | GET | `/conversations/:conversationId` | Yes — open thread / reload / share-adapter fetch (`AgentsApi.fetchAgentConversation`, `streaming.ts`) |
| 5 | DELETE | `/conversations/:conversationId` | Yes — delete from sidebar (`AgentsApi.deleteAgentConversation`) |
| 6 | PATCH | `/conversations/:conversationId/title` | Yes — rename (`AgentsApi.renameAgentConversation`) |
| 7 | POST | `/conversations/:conversationId/message/:messageId/regenerate` | Yes — regenerate (`ChatApi.streamAgentRegenerate`) |
| 8 | POST | `/conversations/:conversationId/message/:messageId/feedback` | Yes — thumbs feedback (`ChatApi.submitAgentFeedback`) |
| 9 | POST | `/conversations/:conversationId/archive` | Yes — archive from sidebar (`AgentsApi.archiveAgentConversation`) |
| 10 | POST | `/conversations/:conversationId/unarchive` | Yes — restore from archived-chats workspace (`AgentsApi.restoreAgentConversation`) |
| 11 | GET | `/conversations/show/archives` | Yes — per-agent archived list pagination (`AgentsApi.fetchAgentArchivedConversations` via archived-chats) |

**Same shape as your assistant table:** if you write the base as **`/api/v1/agents/:agentKey`**, the “live” paths mirror assistant chat: **`/conversations/stream`**, **`/conversations/:conversationId/messages/stream`**, **`GET /conversations`**, **`GET /conversations/:conversationId`**, **`DELETE …`**, **`PATCH …/title`**, regenerate, feedback, archive/unarchive, plus **`GET /conversations/show/archives`**. There is **no** agent analogue to **`/:conversationId/share`** / **`unshare`** in this router; the UI even notes agent threads are not shareable (`page.tsx` around the share gating).

### Mounted once under `/api/v1/agents` (no `:agentKey` in path)

| # | Method | Path | Used |
|---|--------|------|------|
| 12 | GET | `/api/v1/agents/conversations/show/archives` | Yes — grouped archived agents view (`AgentsApi.fetchAllAgentsArchivedConversations`) |

### Cross-cutting (assistant route, but used when working with **agent** rows too)

| # | Method | Path | Used |
|---|--------|------|------|
| 13 | GET | `/api/v1/conversations/show/archives/search` | Yes — unified archived search; results include `source: 'agent'` and `agentKey` (`ChatApi.searchArchivedConversations`) |

### Excluded on purpose

- **Internal:** `POST /api/v1/agents/:agentKey/conversations/internal/stream` and `POST .../internal/:conversationId/messages/stream` (scoped service tokens), not the normal browser session.
- **Declared but not used by the web client for chat:** `POST /:agentKey/conversations` and `POST /:agentKey/conversations/:conversationId/messages` (non-stream create / reply) — streaming replaces them for the UI.

### Important distinction: “Universal agent” in the same chat UI

If the user picks **universal agent** mode, the client deliberately keeps using **`POST /api/v1/conversations/stream`** and **`POST /api/v1/conversations/:conversationId/messages/stream`** with an `agent`-style `chatMode` and tools — not the `/api/v1/agents/:agentKey/conversations/...` routes (`runtime.ts` comments around the “Option A” contract). For that mode, your **original assistant conversation table** applies, not the agent-key table above.

Integration tests under `integration-tests/enterprise_search/` currently hit **`/api/v1/conversations/stream`** only, not the scoped agent conversation URLs.