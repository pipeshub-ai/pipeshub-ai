'use client';

import React from 'react';
import { Flex, Card, Text } from '@radix-ui/themes';
import type { ChatSource } from '../../types';

interface MessageSourcesProps {
  sources: ChatSource[];
}

// Icon mapping for different source types
const getSourceIcon = (type: string): string => {
  const icons: Record<string, string> = {
    slack: '💬',
    jira: '📋',
    notion: '📝',
    sheets: '📊',
    confluence: '📄',
    github: '💻',
    drive: '📁',
  };
  return icons[type] || '📄';
};

export function MessageSources({ sources }: MessageSourcesProps) {
  if (!sources || sources.length === 0) return null;

  return (
    <Flex direction="column" gap="2" style={{ marginTop: 'var(--space-3)' }}>
      <Text size="1" weight="medium" style={{ color: 'var(--slate-11)' }}>
        Sources ({sources.length}):
      </Text>
      <Flex direction="column" gap="2">
        {sources.map((source) => (
          <Card
            key={source.id}
            style={{
              backgroundColor: 'var(--slate-2)',
              borderRadius: 'var(--radius-2)',
              padding: 'var(--space-2)',
              border: '1px solid var(--slate-6)',
            }}
          >
            <Flex direction="column" gap="1">
              <Flex gap="2" align="center">
                <Text size="1">{getSourceIcon(source.type)}</Text>
                <Text size="2" weight="medium" style={{ color: 'var(--slate-12)' }}>
                  {source.title}
                </Text>
                {source.category && (
                  <Text size="1" style={{ color: 'var(--slate-10)' }}>
                    • {source.category}
                  </Text>
                )}
              </Flex>

              {source.summary && (
                <Text
                  size="1"
                  style={{
                    color: 'var(--slate-11)',
                    lineHeight: '1.4',
                  }}
                >
                  {source.summary}
                </Text>
              )}

              {source.topics && source.topics.length > 0 && (
                <Flex gap="1" wrap="wrap" style={{ marginTop: 'var(--space-1)' }}>
                  {source.topics.map((topic, idx) => (
                    <Text
                      key={idx}
                      size="1"
                      style={{
                        backgroundColor: 'var(--accent-3)',
                        color: 'var(--accent-11)',
                        padding: '2px 6px',
                        borderRadius: 'var(--radius-1)',
                      }}
                    >
                      {topic}
                    </Text>
                  ))}
                </Flex>
              )}

              {source.url && (
                <a
                  href={source.url}
                  target="_blank"
                  rel="noopener noreferrer"
                  style={{ textDecoration: 'none', marginTop: 'var(--space-1)' }}
                >
                  <Text size="1" style={{ color: 'var(--accent-11)' }}>
                    View source →
                  </Text>
                </a>
              )}
            </Flex>
          </Card>
        ))}
      </Flex>
    </Flex>
  );
}
