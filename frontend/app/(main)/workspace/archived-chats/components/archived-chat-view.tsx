'use client';

import React, { useMemo } from 'react';
import { Flex, Box, Text } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';
import { ChatResponse, emptyCitationMaps } from '@/chat/components';
import { ChatPixelIcon } from '@/app/components/ui/chat-pixel-icon';
import type { ConversationMessage } from '../types';

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
  conversationTitle: string;
  messages: ConversationMessage[];
  isLoading: boolean;
  error: string | null;
}

export function ArchivedChatView({
  conversationTitle: _conversationTitle,
  messages,
  isLoading,
  error,
}: ArchivedChatViewProps) {
  const { t } = useTranslation();

  const messagePairs = useMemo(() => buildMessagePairs(messages), [messages]);

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

    </Flex>
  );
}

/**
 * Shown when no conversation has been selected yet.
 */
export function ArchivedChatsEmptyState() {
  return <EmptyState />;
}
