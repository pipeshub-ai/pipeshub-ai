'use client';

import React from 'react';
import { Text } from '@radix-ui/themes';

/** Why a service row is not ready, wrapped in full under its description (never cut to one line). */
export function ServiceReason({ text }: { text?: string | null }) {
  if (!text) return null;
  return (
    <Text size="1" style={{ color: 'var(--slate-11)', display: 'block', marginTop: 6, lineHeight: '16px' }}>
      {text}
    </Text>
  );
}

export interface ReasonedServiceMeta {
  key: string;
  icon: string;
  label: string;
  description: string;
  reason?: string;
}
