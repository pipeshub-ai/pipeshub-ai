'use client';

import { useState, useRef, useEffect } from 'react';
import { useRouter, useSearchParams } from 'next/navigation';
import { buildChatHref } from '@/chat/build-chat-url';
import { ChatStarIcon } from '@/app/components/ui/chat-star-icon';
import { Box, Flex } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';
import { Conversation } from '@/chat/types';
import { useChatStore } from '@/chat/store';
import { ChatApi } from '@/chat/api';
import { AgentsApi } from '@/app/(main)/agents/api';
import { ICON_SIZE_DEFAULT, CHAT_ITEM_HEIGHT } from '@/app/components/sidebar';
import { SidebarItem } from './sidebar-item';
import { ChatItemMenu } from './chat-item-menu';
import { DeleteChatDialog, ArchiveChatDialog } from './dialogs';
import { Spinner } from '@/app/components/ui/spinner';

/** Duration must match `typing-reveal` animation duration in globals.css */
const TYPING_ANIMATION_DURATION_MS = 400;

function TypingTitle({ title }: { title: string }) {
  return (
    <span className="title-typing-animation">
      {title}
    </span>
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
}

/**
 * A single conversation item in the chat sidebar.
 */
export function ChatSectionElement({ conversation, isActive, onClick, agentId }: ChatSectionElementProps) {
  const [isHovered, setIsHovered] = useState(false);
  const [menuOpen, setMenuOpen] = useState(false);
  const [isRenaming, setIsRenaming] = useState(false);
  const [renameValue, setRenameValue] = useState(conversation.title);
  const [isSavingRename, setIsSavingRename] = useState(false);
  const [deleteDialogOpen, setDeleteDialogOpen] = useState(false);
  const [archiveDialogOpen, setArchiveDialogOpen] = useState(false);
  const [isDeleting, setIsDeleting] = useState(false);
  const [isArchiving, setIsArchiving] = useState(false);
  const [isTypingTitle, setIsTypingTitle] = useState(false);
  const renameInputRef = useRef<HTMLInputElement>(null);
  const router = useRouter();
  const searchParams = useSearchParams();

  const newlyResolvedIds = useChatStore((s) => s.newlyResolvedIds);
  const clearNewlyResolvedId = useChatStore((s) => s.clearNewlyResolvedId);

  useEffect(() => {
    if (newlyResolvedIds.has(conversation.id)) {
      setIsTypingTitle(true);
      clearNewlyResolvedId(conversation.id);
      const timer = setTimeout(() => setIsTypingTitle(false), TYPING_ANIMATION_DURATION_MS);
      return () => clearTimeout(timer);
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [newlyResolvedIds]);

  const removeConversation = useChatStore((s) => s.removeConversation);
  const renameConversation = useChatStore((s) => s.renameConversation);
  const bumpConversationsVersion = useChatStore((s) => s.bumpConversationsVersion);

  useEffect(() => {
    if (isRenaming) {
      renameInputRef.current?.focus();
      renameInputRef.current?.select();
    }
  }, [isRenaming]);

  const handleStartRename = () => {
    setRenameValue(conversation.title);
    setIsRenaming(true);
  };

  const handleRenameBlur = async () => {
    if (!isRenaming || isSavingRename) return;
    const trimmed = renameValue.trim();
    if (!trimmed || trimmed === conversation.title) {
      setIsRenaming(false);
      return;
    }
    setIsSavingRename(true);
    try {
      await ChatApi.renameConversation(conversation.id, trimmed);
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
      const urlConvId = searchParams.get('conversationId');
      if (urlConvId === conversation.id) {
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
    setIsArchiving(true);
    try {
      await ChatApi.archiveConversation(conversation.id);
      removeConversation(conversation.id);
      bumpConversationsVersion();
      setArchiveDialogOpen(false);
    } catch {
      // keep dialog open on error
    } finally {
      setIsArchiving(false);
    }
  };

  // Inline rename mode — render a plain input instead of SidebarItem (main chats only)
  if (isRenaming && !agentId) {
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

  const conversationHref = buildChatHref({ agentId, conversationId: conversation.id });

  return (
    <>
      <SidebarItem
        label={isTypingTitle ? <TypingTitle title={conversation.title} /> : conversation.title}
        isActive={isActive}
        href={conversationHref}
        onClick={onClick}
        textColor="var(--slate-12)"
        fontWeight={500}
        forceHighlight={menuOpen}
        onHoverChange={setIsHovered}
        rightSlot={
          conversation.isOwner === true ? (
            <ChatItemMenu
              isParentHovered={isHovered}
              onOpenChange={setMenuOpen}
              onRename={handleStartRename}
              onArchive={() => setArchiveDialogOpen(true)}
              onDelete={() => setDeleteDialogOpen(true)}
              showRename={!agentId}
              showArchive={!agentId}
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

      {!agentId && (
        <ArchiveChatDialog
          open={archiveDialogOpen}
          onOpenChange={setArchiveDialogOpen}
          onConfirm={handleConfirmArchive}
          chatTitle={conversation.title}
          isArchiving={isArchiving}
        />
      )}
    </>
  );
}

/**
 * "Generating Title…" shimmer item shown when a new chat is being streamed.
 * Uses a gradient sweep animation that moves left-to-right across the text,
 * matching the Figma design (node 2245:22924).
 *
 * Clickable — switches to the streaming temp slot so the user can
 * return to a new chat that's still generating in the background.
 */
export function GeneratingTitleItem({ slotId }: { slotId: string }) {
  const { t } = useTranslation();
  const router = useRouter();
  const searchParams = useSearchParams();
  const currentConversationId = searchParams.get('conversationId');
  const activeSlotId = useChatStore((s) => s.activeSlotId);
  const slotConvId = useChatStore((s) => s.slots[slotId]?.convId ?? null);
  /**
   * Match other sidebar rows: highlight from the URL when we know the real id
   * (server sends it in the SSE `connected` event). Before that, use the
   * active slot so a temp new chat without a URL id still looks selected.
   */
  const isActive = slotConvId
    ? currentConversationId === slotConvId
    : activeSlotId === slotId;

  const rawAgent = searchParams.get('agentId');
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
