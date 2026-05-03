'use client';

import React from 'react';
import { Button, Text } from '@radix-ui/themes';
import Image from 'next/image';

interface ShareButtonProps {
  onClick: () => void;
}

export function ShareButton({ onClick }: ShareButtonProps) {
  return (
    <Button
      size="1"
      variant="ghost"
      color="gray"
      onClick={onClick}
      style={{
        padding: '0 var(--space-2)',
        gap: 'var(--space-1)',
        borderRadius: 'var(--radius-1)',
        cursor: 'pointer',
        flexShrink: 0,
        color: 'var(--slate-11)',
        height: '20px',
      }}
    >
      <Image
        src="/icons/share/share-2.svg"
        alt=""
        width={14}
        height={14}
      />
      <Text
        size="1"
        weight="regular"
        style={{ whiteSpace: 'nowrap', color: 'var(--slate-11)', letterSpacing: '0.04px' }}
      >
        Share
      </Text>
    </Button>
  );
}
