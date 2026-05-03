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
      align="start"
      gap="2"
      style={{
        marginTop: 'var(--space-2)',
        marginBottom: 'var(--space-2)',
        minWidth: 0,
      }}
    >
      {/* Keep the loader aligned with the first line of text even when the
          status wraps across multiple lines. */}
      <span style={{ flex: '0 0 auto', display: 'inline-flex', paddingTop: 2 }}>
        <LottieLoader variant="thinking" size={20} />
      </span>

      {/* Shimmer status text: base layer always legible, overlay sweeps a bright band.
          Override the shimmer's default `white-space: nowrap` (needed for the sidebar
          "Generating title…" case) so long SSE status messages wrap inside the chat
          column instead of overflowing horizontally. `minWidth: 0` lets this flex child
          shrink below its intrinsic content width. */}
      <span
        className="generating-shimmer"
        style={{
          fontSize: 'var(--font-size-2)',
          whiteSpace: 'normal',
          overflowWrap: 'anywhere',
          wordBreak: 'break-word',
          flex: '1 1 auto',
          minWidth: 0,
          maxWidth: '100%',
        }}
      >
        <span className="generating-shimmer-base">{statusText}</span>
        <span className="generating-shimmer-overlay" aria-hidden="true">{statusText}</span>
      </span>
    </Flex>
  );
}
