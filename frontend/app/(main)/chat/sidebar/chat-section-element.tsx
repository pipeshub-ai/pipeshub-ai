'use client';

import { useState, useRef, useEffect } from 'react';
import { useRouter, useSearchParams } from 'next/navigation';
import { buildChatHref } from '@/chat/build-chat-url';
import { ChatStarIcon } from '@/app/components/ui/chat-star-icon';
import { Box, Flex, Tooltip, VisuallyHidden } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { useTranslation } from 'react-i18next';
import { Conversation } from '@/chat/types';
import { useChatStore, isConversationStreamingInScope } from '@/chat/store';
import { ChatApi } from '@/chat/api';
import { AgentsApi } from '@/app/(main)/agents/api';
import { ProjectApi } from '@/chat/project-api';
import { useFeatureFlagsStore, selectProjectsEnabled } from '@/lib/store/feature-flags-store';
import { ICON_SIZE_DEFAULT, CHAT_ITEM_HEIGHT } from '@/app/components/sidebar';
import { SidebarItem } from './sidebar-item';
import { ChatItemMenu } from './chat-item-menu';
import { DeleteChatDialog, ArchiveChatDialog, MoveToProjectDialog } from './dialogs';
import { Spinner } from '@/app/components/ui/spinner';
import { toast } from '@/lib/store/toast-store';
import { exportConversation } from '@/chat/utils/export-conversation';
import { sidebarDot, type SidebarDot } from '@/lib/harness-jobs/jobs-list';
import { slotWaitingCount } from '@/chat/interactions';
import type { ChatSlot } from '@/chat/types';

/** Duration must match `typing-reveal` animation duration in globals.css */
const TYPING_ANIMATION_DURATION_MS = 400;

function TypingTitle({ title }: { title: string }) {
  return (
    <span className="title-typing-animation">
      {title}
    </span>
  );
}

/** A running chat whose title is not known yet. */
function GeneratingTitleLabel({ label }: { label: string }) {
  return (
    <span className="generating-shimmer">
      <span className="generating-shimmer-base">{label}</span>
      <span className="generating-shimmer-overlay" aria-hidden="true">{label}</span>
    </span>
  );
}

const DOT_COLOR: Record<'needsYou' | 'finished', string> = { needsYou: 'var(--amber-9)', finished: 'var(--accent-9)' };

/**
 * A chat row's one mark (D10): amber for needs you, the spinner for working, the jade dot for finished and not seen.
 * Its words are always there for a screen reader; colour is never the only sign.
 */
export function ChatDotMark({ dot, label }: { dot: SidebarDot['dot']; label: string }) {
  return (
    <span data-testid="chat-dot" data-dot={dot} style={{ flexShrink: 0, display: 'inline-flex', alignItems: 'center' }}>
      <span aria-hidden style={{ display: 'inline-flex', lineHeight: 0 }}>
        {dot === 'working' ? (
          <Spinner size={12} color="var(--accent-11)" />
        ) : (
          <span style={{ width: 8, height: 8, borderRadius: '50%', background: DOT_COLOR[dot] }} />
        )}
      </span>
      <VisuallyHidden>{label}</VisuallyHidden>
    </span>
  );
}

/** Title text with the row's mark after it: every row, the one this tab streams too (D10). */
function MarkedTitleLabel({ title, mark }: { title: React.ReactNode; mark: React.ReactNode }) {
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
      {mark}
    </Flex>
  );
}

/** The row's mark in words, for a screen reader: colour is never the only sign (D10). */
export function markWords(t: (key: string) => string, mark: SidebarDot): string {
  if (mark.dot === 'needsYou') return t('sidebar.dot.needsYou');
  if (mark.dot === 'working') return t('sidebar.dot.working');
  return mark.unseen ? t('sidebar.dot.finished') : t('chat.answerReady.unread');
}

/** A slot of this chat, in this sidebar's scope, holds an ask open on its person (`slotWaitingCount`, D10). */
function waitsInScope(slots: Record<string, ChatSlot>, conversationId: string, scopeAgentId: string | null): boolean {
  return Object.values(slots).some(
    (slot) =>
      slot.convId === conversationId &&
      (scopeAgentId === null ? !slot.threadAgentId : slot.threadAgentId === scopeAgentId) &&
      slotWaitingCount(slot) > 0,
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
  /**
   * Called with the new pin state when the user pins or unpins this row, and
   * again with the old one if the request fails. For lists that keep their own
   * copy of the rows (the "More" panel) rather than reading the store.
   */
  onPinnedChange?: (pinned: boolean) => void;
}

/**
 * A single conversation item in the chat sidebar.
 */
export function ChatSectionElement({
  conversation,
  isActive,
  onClick,
  agentId: sectionAgentId,
  projectId,
  onPinnedChange,
}: ChatSectionElementProps) {
  const { t } = useTranslation();
  // A shared agent chat in the global list carries its own agent; the agent sidebar passes it per section.
  const agentId = sectionAgentId ?? conversation.agentKey;

  const [isHovered, setIsHovered] = useState(false);
  const [menuOpen, setMenuOpen] = useState(false);
  const [isRenaming, setIsRenaming] = useState(false);
  const [renameValue, setRenameValue] = useState(conversation.title);
  const [isSavingRename, setIsSavingRename] = useState(false);
  const [deleteDialogOpen, setDeleteDialogOpen] = useState(false);
  const [archiveDialogOpen, setArchiveDialogOpen] = useState(false);
  const [moveDialogOpen, setMoveDialogOpen] = useState(false);
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
  const isConversationWaiting = useChatStore((s) => waitsInScope(s.slots, conversation.id, agentId ?? null));

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
    setRenameValue(conversation.title);
    setIsRenaming(true);
  };

  const handleRenameBlur = async () => {
    if (!isRenaming || isSavingRename) return;
    if (convStreamingBlocksSidebarMutation()) {
      setRenameValue(conversation.title);
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

  const handleTogglePin = async () => {
    if (isPinRequestInFlight.current) return;
    isPinRequestInFlight.current = true;
    const pinned = conversation.isPinned !== true;
    setConversationPinned(conversation.id, pinned);
    onPinnedChange?.(pinned);
    try {
      await ChatApi.setConversationPinned(conversation.id, pinned);
      bumpConversationsVersion();
    } catch {
      setConversationPinned(conversation.id, !pinned);
      onPinnedChange?.(!pinned);
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
          title: conversation.title,
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

  const handleConfirmMoveToProject = async (projectId: string | null) => {
    if (convStreamingBlocksSidebarMutation()) {
      throw new Error('Cannot move a conversation while it is streaming.');
    }
    await ProjectApi.setConversationProject(conversation.id, projectId, {
      agentKey: agentId,
    });
    moveConversationToProject(conversation.id, projectId);
    bumpConversationsVersion();
  };

  const handleRemoveFromProject = async () => {
    if (convStreamingBlocksSidebarMutation()) return;
    try {
      await ProjectApi.setConversationProject(conversation.id, null, { agentKey: agentId });
      moveConversationToProject(conversation.id, null);
      bumpConversationsVersion();
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
            <MaterialIcon name="smart_toy" size={13} color="var(--accent-11)" />
          </span>
        </Tooltip>
      </>
    ) : (
      sharedByText
    );


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
        {isSavingRename && <Spinner size={12} color="var(--slate-11)" />}
      </Flex>
    );
  }

  const conversationHref = buildChatHref({ agentId, projectId, conversationId: conversation.id });
  const mark = sidebarDot(conversation.harnessState, {
    streaming: isConversationStreaming,
    unread: hasUnreadAnswer,
    active: isActive,
    waiting: isConversationWaiting,
  });

  return (
    <>
      <SidebarItem
        label={
          isConversationStreaming && !conversation.title ? (
            <GeneratingTitleLabel label={t('chat.generatingTitle')} />
          ) : mark ? (
            <MarkedTitleLabel
              title={isTypingTitle ? <TypingTitle title={conversation.title} /> : conversation.title}
              mark={<ChatDotMark dot={mark.dot} label={markWords(t, mark)} />}
            />
          ) : isTypingTitle ? (
            <TypingTitle title={conversation.title} />
          ) : (
            conversation.title
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
        rightSlot={
          conversation.isOwner === true ? (
            <ChatItemMenu
              isParentHovered={isHovered}
              onOpenChange={setMenuOpen}
              onRename={handleStartRename}
              onExport={() => void handleExport()}
              // Agent chats have no pin API, and project rows come from a list that
              // does not carry the pin, so only the main list offers it.
              onTogglePin={agentId || projectId ? undefined : () => void handleTogglePin()}
              isPinned={conversation.isPinned === true}
              onArchive={() => setArchiveDialogOpen(true)}
              onDelete={() => setDeleteDialogOpen(true)}
              showRename={true}
              showArchive={true}
              onMoveToProject={projectsEnabled ? () => setMoveDialogOpen(true) : undefined}
              onRemoveFromProject={
                projectsEnabled && conversation.projectId ? () => void handleRemoveFromProject() : undefined
              }
            />
          ) : undefined
        }
      />

      {/* Delete confirmation dialog */}
      <DeleteChatDialog
        open={deleteDialogOpen}
        onOpenChange={setDeleteDialogOpen}
        onConfirm={handleConfirmDelete}
        chatTitle={conversation.title}
        isDeleting={isDeleting}
      />

      <ArchiveChatDialog
        open={archiveDialogOpen}
        onOpenChange={setArchiveDialogOpen}
        onConfirm={handleConfirmArchive}
        chatTitle={conversation.title}
        isArchiving={isArchiving}
      />

      <MoveToProjectDialog
        open={moveDialogOpen}
        onOpenChange={setMoveDialogOpen}
        currentProjectId={conversation.projectId ?? null}
        onConfirm={handleConfirmMoveToProject}
      />
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
  const waiting = useChatStore((s) => slotWaitingCount(s.slots[slotId]) > 0);

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

  // D10: the row this tab streams is marked as any row is, so an ask it holds open shows Needs you over the spinner.
  const mark = sidebarDot(null, { streaming: true, unread: false, active: isActive, waiting }) ?? { dot: 'working' as const };
  const dot = <ChatDotMark dot={mark.dot} label={markWords(t, mark)} />;

  if (pendingTitle) {
    return (
      <SidebarItem
        isActive={isActive}
        href={href}
        onClick={handleClick}
        label={<MarkedTitleLabel title={pendingTitle} mark={dot} />}
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
        mark.dot === 'needsYou' ? (
          <MarkedTitleLabel title={<GeneratingTitleLabel label={t('chat.generatingTitle')} />} mark={dot} />
        ) : (
          <GeneratingTitleLabel label={t('chat.generatingTitle')} />
        )
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
