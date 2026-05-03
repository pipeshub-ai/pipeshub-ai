'use client';

import React, { useState } from 'react';
import { Flex, Box, Text, IconButton } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';
import { LottieLoader } from '@/app/components/ui/lottie-loader';
import { ChatStarIcon } from '@/app/components/ui/chat-star-icon';

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
        style={{
          height: '1px',
          backgroundColor: 'var(--olive-3)',
          marginBottom: 'var(--space-5)',
          opacity: 0,
          animation: 'askMoreItemIn 200ms ease-out 0ms forwards',
        }}
      />

      {/* Header: logo + "Ask More" — appears with first item */}
      <Flex
        align="center"
        gap="2"
        style={{
          marginBottom: 'var(--space-4)',
          animation: 'askMoreItemIn 200ms ease-out 0ms forwards'
        }}
      >
       <LottieLoader variant="thinking" size={24} />
        <Text size="3" weight="medium" style={{ color: 'var(--slate-12)' }}>
          {t('chat.askMore')}
        </Text>
      </Flex>

      {/* Question rows — each item staggers in 80ms apart */}
      <Flex direction="column" gap="1">
        {questions.map((question, index) => (
          <Flex
            key={index}
            align="center"
            justify="between"
            onClick={() => onQuestionClick(question)}
            onMouseEnter={() => setHoveredIndex(index)}
            onMouseLeave={() => setHoveredIndex(null)}
            style={{
              background: hoveredIndex === index ? 'var(--olive-3)' : 'var(--olive-2)',
              border: '1px solid var(--olive-3)',
              borderRadius: 'var(--radius-1)',
              padding: 'var(--space-2) var(--space-2) var(--space-2) var(--space-3)',
              cursor: 'pointer',
              transition: 'background-color 150ms ease',
              opacity: 0,
              animation: `askMoreItemIn 220ms ease-out ${(questions.length - 1 - index) * 80}ms forwards`,
            }}
          >
            <Text
              size="2"
              weight="medium"
              style={{
                color: 'var(--slate-11)',
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
                backgroundColor: 'var(--olive-4)',
                marginLeft: 'var(--space-2)',
                flexShrink: 0,
              }}
            >
              <ChatStarIcon
                size={16}
                color="var(--slate-11)"
              />
            </IconButton>
          </Flex>
        ))}
      </Flex>

      {/* Inline keyframes */}
      <style>{`
        @keyframes askMoreItemIn {
          from { opacity: 0; transform: translateY(10px); }
          to   { opacity: 1; transform: translateY(0); }
        }
      `}</style>
    </Box>
  );
}
