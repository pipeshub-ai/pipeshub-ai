# Shared chats and searches: per-viewer access

A chat or search shared with someone is rendered for the person looking at it,
not for its author. A message is shown in full only to the user it was generated for
(see "Write access and turns" below); for a chat that was never written to by anyone
but its owner, that is the owner, as before.

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
a message gets `redaction: { redacted, partial, reason }`.

**Which text goes: fail closed by citation spans** (`redactAnswerText` in `utils/viewer-redaction.ts`;
no language heuristics; D-22 refinement 2). When a turn used **any** source the viewer cannot read, the
viewer sees **only** the spans that have a readable citation; everything else is replaced by the
`{{redacted}}` notice (consecutive notices merge). A turn whose sources the viewer can all read is
returned unchanged.

- A *span* is the text from the end of the previous citation group (or the block start) up to and including the next group, plus punctuation written right after it. A *group* is citations separated only by whitespace, commas or semicolons.
- Group all unreadable: the span becomes a notice. Mixed group: the text stays and only the readable citations are shown. Text after the last group of a block is uncited and becomes a notice.
- Inside one contiguous block (no blank line, heading, table row or fence between lines) a span reaches back across line breaks and list items to the previous group, and ends at every group, so separately cited lines never merge.
- Hard boundaries: blank lines, headings, table cells (a cell keeps only what a readable citation in that cell covers; the separator row stays) and fenced code. **Code blocks are always replaced** in such a turn: markers inside code never count. Tables cited once below them are therefore redacted as a whole.
- Only the canonical form counts as a readable citation: `[N](url)` where N is one of the message's citations and the url is that citation's own record or web URL, outside code. A number that is one of its citations in any other form (bare `[2]`, `[[2]]` even as `[[2]](own url)`, a foreign or wrong URL, text in inline code) ends the span as an **unreadable** group, so the span is redacted. A number that is none of its citations (`arr[9]`) is plain text. A citation with no populated document makes the whole answer hidden.
- A hidden table is never shown with its shape: if no cell keeps visible text, the whole table is one notice. Hidden blocks (paragraphs, tables, code, headings, list items) with nothing visible between them merge into a single `{{redacted}}`, so a viewer learns only that something was hidden.
- Cost, accepted: in mixed-source turns viewers lose uncited connective text, tables and headings that no readable citation covers. Residual: a mixed group in prose keeps its text, and a citation that *leads* its sentence leaves the sentence after it uncited (hence redacted).
- The answer prompts must keep citations after the claim (pinned by `tests/unit/agents/test_citation_placement_prompt.py`).

**Which records a turn drew on.** The query service stores, with every answer, the ids of the
records its turn retrieved or looked at, cited or not (`sourceRecordIds`: prefetched retrieval, search
and fetch tools, listings), plus the attachments of the question it answers (added when Node saves the
answer). Output that no record id names (connector, web, MCP or sub-agent tools, more than 1000
records) adds `*`, which no viewer can read. An empty list means the turn provably used no sources.
Another person's answer is shown only if the stored list exists and the viewer can read every id in it:

- a record in the list the viewer cannot read counts as an unreadable source, cited or not, so the
  fail-closed span rule above applies; with no citation at all the answer is hidden whole
  (`redaction.redacted`, empty content);
- **no stored list** (answers saved before this field, stopped partials Node saved itself): an
  answer without citations is hidden. This is fail-closed and accepted: older uncited answers are
  hidden from everyone but their author, the owner viewing a write-sharer's turn included;
- follow-up questions and every other text derived from the answer go with a hidden answer;
- the list is internal: it is never returned to a viewer (the author's own turn aside).

Error text, the failure reason and the compacted summary of a chat are shown to its owner only
(`addComputedFields`, `buildConversationResponse`, the project conversation list).

Tool results, reasoning, `parts` and `referenceData` are dropped for every viewer who is not the message's user (`detailsHidden: true`), since tool output can carry record content that no
citation lets us verify; follow-up questions are dropped on redacted messages. Paraphrase in uncited or mixed sentences can still
reflect unreadable content.

**Every path that returns messages to a viewer** (the archived list `GET /conversations/show/archives` included) goes through `redactMessagesFor` /
`redactConversationFor` in `utils/viewer-redaction.ts`: the GET reads, the `complete`
frames of add-message (chat and agent) and regenerate, and the history sent to the model
for the requester. Error frames carry a conversation only when every message in it is the
receiver's own (`conversationForAuthorOnly`). Content search (`?search=`) matches only
text from the searcher's own turns (`findSessionIdsMatchingContent`), so a match cannot
reveal text in someone else's answer.

**Write access and turns.** `sharedWith[].accessLevel` is `read` or `write`. A user with
`write` can add messages (`addMessage`, `addMessageStream` and the agent equivalents;
`ownerOrWriterFilter` in `utils/utils.ts`, which uses `$elemMatch` so one entry must carry
both the user and the write level). A reader gets 403 (`assertNotReadOnlyViewer`) before any
stream starts; anyone with no access gets 404.

- **A turn runs as its requester.** The request to the query service carries the
  requester's own token, so retrieval, tools, agent permission and attachment checks use
  their access (the query service takes the user from the token, never from the
  conversation). The project context is loaded for the requester too. The conversation
  stays the owner's (`userId`, `initiator`).
- **`authorUserId`** on each message (`chatSessionMessages`) is the user it was generated
  for: the requester's user message, the answer, the stopped partial answer and the
  question cards of that turn. A message without it belongs to the conversation's initiator,
  so no backfill is needed (`messageAuthorId`). Error messages are not stamped.
- **Redaction is per turn.** `redactMessagesFor` leaves a viewer's own messages as they are,
  tool results and reasoning included, and redacts every other message for the viewer, the
  conversation owner included. The history sent to the model goes through the same call,
  so a requester's model only sees what that requester may read of the other turns.
- **Ordering.** Turns take `seq` from the session's atomic `nextSeq` counter, so two users
  adding at once get distinct, increasing numbers. A conversation runs one answer at a
  time (`reserveRun`): a second send while one is running is refused with a conflict.
- **Regenerate** is for the user a message was generated for only; anyone else (the owner
  included) is refused and nothing changes. The regenerated answer keeps its author.
- **Stop** (cancel) is open to the owner and to writers.

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

**Write-shared UI.** The composer shows for the owner and for `access.accessLevel === 'write'`
(slot `canWrite`); a read-only share still hides it. Other users' turns render with the redaction UI above and the
"Details such as tool results and reasoning are hidden for shared viewers." note. The Regenerate button is shown only on an
answer generated for the signed-in user.
