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

      {/* Shimmer status text: base layer always legible, overlay sweeps a bright band */}
      <span className="generating-shimmer" style={{ fontSize: 'var(--font-size-2)' }}>
        <span className="generating-shimmer-base">{statusText}</span>
        <span className="generating-shimmer-overlay" aria-hidden="true">{statusText}</span>
      </span>
    </Flex>
  );
}
