'use client';

import React, { useState } from 'react';
import { Flex, Box, Text, IconButton } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';
import { LottieLoader } from '@/app/components/ui/lottie-loader';
import { ChatStarIcon } from '@/app/components/ui/chat-star-icon';

/** The beat between one conversation entrance and the next in a list. */
const ENTRANCE_STAGGER_MS = 55;

interface AskMoreProps {
  questions: string[];
  onQuestionClick: (question: string) => void;
}

export function AskMore({ questions, onQuestionClick }: AskMoreProps) {
  const [hoveredIndex, setHoveredIndex] = useState<number | null>(null);
  const { t } = useTranslation();

  if (questions.length === 0) return null;

  return (
    <Box>
      {/* Separator line — fades in with the first item */}
      <Box
        className="conversation-fade-in no-rise"
        style={{
          height: '1px',
          backgroundColor: 'var(--fill-muted)',
          marginBottom: 'var(--space-5)',
        }}
      />

      {/* Header: logo + "Ask More" — appears with first item */}
      <Flex
        align="center"
        gap="2"
        className="conversation-fade-in"
        style={{ marginBottom: 'var(--space-4)' }}
      >
       <LottieLoader variant="thinking" size={24} />
        <Text size="3" weight="medium" style={{ color: 'var(--text-primary)' }}>
          {t('chat.askMore')}
        </Text>
      </Flex>

      {/* Question rows: each one enters a beat after the one below it */}
      <Flex direction="column" gap="1">
        {questions.map((question, index) => (
          <Flex
            key={index}
            align="center"
            justify="between"
            onClick={() => onQuestionClick(question)}
            onMouseEnter={() => setHoveredIndex(index)}
            onMouseLeave={() => setHoveredIndex(null)}
            className="conversation-fade-in"
            style={{
              background: hoveredIndex === index ? 'var(--fill-muted)' : 'var(--surface-subtle)',
              border: '1px solid var(--border-subtle)',
              borderRadius: 'var(--radius-1)',
              padding: 'var(--space-2) var(--space-2) var(--space-2) var(--space-3)',
              cursor: 'pointer',
              transition: 'background-color var(--motion-micro) var(--motion-ease)',
              animationDelay: `${(questions.length - 1 - index) * ENTRANCE_STAGGER_MS}ms`,
            }}
          >
            <Text
              size="2"
              weight="medium"
              style={{
                color: 'var(--text-secondary)',
                flex: 1,
                minWidth: 0,
              }}
            >
              {question}
            </Text>

            {/* Assistant icon */}
            <IconButton
              size="1"
              variant="soft"
              style={{
                borderRadius: 'var(--radius-1)',
                backgroundColor: 'var(--fill-hover)',
                marginLeft: 'var(--space-2)',
                flexShrink: 0,
              }}
            >
              <ChatStarIcon
                size={16}
                color="var(--text-secondary)"
              />
            </IconButton>
          </Flex>
        ))}
      </Flex>

    </Box>
  );
}
