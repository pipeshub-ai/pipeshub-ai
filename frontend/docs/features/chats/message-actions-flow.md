# Message Actions — Architecture

An extension of the multi-streaming chat architecture that handles **Regenerate Response** and **Edit Query** — two flows that let users revise a previous exchange. Both share a single local state primitive in `ChatInput`, route through the command bus, and render inside the existing expansion panel framework.

---

## Overview

| Feature | Trigger | On Submit | API |
|---------|---------|-----------|-----|
| Regenerate Response | ↻ on message action bar | Clears answer → `streamRegenerateForSlot` | Exists |
| Edit Query | ✏ on question heading (hover) | Toast "Coming Soon" | Not yet built |

Both flows follow the same pattern:
1. Trigger dispatches a command bus event.
2. `ChatInput` receives the command, sets `activeMessageAction` local state.
3. The expansion panel area renders a slim `MessageActionIndicator` in place of the textarea.
4. The user presses Enter / Send (or the X dismiss). If confirmed, the action executes.

---

## State: `activeMessageAction`

```typescript
// types.ts
type ActiveMessageAction =
  | null
  | { type: 'regenerate'; messageId: string }
  | { type: 'editQuery'; messageId: string; text: string }

type ModelOverride = {
  modelKey: string;
  modelName: string;
  modelFriendlyName: string;
}
```

Both are component-local in `ChatInput`. Neither lives in Zustand.

```typescript
// chat-input.tsx
const [activeMessageAction, setActiveMessageAction] = useState<ActiveMessageAction>(null);
const [regenModelOverride, setRegenModelOverride] = useState<ModelOverride | null>(null);
```

**Why not in the store?** The indicator is transient UI — dismissed on submit, Escape, or conversation switch. No other component reads it. Putting it in the store would add selector evaluations on every `updateSlot` call (~60/sec during streaming) for state that is invisible to everything outside `ChatInput`.

---

## Command Bus Integration

Triggers live deep in the message tree. `ChatInput` is a sibling tree. The command bus bridges them without prop drilling.

```
MessageActions (action bar)          ChatResponse (question heading)
  handleRegenerate()                    handleEditQuery()
    → dispatch('showRegenBar',            → dispatch('showEditQuery',
        messageId)                            { messageId, text })
         ↓                                         ↓
              useCommandStore (existing pub/sub)
                         ↓
                     ChatInput
          register('showRegenBar', handler)
          register('showEditQuery', handler)
```

`ChatInput` registers both commands on mount and unregisters on unmount:

```typescript
useEffect(() => {
  const { register, unregister } = useCommandStore.getState();
  register('showRegenBar', handleShowRegenBar);
  register('showEditQuery', handleShowEditQuery);
  return () => {
    unregister('showRegenBar');
    unregister('showEditQuery');
  };
}, [handleShowRegenBar, handleShowEditQuery]);
```

---

## Expansion Panel Rendering

`MessageActionIndicator` is a new branch in the existing mutually exclusive if/else chain inside `ChatInput`. The chain determines whether the textarea area shows the textarea, a full-height panel (mode / collections), or the slim action indicator.

```
isModePanelOpen          → QueryModePanel        (380px)
isCollectionsPanelOpen   → ConnectorsPanel       (380px)
activeMessageAction      → MessageActionIndicator (auto / min 40px)
default                  → textarea
```

The `ChatInputExpansionPanel` container accepts optional `height` / `minHeight` props (defaulting to `380px`) so the indicator renders as a slim bar rather than a full-height panel. The bottom toolbar (mode switcher, Quick toggle, collections, mic, send) remains visible in all cases.

### Mutual Exclusivity

Opening mode or collections panels closes other panels visually via the if/else chain, but **does not clear `activeMessageAction` state**. The indicator reappears when the user closes the other panel. This preserves the conversation ID and message ID across transient toolbar interactions (changing model, toggling Quick, opening collections).

Conversely, activating an indicator (`handleShowRegenBar` / `handleShowEditQuery`) calls `setIsModePanelOpen(false)`, `setIsCollectionsPanelOpen(false)`, and `setShowUploadArea(false)` to ensure those panels collapse.

### State Lifetime

| Event | Effect on `activeMessageAction` |
|-------|--------------------------------|
| Indicator X clicked | Cleared |
| Enter / Send | Cleared (action executes) |
| Conversation switch (`activeSlotId` changes) | Cleared via `useEffect([activeSlotId])` |
| Mode / collections / upload panel opened | **Not cleared** — indicator persists, reappears on close |
| Regenerate button clicked again | Replaced with new regenerate action |

---

## `MessageActionIndicator` Component

Location: `expansion-panels/message-actions/message-action-indicator.tsx`

```typescript
interface MessageActionIndicatorProps {
  action: NonNullable<ActiveMessageAction>;
  onDismiss: () => void;
  onSubmit: (editedText?: string) => void;
}
```

**Regenerate variant:** Renders a chip labelled "Regenerate response" with an X dismiss. No textarea.

**Edit Query variant:** Same chip, labelled "Edit Query". Clicking the chip expands an inline textarea pre-filled with `action.text` (the original query). Enter inside the textarea calls `onSubmit(editedText)`.

Both variants are fully self-contained — no store reads, no API calls.

---

## Submit Flow

Two paths converge on `executeMessageAction`:

1. **Send button / Enter on main ChatInput** → `handleSubmit` checks `activeMessageAction` first; if set, calls `executeMessageAction()` and returns early.
2. **Enter inside the Edit Query textarea** → `MessageActionIndicator.onSubmit` → wired to `executeMessageAction`.

```typescript
const executeMessageAction = useCallback((editedText?: string) => {
  if (!activeMessageAction) return;

  if (activeMessageAction.type === 'regenerate') {
    const modelOverride = regenModelOverride ?? undefined;
    setActiveMessageAction(null);
    streamRegenerateForSlot(activeSlotId, activeMessageAction.messageId, modelOverride);
    return;
  }

  if (activeMessageAction.type === 'editQuery') {
    toast.info('Coming Soon', { description: "We're building out support for edit" });
    setActiveMessageAction(null);
    return;
  }
}, [activeMessageAction, regenModelOverride, activeSlotId]);
```

The send button's enabled state accounts for the indicator:

```typescript
const canSubmit = message.trim().length > 0 || uploadedFiles.length > 0 || activeMessageAction !== null;
```

---

## Regenerate: Store & Streaming

`streamRegenerateForSlot` in `streaming.ts` accepts an optional `ModelOverride`. If none is provided, `DEFAULT_MODEL` from `constants.ts` is used.

```typescript
export async function streamRegenerateForSlot(
  slotId: string,
  messageId: string,
  modelOverride?: ModelOverride
): Promise<void>
```

On invocation it immediately writes to the slot:

```typescript
updateSlot(slotId, {
  isStreaming: true,
  regenerateMessageId: messageId,
  streamingContent: '',
  currentStatusMessage: null,
  streamingCitationMaps: null,
  abortController,
});
```

This causes `MessageList` to mark that message pair as `isStreaming: true`. With `streamingContent` starting at `''`, `MessageList` passes `answer: ''` for that pair (old content cleared immediately) and fills in `streamingContent` as chunks arrive.

On `onComplete`, the slot reloads history from the API (`ChatApi.fetchConversation`) and replaces `messages` with the finalized result, clearing all streaming state. The regenerated message is part of `slot.messages` with its final `messageId`, `citationMaps`, `confidence`, and `modelInfo` metadata.

The rAF-batched flush, background slot throttling (200 ms for inactive slots), and citation deduplication from the normal streaming path apply identically.

### `MessageList` Integration

`MessageList` reads `regenerateMessageId` from the active slot:

```typescript
const regenerateMessageId = useChatStore(s =>
  s.activeSlotId ? s.slots[s.activeSlotId]?.regenerateMessageId ?? null : null
);
```

When building `messagePairs`, any pair whose `metadata.messageId === regenerateMessageId` gets:
- `isStreaming: true` — activates the streaming render path in `ChatResponse`
- `citationMaps: EMPTY_CITATION_MAPS` — streaming citations come via the `streamingCitationMaps` prop
- `answer: ''` — old content cleared immediately; `streamingContent` fills in as chunks arrive

No changes to `MessageList`'s selector set. `regenerateMessageId` was already selected.

---

## API: `streamRegenerate`

```
POST /api/v1/conversations/:conversationId/message/:messageId/regenerate
```

Request body:
```json
{
  "modelKey": "...",
  "modelName": "...",
  "modelFriendlyName": "...",
  "chatMode": "quick" | "chat",
  "filters": { "apps": [], "kb": [] }
}
```

`modelKey` / `modelName` / `modelFriendlyName` come from `modelOverride ?? DEFAULT_MODEL`. `chatMode` is derived from `settings.isQuick`. `filters` from `settings.filters`. SSE response shape is identical to `streamMessage` (`connected`, `status`, `answer_chunk`, `complete`, `error`).

---
