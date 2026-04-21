'use client';

import React from 'react';
import { Flex, Text } from '@radix-ui/themes';
import { LottieLoader } from '@/app/components/ui/lottie-loader';
import type { StatusMessage } from '@/chat/types';

interface StatusMessageProps {
  status: StatusMessage;
}

export function StatusMessageComponent({ status }: StatusMessageProps) {
  // Use the actual message from the stream
  const statusText = status.message || 'Processing...';

  return (
    <Flex
      align="center"
      gap="2"
      style={{
        marginTop: 'var(--space-2)',
        marginBottom: 'var(--space-2)',
      }}
    >
      <LottieLoader variant="thinking" size={20} />

      {/* Gradient status text with shimmer sweep */}
      <Text
        size="2"
        style={{
          background:
            'linear-gradient(90deg, var(--accent-9) 0%, var(--accent-11) 40%, var(--accent-9) 60%, var(--accent-9) 100%)',
          backgroundSize: '200% 100%',
          WebkitBackgroundClip: 'text',
          WebkitTextFillColor: 'transparent',
          backgroundClip: 'text',
          animation: 'shimmer-sweep 2s ease-in-out infinite',
        }}
      >
        {statusText}
      </Text>
    </Flex>
  );
}
