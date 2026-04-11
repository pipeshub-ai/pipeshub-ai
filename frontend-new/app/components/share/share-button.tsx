'use client';

import React, { useState } from 'react';
import { Button, Text } from '@radix-ui/themes';
import Image from 'next/image';

interface ShareButtonProps {
  onClick: () => void;
}

export function ShareButton({ onClick }: ShareButtonProps) {
  const [isHovered, setIsHovered] = useState(false);

  return (
    <Button
      variant="ghost"
      onClick={onClick}
      onMouseEnter={() => setIsHovered(true)}
      onMouseLeave={() => setIsHovered(false)}
      style={{
        padding: '0 var(--space-3)',
        gap: 'var(--space-2)',
        borderRadius: 'var(--radius-2)',
        cursor: 'pointer',
        flexShrink: 0,
        color: 'var(--slate-11)',
        backgroundColor: isHovered ? 'var(--slate-a3)' : undefined,
        height: 'var(--space-5)',
      }}
    >
      <Image
        src="/icons/share/share-2.svg"
        alt="Share"
        width={16}
        height={16}
      />
      <Text size="1" weight="medium" style={{ whiteSpace: 'nowrap', color: 'var(--slate-11)' }}>
        Share
      </Text>
    </Button>
  );
}
