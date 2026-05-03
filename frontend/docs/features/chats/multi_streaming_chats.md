# Multi-Streaming Chats — Architecture Plan (v2)

Refactor the chat architecture to handle multiple active chats concurrently. The primary change involves overhauling the state management so that chat streams, UI states, and per-conversation data are scoped to specific conversation IDs rather than being global. This guarantees $O(1)$ lookup for chat instances and allows switching between chats without interrupting active streams.

---

## Architecture Decision

### Runtime: `useExternalStoreRuntime` (single instance)

We use **one** `<AssistantRuntimeProvider>` with a single `useExternalStoreRuntime` that reads from whichever conversation is currently active in Zustand.

**Why single runtime, not N runtimes:**
- N `<AssistantRuntimeProvider>` trees = N DOM trees kept alive. A conversation's React tree (~50+ components) is real weight, and a hidden tree that's just tracking Zustand state it won't display provides zero value.
- With a single runtime, switching conversations = update `activeSlotId` in Zustand → runtime reactively reads the new slot's `messages` and `isRunning` → one rerender. Background SSE streams continue writing to their slot in the Zustand dictionary regardless.
- Scroll position is saved per-slot in the store (`savedScrollTop`) and restored after rerender via Loading Tracker.

**Why no `ExternalStoreThreadListAdapter`:**
- `ExternalStoreThreadListAdapter` gives you `ThreadListPrimitive.*` UI components and formalized `onSwitchToThread` callbacks. We don't use assistant-ui's thread list UI — our sidebar is fully custom. The adapter would add a mapping layer between `convId` ↔ adapter `threadId` for no benefit.
- Thread switching is done directly in Zustand: set `activeSlotId` → runtime picks up new slot data. Simpler, same result.

**What assistant-ui handles for us:**
- Reactive message rendering via `useThread()` — components rerender when `messages` changes, not on every keystroke or unrelated store update.
- The runtime protocol: `onNew` callback from `useExternalStoreRuntime` is how we intercept sends and route them to our SSE streaming.
- `onCancel` for abort.
- `useThreadRuntime()` for imperative operations (`append`, `reset`).

> [!IMPORTANT]
> **Single Source of Truth:** Zustand is the single owner of all conversation data (`messages`, `isStreaming`, `streamingContent`, etc.). assistant-ui's `useExternalStoreRuntime` is a read-only reactive bridge — it never copies or caches messages. It reads `messages` and `isRunning` from the active slot on each render. This means there is exactly one source of truth: Zustand.

---

## Rerender Discipline

> [!CAUTION]
> Every store selector, every prop, every `useEffect` dependency must be intentional. Failing here causes the worst kind of bug — things work but feel sluggish and the cause is invisible.

### Principles

1. **Zustand selectors must be narrow.** Never subscribe to the full store. Every component selects only the exact fields it renders.
   ```ts
   // BAD — rerenders on ANY store change
   const store = useChatStore();
   
   // GOOD — rerenders only when this slot's isStreaming changes
   const isStreaming = useChatStore(s => s.slots[s.activeSlotId]?.isStreaming);
   ```

2. **Derived values are computed in selectors, not in components.** If a component needs `messagePairs`, the selector computes and returns them. The selector uses `useShallow` or a custom equality function so that a new array reference isn't created unless the actual data changed.

3. **Sidebar never rerenders from message-area state.** The sidebar subscribes to `conversations`, `sharedConversations`, `activeSlotId`, and `pendingConversation`. It does NOT subscribe to anything inside `slots[x]` (streaming content, messages, status, scroll state). Conversely, the message area never subscribes to sidebar-level state.

4. **Slot updates are scoped.** `updateSlot(slotId, patch)` produces a new `slots` object where only `slots[slotId]` is new. Other slot references are unchanged. This means components subscribed to `slots[otherSlotId]` don't rerender.

5. **Streaming content AND citation updates are batched together.** SSE `onChunk` fires frequently. We accumulate content in a local variable inside the streaming function and flush to the store on a `requestAnimationFrame` cadence (not on every chunk). Citation maps are staged in the same accumulator and flushed in the **same** `updateSlot` call — one store write per frame, not separate writes for content vs. citations.

6. **Citation maps are deduplicated before staging.** The SSE sends the full cumulative citations array on every chunk. Many consecutive chunks carry the same citations data. We `JSON.stringify` the raw `data.citations` array and compare against the previous key — `buildCitationMapsFromStreaming` is only called (and the maps only staged for the next rAF flush) when the key changes. This avoids creating new `CitationMaps` object references that would trigger downstream rerenders via `Object.is` failure.

7. **Spacer recalculation is decoupled from rerenders.** `ResizeObserver` on the last message element handles padding block recalculation — it runs in response to DOM size changes, not React rerenders. Spacer height is set via a `ref` + direct DOM mutation (not state) during streaming; state is synced only on settle.

8. **Background slot flush throttling.** Even though narrow selectors prevent rerenders, every `updateSlot()` call synchronously triggers ALL subscriber selectors across the app. With N background streams at 60 fps each, that’s N×60 store writes/sec × ~30 selectors = thousands of synchronous JS evaluations per second, starving the main thread for the active chat’s scroll tracking (ResizeObserver → executeScroll pipeline). To prevent this, `scheduleFlush()` checks `useChatStore.getState().activeSlotId === slotId` on every call. Active slots flush at 16 ms (~60 fps, unchanged). Inactive (background) slots flush at 200 ms (~5 fps). When the user switches to a background slot, the next `scheduleFlush` call detects the slot is now active and immediately adopts the fast cadence.

9. **Scroll lock grace period.** After the user scrolls to the bottom of a streaming chat (clearing `userScrollOverride`), a 500 ms grace period prevents the lock from re-engaging. This handles the race where streaming content grows `scrollHeight` between the moment the user reaches bottom and the next `throttledResize` → `executeScroll` call (which sets `isAutoScrollingRef`). Without the grace period, trackpad inertia events arriving in that gap would see `distFromBottom > threshold` and wrongly re-lock scroll tracking.

> [!WARNING]
> **Sidebar subscription discipline.** The sidebar components (`sidebar/index.tsx`, `sidebar/chat-sections.tsx`, `sidebar/more-chats-sidebar.tsx`) must use narrow selectors — never `useChatStore()` with destructuring. A `useChatStore()` call without a selector subscribes to the **entire** store object. Since `updateSlot` creates a new `slots` reference on every call (~60/sec during streaming via rAF), a full-store subscriber rerenders on every frame even if it reads zero slot data. The sidebar only needs `conversations`, `sharedConversations`, `pendingConversation`, `isMoreChatsPanelOpen`, and `moreChatsSectionType` — each should be its own narrow selector. Same applies to `ChatContent` in `page.tsx`, which only needs action setters + `previewFile` + `previewMode`.

---

## File Responsibilities

| File | Responsibility |
|------|---------------|
| `store.ts` | Dictionary-based slot tracking, sidebar state, all shared state. Single source of truth. |
| `page.tsx` | Single `<AssistantRuntimeProvider>`, URL ↔ store sync, slot lifecycle (create / init / evict), renders `<MessageList>` and `<ChatInput>`. |
| `runtime.ts` | `buildExternalStoreConfig(slotId)` — returns the `{ messages, isRunning, onNew, onCancel }` config for `useExternalStoreRuntime`. Also exports `loadHistoricalMessages()` for transforming API messages → `ThreadMessageLike[]`. |
| `streaming.ts` (new) | `streamMessageForSlot(slotId, request)` — SSE streaming logic extracted from the old `chatModelAdapter.run()`. Writes to `slots[slotId]` in Zustand. Decoupled from any React component. |
| `api.ts` | Unchanged — SSE transport, API calls. |
| `types.ts` | Updated with `ChatSlot`, `SlotId` types. |

---

## Pseudocode Flow

### Store Structure

```typescript
interface ChatSlot {
  convId: string | null;       // null for new chat, server ID once assigned
  isTemp: boolean;             // true until server assigns a real convId
  isInitialized: boolean;      // true once messages are loaded (or immediately for new chats)
  hasLoaded: boolean;          // true after first successful load — drives tracker selection
  
  // Messages in ThreadMessageLike[] format — the runtime reads these directly
  messages: ThreadMessageLike[];
  
  // Per-slot streaming state (moved from global)
  isStreaming: boolean;
  streamingContent: string;
  streamingQuestion: string;
  currentStatusMessage: StatusMessage | null;
  streamingCitationMaps: CitationMaps | null;
  
  // Per-slot scroll state (persisted across switches)
  userScrollOverride: boolean;
  savedScrollTop: number | null;    // captured on switch-away, restored on switch-in
  
  // Per-slot UI state
  activeTab: ResponseTab;           // 'answer' | 'sources' | 'citations'
  regenerateMessageId: string | null;
  pendingCollections: Array<{ id: string; name: string }>;
}

// Top-level store shape
interface ChatStore {
  // ── Slot dictionary ──
  slots: Record<string, ChatSlot>;   // keyed by slotId (stable UUID)
  activeSlotId: string | null;       // which slot is currently rendered
  
  // ── Sidebar state (global, not per-slot) ──
  conversations: Conversation[];
  sharedConversations: Conversation[];
  isConversationsLoading: boolean;
  conversationsError: string | null;
  pagination: Pagination | null;
  pendingConversation: PendingConversation | null;
  isMoreChatsPanelOpen: boolean;
  moreChatsSectionType: 'shared' | 'your' | null;
  
  // ── URL sync ──
  hasConsumedUrlNavigation: boolean;
  
  // ── Global settings (apply to all chats) ──
  settings: ChatSettings;
  
  // ── File preview (global — only one preview open at a time) ──
  previewFile: ChatPreviewFile | null;
  previewMode: 'sidebar' | 'fullscreen';
  
  // ── Expansion panel (global — applies to the active chat input) ──
  expansionViewMode: 'inline' | 'overlay';
  
  // ── Cache ──
  collectionNamesCache: Record<string, string>;
  
  // ── Slot actions ──
  createSlot: (convId: string | null) => string;          // returns slotId
  updateSlot: (slotId: string, patch: Partial<ChatSlot>) => void;
  setActiveSlot: (slotId: string) => void;
  evictSlot: (slotId: string) => void;
  resolveSlotConvId: (slotId: string, realConvId: string) => void;
  getSlotByConvId: (convId: string) => { slotId: string; slot: ChatSlot } | null;
  
  // ── Sidebar actions (unchanged from v1) ──
  setConversations, moveConversationToTop, addPendingConversation,
  resolvePendingConversation, clearPendingConversation, ...
  
  // ── Other actions ──
  setPreviewFile, clearPreview, setSettings, reset, ...
}
```

**Key design choices:**

1. **`slotId` (stable UUID) is the dictionary key**, not `convId`. A new chat starts with `convId: null`. When the server assigns a real ID, we call `resolveSlotConvId(slotId, realId)` — only the slot's `convId` field changes, the key stays the same. No dictionary re-keying, no React key change.

2. **`getSlotByConvId(convId)`** scans slots (max ~5) to find if a conversation already has an open slot. Used on navigation to avoid duplicate slots.

3. **`activeSlotId`** is the single source of truth for "which chat is shown." The sidebar reads this to highlight the active item. The runtime reads `slots[activeSlotId]` for messages. URL sync reads/writes it.

### Store Helper

```typescript
updateSlot(slotId: string, patch: Partial<ChatSlot>) {
  set(state => ({
    slots: {
      ...state.slots,
      [slotId]: { ...state.slots[slotId], ...patch },
    },
  }));
}
```

Note: Only `slots[slotId]` gets a new reference. All other slot entries keep their existing references. Components subscribed to other slots via narrow selectors do not rerender.

### Flow: USER OPENS A PAGE

ie., when the page opens:

**1. Sidebar**
  a. Checks if `hasConsumedUrlNavigation` is true.
  b. If not, reads the `?conversationId=xxx` URL param.
  c. If a `conversationId` param exists:
     - Calls `getSlotByConvId(conversationId)` — if a slot already exists, set `activeSlotId` to it.
     - Otherwise, calls `createSlot(conversationId)` → returns new `slotId` → sets `activeSlotId`.
  d. Sets `hasConsumedUrlNavigation = true`.
  e. **Bi-directional URL sync** (runs continuously in `page.tsx`):
     - URL changes (sidebar click, browser back) → store adopts new `activeSlotId` (creating slot if needed).
     - Store changes (server assigns `convId`) → router updates URL. `useRef` flags prevent infinite loops.

**2. Main page (`page.tsx`)**
  a. Reads `activeSlotId` from store.
  b. If `activeSlotId` is null → renders New Chat screen.
  c. If `activeSlotId` exists but `slot.isInitialized` is false → renders loader.
  d. If `activeSlotId` exists and `slot.isInitialized` is true → renders `<MessageList>`.
  e. The single `useExternalStoreRuntime` reads from `slots[activeSlotId]`:
     ```ts
     const activeSlot = useChatStore(s => s.activeSlotId ? s.slots[s.activeSlotId] : null);
     
     const runtime = useExternalStoreRuntime({
       messages: activeSlot?.messages ?? [],
       isRunning: activeSlot?.isStreaming ?? false,
       onNew: (msg) => streamMessageForSlot(activeSlotId, msg),
       onCancel: () => cancelStreamForSlot(activeSlotId),
     });
     ```

### Flow: USER SENDS A MESSAGE

1. User types in `<ChatInput>` and hits send.
2. `ChatInput` calls `threadRuntime.append({ role: 'user', content: [...], startRun: true })`.
3. This triggers the runtime's `onNew` callback → `streamMessageForSlot(activeSlotId, msg)`.
4. If the slot has `convId: null` (new chat), the streaming function uses new-conversation API params. Otherwise, uses existing-conversation params.

### Flow: Create / Initialize a Slot

**Triggered by:** navigation (URL param or sidebar click) or "New Chat" button.

1. If navigating to an existing `convId`, check `getSlotByConvId(convId)`:
   - If found → set `activeSlotId` to that slot. Done (reuses existing slot).
   - If not found → `createSlot(convId)` → new slot with `isTemp: false, isInitialized: false`.
2. If starting a new chat → `createSlot(null)` → new slot with `isTemp: true, isInitialized: true, hasLoaded: false`. (New chats are "initialized" immediately — no history to load.)
3. Set `activeSlotId` to the new/found slot.
4. **Initialize non-temp slots** (in a `useEffect` watching `activeSlotId` + `isInitialized`):
   - Fetch conversation history from API.
   - Transform via `loadHistoricalMessages()` → `ThreadMessageLike[]`.
   - `updateSlot(slotId, { messages, isInitialized: true, hasLoaded: true })`.
   - The runtime reactively picks up the new messages.
5. **Eviction check:** If `Object.keys(slots).length > MAX_SLOTS` (e.g. 5), evict the LRU non-active, non-streaming slot:
   - Call `evictSlot(lruSlotId)` → removes from dictionary.
   - Next time the user navigates to that conversation, a new slot is created and history is re-fetched.

### Flow: Stream Message (SSE)

Extracted to `streaming.ts`, decoupled from React:

```typescript
async function streamMessageForSlot(slotId: string, query: string, request: StreamChatRequest) {
  const store = useChatStore.getState();
  const slot = store.slots[slotId];
  if (!slot) return;
  
  // Append user message to slot immediately
  store.updateSlot(slotId, {
    isStreaming: true,
    streamingQuestion: query,
    streamingContent: '',
    messages: [...slot.messages, { role: 'user', content: [{ type: 'text', text: query }] }],
  });
  
  // For new conversations, add pending sidebar entry
  if (slot.isTemp) {
    store.addPendingConversation();
  }
  
  // Batched content + citation accumulator (flush on rAF, not every chunk)
  // Content and citations are flushed in a SINGLE updateSlot call per frame.
  let accumulatedContent = '';
  let pendingCitationMaps = null;
  let lastCitationKey = '';   // JSON.stringify dedup key
  let rafPending = false;
  
  function flushToStore() {
    const patch = { streamingContent: accumulatedContent };
    if (pendingCitationMaps) {
      patch.streamingCitationMaps = pendingCitationMaps;
      pendingCitationMaps = null;
    }
    useChatStore.getState().updateSlot(slotId, patch);
    rafPending = false;
  }
  
  await ChatApi.streamMessage(request, {
    onChunk: (data) => {
      accumulatedContent = data.accumulated;
      // Deduplicate citation maps — SSE sends the full cumulative array
      // on every chunk, so many consecutive chunks are structurally identical.
      // JSON.stringify on raw citations (before buildCitationMapsFromStreaming)
      // avoids both the transform cost AND the new-object-reference issue.
      if (data.citations?.length) {
        const key = JSON.stringify(data.citations);
        if (key !== lastCitationKey) {
          lastCitationKey = key;
          pendingCitationMaps = buildCitationMapsFromStreaming(data.citations);
        }
      }
      if (!rafPending) {
        rafPending = true;
        requestAnimationFrame(flushToStore);
      }
    },
    
    onStatus: (data) => {
      store.updateSlot(slotId, {
        currentStatusMessage: { id: `status-${Date.now()}`, status: data.status, message: data.message, timestamp: new Date().toISOString() },
      });
    },
    
    onComplete: (data) => {
      const botMessage = data.conversation.messages.find(m => m.messageType === 'bot_response');
      const newConvId = data.conversation._id;
      
      // Build finalized messages array from API response
      const finalMessages = loadHistoricalMessages(data.conversation.messages);
      
      store.updateSlot(slotId, {
        isStreaming: false,
        streamingContent: '',
        streamingQuestion: '',
        currentStatusMessage: null,
        streamingCitationMaps: null,
        pendingCollections: [],
        messages: finalMessages,
        hasLoaded: true,
      });
      
      // Resolve temp → real convId
      if (slot.isTemp && newConvId) {
        store.resolveSlotConvId(slotId, newConvId);
        store.resolvePendingConversation(/* build Conversation from data */);
      } else {
        // Existing conversation — move to top of sidebar list
        store.moveConversationToTop(slot.convId!);
      }
    },
    
    onError: (error) => {
      store.updateSlot(slotId, {
        isStreaming: false,
        streamingContent: '',
        streamingQuestion: '',
        currentStatusMessage: null,
        streamingCitationMaps: null,
        pendingCollections: [],
      });
      if (slot.isTemp) {
        store.clearPendingConversation();
      }
    },
    
    signal: abortController.signal,
  });
}
```

### Flow: Regenerate Message

1. Identify the `messageId` to regenerate. Set `updateSlot(slotId, { regenerateMessageId })`.
2. Remove the last assistant message from `slot.messages` in the store.
3. Call `ChatApi.streamRegenerate()` routed through the same `streamMessageForSlot` pattern (slightly different request shape, same streaming logic).
4. On complete, clear `regenerateMessageId`, finalize messages.
5. Streaming Tracker takes over automatically (isStreaming becomes true → UI responds).

### Flow: Switch Conversation

1. **Save outgoing slot state:** `updateSlot(currentSlotId, { savedScrollTop: scrollContainer.scrollTop })`.
2. **Set new active:** `setActiveSlot(newSlotId)`.
3. **Runtime reacts:** `useExternalStoreRuntime` reads `slots[newSlotId].messages` and `isRunning` → one rerender of the message area.
4. **Restore scroll:** After the rerender paint, restore `scrollContainer.scrollTop = slot.savedScrollTop` (or invoke Loading/Streaming Tracker if `savedScrollTop` is null).
5. **Background streaming continues:** If the old slot had `isStreaming: true`, the SSE callback keeps writing to `slots[oldSlotId]` — Zustand accepts the writes, no React component is subscribed to that slot's streaming fields, so zero rerenders happen from background streaming.

---

## UI & UX Rendering

### Plan

**Padding Block**

If the title is snapped to the top of the viewport, and:
  a. the height of the Message Area < (height of viewport - height of ChatInputArea - some buffer height)
     → Insert Padding Block
  b. the height of the Message Area > (height of viewport - height of ChatInputArea - some buffer height)
     → Remove Padding Block

Padding block height is computed via `ResizeObserver` on the last message element, NOT via React state during streaming. During active streaming, spacer height is written directly to the DOM element's `style.minHeight` via a ref. On streaming completion, the final value is synced into React state for the settle rerender.

**Message Area** — Only considered for the latest message in the conversation:

1. **Title**
2. **Message Status Indicator**
    a. If streaming, the line that indicates the current status of the stream
    b. If not streaming, the line that indicates the confidence level
3. **Message Body**
    a. If streaming, the unfinished streaming content
    b. If not streaming, the finished content
4. **Message Action Bar**
    a. If streaming, no action bar
    b. If not streaming, the action bar with the feedback, copy, regenerate, read aloud buttons and the mode and model indicator
5. **Ask More**
    a. If streaming, no ask more
    b. If not streaming, the ask more section

**Tracking User Scroll Position**
1. If user scrolls up, set `userScrollOverride = true` in the active slot.
2. If user scrolls to the bottom, clear `userScrollOverride`.
3. On conversation switch-in (becoming active), reset `userScrollOverride = false`.

**Switch to Active State**
- The runtime reads the new slot's data → one rerender.
- Restore `savedScrollTop` from the slot.
- Re-initialize the appropriate Tracker based on current slot state.

**Tracking Details and cases:**
1. Scroll position
    a. If `!hasLoaded`
        A. Historical conversation → Loading Tracker
        B. New conversation → Nothing needed
    b. If `hasLoaded && !isStreaming` → Loading Tracker
    c. If `hasLoaded && isStreaming` → Streaming Tracker
    d. If `!hasLoaded && isStreaming` → Not Allowed/Possible
2. Bottom padding → Covered under Padding Block section
3. Show the message action bar or not → Covered under Message Area section
4. Show the Ask More section → Covered under Message Area section

**Streaming Tracker**
1. If `userScrollOverride` is true, do not change scroll position.
2. If `userScrollOverride` is false, change scroll position to the bottom of Message Area, and track it.
3. If height of Message Area < (height of viewport - height of ChatInputArea - buffer) → snap the message to the top of the viewport with some padding on top.
4. If height of Message Area > (height of viewport - height of ChatInputArea - buffer) → invoke bottom tracking:
    a. Track the bottom of the Message Area and ensure it's always in view, ending just above the Chat Input Area with some padding/buffer height.
    b. The tracking should be smooth — implemented such that there's no jarring or jittering. Use `requestAnimationFrame` for scroll updates, debounce `ResizeObserver` callbacks.
    c. When the streaming is complete, and the message is replaced with the actual message (finalized from API), the scroll position must be preserved. Because messages are replaced in one atomic `updateSlot`, the rerender happens once and scroll is restored from the DOM's current position.
    d. If Ask More area or Message Action Bar is visible in the Message Area, smoothly scroll to the bottom of the message area.

**Loading Tracker**
1. Only runs when a chat is becoming visible (first load, or switch-in with `savedScrollTop === null`).
2. The objective is that the title of the last Message Area should be snapped to the top of the viewport (with some padding on top).
3. To ensure a smooth transition, scroll to 80% of the way immediately, and the remaining 20% do a smooth animated scroll.
4. If `savedScrollTop` is present (returning to a previously viewed conversation):
   a. If `savedScrollWasStreaming === true` and the slot is **no longer** streaming → the saved position is stale (mid-stream offset). Scroll to `bottom-of-container` with smooth animation so the user sees the completed answer, Ask More, and action bar.
   b. Otherwise → restore that exact position instantly (no animation) and restore `userScrollOverride` from the saved state.

### Other cases

**No Message Area exists**
1. Show the empty state UI — the New Chat screen.

**Switch Away from Active Chat**
1. Save `savedScrollTop` and `savedScrollWasStreaming` into the outgoing slot.
2. Switch `activeSlotId` → runtime rerenders with new data.
3. Background streaming continues writing to the old slot in Zustand — at a reduced flush cadence (200 ms) to avoid main thread contention. Zero rerenders since no component is subscribed to inactive slot fields.

**Regenerating a Response**
1. The last assistant message is removed, streaming begins.
2. The new streaming message automatically becomes the latest Message Area.
3. Streaming Tracker takes over.

### Practical Cases & Handling

1. **New User / New Chat Landing**
   - **Plan Mapping:** `Tracking Details` → 1.a.B (New conversation — Nothing needed). `Other cases` → No Message Area exists.
   - **Coverage:** New Chat screen renders. No tracker needed.

2. **Loading a Historic Chat**
   - **Plan Mapping:** 
     - `Tracking Details` → 1.a.A (Historical conversation → Loading Tracker).
     - `Loading Tracker` → 1, 2, 3 (80% instant jump + 20% smooth scroll to snap title to top).
     - `Message Area` → 1-5 (Renders finished state: content, action bar, Ask More).
     - `Padding Block` rules apply if content is shorter than viewport.
   - **Coverage:** All mapped cleanly.

3. **Active Chat with History (Idle)**
   - **Plan Mapping:**
     - `Tracking Details` → 1.b (`hasLoaded && !isStreaming` → Loading Tracker).
     - `Message Area` → 4.b, 5.b (Action Bar and Ask More visible).
   - **Coverage:** Covered.

4. **Active Chat (Actively Streaming)**
   - **Plan Mapping:**
     - `Tracking Details` → 1.c (`hasLoaded && isStreaming` → Streaming Tracker).
     - `Message Area` → 2.a, 3.a, 4.a, 5.a (Status line, unfinished content, no action bar, no ask more).
     - `Tracking User Scroll Position` → 1, 2 (userScrollOverride flags).
     - `Streaming Tracker` → 1-4 (Obeys override, snaps or tracks bottom, smooth, preserves position on complete).
   - **Coverage:** All mapped comprehensively.

5. **Switched Chat (Actively Streaming in Background)**
   - **Plan Mapping:**
     - `Switch Away` → Save scroll + `savedScrollWasStreaming`, switch `activeSlotId`, background streaming continues in Zustand at reduced flush cadence.
   - **Coverage:** SSE writes to `slots[oldSlotId]` at 200 ms intervals (vs 16 ms for active). No React component is subscribed to inactive slot's streaming state. Near-zero main thread overhead from background streaming. When user switches back, the accumulated content is already in the slot.

6. **Switched Chat (Streaming, Just Switched In)**
   - **Plan Mapping:**
     - `userScrollOverride` → reset to false on switch-in.
     - `Switch to Active State` → runtime reads new slot data, rerenders once.
     - `Tracking Details` → 1.c (triggers Streaming Tracker).
     - `Streaming Tracker` → 2, 4 (scrolls to bottom or snaps as appropriate).
     - Background flush cadence for this slot automatically switches from 200 ms to 16 ms.
   - **Coverage:** The sequence is: restore data → single rerender → Streaming Tracker activates → scroll tracking resumes.

7. **Regenerating a Previous Message**
   - **Plan Mapping:**
     - `Regenerating a Response` → 1, 2 (Message replaced, Streaming Tracker takes over).
     - `Message Area` → 2.a, 3.a, 4.a, 5.a (streaming state).
   - **Coverage:** Covered.

8. **Return to Chat That Completed Streaming While Away**
   - **Plan Mapping:**
     - `Loading Tracker` → 4.a (`savedScrollWasStreaming === true`, slot no longer streaming).
     - `Switch to Active State` → runtime reads finalized messages.
   - **Coverage:** The stale mid-stream `savedScrollTop` is discarded. A smooth scroll to `bottom-of-container` shows the completed answer, Ask More, and action bar — matching where the user would have been if they’d stayed.

9. **Multiple Simultaneous Streaming Chats (Active Chat Scroll Under Load)**
   - **Plan Mapping:**
     - `Rerender Discipline` → 8 (Background slot flush throttling).
     - `Streaming Tracker` → 4.b (smooth tracking without jitter).
     - `Tracking User Scroll Position` + `Rerender Discipline` → 9 (scroll lock grace period).
   - **Coverage:** Background streams flush at 200 ms (not 16 ms), reducing main thread selector evaluations by ~12× per background stream. The active chat’s ResizeObserver → throttledResize → executeScroll pipeline runs unimpeded. Scroll lock has a 500 ms grace period after clearing to prevent the content-growth race from re-locking.

---

## Rerender Audit — Component → Selector Mapping

| Component | Zustand Selectors | Rerenders When |
|-----------|-------------------|----------------|
| **Sidebar** (conversation list) | `conversations`, `sharedConversations`, `pendingConversation`, `activeSlotId` → derive active `convId` | Conversation list changes, active chat switches |
| **Sidebar** (item highlight) | `activeSlotId`, `slots[activeSlotId]?.convId` | Active chat changes only |
| **MessageList** | `slots[activeSlotId]?.messages`, `slots[activeSlotId]?.isStreaming`, `slots[activeSlotId]?.streamingQuestion`, `slots[activeSlotId]?.regenerateMessageId` | Active slot messages change, streaming state toggles |
| **ChatResponse** (single message) | Props only (from MessageList) | Parent rerenders with new props |
| **StatusMessage** | `slots[activeSlotId]?.currentStatusMessage` | Status changes during active stream |
| **ChatInput** | `activeSlotId` (to know where to send), `settings` | Chat switch, settings change |
| **FilePreview** | `previewFile`, `previewMode` | Preview open/close (global, not per-slot) |
| **ExpansionPanel** | `expansionViewMode`, `settings.filters` | User changes settings |

**What does NOT cause rerenders:**
- Background slot streaming updates → no component subscribes to `slots[inactiveSlotId].*`
- `savedScrollTop` changes → imperative DOM operations, not React state
- Spacer height during streaming → direct DOM mutation via ref
- SSE chunk accumulation → batched via rAF, not per-chunk state update
- Duplicate citation data → `JSON.stringify` dedup skips `buildCitationMapsFromStreaming` and avoids staging a new object when the raw citations array hasn't changed
- Citation maps flush → merged into the same rAF `updateSlot` call as `streamingContent`, so no separate store write

**What WILL cause unnecessary rerenders if discipline is broken:**
- `useChatStore()` without a selector (full-store subscription) → rerenders ~60/sec during any streaming because `updateSlot` creates a new top-level `slots` reference on every call. **Sidebar components and ChatContent must use narrow selectors.**
- Selectors that return objects (e.g. `useChatStore(s => s.slots[s.activeSlotId])`) → new slot object on every `updateSlot`. Use primitive selectors or `useShallow`.

---

## TBD

1. **Eviction policy.** LRU eviction when `Object.keys(slots).length > MAX_SLOTS` (e.g. 5). Evict oldest non-active, non-streaming slot. The evicted conversation's data is simply removed; re-navigating to it creates a fresh slot and re-fetches history.
2. **Active tab handling.** `activeTab` is per-slot (already in `ChatSlot`). Switching conversations preserves each conversation's tab state.
3. **User scroll state.** `userScrollOverride` and `savedScrollTop` are per-slot (already in `ChatSlot`). Saves on switch-away, restores on switch-in.