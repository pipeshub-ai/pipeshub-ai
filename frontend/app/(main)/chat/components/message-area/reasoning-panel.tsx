'use client';

import React, { useEffect, useState } from 'react';
import { Box, Flex, Text } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';

export interface ReasoningPanelProps {
  reasoning: string;
  isStreaming?: boolean;
  /** When true, answer text is visible — panel starts collapsed unless user expands */
  hasAnswerContent?: boolean;
}

export const ReasoningPanel = React.memo(function ReasoningPanel({
  reasoning,
  isStreaming = false,
  hasAnswerContent = false,
}: ReasoningPanelProps) {
  const { t } = useTranslation();
  const trimmed = reasoning.trim();
  const [expanded, setExpanded] = useState(true);

  useEffect(() => {
    if (isStreaming && trimmed && !hasAnswerContent) {
      setExpanded(true);
    } else if (hasAnswerContent && !isStreaming) {
      setExpanded(false);
    }
  }, [isStreaming, trimmed, hasAnswerContent]);

  if (!trimmed) {
    return null;
  }

  const title = t('chatStream.reasoningTitle', { defaultValue: 'Thinking' });

  return (
    <Box
      style={{
        marginBottom: 'var(--space-3)',
        borderRadius: 'var(--radius-3)',
        border: '1px solid var(--olive-6)',
        backgroundColor: 'var(--olive-2)',
        overflow: 'hidden',
      }}
    >
      <button
        type="button"
        onClick={() => setExpanded((v) => !v)}
        aria-expanded={expanded}
        style={{
          appearance: 'none',
          margin: 0,
          padding: 'var(--space-2) var(--space-3)',
          width: '100%',
          border: 'none',
          background: 'transparent',
          cursor: 'pointer',
          display: 'flex',
          alignItems: 'center',
          gap: 'var(--space-2)',
          color: 'var(--olive-11)',
          font: 'inherit',
          textAlign: 'left',
        }}
      >
        <MaterialIcon
          name={expanded ? 'expand_more' : 'chevron_right'}
          size={18}
          style={{ color: 'var(--olive-9)', flexShrink: 0 }}
        />
        <Text size="2" weight="medium" style={{ flex: 1 }}>
          {title}
        </Text>
        {isStreaming && (
          <Text size="1" color="gray" style={{ flexShrink: 0 }}>
            …
          </Text>
        )}
      </button>
      {expanded && (
        <Box
          style={{
            padding: '0 var(--space-3) var(--space-3)',
            maxHeight: isStreaming ? 280 : 200,
            overflowY: 'auto',
          }}
        >
          <Text
            size="2"
            style={{
              color: 'var(--olive-11)',
              whiteSpace: 'pre-wrap',
              wordBreak: 'break-word',
              lineHeight: 1.5,
              fontFamily: 'var(--font-mono, ui-monospace, monospace)',
            }}
          >
            {trimmed}
          </Text>
        </Box>
      )}
    </Box>
  );
});
