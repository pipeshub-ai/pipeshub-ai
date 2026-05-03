'use client';

import React from 'react';
import { Text } from '@radix-ui/themes';

interface FieldLabelProps {
  label: string;
  labelSuffix?: string;
  required?: boolean;
}

export function FieldLabel({ label, labelSuffix, required }: FieldLabelProps) {
  return (
    <Text size="2" weight="medium" style={{ color: 'var(--slate-12)' }}>
      {label}
      {required && (
        <Text as="span" style={{ color: 'var(--red-9)' }}>
          {' '}*
        </Text>
      )}
      {labelSuffix && (
        <Text as="span" size="1" style={{ color: 'var(--slate-10)', fontWeight: 400 }}>
          {' '}{labelSuffix}
        </Text>
      )}
    </Text>
  );
}
