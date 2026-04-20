'use client';

import React from 'react';
import { Flex, Card, Text, Avatar } from '@radix-ui/themes';
import { MessageSources } from './message-sources';
import type { ChatSource } from '../../types';

interface MessageBubbleProps {
  role: 'user' | 'assistant';
  content: string;
  sources?: ChatSource[];
}

export function MessageBubble({ role, content, sources }: MessageBubbleProps) {
  const isUser = role === 'user';

  return (
    <Flex
      gap="2"
      align="start"
      justify={isUser ? 'end' : 'start'}
      style={{
        width: '100%',
        paddingLeft: isUser ? '20%' : '0',
        paddingRight: isUser ? '0' : '20%',
      }}
    >
      {!isUser && (
        <Avatar
          size="2"
          fallback="AI"
          style={{
            backgroundColor: 'var(--accent-9)',
            color: 'white',
            flexShrink: 0,
          }}
        />
      )}

      <Flex direction="column" gap="2" style={{ flex: 1, minWidth: 0 }}>
        <Card
          style={{
            backgroundColor: isUser ? 'var(--accent-3)' : 'var(--slate-3)',
            borderRadius: 'var(--radius-3)',
            padding: 'var(--space-3)',
            maxWidth: '100%',
          }}
        >
          <Text
            size="2"
            style={{
              color: 'var(--slate-12)',
              whiteSpace: 'pre-wrap',
              wordBreak: 'break-word',
            }}
          >
            {content}
          </Text>
        </Card>

        {!isUser && sources && sources.length > 0 && (
          <MessageSources sources={sources} />
        )}
      </Flex>

      {isUser && (
        <Avatar
          size="2"
          fallback="U"
          style={{
            backgroundColor: 'var(--slate-9)',
            color: 'white',
            flexShrink: 0,
          }}
        />
      )}
    </Flex>
  );
}
