'use client';

import React from 'react';
import { Flex, Text, Tooltip } from '@radix-ui/themes';
import type { ConfidenceLevel } from '@/chat/types';

interface ConfidenceIndicatorProps {
  confidence: ConfidenceLevel;
}

const getConfidenceColor = (confidence: ConfidenceLevel): string => {
  switch (confidence) {
    case 'Very High':
      return 'var(--accent-11)';
    case 'High':
      return 'var(--accent-11)';
    case 'Medium':
      return 'var(--amber-11)';
    case 'Low':
      return 'var(--red-11)';
    default:
      return 'var(--slate-11)';
  }
};

export function ConfidenceIndicator({ confidence }: ConfidenceIndicatorProps) {
  const color = getConfidenceColor(confidence);

  return (
    <Tooltip content="Confidence level of assistant's response">
      <Flex 
        align="center" 
        gap="1" 
        style={{ 
          marginBottom: 'var(--space-3)', 
          border: '0.667px solid var(--olive-3)',
          width: 'fit-content',
          padding: 'var(--space-1) var(--space-2)',
          borderRadius: 'var(--radius-2)',
          background: 'var(--olive-2)',
          cursor: 'default',
        }}
      >
        <span
          className="material-icons-outlined"
          style={{
            fontSize: '11px',
            color,
          }}
        >
          auto_awesome
        </span>
        <Text
          size="1"
          weight="medium"
          style={{ color }}
        >
          {confidence}
        </Text>
      </Flex>
    </Tooltip>
  );
}
