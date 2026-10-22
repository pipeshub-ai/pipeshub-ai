# Fork chat

A chat owner can start a new, private chat from any assistant message. The new chat holds a copy of the conversation up to and including that message. After that the two chats are independent: nothing said, edited or deleted in one shows up in the other.

## Entry points

| Chat kind | Route | Scope |
| --- | --- | --- |
| Chat | `POST /api/v1/conversations/:conversationId/message/:messageId/fork` | `conversation:chat` |
| Agent chat | `POST /api/v1/agents/:agentKey/conversations/:conversationId/message/:messageId/fork` | `agent:execute` |

Both routes answer `201` with the new conversation (`id`, `title`, `messageCount`, `forkedFrom`). Both go through `runFork` in `es_controller.ts`; only the lookup of the source chat differs. The UI shows a Fork button on each assistant message (`message-actions.tsx`).

The routes are in `pipeshub-openapi.yaml`. The artifact copy route described below is internal (Node to the connectors service) and is not part of the public API.

## Who can fork

Only the owner. A shared recipient, a project member and a user from another org all get `404`, the same answer as for a chat that does not exist. The reason: a recipient's access to the original's attachments and artifacts is revoked when the owner stops sharing, which would leave a fork pointing at files it can no longer open.

The agent route only forks chats of that agent (`agentKey` must match), and the plain route does not fork agent chats.

## What is written to MongoDB

Code: `utils/fork-conversation.ts`, `copySessionUpToMessage`.

1. A new `chatSessions` document owned by the caller:
   - `title` is `Fork of <original title>`, cut to 200 characters.
   - `isShared: false`. Share state is never copied.
   - `modelInfo` is copied. For an agent chat `agentKey` and `conversationSource: 'agent_chat'` are set.
   - `metadata` holds `forkedFromConversationId` and `forkedFromMessageId`.
   - If the source was in a project and the caller still has viewer access to it, the fork is in that project with `projectVisibility: 'private'`. If access was lost the fork is outside any project.
2. `chatSessionMessages` for every message with `seq` up to the fork point, in order, in batches of `FORK_COPY_BATCH_SIZE` (200) so a long thread is never held in memory whole. Each copy gets a fresh `_id`, `sessionId` and contiguous `seq` from `appendMessages`.

When the deployment has a replica set, the whole fork runs in one transaction. Without one, it is written step by step and cleaned up on failure (see below).

## What is copied, and what is not

| Part of a message | In the fork |
| --- | --- |
| Text, content format, message type, model info, tool calls and other `parts` | Copied |
| Citations | Shared by id: the same `citation` documents are referenced, not duplicated |
| Attachments | Shared by record id. Each chat resolves them from its own messages, and this was checked live in both directions |
| Feedback (`feedback`) | Not copied. The fork starts with no ratings |
| `_id`, `__v`, `id`, `sessionId`, `orgId`, `seq`, `createdAt`, `updatedAt` | Replaced by the fork's own |
| Tool approvals still waiting | Settled as expired in the copy (`approvalAnswer: 'expired'`). The original keeps waiting. A pending approval is tied to its own conversation on the Python side and cannot be answered from another |
| Artifacts | Copied into new artifacts, see below |

## Artifacts

An artifact is reachable by anyone with a permission edge to its record, not by conversation. If the fork kept the original's artifact id it would see the original's later versions and its edits would be written into the original. So the fork copies them.

1. `runFork` allocates the fork's id first. Before anything is written, Node scans the copied range for artifact cards (`::artifact[name](record:ID){mime|docId|recordId|TYPE|version}`) and records the highest version each artifact shows (`fork-artifacts.ts`, `collectArtifactRefs`).
2. Node calls the connectors service, as the caller, with `POST /api/v1/artifacts/copy` (`forkArtifactCopier` in `es_controller.ts`). For each artifact the registry (`ArtifactRegistryService.copy_to_conversation`) creates a new artifact owned by the caller, filed under the fork's conversation id, and copies versions 1 up to that highest version. Later versions stay behind. The route accepts the scopes of the two fork routes (`conversation:chat`, `agent:execute`), so any client that may fork may copy.
3. Only then is the fork's chat written, under the id the artifacts were filed under, and Node rewrites the copied messages: the card (record id, document id, `record:` url, version) and every other occurrence of the old artifact or document id in the text and in `parts`, because a replayed tool call shows those ids to the model (`pointMessageAtCopies`).

The copy runs **before** the MongoDB transaction, not inside it. It uploads every version again and can take minutes, longer than MongoDB lets a transaction live (60 seconds by default), and `withTransaction` runs its callback again on a transient error, which would copy everything a second time. A retried transaction reuses the artifacts already copied.

Because the copies are filed under the fork's conversation id, deleting a chat deletes the artifacts of that chat only: deleting the original leaves the fork's copies, and deleting the fork removes its copies.

Version numbers in the copy normally match the source. When two source versions have identical content the copy keeps one, so a card's version is mapped through the copy's own table.

What is not covered: an artifact that is only named inside a tool call and has no card in any copied message is not copied and stays shared. The copy does not carry a description, because the registry does not store one on create.

## Failure handling

| Situation | Result |
| --- | --- |
| Source chat or message not found, or caller is not the owner | `404`, nothing written |
| An artifact is gone (`not_found`) or no longer readable (`forbidden`) | The fork continues. Its card is left as it was |
| An artifact copy fails for any other reason (`error`), or the connectors service cannot be reached | The fork fails with `500`. No chat was written yet, so there is nothing to remove in MongoDB |
| Message copy fails with a transaction | The transaction rolls back |
| Message copy fails without a transaction | The new messages and the new session are deleted |
| One artifact fails half way through its versions | The half-built copy is marked deleted |
| The fork fails after some artifacts were copied | The copies are removed by asking the connectors service to delete the artifacts filed under the fork's id (the same call that deleting a chat makes) |

The artifact copy is attempted once. A retry after a timeout would leave a second, unused copy.

The removal of copies is best effort: if it fails, the original error is still the one returned, and the failure is logged. The copies then stay behind unreferenced. That also happens when the caller's token may fork but may not delete (that call needs `conversation:write` or `agent:write`).

## Tests

- Node unit: `tests/modules/enterprise_search/utils/fork-conversation.test.ts`, `fork-artifacts.test.ts`, `controller/fork-artifact-copier.test.ts`, `controller/fork-artifact-flow.test.ts` (copy before the chat is written, removal of copies on failure), `controller/es_controller.ownership.test.ts`.
- Node against a real MongoDB (skipped unless `FORK_TEST_MONGO_URI` is set; run once with a replica set and `REPLICA_SET_AVAILABLE=true`, once standalone): `fork-conversation.mongo.test.ts`, `fork-conversation.history.mongo.test.ts` (what the model is shown after a fork).
- Python: `tests/unit/services/artifact_registry/test_copy_to_conversation.py` (including deleting the original or the fork), `tests/unit/connectors/api/test_artifacts_router_copy.py` (including which scopes may call it).
- Frontend: `message-actions-fork.test.tsx`.

## Not verified

- Forking an agent chat that contains artifacts has not been run end to end; the Agents UI was not available in the build used for testing.
- The removal of copies after a failed fork is covered by tests with the delete call stubbed; it has not been run against a live connectors service.
- Citations are shared by id. Deleting either chat soft-deletes them (existing delete behaviour). How the other chat's citation panel renders afterwards has not been checked.
