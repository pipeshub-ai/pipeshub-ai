'use client';

import { useCallback, useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useThreadRuntime } from '@assistant-ui/react';
import { ChatInput } from '../chat-input';
import type { MentionRef } from '../composer/composer-input.types';
import { useChatStore, ctxKeyFromAgent } from '@/chat/store';
import { useEffectiveAgentId } from '@/chat/hooks/use-effective-agent-id';
import { fetchModelsForContext } from '@/chat/utils/fetch-models-for-context';
import { ChatApi } from '@/chat/api';
import {
  buildAssistantApiFilters,
  type AttachmentRef,
  type ChatCollectionAttachment,
  type SearchRequest,
} from '@/chat/types';
import {
  isRequestCancelledError,
  isSearchNoAccessibleDocumentsNotFound,
} from '@/lib/api';
import { useServicesHealthStore } from '@/lib/store/services-health-store';
import { useCollabMessageContext } from '@/chat/hooks/use-collab-message-context';
import { AudienceNotice } from './audience-notice';
import { NonParticipantPrompt, type NonParticipant } from './non-participant-prompt';
import { useFeatureFlagsStore, selectChatMentionsEnabled } from '@/lib/store/feature-flags-store';
import { CollaborationApi } from '@/chat/collaboration-api';
import type { CollaboratorAccessLevel } from '@/chat/collaboration-types';
import { classifyResponder } from '@/chat/mentions/classify';
import { MentionsApi } from '@/chat/mentions/api';
import { useChatParticipants } from '@/chat/mentions/use-chat-participants';
import { useParticipantsStore, labelOfMention } from '@/chat/mentions/participants-store';
import { newClientMessageId } from '@/chat/utils/collab-send-fields';
import { refreshFeedForSlot } from '@/chat/utils/collab-send';
import { conversationErrorMessage } from '@/chat/utils/conversation-errors';
import { toast } from '@/lib/store/toast-store';
import { useUserStore } from '@/lib/store/user-store';
import { useSendCoachmarks } from './use-send-coachmarks';

// Module-level abort controller for cancelling in-flight searches
let currentSearchAbort: AbortController | null = null;
/** Increments on each submit so superseded requests never clear loading for a newer search. */
let searchSubmitGeneration = 0;
let lastEffectiveAgentIdForQueryMode: string | null = null;

/**
 * Wrapper component that connects ChatInput to assistant-ui runtime.
 * Must be used inside AssistantRuntimeProvider.
 */
export function ChatInputWrapper() {
  const threadRuntime = useThreadRuntime();
  const effectiveAgentId = useEffectiveAgentId();
  const isAgentChat = Boolean(effectiveAgentId);
  const { collabActive } = useCollabMessageContext();
  const { t } = useTranslation();
  const mentionsEnabled = useFeatureFlagsStore(selectChatMentionsEnabled);
  const participants = useChatParticipants(mentionsEnabled);
  const [outsiders, setOutsiders] = useState<NonParticipant[]>([]);
  const [adding, setAdding] = useState(false);
  const [shareToolResults, setShareToolResults] = useState(false);
  const participantCount = useChatStore((s) => {
    const convId = s.activeSlotId ? s.slots[s.activeSlotId]?.convId : null;
    if (!convId) return undefined;
    const row = [...s.conversations, ...s.sharedConversations].find((c) => c.id === convId);
    return row?.collaboratorCount !== undefined ? row.collaboratorCount + 1 : undefined;
  });

  // The busy banner (someone else's run, a queued send, the "new messages" notice) sits where the tip would; it goes first.
  const busyBannerShown = useChatStore((s) => {
    const slot = s.activeSlotId ? s.slots[s.activeSlotId] : undefined;
    if (!slot || slot.accessLost) return false;
    return Boolean(slot.queuedSend || slot.changedNotice || (slot.activeRun && !slot.isStreaming));
  });

  const sendTips = useSendCoachmarks({
    enabled: mentionsEnabled,
    shared: collabActive,
    participantCount,
    respondMode: participants.respondMode,
    sessionKind: isAgentChat ? 'agent' : 'chat',
    paused: outsiders.length > 0 || busyBannerShown,
  });

  useEffect(() => {
    if (effectiveAgentId) {
      lastEffectiveAgentIdForQueryMode = effectiveAgentId;
      const store = useChatStore.getState();
      store.setQueryMode('agent');
      if (store.settings.mode === 'search') {
        store.setMode('chat');
        store.clearSearchResults();
      }
      return;
    }

    if (lastEffectiveAgentIdForQueryMode !== null) {
      lastEffectiveAgentIdForQueryMode = null;
      useChatStore.getState().setQueryMode('agent');
    }
  }, [effectiveAgentId]);

  // Make sure models for the EFFECTIVE context (URL or slot agent) are loaded
  // and validated, regardless of which URL the page was opened on. This keeps
  // the pill + submit in sync when the active slot carries an agent that the
  // URL doesn't reflect (e.g. navigating into an existing agent conversation).
  // The fetch util dedupes so this is cheap when page.tsx already ran.
  useEffect(() => {
    const ctxKey = ctxKeyFromAgent(effectiveAgentId);
    fetchModelsForContext(ctxKey).catch((err) => {
      if (useServicesHealthStore.getState().apiServerReachable) {
        console.error('Failed to fetch models for effective context', ctxKey, err);
      }
    });
  }, [effectiveAgentId]);

  const handleSearchSubmit = async (query: string) => {
    const store = useChatStore.getState();

    // Cancel any in-flight search
    if (currentSearchAbort) {
      currentSearchAbort.abort();
    }
    const myGeneration = ++searchSubmitGeneration;
    const searchController = new AbortController();
    currentSearchAbort = searchController;

    store.setIsSearching(true);
    store.setSearchError(null);

    const streamFilters = buildAssistantApiFilters(store.settings.filters);
    const request: SearchRequest = {
      query,
      limit: 10,
      filters: {
        apps: streamFilters.apps,
        kb: streamFilters.kb,
      },
    };

    try {
      const response = await ChatApi.search(request, searchController.signal);
      store.setSearchResults(
        response.searchResponse.searchResults,
        response.searchId,
        query
      );
    } catch (error: unknown) {
      if (isRequestCancelledError(error)) return;
      if (isSearchNoAccessibleDocumentsNotFound(error)) {
        store.setSearchResults([], null, query);
        return;
      }
      store.setSearchError((error as Error)?.message || 'Search failed');
    } finally {
      if (currentSearchAbort === searchController) {
        currentSearchAbort = null;
      }
      if (myGeneration === searchSubmitGeneration) {
        store.setIsSearching(false);
      }
    }
  };

  /**
   * Per-file upload, fired by `ChatInput` the moment a chip is added to the
   * composer. Wraps the batch-upload endpoint with a single file in the
   * FormData so we don't need a new backend route. `signal` is forwarded so
   * `ChatInput` can abort when the user removes a chip mid-flight.
   */
  const handleUploadFile = useCallback(
    async (file: File, signal: AbortSignal): Promise<AttachmentRef> => {
      const store = useChatStore.getState();
      const slot = store.activeSlotId ? store.slots[store.activeSlotId] : null;
      const refs = await ChatApi.uploadAttachments([file], {
        agentId: effectiveAgentId,
        conversationId: slot?.convId ?? null,
        signal,
      });
      const ref = refs[0];
      if (!ref) throw new Error("The upload didn't finish. Please attach the file again.");
      return ref;
    },
    [effectiveAgentId],
  );

  const handleDeleteFile = useCallback(
    (recordId: string) => {
      // Fire and forget — must never block the UI.
      ChatApi.deleteAttachment(recordId, { agentId: effectiveAgentId }).catch(() => {
        // Swallow silently: an orphan record is acceptable; blocking the UI is not.
      });
    },
    [effectiveAgentId],
  );

  const postNote = async (message: string, attachments: AttachmentRef[] | undefined, mentions: MentionRef[]): Promise<boolean> => {
    const { ref } = participants;
    const slotId = useChatStore.getState().activeSlotId;
    if (!ref || !slotId) return false;
    const restore = () => useChatStore.getState().updateSlot(slotId, { composerRestore: message });
    if (attachments && attachments.length > 0) {
      toast.error(
        t('chat.mentions.note.noAttachments', {
          defaultValue: 'A note cannot carry attachments. Mention @assistant to ask about a file.',
        }),
      );
      restore();
      return false;
    }
    try {
      const outcome = await MentionsApi.postNote(ref, {
        query: message.trim(),
        mentions,
        clientMessageId: newClientMessageId(),
      });
      await refreshFeedForSlot(slotId);
      const labels = useParticipantsStore.getState().labels;
      setOutsiders(
        outcome.nonParticipants.map((userId) => ({
          userId,
          name: labelOfMention(labels, { type: 'user', id: userId }) ?? '',
        })),
      );
      return true;
    } catch (error) {
      toast.error(conversationErrorMessage(t, error));
      restore();
      return false;
    }
  };

  const handleAddOutsider = async (person: NonParticipant, level: CollaboratorAccessLevel) => {
    if (!participants.ref) return;
    setAdding(true);
    try {
      await CollaborationApi.putCollaborators(participants.ref, {
        collaborators: [{ principalType: 'user', principalId: person.userId, accessLevel: level }],
      });
      setOutsiders((list) => list.filter((p) => p.userId !== person.userId));
      toast.success(t('chat.mentions.resolve.added', { defaultValue: 'Added to this chat' }));
    } catch (error) {
      toast.error(conversationErrorMessage(t, error));
    } finally {
      setAdding(false);
    }
  };

  const handleSend = async (message: string, attachments?: AttachmentRef[], mentions?: MentionRef[]) => {
    if (!message.trim() && (!attachments || attachments.length === 0)) return;

    const store = useChatStore.getState();

    // Search mode: direct API call, no slots/runtime (disabled for agent-scoped chat)
    // Attachments are not supported in search mode — silently ignored.
    if (store.settings.mode === 'search' && !isAgentChat) {
      if (message.trim()) handleSearchSubmit(message.trim());
      return;
    }

    // A note asks nobody, so it skips the run block below and send-when-free (MN-10): it can land while
    // someone else's run streams. Without a conversation yet there is nobody to note. In `mention_only` a
    // message that mentions nobody is a note too (MN-09).
    if (mentionsEnabled && participants.ref) {
      const noted = mentions ?? [];
      const responder = classifyResponder({
        mentions: noted,
        respondMode: participants.respondMode,
        sessionKind: isAgentChat ? 'agent' : 'chat',
      });
      if (responder === 'note') {
        if (await postNote(message, attachments, noted)) sendTips.onSent(noted);
        return;
      }
    }

    // ── Chat mode ──
    // Block a second send while a run is in flight, but allow Enter after
    // Stop (`stopping`) so a follow-up can start with a new runId before
    // the old run's grace timer fires.
    const active = store.activeSlotId ? store.slots[store.activeSlotId] : undefined;
    if (active?.isStreaming && !active.stopping) {
      return;
    }

    // Ensure a slot exists for new chats
    let activeSlotId = store.activeSlotId;
    if (!activeSlotId) {
      activeSlotId = store.createSlot(null);
      store.setActiveSlot(activeSlotId);
      const urlParams =
        typeof window !== 'undefined' ? new URLSearchParams(window.location.search) : null;
      const rawAgentId = urlParams?.get('agentId');
      const agentIdFromUrl = rawAgentId?.trim() ? rawAgentId : null;
      if (agentIdFromUrl) {
        store.updateSlot(activeSlotId, {
          threadAgentId: agentIdFromUrl,
          agentStreamTools:
            store.agentStreamTools === null ? null : [...store.agentStreamTools],
        });
      } else {
        // A thread can't be scoped to both an agent and a project.
        const rawProjectId = urlParams?.get('projectId');
        const projectIdFromUrl = rawProjectId?.trim() ? rawProjectId : null;
        if (projectIdFromUrl) {
          store.updateSlot(activeSlotId, { projectId: projectIdFromUrl });
        }
      }
    }

    const { settings, collectionNamesCache } = store;

    // Collections for message metadata + slot UI; `settings.filters` is not cleared on send.
    const hubApps = settings.filters.apps ?? [];
    const recordGroups = settings.filters.kb ?? [];
    const collectionsAtSendTime: ChatCollectionAttachment[] = [
      ...hubApps.map((id) => ({
        id,
        name: collectionNamesCache[id] || 'Collection',
        kind: 'collectionRoot' as const,
      })),
      ...recordGroups.map((id) => ({
        id,
        name: collectionNamesCache[id] || 'Collection',
        kind: 'recordGroup' as const,
      })),
    ];

    if (collectionsAtSendTime.length > 0) {
      store.updateSlot(activeSlotId, {
        pendingCollections: collectionsAtSendTime,
      });
    }

    // The stream route accepts a mention of a colleague outside the chat too; with the full list in hand the owner is offered to add them.
    if (mentionsEnabled && participants.canInvite && mentions?.length) {
      const known = new Set(participants.candidates.map((c) => c.ref.id));
      const meUserId = useUserStore.getState().profile?.userId;
      const labels = useParticipantsStore.getState().labels;
      const outside = mentions
        .filter((m) => m.type === 'user' && m.id !== meUserId && !known.has(m.id))
        .map((m) => ({ userId: m.id, name: labelOfMention(labels, m) ?? '' }));
      if (outside.length > 0) setOutsiders(outside);
    }

    // Attachments were uploaded the moment they were added to the composer,
    // so by the time we reach here every ref is already server-assigned.
    // Forward verbatim to the runtime; no upload step at send time.
    sendTips.onSent(mentions);
    threadRuntime.append({
      role: 'user',
      content: [{ type: 'text', text: message }],
      metadata: {
        custom: {
          collections: collectionsAtSendTime.length > 0 ? collectionsAtSendTime : undefined,
          attachments: attachments && attachments.length > 0 ? attachments : undefined,
          mentions: mentions && mentions.length > 0 ? mentions : undefined,
          shareToolResults: collabActive && isAgentChat && shareToolResults ? true : undefined,
        },
      },
      startRun: true,
    });
    // The choice covers one turn; the next send starts from "private" again.
    setShareToolResults(false);
  };

  const input = sendTips.wrap(
    <ChatInput
      onSend={handleSend}
      placeholder={
        mentionsEnabled
          ? t(collabActive ? 'chat.mentions.placeholder.shared' : 'chat.mentions.placeholder.solo')
          : undefined
      }
      onUploadFile={handleUploadFile}
      onDeleteFile={handleDeleteFile}
      isAgentChat={isAgentChat}
      agentId={effectiveAgentId}
    />,
  );
  const prompt =
    mentionsEnabled && outsiders.length > 0 ? (
      <NonParticipantPrompt
        people={outsiders}
        canInvite={participants.canInvite}
        busy={adding}
        onAdd={(person, level) => void handleAddOutsider(person, level)}
        onDismiss={() => setOutsiders([])}
      />
    ) : null;
  // One shape for solo and shared chats: the composer keeps its position, so it is not remounted (losing the
  // draft and focus) when the chat turns shared, for example once the history load reports the access.
  return (
    <>
      {prompt}
      {collabActive ? (
        <AudienceNotice
          participantCount={participantCount}
          isAgentChat={isAgentChat}
          shareToolResults={shareToolResults}
          onShareToolResultsChange={setShareToolResults}
        />
      ) : null}
      {input}
    </>
  );
}
