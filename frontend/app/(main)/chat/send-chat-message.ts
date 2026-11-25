import type { useAui } from '@assistant-ui/react';
import { useChatStore } from './store';
import { showRemoteRunInProgressToast } from './utils/remote-run-toast';
import type { MentionNode } from './utils/node-mentions';
import type { AttachmentRef, ChatCollectionAttachment } from './types';

type Aui = ReturnType<typeof useAui>;

/**
 * Sends a chat-mode message through the assistant runtime: the composer's one send. Makes the slot a new chat
 * needs, scoped to the agent or project in the URL, and records the collections the turn searched.
 * Returns false when nothing was sent (empty, a run still going, or a run from before a refresh still writing).
 */
export function sendChatMessage(aui: Aui, message: string, attachments?: AttachmentRef[], mentions?: MentionNode[]): boolean {
  if (!message.trim() && (!attachments || attachments.length === 0)) return false;
  const store = useChatStore.getState();

  // Block a second send while a run is in flight, but allow Enter after
  // Stop (`stopping`) so a follow-up can start with a new runId before
  // the old run's grace timer fires.
  const active = store.activeSlotId ? store.slots[store.activeSlotId] : undefined;
  if (active?.isStreaming && !active.stopping) {
    return false;
  }
  if (active?.remoteRunInProgress) {
    showRemoteRunInProgressToast();
    return false;
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

  // Attachments were uploaded the moment they were added to the composer,
  // so by the time we reach here every ref is already server-assigned.
  // Forward verbatim to the runtime; no upload step at send time.
  aui.thread().append({
    role: 'user',
    content: [{ type: 'text', text: message }],
    metadata: {
      custom: {
        collections: collectionsAtSendTime.length > 0 ? collectionsAtSendTime : undefined,
        attachments: attachments && attachments.length > 0 ? attachments : undefined,
        mentionIds: mentions && mentions.length > 0 ? mentions.map((node) => node.id) : undefined,
      },
    },
    startRun: true,
  });
  return true;
}
