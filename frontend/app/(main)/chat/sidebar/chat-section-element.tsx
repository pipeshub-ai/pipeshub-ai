'use client';

import { useState, useRef, useEffect } from 'react';
import { useRouter, useSearchParams } from 'next/navigation';
import { buildChatHref } from '@/chat/build-chat-url';
import { ChatStarIcon } from '@/app/components/ui/chat-star-icon';
import { Box, Flex } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';
import { Conversation } from '@/chat/types';
import { useChatStore, isConversationStreamingInScope } from '@/chat/store';
import { ChatApi } from '@/chat/api';
import { AgentsApi } from '@/app/(main)/agents/api';
import { ProjectApi } from '@/chat/project-api';
import { CollaborationApi } from '@/chat/collaboration-api';
import type { AccessChange, ConversationRef } from '@/chat/collaboration-types';
import { AccessChangeDialog } from '@/chat/components/collaboration/access-change-dialog';
import { describeConversationError } from '@/chat/utils/conversation-errors';
import { useToastStore } from '@/lib/store/toast-store';
import { useFeatureFlagsStore, selectProjectsEnabled, selectCollaborativeChatsEnabled } from '@/lib/store/feature-flags-store';
import { ICON_SIZE_DEFAULT, CHAT_ITEM_HEIGHT } from '@/app/components/sidebar';
import { SidebarItem } from './sidebar-item';
import { ChatItemMenu } from './chat-item-menu';
import { SharedChatItemMenu } from './shared-chat-item-menu';
import { ChatRowBadges } from './chat-row-badges';
import { DeleteChatDialog, ArchiveChatDialog, MoveToProjectDialog, LeaveChatDialog } from './dialogs';
import { Spinner } from '@/app/components/ui/spinner';
import { displayTitle } from '@/lib/utils/display-title';

/** Duration must match `typing-reveal` animation duration in globals.css */
const TYPING_ANIMATION_DURATION_MS = 400;

function TypingTitle({ title }: { title: string }) {
  return (
    <span className="title-typing-animation">
      {title}
    </span>
  );
}

/** Title text with a streaming spinner — shared between ChatSectionElement and GeneratingTitleItem. */
function StreamingTitleLabel({ title }: { title: string }) {
  return (
    <Flex align="center" gap="2" style={{ minWidth: 0, width: '100%' }}>
      <span
        style={{
          flex: 1,
          overflow: 'hidden',
          textOverflow: 'ellipsis',
          whiteSpace: 'nowrap',
        }}
      >
        {title}
      </span>
      <Spinner size={12} color="var(--accent-11)" />
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
 * A single conversation item in the chat sidebar.
 */
export function ChatSectionElement({ conversation, isActive, onClick, agentId, projectId }: ChatSectionElementProps) {
  const { t } = useTranslation();
  const shownTitle = displayTitle(conversation.title) ?? '';

  const [isHovered, setIsHovered] = useState(false);
  const [menuOpen, setMenuOpen] = useState(false);
  const [isRenaming, setIsRenaming] = useState(false);
  const [renameValue, setRenameValue] = useState(shownTitle);
  const [isSavingRename, setIsSavingRename] = useState(false);
  const [deleteDialogOpen, setDeleteDialogOpen] = useState(false);
  const [archiveDialogOpen, setArchiveDialogOpen] = useState(false);
  const [moveDialogOpen, setMoveDialogOpen] = useState(false);
  const [leaveDialogOpen, setLeaveDialogOpen] = useState(false);
  const [isLeaving, setIsLeaving] = useState(false);
  const [pendingProjectChange, setPendingProjectChange] = useState<AccessChange | null>(null);
  const collabEnabled = useFeatureFlagsStore(selectCollaborativeChatsEnabled);
  const projectsEnabled = useFeatureFlagsStore(selectProjectsEnabled);
  const [isDeleting, setIsDeleting] = useState(false);
  const [isArchiving, setIsArchiving] = useState(false);
  const [isTypingTitle, setIsTypingTitle] = useState(false);
  const renameInputRef = useRef<HTMLInputElement>(null);
  const router = useRouter();
  const searchParams = useSearchParams();
  const urlConversationId = searchParams?.get('conversationId') ?? null;

  const isConversationStreaming = useChatStore((s) =>
    isConversationStreamingInScope(s.slots, conversation.id, agentId ?? null),
  );

  const convStreamingBlocksSidebarMutation = () =>
    isConversationStreamingInScope(
      useChatStore.getState().slots,
      conversation.id,
      agentId ?? null,
    );

  const newlyResolvedIds = useChatStore((s) => s.newlyResolvedIds);
  const clearNewlyResolvedId = useChatStore((s) => s.clearNewlyResolvedId);

  useEffect(() => {
    if (newlyResolvedIds.has(conversation.id)) {
      setIsTypingTitle(true);
      clearNewlyResolvedId(conversation.id);
      const timer = setTimeout(() => setIsTypingTitle(false), TYPING_ANIMATION_DURATION_MS);
      return () => clearTimeout(timer);
    }
  }, [newlyResolvedIds]);

  const removeConversation = useChatStore((s) => s.removeConversation);
  const renameConversation = useChatStore((s) => s.renameConversation);
  const bumpConversationsVersion = useChatStore((s) => s.bumpConversationsVersion);
  const moveConversationToProject = useChatStore((s) => s.moveConversationToProject);

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
    try {
      if (agentId) {
        await AgentsApi.renameAgentConversation(agentId, conversation.id, trimmed);
      } else {
        await ChatApi.renameConversation(conversation.id, trimmed);
      }
      renameConversation(conversation.id, trimmed);
      bumpConversationsVersion();
    } catch {
      // revert silently
    } finally {
      setIsSavingRename(false);
      setIsRenaming(false);
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

  const handleConfirmDelete = async () => {
    if (convStreamingBlocksSidebarMutation()) return;
    setIsDeleting(true);
    try {
      if (agentId) {
        await AgentsApi.deleteAgentConversation(agentId, conversation.id);
      } else {
        await ChatApi.deleteConversation(conversation.id);
      }
      removeConversation(conversation.id);
      bumpConversationsVersion();
      setDeleteDialogOpen(false);
      if (urlConversationId === conversation.id) {
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

  const handleConfirmArchive = async () => {
    if (convStreamingBlocksSidebarMutation()) return;
    setIsArchiving(true);
    try {
      if (agentId) {
        await AgentsApi.archiveAgentConversation(agentId, conversation.id);
      } else {
        await ChatApi.archiveConversation(conversation.id);
      }
      removeConversation(conversation.id);
      bumpConversationsVersion();
      setArchiveDialogOpen(false);
      if (urlConversationId === conversation.id) {
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
      setIsArchiving(false);
    }
  };

  const moveToProject = async (projectId: string | null) => {
    if (convStreamingBlocksSidebarMutation()) {
      throw new Error('Cannot move a conversation while it is streaming.');
    }
    await ProjectApi.setConversationProject(conversation.id, projectId, {
      agentKey: agentId,
    });
    moveConversationToProject(conversation.id, projectId);
    bumpConversationsVersion();
  };

  // With collaboration on, a link or unlink changes who can open the chat, so it is previewed first.
  const requestProjectChange = (projectId: string | null) => {
    setPendingProjectChange(projectId === null ? { type: 'unlink' } : { type: 'link', projectId });
  };

  const handleConfirmMoveToProject = async (projectId: string | null) => {
    if (collabEnabled) {
      requestProjectChange(projectId);
      return;
    }
    await moveToProject(projectId);
  };

  const handleRemoveFromProject = async () => {
    if (convStreamingBlocksSidebarMutation()) return;
    if (collabEnabled) {
      requestProjectChange(null);
      return;
    }
    try {
      await moveToProject(null);
    } catch {
      // Non-fatal — row simply stays linked; user can retry from the menu.
    }
  };

  const isSharedWithMe = collabEnabled && (conversation.access?.isOwner ?? conversation.isOwner) === false;
  const conversationRef: ConversationRef = agentId
    ? { kind: 'agent', agentKey: agentId, id: conversation.id }
    : { kind: 'chat', id: conversation.id };

  const leaveActiveConversation = () => {
    if (urlConversationId !== conversation.id) return;
    const store = useChatStore.getState();
    const found = store.getSlotByConvId(conversation.id, { forAgentId: agentId ?? null });
    if (found) store.evictSlot(found.slotId);
    else store.clearActiveSlot();
    router.replace(agentId ? buildChatHref({ agentId }) : '/chat/');
  };

  const showCollabError = (error: unknown) => {
    useToastStore.getState().addToast({
      variant: 'error',
      title: t(describeConversationError(error).i18nKey),
    });
  };

  const handleConfirmLeave = async () => {
    if (convStreamingBlocksSidebarMutation()) return;
    setIsLeaving(true);
    try {
      await CollaborationApi.leave(conversationRef);
      removeConversation(conversation.id);
      bumpConversationsVersion();
      setLeaveDialogOpen(false);
      leaveActiveConversation();
    } catch (error) {
      setLeaveDialogOpen(false);
      showCollabError(error);
    } finally {
      setIsLeaving(false);
    }
  };

  const handleArchiveSelf = async () => {
    try {
      await CollaborationApi.archiveSelf(conversationRef);
      removeConversation(conversation.id);
      bumpConversationsVersion();
      leaveActiveConversation();
    } catch (error) {
      showCollabError(error);
    }
  };

  const handleUnarchiveSelf = async () => {
    try {
      await CollaborationApi.unarchiveSelf(conversationRef);
      removeConversation(conversation.id);
      bumpConversationsVersion();
    } catch (error) {
      showCollabError(error);
    }
  };

  const sharedByName = conversation.sharedBy?.name?.trim();
  const sharedBySubtitle =
    conversation.isOwner === false && sharedByName
      ? t('chat.sharedBy', { name: sharedByName })
      : undefined;


  // Inline rename mode — render a plain input instead of SidebarItem
  if (isRenaming) {
    return (
      <Flex
        align="center"
        style={{
          height: CHAT_ITEM_HEIGHT,
          padding: '0 var(--space-3)',
          borderRadius: 'var(--radius-1)',
          backgroundColor: 'var(--olive-3)',
          border: '1px solid var(--olive-4)',
          boxSizing: 'border-box',
        }}
      >
        <input
          ref={renameInputRef}
          value={renameValue}
          onChange={(e) => setRenameValue(e.target.value)}
          onBlur={handleRenameBlur}
          onKeyDown={handleRenameKeyDown}
          disabled={isSavingRename}
          style={{
            flex: 1,
            background: 'transparent',
            border: 'none',
            outline: 'none',
            fontSize: 14,
            fontWeight: 500,
            lineHeight: 'var(--line-height-2)',
            color: 'var(--slate-12)',
            font: 'inherit',
          }}
        />
        {isSavingRename && <Spinner size={12} color="var(--slate-10)" />}
      </Flex>
    );
  }

  const ownerMenu =
    conversation.isOwner === true ? (
      <ChatItemMenu
        isParentHovered={isHovered}
        onOpenChange={setMenuOpen}
        onRename={handleStartRename}
        onArchive={() => setArchiveDialogOpen(true)}
        onDelete={() => setDeleteDialogOpen(true)}
        showRename={true}
        showArchive={true}
        onMoveToProject={projectsEnabled ? () => setMoveDialogOpen(true) : undefined}
        onRemoveFromProject={
          projectsEnabled && conversation.projectId ? () => void handleRemoveFromProject() : undefined
        }
      />
    ) : undefined;
  const sharedMenu = isSharedWithMe ? (
    <SharedChatItemMenu
      isParentHovered={isHovered}
      onOpenChange={setMenuOpen}
      isArchived={conversation.archivedForMe === true}
      onArchive={() => void handleArchiveSelf()}
      onUnarchive={() => void handleUnarchiveSelf()}
      onLeave={() => setLeaveDialogOpen(true)}
    />
  ) : undefined;
  // Flag off keeps the legacy slot (owner menu or nothing); the badges and shared menu exist only with it on.
  const rightSlot = collabEnabled ? (
    <>
      <ChatRowBadges conversation={conversation} />
      {ownerMenu ?? sharedMenu}
    </>
  ) : (
    ownerMenu
  );

  const conversationHref = buildChatHref({ agentId, projectId, conversationId: conversation.id });

  return (
    <>
      <SidebarItem
        label={
          isConversationStreaming ? (
            <StreamingTitleLabel title={shownTitle} />
          ) : isTypingTitle ? (
            <TypingTitle title={shownTitle} />
          ) : (
            shownTitle
          )
        }
        isActive={isActive}
        href={conversationHref}
        onClick={onClick}
        textColor="var(--slate-12)"
        fontWeight={500}
        subtitle={sharedBySubtitle}
        forceHighlight={menuOpen}
        onHoverChange={setIsHovered}
        rightSlot={rightSlot}
      />

      <LeaveChatDialog
        open={leaveDialogOpen}
        onOpenChange={setLeaveDialogOpen}
        onConfirm={handleConfirmLeave}
        isLeaving={isLeaving}
      />

      {/* Delete confirmation dialog */}
      <DeleteChatDialog
        open={deleteDialogOpen}
        onOpenChange={setDeleteDialogOpen}
        onConfirm={handleConfirmDelete}
        chatTitle={shownTitle}
        isDeleting={isDeleting}
      />

      <ArchiveChatDialog
        open={archiveDialogOpen}
        onOpenChange={setArchiveDialogOpen}
        onConfirm={handleConfirmArchive}
        chatTitle={shownTitle}
        isArchiving={isArchiving}
      />

      <MoveToProjectDialog
        open={moveDialogOpen}
        onOpenChange={setMoveDialogOpen}
        currentProjectId={conversation.projectId ?? null}
        onConfirm={handleConfirmMoveToProject}
      />

      {collabEnabled && (
        <AccessChangeDialog
          change={pendingProjectChange}
          conversationRef={conversationRef}
          onApply={() => moveToProject(pendingProjectChange?.type === 'link' ? pendingProjectChange.projectId : null)}
          onClose={() => setPendingProjectChange(null)}
        />
      )}
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
        label={<StreamingTitleLabel title={pendingTitle} />}
        textColor="var(--slate-12)"
        fontWeight={500}
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
      textColor="var(--slate-11)"
      fontWeight={500}
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
