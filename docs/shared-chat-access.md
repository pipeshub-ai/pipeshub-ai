# Shared chats and searches: per-viewer access

A chat or search shared with someone is rendered for the person looking at it,
not for its author. The author always sees everything, as before.

**Where it runs.** `getConversationById`, `getAgentConversationById` and
`getSearchById` in `backend/nodejs/apps/src/modules/enterprise_search/controller/es_controller.ts`
collect the record ids of a page of messages (citations, attachments), ask the
query service once which of them the viewer may read, and redact the rest before
building the response (`utils/viewer-redaction.ts`). The check is
`POST /api/v1/chat/records/access` in `backend/python/app/api/routes/chatbot.py`: the same
`check_access` as search and `fetch_record`, signed for the viewer with a scoped
service token. If the check fails, every source is treated as unreadable.

**What a redacted response contains.** No text, name, URL or excerpt of the
hidden source. A redacted citation or attachment is
`{ citationId | recordId, redacted: true, reason: "no_access", requestAccess }`;
a message gets `redaction: { redacted, partial, reason }`. Answer text is cut into
sentences; `[N]` markers map to the citation whose `chunkIndex` is N.

| Sentence | Result |
| --- | --- |
| all its citations unreadable | replaced by `{{redacted}}` |
| some unreadable | kept, unreadable markers removed |
| no citations, answer used unreadable sources | kept; `redaction.partial` shows the "partially based on sources you can't access" marker |
| every citation of the message unreadable | whole body dropped (`redaction.redacted`) |

Tool results, reasoning, `parts` and `referenceData` are dropped for every non-author
viewer (`detailsHidden: true`), since tool output can carry record content that no
citation lets us verify; follow-up questions are dropped on redacted messages. Paraphrase in uncited or mixed sentences can still
reflect unreadable content.

**Every path that returns messages to a non-author** goes through `redactMessagesFor` /
`redactConversationFor` in `utils/viewer-redaction.ts`: the GET reads, the `complete`
frames of add-message (chat and agent) and regenerate, and the history sent to the model
for a write-shared viewer. Error frames carry a conversation only for its author
(`conversationForAuthorOnly`). Content search (`?search=`) matches only chats the searcher
authored, so a match cannot reveal text in a shared chat.

**Requesting access** (`POST /api/v1/access-requests`, `schema/access.request.schema.ts`,
`controller/access_request.controller.ts`). Org-scoped, 20 per user per hour. Kind
and owner come from the graph, never the client.

- Record of a collection (knowledge base): access exists only at the collection (no
  per-record owner, and a direct record grant inside a collection the user cannot enter
  is ignored by `check_access`), so the request is **for the whole collection**. The viewer is told only "the collection that contains this source"; the collection's name, the record name and the owners stay server-side (they appear in the owners' notification and approve dialog). It is
  addressed to the collection's owners, who are all notified; any of them can approve
  (`POST /api/v1/access-requests/:id/approve`, after a confirmation dialog that names the
  collection). Approving makes the requester a READER of the collection
  (`POST /api/v1/chat/records/access-grant`, owner only, never downgrades a member). One
  open request per requester and collection. The viewer sees the source after a refresh.
  Per-record grants inside a collection are not supported.
- Other uploads (chat attachments): the record's own OWNER is asked and a READER edge is
  written on that record.
- Connector record: stored `forwarded`, the owner is notified and `sourceUrl` links to
  the source system. Nothing is ever granted here; access arrives with the next
  permission sync.

**Saved searches.** `GET /api/v1/search` and `GET /api/v1/search/:id` redact a shared search through `redactSearchesFor` (hits and the `records` map).

**UI.** The owner approves or denies from the notification (`frontend/app/(main)/notifications/access-request-actions.tsx`). Viewer side: `frontend/app/(main)/chat/components/message-area/redacted-sources.tsx`
(inline chip for `{{redacted}}`, notice with one Request access row per hidden source).
