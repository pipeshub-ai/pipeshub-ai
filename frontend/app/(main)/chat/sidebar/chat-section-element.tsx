'use client';

import { useState, useRef, useEffect, type ReactNode } from 'react';
import { useRouter, useSearchParams } from 'next/navigation';
import { buildChatHref } from '@/chat/build-chat-url';
import { ChatStarIcon } from '@/app/components/ui/chat-star-icon';
import { Box, Flex, TextField, Tooltip, VisuallyHidden } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { useTranslation } from 'react-i18next';
import { Conversation } from '@/chat/types';
import { useChatStore, isConversationStreamingInScope } from '@/chat/store';
import type { MoreChatsRowSnapshot, RailRowsSnapshot } from '@/chat/store';
import { ChatApi } from '@/chat/api';
import { holdTitle } from '@/chat/utils/held-titles';
import { dropQueuedFor } from '@/chat/utils/queued-send';
import { AgentsApi } from '@/app/(main)/agents/api';
import { ProjectApi } from '@/chat/project-api';
import { useFeatureFlagsStore, selectProjectsEnabled } from '@/lib/store/feature-flags-store';
import { ICON_SIZE_DEFAULT, CHAT_ITEM_HEIGHT } from '@/app/components/sidebar';
import { SidebarItem } from './sidebar-item';
import { ChatItemMenu } from './chat-item-menu';
import { DeleteChatDialog, MoveToProjectDialog } from './dialogs';
import { Spinner } from '@/app/components/ui/spinner';
import { toast, useToastStore } from '@/lib/store/toast-store';
import { exportConversation } from '@/chat/utils/export-conversation';
import { chatExtension, chatSlots } from '@/config';

/** Duration must match `typing-reveal` animation duration in globals.css */
const TYPING_ANIMATION_DURATION_MS = 400;

function TypingTitle({ title }: { title: string }) {
  return (
    <span className="title-typing-animation">
      {title}
    </span>
  );
}

/**
 * A pinned chat's mark after its title: the pin, filled and tilted the way a pin goes into a board (upright it reads
 * as a location marker), and ", pinned" for the row's name, since the glyph itself is hidden.
 */
function PinnedMark() {
  const { t } = useTranslation();
  return (
    <>
      <span
        aria-hidden
        data-pinned-mark=""
        style={{ display: 'inline-flex', flexShrink: 0, lineHeight: 0, transform: 'rotate(45deg)' }}
      >
        <MaterialIcon name="keep" variant="filled" size={ICON_SIZE_DEFAULT} color="var(--text-secondary)" />
      </span>
      <VisuallyHidden>{t('chat.pinnedRowSuffix')}</VisuallyHidden>
    </>
  );
}

/** A row's title, filling its line and truncating; a pin sits right after the text and the title gives way first. */
function RowTitle({ title, pin }: { title: ReactNode; pin?: ReactNode }) {
  const text = (
    <span style={{ flex: pin ? undefined : 1, minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
      {title}
    </span>
  );
  if (!pin) return text;
  return (
    <span style={{ flex: 1, minWidth: 0, display: 'flex', alignItems: 'center', gap: 'var(--space-2)' }}>
      {text}
      {pin}
    </span>
  );
}

/**
 * Title text with the pulsing activity dot while an answer is being written; shared between
 * ChatSectionElement and GeneratingTitleItem. A dot, not a spinner: the row stays quiet.
 */
function StreamingTitleLabel({ title, isOpen, pin }: { title: string; isOpen: boolean; pin?: ReactNode }) {
  const { t } = useTranslation();
  return (
    <Flex align="center" gap="2" style={{ minWidth: 0, width: '100%' }}>
      <RowTitle title={title} pin={pin} />
      <span
        className="sidebar-activity-dot"
        data-live=""
        role="img"
        aria-label={isOpen ? t('chat.sidebarRunningOpen') : t('chat.sidebarRunning')}
      />
    </Flex>
  );
}

/** Title text with a dot for an answer that finished while the chat was out of view. */
function UnreadTitleLabel({ title, dotLabel, pin }: { title: string; dotLabel: string; pin?: ReactNode }) {
  return (
    <Flex align="center" gap="2" style={{ minWidth: 0, width: '100%' }}>
      <RowTitle title={title} pin={pin} />
      <span
        role="img"
        aria-label={dotLabel}
        data-testid="unread-dot"
        style={{
          flexShrink: 0,
          width: 8,
          height: 8,
          borderRadius: '50%',
          background: 'var(--accent-9)',
        }}
      />
    </Flex>
  );
}

interface ChatSectionElementProps {
  conversation: Conversation;
  isActive: boolean;
  onClick: () => void;
  /**
   * When set, this row is an agent conversation — use agent delete API only
   * (rename/archive are not supported for agent chats).
   */
  agentId?: string;
  /**
   * Set by `ProjectConversationsSidebar` so the row's href keeps `projectId`
   * in the URL — otherwise navigating away from the project's own sidebar
   * list would drop back to the main chat sidebar mid-click. Ignored when
   * `agentId` is set (see `buildChatHref`).
   */
  projectId?: string;
}

/**
 * The chat open in the page, read when a row's action needs it: a row does not subscribe to the URL, which changes as a
 * new chat gets its id when its first answer ends, and every row would draw again in that frame.
 */
const openConversationId = (): string | null =>
  typeof window === 'undefined' ? null : new URLSearchParams(window.location.search).get('conversationId');

/**
 * A single conversation item in the chat sidebar.
 */
export function ChatSectionElement({
  conversation,
  isActive,
  onClick,
  agentId: sectionAgentId,
  projectId,
}: ChatSectionElementProps) {
  const { t } = useTranslation();
  // A shared agent chat in the global list carries its own agent; the agent sidebar passes it per section.
  const agentId = sectionAgentId ?? conversation.agentKey;
  const shownTitle = chatExtension.formatConversationTitle(conversation.title) ?? '';

  const [isHovered, setIsHovered] = useState(false);
  const [menuOpen, setMenuOpen] = useState(false);
  const [isRenaming, setIsRenaming] = useState(false);
  const [renameValue, setRenameValue] = useState(shownTitle);
  const [isSavingRename, setIsSavingRename] = useState(false);
  const [deleteDialogOpen, setDeleteDialogOpen] = useState(false);
  const [moveDialogOpen, setMoveDialogOpen] = useState(false);
  const projectsEnabled = useFeatureFlagsStore(selectProjectsEnabled);
  const [isDeleting, setIsDeleting] = useState(false);
  const isArchiving = useRef(false);
  const [isTypingTitle, setIsTypingTitle] = useState(false);
  const renameInputRef = useRef<HTMLInputElement>(null);
  const router = useRouter();

  const isConversationStreaming = useChatStore((s) =>
    isConversationStreamingInScope(s.slots, conversation.id, agentId ?? null),
  );

  const hasUnreadAnswer = useChatStore((s) => s.unreadConversationIds.has(conversation.id));
  const markConversationRead = useChatStore((s) => s.markConversationRead);

  // Covers every way a chat gets opened (click, URL, back button), not just a row click.
  useEffect(() => {
    if (isActive && hasUnreadAnswer) markConversationRead(conversation.id);
  }, [isActive, hasUnreadAnswer, conversation.id, markConversationRead]);

  const convStreamingBlocksSidebarMutation = () =>
    isConversationStreamingInScope(
      useChatStore.getState().slots,
      conversation.id,
      agentId ?? null,
    );

  // Whether this row's chat just got its title: only that, so a chat resolving elsewhere draws no other row again.
  const justResolved = useChatStore((s) => s.newlyResolvedIds.has(conversation.id));
  const clearNewlyResolvedId = useChatStore((s) => s.clearNewlyResolvedId);

  useEffect(() => {
    if (justResolved) {
      setIsTypingTitle(true);
      clearNewlyResolvedId(conversation.id);
      const timer = setTimeout(() => setIsTypingTitle(false), TYPING_ANIMATION_DURATION_MS);
      return () => clearTimeout(timer);
    }
  }, [justResolved]);

  const removeConversation = useChatStore((s) => s.removeConversation);
  const renameConversation = useChatStore((s) => s.renameConversation);
  const moveConversationToProject = useChatStore((s) => s.moveConversationToProject);
  // A project's rail lists its rows itself: after a change to one of them it reloads, the other rails do not.
  const reloadProjectRail = () => {
    if (projectId) useChatStore.getState().bumpProjectsVersion();
  };
  const setConversationPinned = useChatStore((s) => s.setConversationPinned);
  // One request at a time: a second click while the first is in flight would
  // otherwise send the opposite request and the two could land out of order.
  const isPinRequestInFlight = useRef(false);

  useEffect(() => {
    if (isRenaming) {
      renameInputRef.current?.focus();
      renameInputRef.current?.select();
    }
  }, [isRenaming]);

  const handleStartRename = () => {
    if (convStreamingBlocksSidebarMutation()) return;
    setRenameValue(shownTitle);
    setIsRenaming(true);
  };

  const handleRenameBlur = async () => {
    if (!isRenaming || isSavingRename) return;
    if (convStreamingBlocksSidebarMutation()) {
      setRenameValue(shownTitle);
      setIsRenaming(false);
      return;
    }
    const trimmed = renameValue.trim();
    if (!trimmed || trimmed === conversation.title) {
      setIsRenaming(false);
      return;
    }
    setIsSavingRename(true);
    // The row takes the new title at once, and keeps it over any list read that may not have it yet; a refusal puts
    // the old one back.
    const previousTitle = conversation.title;
    renameConversation(conversation.id, trimmed);
    const held = holdTitle(conversation.id, trimmed);
    setIsRenaming(false);
    try {
      if (agentId) {
        await AgentsApi.renameAgentConversation(agentId, conversation.id, trimmed);
      } else {
        await ChatApi.renameConversation(conversation.id, trimmed);
      }
      held.saved();
      reloadProjectRail();
    } catch {
      held.dropped();
      // The row goes back to its old title; say so, rather than reverting without a word.
      renameConversation(conversation.id, previousTitle);
      setRenameValue(shownTitle);
      toast.error(t('chat.renameFailedTitle'), { description: t('chat.renameFailedDescription') });
    } finally {
      setIsSavingRename(false);
    }
  };

  const handleRenameKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'Enter') {
      e.preventDefault();
      renameInputRef.current?.blur();
    } else if (e.key === 'Escape') {
      setIsRenaming(false);
    }
  };

  const handleTogglePin = async () => {
    if (isPinRequestInFlight.current) return;
    isPinRequestInFlight.current = true;
    const pinned = conversation.isPinned !== true;
    const undo = setConversationPinned(conversation.id, pinned);
    try {
      await ChatApi.setConversationPinned(conversation.id, pinned);
    } catch {
      undo();
      toast.error(pinned ? t('chat.pinFailed') : t('chat.unpinFailed'));
    } finally {
      isPinRequestInFlight.current = false;
    }
  };

  const handleExport = async () => {
    try {
      await toast.promise(
        exportConversation({
          conversationId: conversation.id,
          title: shownTitle,
          agentId,
          labels: {
            you: t('common.you'),
            assistant: t('chat.export.assistant'),
            sources: t('chat.sources'),
          },
        }),
        {
          loading: t('chat.export.exporting'),
          success: t('chat.export.done'),
          error: t('chat.export.failed'),
        },
      );
    } catch {
      // error already shown via toast
    }
  };

  const handleConfirmDelete = async () => {
    if (convStreamingBlocksSidebarMutation()) return;
    const wasOpen = openConversationId() === conversation.id;
    setIsDeleting(true);
    try {
      if (agentId) {
        await AgentsApi.deleteAgentConversation(agentId, conversation.id);
      } else {
        await ChatApi.deleteConversation(conversation.id);
      }
      // What this tab queued there never goes out.
      dropQueuedFor(conversation.id);
      removeConversation(conversation.id);
      reloadProjectRail();
      setDeleteDialogOpen(false);
      if (wasOpen) {
        const store = useChatStore.getState();
        const found = agentId
          ? store.getSlotByConvId(conversation.id, { forAgentId: agentId })
          : store.getSlotByConvId(conversation.id, { forAgentId: null });
        if (found) {
          store.evictSlot(found.slotId);
        } else {
          store.clearActiveSlot();
        }
        router.replace(agentId ? buildChatHref({ agentId }) : '/chat/');
      }
    } catch {
      // keep dialog open on error
    } finally {
      setIsDeleting(false);
    }
  };

  // Archive is reversible, so it asks nothing first: the row goes, and the toast says where
  // it went and offers Undo, which restores it through the existing restore call.
  const handleRestore = async (
    reopen: boolean,
    moreChatsPlace: MoreChatsRowSnapshot | null,
    railPlace: RailRowsSnapshot,
  ) => {
    try {
      if (agentId) {
        await AgentsApi.restoreAgentConversation(agentId, conversation.id);
      } else {
        await ChatApi.restoreConversation(conversation.id);
      }
      // Back where it was, in the rail and in an open More chats list: nothing is reloaded.
      useChatStore.getState().restoreRailRows(railPlace);
      if (moreChatsPlace) useChatStore.getState().restoreMoreChatsRow(moreChatsPlace);
      reloadProjectRail();
      // Undo puts back what Archive took: the chat it closed opens again.
      if (reopen) router.push(buildChatHref({ agentId, projectId, conversationId: conversation.id }));
    } catch {
      // The app's error toast says why; the chat stays archived.
    }
  };

  const handleArchive = async () => {
    if (convStreamingBlocksSidebarMutation() || isArchiving.current) return;
    isArchiving.current = true;
    const wasOpen = openConversationId() === conversation.id;
    // The row goes at once, its places kept for a refusal and for Undo.
    const moreChatsPlace = useChatStore.getState().snapshotMoreChatsRow(conversation.id);
    const railPlace = useChatStore.getState().snapshotRailRows(conversation.id);
    removeConversation(conversation.id, { keepUnread: true });
    try {
      if (agentId) {
        await AgentsApi.archiveAgentConversation(agentId, conversation.id);
      } else {
        await ChatApi.archiveConversation(conversation.id);
      }
    } catch {
      // The app's error toast says why; the row comes back where it was.
      useChatStore.getState().restoreRailRows(railPlace);
      if (moreChatsPlace) useChatStore.getState().restoreMoreChatsRow(moreChatsPlace);
      isArchiving.current = false;
      return;
    }
    dropQueuedFor(conversation.id);
    // Archived, its "new answer" dot goes, as it did when the row was taken out after the request.
    useChatStore.getState().markConversationRead(conversation.id);
    reloadProjectRail();
    if (wasOpen) {
      const store = useChatStore.getState();
      const found = agentId
        ? store.getSlotByConvId(conversation.id, { forAgentId: agentId })
        : store.getSlotByConvId(conversation.id, { forAgentId: null });
      if (found) {
        store.evictSlot(found.slotId);
      } else {
        store.clearActiveSlot();
      }
      router.replace(agentId ? buildChatHref({ agentId }) : '/chat/');
    }
    isArchiving.current = false;
    const id = toast.success(t('chat.chatArchived'), {
      description: t('chat.archivedDescription', { title: shownTitle }),
      action: {
        label: t('chat.undo'),
        onClick: () => {
          useToastStore.getState().removeToast(id);
          void handleRestore(wasOpen, moreChatsPlace, railPlace);
        },
      },
    });
  };

  const moveToProject = async (projectId: string | null) => {
    if (convStreamingBlocksSidebarMutation()) {
      throw new Error('Cannot move a conversation while it is streaming.');
    }
    await ProjectApi.setConversationProject(conversation.id, projectId, {
      agentKey: agentId,
    });
    moveConversationToProject(conversation.id, projectId);
    const store = useChatStore.getState();
    // Project rails list their own chats.
    store.bumpProjectsVersion();
    // With Projects on, Your chats lists no project chat: the row leaves it for the project, or comes back to it.
    if (projectsEnabled && !agentId) {
      if (projectId) store.removeConversation(conversation.id, { keepUnread: true });
      else store.insertConversationRow({ ...conversation, projectId: undefined }, 'conversations');
    }
  };

  const handleConfirmMoveToProject = async (projectId: string | null) => {
    if (row.interceptProjectChange(projectId)) return;
    await moveToProject(projectId);
  };

  const handleRemoveFromProject = async () => {
    if (convStreamingBlocksSidebarMutation()) return;
    if (row.interceptProjectChange(null)) return;
    try {
      await moveToProject(null);
    } catch {
      // Non-fatal — row simply stays linked; user can retry from the menu.
    }
  };

  const sharedByName = conversation.sharedBy?.name?.trim();
  const sharedByText =
    conversation.isOwner === false && sharedByName ? t('chat.sharedBy', { name: sharedByName }) : undefined;
  // In the global list an agent chat looks like any other shared chat, so the agent symbol follows the sharer's name.
  const isAgentChatInGlobalList = Boolean(conversation.agentKey) && !sectionAgentId;
  const sharedBySubtitle =
    sharedByText && isAgentChatInGlobalList ? (
      <>
        {sharedByText}
        <Tooltip content={t('chat.agentChat')}>
          <span
            aria-label={t('chat.agentChat')}
            role="img"
            style={{ display: 'inline-flex', verticalAlign: 'middle', marginLeft: 4 }}
          >
            <MaterialIcon name="smart_toy" size={13} color="var(--accent-9)" />
          </span>
        </Tooltip>
      </>
    ) : (
      sharedByText
    );

  // Agent chats have no pin API, and project rows come from a list that does not carry the pin, so only the main
  // list offers it.
  const onTogglePin = agentId || projectId ? undefined : () => void handleTogglePin();
  // Follows the row's own pin state, which Pin and Unpin change at once.
  const pinMark = onTogglePin && conversation.isPinned === true ? <PinnedMark /> : undefined;
  const ownerMenu =
    conversation.isOwner === true ? (
      <ChatItemMenu
        title={shownTitle}
        isParentHovered={isHovered}
        onOpenChange={setMenuOpen}
        onRename={handleStartRename}
        onExport={() => void handleExport()}
        onTogglePin={onTogglePin}
        isPinned={conversation.isPinned === true}
        onArchive={() => void handleArchive()}
        onDelete={() => setDeleteDialogOpen(true)}
        showRename={true}
        showArchive={true}
        onMoveToProject={projectsEnabled ? () => setMoveDialogOpen(true) : undefined}
        onRemoveFromProject={
          projectsEnabled && conversation.projectId ? () => void handleRemoveFromProject() : undefined
        }
      />
    ) : undefined;
  const row = chatSlots.useChatRow({
    conversation,
    agentId,
    ownerMenu,
    isHovered,
    isActive,
    onMenuOpenChange: setMenuOpen,
    applyProjectChange: moveToProject,
    isMutationBlocked: convStreamingBlocksSidebarMutation,
    onTogglePin,
  });

  // Inline rename mode: a Radix text field in the row, in place of the title.
  if (isRenaming) {
    return (
      <Flex
        align="center"
        data-inline-rename=""
        style={{
          height: CHAT_ITEM_HEIGHT,
          padding: '0 var(--space-2)',
          borderRadius: 'var(--radius-1)',
          backgroundColor: 'var(--fill-muted)',
          border: '1px solid var(--border-default)',
          boxSizing: 'border-box',
        }}
      >
        <TextField.Root
          ref={renameInputRef}
          size="1"
          value={renameValue}
          aria-label={t('chat.renameFieldLabel')}
          onChange={(e) => setRenameValue(e.target.value)}
          onBlur={handleRenameBlur}
          onKeyDown={handleRenameKeyDown}
          disabled={isSavingRename}
          style={{ flex: 1, minWidth: 0 }}
        >
          {isSavingRename && (
            <TextField.Slot side="right">
              <Spinner size={12} color="var(--text-secondary)" />
            </TextField.Slot>
          )}
        </TextField.Root>
      </Flex>
    );
  }

  const conversationHref = buildChatHref({ agentId, projectId, conversationId: conversation.id });

  return (
    <>
      <SidebarItem
        label={
          isConversationStreaming ? (
            <StreamingTitleLabel title={shownTitle} isOpen={isActive} pin={pinMark} />
          ) : hasUnreadAnswer && !isActive ? (
            <UnreadTitleLabel title={shownTitle} dotLabel={t('chat.answerReady.unread')} pin={pinMark} />
          ) : isTypingTitle ? (
            pinMark ? (
              <RowTitle title={<TypingTitle title={shownTitle} />} pin={pinMark} />
            ) : (
              <TypingTitle title={shownTitle} />
            )
          ) : (
            shownTitle
          )
        }
        titleEnd={pinMark}
        isActive={isActive}
        href={conversationHref}
        onClick={onClick}
        textColor="var(--text-primary)"
        subtitle={sharedBySubtitle}
        forceHighlight={menuOpen}
        onHoverChange={setIsHovered}
        rightSlot={row.rightSlot}
      />

      {/* Delete confirmation dialog */}
      <DeleteChatDialog
        open={deleteDialogOpen}
        onOpenChange={setDeleteDialogOpen}
        onConfirm={handleConfirmDelete}
        chatTitle={shownTitle}
        isDeleting={isDeleting}
      />

      <MoveToProjectDialog
        open={moveDialogOpen}
        onOpenChange={setMoveDialogOpen}
        currentProjectId={conversation.projectId ?? null}
        onConfirm={handleConfirmMoveToProject}
      />

      {row.dialogs}
    </>
  );
}

/**
 * Sidebar item shown while a new chat is being streamed.
 *
 * Shows the server-provided title from SSE `connected` (pending row updated via store)
 * with a spinner, or "Generating Title…" shimmer until that arrives.
 *
 * Clickable — switches to the streaming temp slot so the user can
 * return to a new chat that's still generating in the background.
 */
export function GeneratingTitleItem({ slotId }: { slotId: string }) {
  const { t } = useTranslation();
  const router = useRouter();
  const searchParams = useSearchParams();
  const currentConversationId = searchParams?.get('conversationId') ?? null;
  const activeSlotId = useChatStore((s) => s.activeSlotId);
  const slotConvId = useChatStore((s) => s.slots[slotId]?.convId ?? null);
  const pendingTitle = useChatStore((s) => s.pendingConversations[slotId]?.title ?? null);

  const isActive = slotConvId
    ? currentConversationId === slotConvId
    : activeSlotId === slotId;

  const rawAgent = searchParams?.get('agentId') ?? null;
  const agentId = rawAgent?.trim() ? rawAgent : null;
  const href =
    slotConvId != null && slotConvId !== ''
      ? buildChatHref({ agentId, conversationId: slotConvId })
      : undefined;

  const handleClick = () => {
    useChatStore.getState().setActiveSlot(slotId);
    if (!slotConvId) {
      router.push(agentId ? buildChatHref({ agentId }) : '/chat/');
    }
  };

  if (pendingTitle) {
    return (
      <SidebarItem
        isActive={isActive}
        href={href}
        onClick={handleClick}
        label={<StreamingTitleLabel title={pendingTitle} isOpen={isActive} />}
        textColor="var(--text-primary)"
      />
    );
  }

  return (
    <SidebarItem
      isActive={isActive}
      href={href}
      onClick={handleClick}
      label={
        <span className="generating-shimmer">
          <span className="generating-shimmer-base">{t('chat.generatingTitle')}</span>
          <span className="generating-shimmer-overlay" aria-hidden="true">{t('chat.generatingTitle')}</span>
        </span>
      }
      textColor="var(--text-secondary)"
    />
  );
}

/**
 * Empty state — prompts user to start a new chat.
 */
export function StartChatButton({ onClick }: { onClick: () => void }) {
  const { t } = useTranslation();
  return (
    <SidebarItem
      icon={
        <ChatStarIcon
          size={ICON_SIZE_DEFAULT}
          color="var(--accent-11)"
        />
      }
      label={t('chat.startChat')}
      onClick={onClick}
      textColor="var(--accent-11)"
    />
  );
}

/**
 * Loading skeleton for a chat item.
 */
export function ChatItemSkeleton() {
  return (
    <SidebarItem
      label={
        <Box
          style={{
            height: 'var(--space-4)',
            backgroundColor: 'var(--slate-4)',
            borderRadius: 'var(--radius-1)',
            width: '75%',
            animation: 'shimmer-pulse 2s cubic-bezier(0.4, 0, 0.6, 1) infinite',
          }}
        />
      }
    />
  );
}
