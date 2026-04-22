'use client';

import React, { useState, useMemo } from 'react';
import {
  Flex,
  Box,
  Text,
  IconButton,
  DropdownMenu,
} from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';
import { ChatResponse, emptyCitationMaps } from '@/chat/components';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { ChatPixelIcon } from '@/app/components/ui/chat-pixel-icon';
import { Spinner } from '@/app/components/ui/spinner';
import type { ConversationMessage } from '../types';
import { DeleteConfirmDialog } from './delete-confirm-dialog';
import { ArchivedChatsApi } from '../api';
import { useToastStore } from '@/lib/store/toast-store';

// Stable empty citation maps — avoids creating new objects per render.
const EMPTY_CITATION_MAPS = emptyCitationMaps();

interface MessagePair {
  key: string;
  question: string;
  answer: string;
}

/**
 * Group raw ConversationMessage[] into user→bot pairs.
 * A user_query without a following bot_response is kept with an empty answer.
 */
function buildMessagePairs(messages: ConversationMessage[]): MessagePair[] {
  const pairs: MessagePair[] = [];

  for (let i = 0; i < messages.length; i++) {
    const msg = messages[i];
    if (msg.messageType !== 'user_query') continue;

    const next = messages[i + 1];
    const answer =
      next?.messageType === 'bot_response' ? next.content : '';

    pairs.push({
      key: msg._id,
      question: msg.content,
      answer,
    });

    // Skip the bot_response in the outer loop
    if (answer) i++;
  }

  return pairs;
}

// ======================================================
// Sub-components
// ======================================================

function EmptyState() {
  const { t } = useTranslation();
  return (
    <Flex
      direction="column"
      align="center"
      justify="center"
      style={{ height: '100%', width: '100%', gap: 'var(--space-3)' }}
    >
      <ChatPixelIcon
        size={72}
        style={{ opacity: 0.55, color: 'var(--accent-11)' }}
      />
      <Text size="2" style={{ color: 'var(--slate-11)' }}>
        {t('workspace.archivedChats.selectPrompt')}
      </Text>
    </Flex>
  );
}

function LoadingState() {
  const { t } = useTranslation();
  return (
    <Flex align="center" justify="center" style={{ height: '100%', width: '100%' }}>
      <Text size="2" style={{ color: 'var(--slate-9)' }}>
        {t('action.loading')}
      </Text>
    </Flex>
  );
}

// ======================================================
// Main component
// ======================================================

interface ArchivedChatViewProps {
  conversationId: string;
  conversationTitle: string;
  messages: ConversationMessage[];
  isLoading: boolean;
  error: string | null;
  /** Called after a successful restore — page should navigate to /chat */
  onRestored: (conversationId: string) => void;
  /** Called after a successful permanent delete — page selects next conv */
  onDeleted: (conversationId: string) => void;
}

export function ArchivedChatView({
  conversationId,
  conversationTitle: _conversationTitle,
  messages,
  isLoading,
  error,
  onRestored,
  onDeleted,
}: ArchivedChatViewProps) {
  const { t } = useTranslation();
  const addToast = useToastStore((s) => s.addToast);

  const [deleteDialogOpen, setDeleteDialogOpen] = useState(false);
  const [isRestoring, setIsRestoring] = useState(false);
  const [isDeleting, setIsDeleting] = useState(false);

  const messagePairs = useMemo(() => buildMessagePairs(messages), [messages]);

  // ── Actions ────────────────────────────────────────────────────────

  const handleRestore = async () => {
    if (isRestoring) return;
    setIsRestoring(true);
    try {
      await ArchivedChatsApi.restoreConversation(conversationId);
      addToast({
        variant: 'success',
        title: t('workspace.archivedChats.restoreSuccess'),
        icon: 'restore',
      });
      onRestored(conversationId);
    } catch {
      addToast({
        variant: 'error',
        title: t('workspace.archivedChats.restoreError'),
      });
    } finally {
      setIsRestoring(false);
    }
  };

  const handleDeleteConfirm = async () => {
    if (isDeleting) return;
    setIsDeleting(true);
    try {
      await ArchivedChatsApi.deleteConversation(conversationId);
      setDeleteDialogOpen(false);
      addToast({
        variant: 'success',
        title: t('workspace.archivedChats.deleteSuccess'),
        description: t('workspace.archivedChats.deleteSuccessDescription'),
        icon: 'check',
      });
      onDeleted(conversationId);
    } catch {
      addToast({
        variant: 'error',
        title: t('workspace.archivedChats.deleteError'),
      });
    } finally {
      setIsDeleting(false);
    }
  };

  // ── Render ─────────────────────────────────────────────────────────

  if (isLoading) {
    return <LoadingState />;
  }

  if (error) {
    return (
      <Flex
        align="center"
        justify="center"
        style={{ height: '100%', width: '100%' }}
      >
        <Text size="2" style={{ color: 'var(--red-9)' }}>
          {error}
        </Text>
      </Flex>
    );
  }

  return (
    <Flex
      direction="column"
      style={{ height: '100%', width: '100%', position: 'relative' }}
    >
      {/* ── Top-right actions ── */}
      <Box
        style={{
          position: 'absolute',
          top: 'var(--space-3)',
          right: 'var(--space-3)',
          zIndex: 10,
        }}
      >
        <DropdownMenu.Root modal={false}>
          <DropdownMenu.Trigger>
            <IconButton
              variant="ghost"
              size="1"
              disabled={isRestoring || isDeleting}
              style={{ cursor: 'pointer', color: 'var(--slate-10)' }}
            >
              <MaterialIcon name="more_horiz" size={20} />
            </IconButton>
          </DropdownMenu.Trigger>
          <DropdownMenu.Content
            side="bottom"
            align="end"
            sideOffset={4}
            style={{ minWidth: 160 }}
          >
            <DropdownMenu.Item
              disabled={isRestoring}
              onClick={(event) => {
                event.preventDefault();
                void handleRestore();
              }}
            >
              <Flex align="center" gap="2">
                {isRestoring ? (
                  <Spinner size={16} color="var(--slate-11)" />
                ) : (
                  <MaterialIcon name="restore" size={16} color="var(--slate-11)" />
                )}
                <Text size="2">
                  {isRestoring
                    ? t('action.loading')
                    : t('workspace.archivedChats.restore')}
                </Text>
              </Flex>
            </DropdownMenu.Item>
            <DropdownMenu.Item
              color="red"
              onClick={() => setDeleteDialogOpen(true)}
            >
              <Flex align="center" gap="2">
                <MaterialIcon name="delete" size={16} />
                <Text size="2">{t('workspace.archivedChats.permanentlyDelete')}</Text>
              </Flex>
            </DropdownMenu.Item>
          </DropdownMenu.Content>
        </DropdownMenu.Root>
      </Box>

      {/* ── Message area ── */}
      <Box
        className="no-scrollbar"
        style={{
          flex: 1,
          overflowY: 'auto',
          padding: '40px 0 var(--space-8)',
          maxWidth: 720,
          width: '100%',
          margin: '0 auto',
        }}
      >
        {messagePairs.length === 0 ? (
          <EmptyState />
        ) : (
          <Flex direction="column" gap="6" style={{ padding: '0 var(--space-6)' }}>
            {/* Message pairs */}
            {messagePairs.map((pair) => (
              <ChatResponse
                key={pair.key}
                question={pair.question}
                answer={pair.answer}
                citationMaps={EMPTY_CITATION_MAPS}
                isStreaming={false}
                isLastMessage={false}
              />
            ))}
          </Flex>
        )}
      </Box>

      {/* ── Permanent delete confirmation ── */}
      <DeleteConfirmDialog
        open={deleteDialogOpen}
        isLoading={isDeleting}
        onOpenChange={setDeleteDialogOpen}
        onConfirm={handleDeleteConfirm}
      />
    </Flex>
  );
}

/**
 * Shown when no conversation has been selected yet.
 */
export function ArchivedChatsEmptyState() {
  return <EmptyState />;
}
